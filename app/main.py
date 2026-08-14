"""
FastAPI app. This is the ONE server for both:
  - Task 2: endpoints n8n calls to read a person's skills and write back
    a category (n8n's SQLite support is weak/community-node-only, so we
    front the DB with a tiny HTTP API instead -- a normal, defensible
    integration pattern, not a workaround).
  - Task 3: the audio collection web app (added next).

Run with: uvicorn app.main:app --reload --port 8000
"""

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = str(Path(__file__).resolve().parent.parent / "consultbae.db")

app = FastAPI(title="ConsultBae Assignment API")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row) -> dict:
    return dict(zip(row.keys(), row))


# ---------- Task 2: n8n integration endpoints ----------

@app.get("/api/people")
def list_people():
    """n8n's trigger/loop step reads this to know who to categorize."""
    conn = get_conn()
    rows = conn.execute("SELECT person_id, canonical_name FROM people ORDER BY person_id").fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/api/people/{person_id}/skills")
def get_person_skills(person_id: int):
    """
    Union the skills this person has across every source they appear in
    (a person can have skills listed in source1's applicant profile AND
    source2's gig profile, sometimes overlapping, sometimes not -- we
    don't want to lose either).
    """
    conn = get_conn()
    person = conn.execute("SELECT * FROM people WHERE person_id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        raise HTTPException(404, "person not found")

    skills = set()
    for table, col in [("applicant_profiles", "skills_norm"), ("gig_profiles", "skills_norm")]:
        for r in conn.execute(f"SELECT {col} FROM {table} WHERE person_id=?", (person_id,)):
            val = r[col]
            if val:
                skills.update(s for s in val.split(",") if s)
    conn.close()
    return {
        "person_id": person_id,
        "canonical_name": person["canonical_name"],
        "skills": sorted(skills),
    }


class SkillCategoryIn(BaseModel):
    category: str
    reasoning: str = ""


@app.post("/api/people/{person_id}/skill-category")
def write_skill_category(person_id: int, body: SkillCategoryIn):
    """n8n's final step POSTs the LLM's categorization result here."""
    conn = get_conn()
    person = conn.execute("SELECT 1 FROM people WHERE person_id=?", (person_id,)).fetchone()
    if not person:
        conn.close()
        raise HTTPException(404, "person not found")
    conn.execute(
        "INSERT INTO skill_categories (person_id, category, reasoning) VALUES (?,?,?)",
        (person_id, body.category, body.reasoning),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "person_id": person_id, "category": body.category}


@app.get("/api/skill-categories")
def list_skill_categories():
    conn = get_conn()
    rows = conn.execute("""
        SELECT sc.id, sc.person_id, p.canonical_name, sc.category, sc.reasoning, sc.tagged_at
        FROM skill_categories sc JOIN people p ON p.person_id = sc.person_id
        ORDER BY sc.tagged_at DESC
    """).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Task 3: audio collection app ----------

import shutil
import uuid
from fastapi import UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.audio_analysis import analyze_audio

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Matching against Task 1's people table: we try to link an audio
# submission to an existing canonical person by normalized phone (the one
# identifier we ask for on this form), same tiering logic as match.py's
# Tier-1 phone match, kept intentionally simple here since the form only
# gives us name+phone, not enough data for the fuzzy tiers.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from normalize import normalize_phone, normalize_name  # noqa: E402


def _find_person_by_phone(conn, phone_norm: str) -> int | None:
    if not phone_norm:
        return None
    row = conn.execute(
        "SELECT person_id FROM applicant_profiles WHERE phone_norm=? LIMIT 1", (phone_norm,)
    ).fetchone()
    if row:
        return row["person_id"]
    row = conn.execute(
        "SELECT person_id FROM nexus_profiles WHERE phone_norm=? LIMIT 1", (phone_norm,)
    ).fetchone()
    return row["person_id"] if row else None


@app.post("/api/audio/submit")
async def submit_audio(
    name: str = Form(...),
    phone: str = Form(...),
    file: UploadFile = File(...),
):
    phone_norm = normalize_phone(phone)
    name_norm = normalize_name(name)

    ext = Path(file.filename or "upload.webm").suffix or ".webm"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    saved_path = UPLOADS_DIR / saved_name

    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        meta = analyze_audio(str(saved_path))
    except Exception as e:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Could not analyze audio file: {e}")

    conn = get_conn()
    person_id = _find_person_by_phone(conn, phone_norm)

    conn.execute("""
        INSERT INTO audio_submissions
        (person_id, name, phone, file_path, duration_sec, sample_rate_hz,
         bitrate_kbps, loudness_db, channels, quality_note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (person_id, name_norm, phone_norm, saved_name, meta.duration_sec,
          meta.sample_rate_hz, meta.bitrate_kbps, meta.loudness_db,
          meta.channels, meta.quality_note))
    conn.commit()
    submission_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return {
        "status": "ok",
        "submission_id": submission_id,
        "linked_person_id": person_id,
        "metadata": {
            "duration_sec": meta.duration_sec,
            "sample_rate_hz": meta.sample_rate_hz,
            "bitrate_kbps": meta.bitrate_kbps,
            "channels": meta.channels,
            "loudness_db": meta.loudness_db,
            "peak_db": meta.peak_db,
            "quality_note": meta.quality_note,
        },
    }


@app.get("/api/audio/submissions")
def list_submissions():
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, p.canonical_name AS linked_person_name
        FROM audio_submissions s
        LEFT JOIN people p ON p.person_id = s.person_id
        ORDER BY s.submitted_at DESC
    """).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/api/audio/file/{filename}")
def get_audio_file(filename: str):
    path = UPLOADS_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "file not found")
    # basic path-traversal guard
    if path.resolve().parent != UPLOADS_DIR.resolve():
        raise HTTPException(400, "invalid path")
    return FileResponse(path)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    return (Path(__file__).resolve().parent / "static" / "index.html").read_text()


@app.get("/submissions", response_class=HTMLResponse)
def serve_submissions():
    return (Path(__file__).resolve().parent / "static" / "submissions.html").read_text()
