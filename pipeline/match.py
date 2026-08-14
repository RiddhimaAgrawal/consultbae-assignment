"""
Entity resolution: decide which raw rows across the 3 sources refer to the
SAME real person, and produce canonical "people" groups.

Strategy (tiered, confidence-scored -- see README for the reasoning):

  Tier 1 (confidence 1.0)  - exact normalized email match
  Tier 1 (confidence 0.95) - exact normalized phone match
  Tier 2 (confidence 0.80) - normalized name + normalized city match
                              ("composite" -- two weak signals together)
  Tier 3 (confidence 0.60-0.75) - fuzzy name match (e.g. "R. Verma" vs
                              "Rohit Verma") + same city as supporting
                              evidence

  Thresholds:
    >= 0.90  -> auto-merge into one person
    0.70-0.89 -> merge but FLAG for manual review
    < 0.70   -> keep as separate people

  Union-Find (disjoint set) is used to merge rows into groups so that
  matches are transitive within a source pair but we still keep the
  per-source rows separate (nothing is overwritten -- see database.py).

  IMPORTANT CAVEAT we deliberately encode: "same name + same city" is NOT
  automatically merged at high confidence, because that's exactly the trap
  in this dataset -- there are TWO Arjun Mehta records in source3 with
  DIFFERENT phone numbers, and only one of them should link to the
  source1/source2 Arjun Mehta. We only auto-merge on name+city when phone
  numbers either agree or are entirely absent on one side; if two
  candidate rows share a name+city but have CONFLICTING phone numbers,
  we treat that as evidence AGAINST merging, not for it.
"""

from dataclasses import dataclass, field
import difflib


@dataclass
class MatchDecision:
    row_a: str
    row_b: str
    tier: str
    confidence: float
    reason: str
    action: str  # "auto_merge" | "flag_review" | "reject"


DECISIONS: list[MatchDecision] = []


def _key(rec: dict) -> str:
    return f"{rec['source']}#{rec['row_number']}"


def _phones_conflict(a: dict, b: dict) -> bool:
    pa, pb = a.get("phone_norm") or a.get("phone"), b.get("phone_norm") or b.get("phone")
    return bool(pa and pb and pa != pb)


def _name_similarity(n1: str, n2: str) -> float:
    """
    NARROW on purpose. Earlier version used generic difflib.SequenceMatcher
    ratio on full names, which produced false positives: "Varun Saxena" vs
    "Vikram Saxena" (0.85 ratio) and "Isha Chopra" vs "Sneha Chopra" (0.87
    ratio) both scored high enough to auto-flag as the same person, even
    though they are clearly different people who just share a surname and
    city. Generic string similarity on full names is not a safe match
    signal in a dataset that intentionally has many people sharing a
    surname (Chopra, Saxena, Mehta...).

    We ONLY treat names as a fuzzy match when one side is a genuine
    single-initial abbreviation of the other's first name AND the surname
    is identical: "R. Verma" vs "Rohit Verma" -> match. "Varun Saxena" vs
    "Vikram Saxena" -> NOT a match (both are full first names, not an
    abbreviation of each other).
    """
    if not n1 or not n2:
        return 0.0
    n1, n2 = n1.lower(), n2.lower()
    parts1, parts2 = n1.replace(".", "").split(), n2.replace(".", "").split()
    if len(parts1) != 2 or len(parts2) != 2:
        return 0.0
    if parts1[-1] != parts2[-1]:  # surname must match exactly
        return 0.0
    first1, first2 = parts1[0], parts2[0]
    if first1 == first2:
        return 0.0  # identical first names -> not this tier's job (tier2 handles it)
    one_is_initial = len(first1) == 1 or len(first2) == 1
    if one_is_initial and first1[0] == first2[0]:
        return 0.85
    return 0.0


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def _to_common(rec: dict) -> dict:
    """Map source-specific field names onto a common shape for comparison."""
    if rec["source"] == "source1":
        return {"name": rec["full_name_norm"], "email": rec["email_norm"],
                "phone": rec["phone_norm"], "city": rec["city_norm"]}
    if rec["source"] == "source2":
        return {"name": rec["worker_name_norm"], "email": rec["email_norm"],
                "phone": None, "city": rec["city_norm"]}
    if rec["source"] == "source3":
        return {"name": rec["name_norm"], "email": None,
                "phone": rec["phone_norm"], "city": rec["city_norm"]}
    raise ValueError(rec["source"])


def _group_has_phone_conflict(uf: "UnionFind", common: dict, key_a: str, key_b: str,
                               members_by_root: dict) -> bool:
    """
    Guard against the transitive-merge bug: union-find chains merges, so
    even if row A and row B were never directly compared (or were directly
    REJECTED for conflicting phones), they can still end up in the same
    group if some third row C matches both of them on a weaker signal
    (e.g. name+city with no phone on C's side). That happened for real
    with the two "Arjun Mehta" rows in source3 (different phones): a
    source2 row with no phone at all bridged them together silently.

    Before performing ANY union, we check every phone number already
    present in each of the two groups-about-to-merge. If the union would
    bring two DIFFERENT non-null phone numbers into the same group, we
    block it -- regardless of which tier/signal triggered this particular
    pairwise match.
    """
    root_a, root_b = uf.find(key_a), uf.find(key_b)
    if root_a == root_b:
        return False
    phones_a = {common[k]["phone"] for k in members_by_root.get(root_a, [key_a]) if common[k]["phone"]}
    phones_b = {common[k]["phone"] for k in members_by_root.get(root_b, [key_b]) if common[k]["phone"]}
    return bool(phones_a and phones_b and phones_a != phones_b and not (phones_a & phones_b))


