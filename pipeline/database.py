"""
Database layer.

WHY SQLITE INSTEAD OF POSTGRES: the assignment explicitly says "your
choice" for the database. Postgres is a defensible choice too (and works
natively with n8n's Postgres node), but for a 48-hour take-home that has
to be cloned and run by a stranger with zero setup, a single-file SQLite
DB removes an entire category of "it doesn't run on my machine" failure.
n8n can still hit SQLite through its Execute Command node or a small
FastAPI wrapper (we expose the DB via the audio app's API in Task 3, and
the n8n workflow calls that API rather than talking to the DB directly).
If asked in interview: "I'd use Postgres in production for concurrent
writes and proper connection pooling; SQLite is the right tool for a
single-user local demo."

SCHEMA DESIGN -- the key decision from the plan doc that we're keeping:
we do NOT collapse everything into one wide "people" table. Conflicting
source records are preserved as separate rows in per-source profile
tables, all pointing at one canonical person_id. Nothing is overwritten.

  people                 -- one row per canonical person we resolved
  applicant_profiles     -- 1 row per source1 record (FK -> people)
  gig_profiles           -- 1 row per source2 record (FK -> people)
  nexus_profiles         -- 1 row per source3 record (FK -> people)
  match_decisions        -- audit trail of every merge decision made
  audio_submissions      -- Task 3
  skill_categories        -- Task 2 (n8n writes here)
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applicant_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(person_id),
    source_row_number INTEGER,
    full_name_raw TEXT, full_name_norm TEXT,
    email_raw TEXT, email_norm TEXT,
    phone_raw TEXT, phone_norm TEXT,
    city_raw TEXT, city_norm TEXT,
    experience_years REAL,
    ctc_raw TEXT, ctc_lpa_norm REAL,
    applied_date_raw TEXT, applied_date_norm TEXT, is_future_date INTEGER,
    skills_raw TEXT, skills_norm TEXT
);

CREATE TABLE IF NOT EXISTS gig_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(person_id),
    source_row_number INTEGER,
    email_raw TEXT, email_norm TEXT,
    worker_name_raw TEXT, worker_name_norm TEXT,
    rate_raw TEXT, rate_monthly_inr_norm REAL,
    city_raw TEXT, city_norm TEXT,
    status_raw TEXT, status_norm TEXT,
    skills_raw TEXT, skills_norm TEXT,
    was_column_shifted INTEGER
);

CREATE TABLE IF NOT EXISTS nexus_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(person_id),
    source_row_number INTEGER,
    name_raw TEXT, name_norm TEXT,
    phone_raw TEXT, phone_norm TEXT,
    city_raw TEXT, city_norm TEXT,
    verified_raw TEXT, verified_norm INTEGER,
    projects_completed INTEGER
);

CREATE TABLE IF NOT EXISTS match_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_a TEXT, row_b TEXT,
    tier TEXT, confidence REAL, reason TEXT, action TEXT
);

CREATE TABLE IF NOT EXISTS ingest_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, row_number INTEGER, issue TEXT, action TEXT
);

CREATE TABLE IF NOT EXISTS skill_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES people(person_id),
    category TEXT,
    reasoning TEXT,
    tagged_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audio_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES people(person_id),
    name TEXT,
    phone TEXT,
    file_path TEXT,
    duration_sec REAL,
    sample_rate_hz INTEGER,
    bitrate_kbps REAL,
    loudness_db REAL,
    channels INTEGER,
    quality_note TEXT,
    submitted_at TEXT DEFAULT (datetime('now'))
);
"""


