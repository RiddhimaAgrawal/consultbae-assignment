# Stretch: Launching the Audio App to 5,000 Gig Workers Over a Weekend

## What breaks first

**1. SQLite write locking.** SQLite allows only one writer at a time. With
5,000 people submitting audio (name + phone + file) over a weekend, even
modest concurrency (a few dozen simultaneous submissions during peak hours)
will start throwing `database is locked` errors on the `INSERT INTO
audio_submissions` call. This is almost certainly the very first thing to
break — SQLite is fine for a single-user local demo, but it was never
designed for concurrent multi-user writes at any real scale.

**2. Synchronous audio analysis blocking the request.** Right now,
`ffprobe`/`ffmpeg` run synchronously inside the HTTP request handler — the
person's browser sits waiting while we decode their whole audio file for the
loudness pass. Under load, request handling threads/workers get tied up
running ffmpeg subprocesses instead of accepting new connections, so response
times degrade sharply well before the server actually runs out of CPU.

**3. Local disk storage.** Audio files are currently written straight to a
local `uploads/` folder. On a single VM, this fills the disk without warning,
has no redundancy (one disk failure = every submission gone), and doesn't
scale horizontally — if we ever needed a second app server to handle load,
its local disk wouldn't have the files the first server saved.

**4. No idempotency / duplicate-submission handling.** If someone's upload
times out on a flaky connection and their browser retries, we'll currently
create two full audio_submissions rows (and store the file twice) rather than
recognizing it's the same attempt.

**5. No auth/rate-limiting on the submit endpoint.** Nothing currently stops
someone from scripting thousands of fake submissions against `/api/audio/submit`,
which becomes a real cost and storage problem at this scale, not just a
theoretical one.

## What I'd change before launch

- **Move to Postgres** for the canonical database — proper connection
  pooling and concurrent writes handle this load without SQLite's
  single-writer bottleneck. This is exactly the tradeoff mentioned in the
  main README: SQLite was the right call for a local demo, Postgres is the
  right call here.
- **Make audio analysis asynchronous.** Accept the upload, store it
  immediately, return a "submission received" response right away, and run
  ffprobe/ffmpeg analysis in a background worker queue (e.g. Celery + Redis,
  or a simple SQS-backed worker). The submissions list page would show
  "processing..." for metadata until the worker finishes, instead of the
  user's browser blocking on a multi-second ffmpeg pass.
- **Move file storage to object storage** (S3 / GCS / Azure Blob) instead of
  local disk. Solves durability (replicated storage), horizontal scaling
  (any app server instance can generate a signed URL, no server owns "its"
  files), and gives cheap built-in options for cost control (lifecycle rules
  to move older audio to cold storage).
- **Add a client-provided idempotency key** (or a content hash of the audio
  blob) so a retried submission from the same person/session doesn't create
  a duplicate record or a duplicate stored file.
- **Add basic rate limiting** (e.g. per-IP or per-phone-number submission
  caps per hour) in front of the submit endpoint, and consider a lightweight
  CAPTCHA or phone-verification step if abuse becomes a real problem, given
  this is a public-facing form with no login.
- **Duplicate people:** the entity-resolution logic from Task 1 was built for
  a one-time batch merge of 3 known CSVs, not a live stream of new
  submissions. At 5,000 live submissions, I'd run matching as a scheduled
  batch job periodically (e.g. hourly) rather than trying to resolve identity
  synchronously on every submission — real-time fuzzy matching against a
  growing table gets slower as the table grows, and correctness matters more
  than instant linking here.

## Rough cost/scale sanity check

5,000 submissions × ~1MB average compressed audio file (a few minutes of
voice, reasonably compressed) is on the order of a few GB total storage —
genuinely cheap on any object storage tier. The actual constraints are far
more about write concurrency and processing throughput than raw storage cost
at this scale.
