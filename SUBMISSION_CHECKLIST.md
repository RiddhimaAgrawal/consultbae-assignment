# Submission Checklist

## Commit history

The project was developed incrementally and committed in logical stages, as requested
by the assignment. The commit history reflects the actual development process; no
timestamps were fabricated or rewritten.

The major commits cover:

- project configuration
- ingestion and normalization
- entity resolution
- canonical database construction
- pipeline tests
- data-quality reporting
- FastAPI application
- n8n skill categorization workflow

Before submission, verify the final working tree is clean and that all required
files are pushed to GitHub.

## Before you record the video

- [ ] Run `python pipeline/database.py` fresh and confirm it builds without errors
- [ ] Run `python tests/test_pipeline.py` and confirm all tests pass
- [ ] Start `uvicorn app.main:app --port 8000` and manually test:
  - [ ] Submit a recorded audio clip through the browser (mic permission works)
  - [ ] Submit an uploaded audio file
  - [ ] Confirm `/submissions` shows both with playback + metadata
- [ ] Set up n8n (see `n8n/README.md`), import the workflow, add your own
      Anthropic credential, and successfully run it against at least one
      real person from the database
- [ ] Re-read `STUCK_LOG.md` and `reports/data_quality_report.md` end to end
      — you need to be able to explain every claim in them, not just have
      them exist

## Recording (max 6 min)

Suggested pacing:
- 0:00–0:30 — architecture: 3 CSVs → clean/normalize → entity resolution → SQLite,
  conflict-preserving schema (people + per-source profile tables)
- 0:30–1:30 — run `pipeline/database.py` live, show it building the DB
- 1:30–2:30 — open the DB (or a small query script) and show 2-3 merged people,
  including one of the deliberately-NOT-merged cases (Deepak Nair or Arjun Mehta)
- 2:30–3:30 — trigger the n8n workflow, show it running and writing a category back
- 3:30–5:00 — record or upload audio through the app end-to-end
- 5:00–5:30 — show the submissions page with extracted metadata
- 5:30–6:00 — talk through the 2-3 hardest decisions (pull straight from
  STUCK_LOG.md, in your own words)

## Submission

- [ ] Push the repo to GitHub (public or with the reviewer's access granted)
- [ ] Confirm README has correct setup steps for someone cloning fresh
- [ ] Confirm the data-quality report and stuck log are both linked/visible
      from the README
- [ ] Reply to the assignment email with repo link + video link before the deadline

## Live-round prep (if shortlisted)

Re-read the list of questions from the original assignment review and make
sure you can actually answer each one without notes:
- Why did you choose SQLite over Postgres?
- How do you resolve duplicate people, concretely?
- Why weren't the two Deepak Nair records merged?
- What happens when two sources disagree on a fact?
- How does the audio loudness calculation work?
- Why n8n instead of pure Python?
- What breaks first at 5,000 simultaneous users?