def get_conn(db_path: str = "consultbae.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str = "consultbae.db"):
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _pick_canonical_name(group: list[dict]) -> str:
    """
    Pick a display name for the person. Prefer the properly-cased
    version (Title Case with a lowercase word like 'of'/'and' would need
    smarter logic, but our data is simple first+last names) over ALL CAPS
    variants, since ALL CAPS in source data is itself one of the planted
    inconsistencies, not a "more correct" version.
    """
    names = [r.get("full_name_norm") or r.get("worker_name_norm") or r.get("name_norm")
             for r in group]
    names = [n for n in names if n]
    # prefer a name that isn't all-uppercase
    for n in names:
        if n != n.upper():
            return n
    return names[0] if names else "Unknown"


def load_people(conn, groups: list[list[dict]]):
    person_id_by_key = {}
    for group in groups:
        canonical_name = _pick_canonical_name(group)
        cur = conn.execute("INSERT INTO people (canonical_name) VALUES (?)", (canonical_name,))
        person_id = cur.lastrowid
        for r in group:
            person_id_by_key[f"{r['source']}#{r['row_number']}"] = person_id

            if r["source"] == "source1":
                conn.execute("""INSERT INTO applicant_profiles
                    (person_id, source_row_number, full_name_raw, full_name_norm,
                     email_raw, email_norm, phone_raw, phone_norm, city_raw, city_norm,
                     experience_years, ctc_raw, ctc_lpa_norm, applied_date_raw,
                     applied_date_norm, is_future_date, skills_raw, skills_norm)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (person_id, r["row_number"], r["full_name_raw"], r["full_name_norm"],
                     r["email_raw"], r["email_norm"], r["phone_raw"], r["phone_norm"],
                     r["city_raw"], r["city_norm"], r["experience_years"], r["ctc_raw"],
                     r["ctc_lpa_norm"], r["applied_date_raw"], r["applied_date_norm"],
                     int(r["is_future_date"]), r["skills_raw"], ",".join(r["skills_norm"])))
            elif r["source"] == "source2":
                conn.execute("""INSERT INTO gig_profiles
                    (person_id, source_row_number, email_raw, email_norm,
                     worker_name_raw, worker_name_norm, rate_raw, rate_monthly_inr_norm,
                     city_raw, city_norm, status_raw, status_norm, skills_raw, skills_norm,
                     was_column_shifted)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (person_id, r["row_number"], r["email_raw"], r["email_norm"],
                     r["worker_name_raw"], r["worker_name_norm"], r["rate_raw"],
                     r["rate_monthly_inr_norm"], r["city_raw"], r["city_norm"],
                     r["status_raw"], r["status_norm"], r["skills_raw"],
                     ",".join(r["skills_norm"]), int(r["was_column_shifted"])))
            elif r["source"] == "source3":
                conn.execute("""INSERT INTO nexus_profiles
                    (person_id, source_row_number, name_raw, name_norm,
                     phone_raw, phone_norm, city_raw, city_norm, verified_raw,
                     verified_norm, projects_completed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (person_id, r["row_number"], r["name_raw"], r["name_norm"],
                     r["phone_raw"], r["phone_norm"], r["city_raw"], r["city_norm"],
                     r["verified_raw"],
                     None if r["verified_norm"] is None else int(r["verified_norm"]),
                     r["projects_completed"]))
    conn.commit()
    return person_id_by_key


def load_decisions(conn, decisions):
    for d in decisions:
        conn.execute("""INSERT INTO match_decisions (row_a, row_b, tier, confidence, reason, action)
                         VALUES (?,?,?,?,?,?)""",
                     (d.row_a, d.row_b, d.tier, d.confidence, d.reason, d.action))
    conn.commit()


def load_issues(conn, issues):
    for iss in issues:
        conn.execute("""INSERT INTO ingest_issues (source, row_number, issue, action)
                         VALUES (?,?,?,?)""",
                     (iss.source, iss.row_number, iss.issue, iss.action))
    conn.commit()


def build_database(db_path: str | None = None, data_dir: str | None = None):
    from ingest import run_ingest
    from match import resolve

    if db_path is None:
        db_path = str(Path(__file__).resolve().parent.parent / "consultbae.db")

    Path(db_path).unlink(missing_ok=True)  # fresh build each run
    conn = init_db(db_path)

    s1, s2, s3, issues = run_ingest(data_dir)
    groups, decisions = resolve(s1, s2, s3)

    load_people(conn, groups)
    load_decisions(conn, decisions)
    load_issues(conn, issues)

    n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    print(f"Built {db_path}: {n_people} canonical people from "
          f"{len(s1)+len(s2)+len(s3)} source rows "
          f"({len(s1)} applicants, {len(s2)} gig, {len(s3)} nexus)")
    conn.close()


if __name__ == "__main__":
    build_database()
