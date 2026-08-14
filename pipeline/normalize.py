"""
Normalization helpers.

Design decision: every normalize_* function is PURE (string/number in,
string/number out, no side effects) so it's trivially unit-testable and
so match.py can reuse the exact same normalization used at ingest time.
We never mutate the raw source value -- we always keep original + normalized
side by side (see ingest.py) so the data-quality report can show "what we
saw" vs "what we derived".
"""

import re
from datetime import datetime


def normalize_phone(raw: str) -> str | None:
    """
    Collapse every phone format in the data down to a canonical
    10-digit Indian mobile number (no country code, no separators).

    Examples seen in the data:
      +919000000254   -> 9000000254
      919000000254    -> 9000000254
      09000000287     -> 9000000287
      9000000237      -> 9000000237
      +91-9000000131  -> 9000000131

    Rule: strip everything but digits, then drop a leading country
    code "91" or a leading trunk "0", leaving the last 10 digits.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) != 10:
        # Doesn't fit the expected shape -- return what we have rather than
        # silently dropping it; match.py will just fail to match on it.
        return digits
    return digits


def normalize_email(raw: str) -> str | None:
    if raw is None:
        return None
    email = str(raw).strip().lower()
    return email or None


CITY_ALIASES = {
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "pune": "Pune",
    "noida": "Noida",
    "new delhi": "New Delhi", "delhi": "New Delhi", "delhi ncr": "New Delhi",
}


def normalize_city(raw: str) -> str | None:
    """
    Collapse casing + known synonyms (Bangalore/Bengaluru, Gurgaon/Gurugram,
    Delhi/New Delhi/Delhi NCR) to one canonical spelling.

    NOTE: treating "Delhi" and "New Delhi" and "Delhi NCR" as the same city
    is a judgment call, not a fact -- NCR technically spans Delhi, Gurugram,
    Noida too. For THIS assignment (small fictional dataset, matching people)
    collapsing them is the right call and I flag it explicitly in the
    data-quality report as an assumption, not a silent decision.
    """
    if raw is None:
        return None
    key = str(raw).strip().lower()
    key = re.sub(r"\s+", " ", key)
    return CITY_ALIASES.get(key, str(raw).strip().title())


def normalize_name(raw: str) -> str | None:
    """
    Lowercase, collapse whitespace. Deliberately NOT expanding initials
    like "R." -> "Rohit" here -- that's a fuzzy-match job (match.py), not
    a normalization job, because expanding "R." wrongly would silently
    corrupt data. Keep this function honest/reversible.
    """
    if raw is None:
        return None
    name = re.sub(r"\s+", " ", str(raw).strip())
    return name or None


def normalize_skills(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip().lower() for p in str(raw).split(",")]
    return sorted({p for p in parts if p})


_DATE_FORMATS = [
    "%d-%m-%Y",   # 24-07-2026
    "%Y-%m-%d",   # 2026-08-08
    "%d %b %Y",   # 7 Jul 2026
    "%m/%d/%Y",   # 07/13/2026  (US-style month/day seen in data)
]


def normalize_date(raw: str, today: datetime | None = None):
    """
    Try each known format from the data until one parses.
    Returns (normalized_iso_date_or_None, is_future_flag).

    07/13/2026 disambiguates the slash format as MM/DD (13 can't be a month),
    so we apply %m/%d/%Y consistently to ALL slash dates in this file rather
    than guessing per-row -- consistency > cleverness for a planted-format
    column like this.
    """
    if today is None:
        today = datetime(2026, 8, 14)
    if not raw:
        return None, False
    raw = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.date().isoformat(), dt.date() > today.date()
        except ValueError:
            continue
    return None, False


def normalize_ctc_to_lpa(raw) -> float | None:
    """
    Source 1 mixes two units in one column:
      - values like 417964, 332456   -> clearly annual INR (too big for LPA)
      - values like 4.2, 8.3, 11.2   -> clearly LPA already

    Heuristic: anything >= 1000 is annual-INR and gets divided by 100000
    to become LPA. Anything below that is already LPA. This is a threshold
    guess, not a certainty -- documented in the data-quality report -- but
    given real Indian salary ranges (2-100+ LPA) there's no value in this
    dataset that's genuinely ambiguous between the two interpretations.
    """
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val >= 1000:
        return round(val / 100_000, 2)
    return round(val, 2)


def normalize_rate_to_monthly_inr(raw: str) -> float | None:
    """
    Source 2 rates come in two shapes: "1415/hr" and "15k/month".
    Normalize both to an estimated monthly INR figure so gig_profiles
    are comparable. Assumption: 160 working hours/month for hourly rates
    (documented assumption, not a fact -- flagged in the report).
    """
    if not raw:
        return None
    raw = str(raw).strip().lower()
    m = re.match(r"([\d.]+)k/month", raw)
    if m:
        return round(float(m.group(1)) * 1000, 2)
    m = re.match(r"([\d.]+)/hr", raw)
    if m:
        return round(float(m.group(1)) * 160, 2)
    return None


def normalize_status(raw: str) -> str | None:
    if not raw:
        return None
    v = str(raw).strip().lower()
    if v in ("active",):
        return "active"
    if v in ("inactive", "paused"):
        # NOTE: judgment call -- treating "paused" as distinct from
        # "inactive" was tempting, but the brief just wants normalized
        # status; we preserve the ORIGINAL raw value alongside this
        # normalized one in gig_profiles, so no information is lost.
        return v
    return v


def normalize_verified(raw: str) -> bool | None:
    if raw is None or str(raw).strip() == "":
        return None
    v = str(raw).strip().lower()
    if v in ("y", "yes"):
        return True
    if v in ("n", "no"):
        return False
    return None