def resolve(s1: list[dict], s2: list[dict], s3: list[dict]):
    all_records = s1 + s2 + s3
    common = {_key(r): _to_common(r) for r in all_records}
    uf = UnionFind()
    n = len(all_records)

    # members_by_root tracks, for each current union-find root, every key
    # merged into it so far -- rebuilt lazily after each successful union.
    members_by_root: dict[str, list[str]] = {_key(r): [_key(r)] for r in all_records}

    def do_union(ka, kb):
        ra, rb = uf.find(ka), uf.find(kb)
        uf.union(ka, kb)
        new_root = uf.find(ka)
        merged = members_by_root.pop(ra, [ra]) if ra in members_by_root else [ra]
        merged += members_by_root.pop(rb, [rb]) if rb in members_by_root and rb != ra else []
        members_by_root[new_root] = list(set(merged))

    for i in range(n):
        for j in range(i + 1, n):
            a, b = all_records[i], all_records[j]
            ka, kb = _key(a), _key(b)
            ca, cb = common[ka], common[kb]

            # Tier 1: email
            if ca["email"] and cb["email"] and ca["email"] == cb["email"]:
                do_union(ka, kb)
                DECISIONS.append(MatchDecision(ka, kb, "tier1_email", 1.0,
                                  f"exact email match ({ca['email']})", "auto_merge"))
                continue

            # Tier 1: phone
            if ca["phone"] and cb["phone"] and ca["phone"] == cb["phone"]:
                do_union(ka, kb)
                DECISIONS.append(MatchDecision(ka, kb, "tier1_phone", 0.95,
                                  f"exact phone match ({ca['phone']})", "auto_merge"))
                continue

            # Direct pairwise conflict: same name, different phones.
            if _phones_conflict(ca, cb):
                if ca["name"] and cb["name"] and ca["name"].lower() == cb["name"].lower():
                    DECISIONS.append(MatchDecision(ka, kb, "conflict_phone", 0.0,
                                      f"same name '{ca['name']}' but CONFLICTING phones "
                                      f"({ca['phone']} vs {cb['phone']}) -- treated as two "
                                      f"different people, not merged", "reject"))
                continue

            # Tier 2: name + city composite -- guarded against transitive
            # conflict (see _group_has_phone_conflict docstring).
            if ca["name"] and cb["name"] and ca["city"] and cb["city"]:
                if ca["name"].lower() == cb["name"].lower() and ca["city"] == cb["city"]:
                    if _group_has_phone_conflict(uf, common, ka, kb, members_by_root):
                        DECISIONS.append(MatchDecision(ka, kb, "tier2_name_city", 0.85,
                                          f"name '{ca['name']}' + city '{ca['city']}' match, but "
                                          f"BLOCKED: merging would bridge two groups that already "
                                          f"contain conflicting phone numbers (transitive-merge guard)",
                                          "reject"))
                        continue
                    do_union(ka, kb)
                    DECISIONS.append(MatchDecision(ka, kb, "tier2_name_city", 0.85,
                                      f"exact name '{ca['name']}' + city '{ca['city']}' match, "
                                      f"no phone conflict", "auto_merge"))
                    continue

            # Tier 3: fuzzy name + same city
            sim = _name_similarity(ca["name"], cb["name"])
            if sim >= 0.80 and ca["city"] and cb["city"] and ca["city"] == cb["city"]:
                conf = round(0.60 + 0.15 * sim, 2)
                action = "auto_merge" if conf >= 0.90 else ("flag_review" if conf >= 0.70 else "reject")
                if action in ("auto_merge", "flag_review") and \
                        _group_has_phone_conflict(uf, common, ka, kb, members_by_root):
                    action = "reject"
                    DECISIONS.append(MatchDecision(ka, kb, "tier3_fuzzy_name_city", conf,
                                      f"fuzzy name match ('{ca['name']}' ~ '{cb['name']}') would "
                                      f"bridge groups with conflicting phones -- BLOCKED", action))
                    continue
                DECISIONS.append(MatchDecision(ka, kb, "tier3_fuzzy_name_city", conf,
                                  f"fuzzy name match ('{ca['name']}' ~ '{cb['name']}', sim={sim:.2f}) "
                                  f"+ same city '{ca['city']}'", action))
                if action in ("auto_merge", "flag_review"):
                    do_union(ka, kb)

    groups: dict[str, list[dict]] = {}
    for r in all_records:
        root = uf.find(_key(r))
        groups.setdefault(root, []).append(r)

    return list(groups.values()), DECISIONS


if __name__ == "__main__":
    from ingest import run_ingest
    s1, s2, s3, issues = run_ingest()
    groups, decisions = resolve(s1, s2, s3)

    multi = [g for g in groups if len(g) > 1]
    print(f"Total records: {len(s1)+len(s2)+len(s3)}")
    print(f"Resolved into {len(groups)} people ({len(multi)} of them merged from 2+ rows)\n")

    for g in multi:
        names = set()
        for r in g:
            names.add(r.get("full_name_norm") or r.get("worker_name_norm") or r.get("name_norm"))
        print(f"PERSON [{', '.join(names)}]:")
        for r in g:
            print(f"   - {r['source']} row {r['row_number']}")
    print(f"\nFlagged-for-review decisions:")
    for d in decisions:
        if d.action == "flag_review":
            print(f"   {d.row_a} <-> {d.row_b}: {d.reason} (conf={d.confidence})")
    print(f"\nRejected (evidence against merging):")
    for d in decisions:
        if d.action == "reject" and d.tier == "conflict_phone":
            print(f"   {d.row_a} <-> {d.row_b}: {d.reason}")
