# Data Quality Report

Generated against the exact 3 supplied CSVs. Every issue below was found by
running the pipeline (`pipeline/ingest.py`, `pipeline/match.py`), not by
manual inspection alone — the row numbers are exact and reproducible by
re-running `python pipeline/database.py`.

## 1. Structural issues (would break a naive `pandas.read_csv` + join)

| # | Source | Row | Issue | How we handled it |
|---|--------|-----|-------|---------------------|
| 1 | source2 | row 12 | Completely blank row (all fields empty) | Detected and dropped during ingest. Not counted as a person. |
| 2 | source2 | row 20 | **Column shift**: `"react, javascript, mysql",ISHA.CHOPRA95@MAILTEST.EXAMPLE.ORG,Isha Chopra,1406/hr,Pune,active` — the skills string sits in the `email_id` column and every other field shifted one position left. | Detected heuristically (field 0 has no `@` but field 1 does) and re-mapped columns. The row's own `skill_tags` value is unrecoverable from this row alone — flagged rather than guessed. This row is also a duplicate of a correctly-formatted Isha Chopra row elsewhere in source2; entity resolution merges both into one person, so the missing skill data is not actually lost (the well-formed row supplies it). |
| 3 | source3 | row 16 | The header row (`Name,Phone Number,City,Verified,Projects Completed`) is repeated verbatim in the middle of the data. | Detected by comparing each row to the header row exactly (not hardcoded to row 16, so it's robust if this shifts). Dropped. |

## 2. Duplicate / near-duplicate person records

| # | Source | Description | How we handled it |
|---|--------|-------------|---------------------|
| 4 | source1 | **Nikhil Chopra** appears twice (rows 27, 37): identical phone, city, CTC, date, skills — only the email differs by one character. | Tier-1 phone match → auto-merged into one canonical person, both source rows preserved under it. |
| 5 | source1 | **Rohit Verma** (row 31) and **R. Verma** (row 25) are the same person — identical email and phone, just formatted differently. | Tier-1 email/phone match → auto-merged. |
| 6 | source2 | **Isha Chopra** appears twice: once well-formed (row 6), once in the column-shifted row (row 20, see issue #2). | Tier-1 email match (after column repair) → auto-merged. |
| 7 | source2 | **Deepak Nair** appears twice with *different* emails, rates, cities, and statuses (`deepak.nair44@example.com`, Bengaluru, paused vs. `deepak.nair57@example.in`, New Delhi, Active). | **Deliberately NOT merged.** Same first+last name is not, by itself, strong evidence — nothing else (email, phone, city) links these two rows to each other or to a shared source1/source3 record. Kept as two separate people. This is a case where *not* merging is the correct call, even though the names match exactly. |
| 8 | source3 | **Arjun Mehta** appears twice with different phone numbers (`9000000131` vs `9000000272`), same city. | **Deliberately NOT merged** — different phone numbers are treated as direct evidence of two different people, even with matching name+city. See §4 for the transitive-merge trap this created. |

## 3. Field-level inconsistencies (normalized, not merged/dropped)

- **CTC mixed units** (source1): some values are annual INR (e.g. `417964`), others already in LPA (e.g. `4.2`). Rule: any value ≥ 1000 is treated as annual INR and divided by 100,000; documented as a threshold heuristic, not a certainty, in `normalize.py`.
- **Phone formats**: `+919000000254`, `919000000254`, `09000000287`, `9000000237`, `+91-9000000131` — all collapsed to a canonical 10-digit form by stripping non-digits and removing a leading country code (`91`) or trunk `0`.
- **City name variants**: Bengaluru/Bangalore, Gurgaon/Gurugram, Pune/pune, Noida/NOIDA, New Delhi/new delhi/Delhi/Delhi NCR. Collapsed via an explicit alias table. Note: treating "Delhi", "New Delhi", and "Delhi NCR" as one city is a judgment call (NCR technically spans Gurugram/Noida too) — reasonable for this dataset's scale, called out explicitly rather than silently assumed.
- **Name formatting**: mixed case, extra whitespace, abbreviations (`R. Verma` vs `Rohit Verma`). Whitespace/case normalized always; abbreviation expansion is handled only by the fuzzy-match tier, narrowly (see §4), never by silently rewriting the stored name.
- **Applied dates in 4 different formats**: `24-07-2026`, `2026-08-08`, `7 Jul 2026`, `07/13/2026`. Parsed with a fixed list of formats tried in order.
- **6 future-dated applications** relative to Aug 14, 2026 (rows 14, 17, 19, 28, 32, 40 in source1 — more than the 2 initially expected). Kept, not silently corrected, and flagged with `is_future_date=True` so downstream consumers can decide what to do with them.
- **Skill capitalization**: `n8n` vs `N8N`, `React` vs `react`, etc. — lowercased and de-duplicated.
- **Status values** (source2): `Active/active/ACTIVE`, `Inactive/paused` — casing normalized; `paused` is kept distinct from `inactive` rather than collapsed, since the source data draws that distinction deliberately and collapsing it would lose information.
- **Rate units** (source2): `1415/hr` vs `15k/month` — converted to a common estimated monthly INR figure using an assumed 160 working hours/month (an assumption, documented, not a fact).
- **Y/N/Yes/No/yes/no** (source3 `Verified` column) — normalized to boolean.

## 4. The hardest issue: a transitive false-merge bug

The Arjun Mehta case (#8 above) initially looked handled — a direct pairwise
check rejects merging two rows with the same name but conflicting phone
numbers. But a third row (`source2`, Arjun Mehta, Noida, **no phone at all**)
matched *both* Arjun Mehta rows independently on name+city. Because merges
are unioned into groups (transitively), the no-phone row silently bridged
the two conflicting-phone rows into one incorrect person.

**Fix:** before performing any merge, we now check every phone number
already present across *both* groups about to be combined — not just the
two rows being directly compared — and block the merge if that would bring
two different non-null phone numbers together. A regression test
(`tests/test_pipeline.py::test_transitive_merge_does_not_bridge_conflicting_phones`)
encodes this synthetically so it can't silently regress.

## 5. Summary

- 103 raw rows across 3 sources → **55 canonical people**
- 31 people have 2+ source rows merged into them; 16 of those are linked
  across all 3 sources
- 3 people have duplicate rows *within a single source* (true dupes)
- 3 pairwise match attempts explicitly rejected due to conflicting evidence
  (all the Arjun Mehta case, including the transitive bridge attempt)
- 9 data-quality issues logged during ingest:
  3 structural issues and 6 future-date anomalies.
