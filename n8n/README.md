# n8n Setup & Demo Guide

## 1. Get n8n running

**Option A — Docker (recommended, fastest to reset if something breaks):**
```bash
docker run -it --rm --name n8n -p 5678:5678 -v ~/.n8n:/home/node/.n8n docker.n8n.io/n8nio/n8n
```
Open http://localhost:5678 and create a local owner account (first run only).

**Option B — n8n Cloud free trial:**
Sign up at https://n8n.io, no local setup, but the HTTP Request nodes will
need your machine's public/tunnel URL to reach the FastAPI app running
locally (use `ngrok http 8000` and use the ngrok URL instead of
`host.docker.internal` in the workflow's HTTP nodes).

## 2. Start the FastAPI app (must be running before you test the workflow)

```bash
cd project
uvicorn app.main:app --port 8000
```

## 3. Import the workflow

In n8n: **Workflows → Import from File** → select
`n8n/skill_tagging_workflow.json`.

## 4. Fix the two things that are environment-specific

- **The Anthropic node's credential**: click "LLM Categorize Skills" → set up
  an Anthropic API credential with your own API key (Settings → Credentials →
  New → Anthropic API). Any model works; a fast one (e.g. Claude Haiku) is
  fine for this task and keeps calls cheap if you tag all 55 people.
- **The URL host**: the HTTP Request nodes call
  `http://host.docker.internal:8000/...`, which resolves your host machine's
  localhost from inside Docker. If you're running n8n natively (not Docker),
  change this to `http://localhost:8000/...` in both HTTP Request nodes.

## 5. Get the webhook URL

Click "Webhook Trigger" → copy the **Test URL** (or **Production URL** once
you activate the workflow). It'll look like:
`http://localhost:5678/webhook-test/tag-skills`

## 6. Run it

Trigger it manually while recording:
```bash
curl -X POST http://localhost:5678/webhook-test/tag-skills \
  -H "Content-Type: application/json" \
  -d '{"person_id": 19}'
```

Or, to tag everyone in one demo-friendly pass: add a **Schedule Trigger** node
(or just call `/api/people` first from a manual "Set" node) that loops over
`GET /api/people` and feeds each `person_id` into the same downstream chain —
n8n's built-in "Split In Batches" node works well here if you want to show a
batch run instead of a single webhook call.

Check the result landed in the database:
```bash
curl http://localhost:8000/api/skill-categories
```

## 7. What to show in the recording (30–60 seconds is enough)

1. The workflow diagram (5 nodes, visually clear left-to-right)
2. Trigger it (curl command or n8n's "Execute Workflow" button)
3. Show the execution succeeding in n8n's UI (green checkmarks on each node)
4. Show the `/api/skill-categories` response with a real LLM-generated
   category and reasoning string — this proves it's not a hardcoded if/else.

## Why this design

- **Webhook trigger, not the "check CSV for duplicates" option**: the LLM
  skill-categorization automation was chosen because it demonstrates both
  AI *and* no-code automation working together, which is closer to what the
  role actually needs, versus a duplicate-alert flow which is closer to pure
  data plumbing.
- **HTTP Request nodes hitting our FastAPI app, not a native SQLite node**:
  n8n's SQLite integration is a community node with weak concurrent-write
  guarantees. An HTTP API in front of the database is the standard
  integration pattern you'd actually use talking to a real backend.
- **A Code node for parsing the LLM response**: LLMs occasionally wrap JSON
  answers in markdown code fences even when told not to. The Code node strips
  those defensively rather than letting one bad response fail the whole
  batch run.
