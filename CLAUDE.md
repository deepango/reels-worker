# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An AI video generation SaaS platform for real-estate short-form Reels (9:16). Users submit a topic → n8n orchestrates Claude (script) + Replicate (images) + ElevenLabs (voice) → a Python worker pulls from Redis, renders with FFmpeg, and uploads to Backblaze B2.

## Common Commands

```bash
# Install Node dependencies (used for n8n workflow-as-code scripts)
npm install

# Initialize or re-seed the database with test customers
node fix_db.js

# Run the video processing worker locally (requires Redis + PostgreSQL)
python worker/main.py

# Install Python dependencies
pip install -r worker/requirements.txt
```

There are no test suites or linters configured in this repo.

## Architecture

### Pipeline Flow

```
POST /webhook/generate-video (n8n)
  → INSERT video_jobs (status=pending)
  → Claude: generate 5-scene JSON script
  → Parallel per scene:
      Replicate: image from prompt
      ElevenLabs: audio from voiceover text
  → RPUSH aggregated payload → Redis queue "reels:jobs"
  → UPDATE video_jobs (status=processing)

worker/main.py (Render Background Worker, Docker)
  → BLPOP "reels:jobs"
  → Download images + audio
  → FFmpeg: Ken Burns zoom per scene → crossfade concat → single MP4
  → boto3: upload to Backblaze B2
  → POST /webhook/render-callback (n8n)
  → UPDATE video_jobs (status=completed, b2_url, generation_time_seconds)
```

### Key Components

| File/Dir | Role |
|---|---|
| `worker/main.py` | Queue consumer: FFmpeg rendering, B2 upload, DB sync. The entire video processing logic lives here. |
| `setup/schema.sql` | PostgreSQL schema — `customers`, `video_jobs`, `usage_tracking` |
| `Reels Video Pipeline.production.json` | n8n workflow export (source of truth for the orchestration DAG) |
| `setup/anthropic-http-template.json` | Anthropic HTTP node config used in n8n (system prompt + prompt caching header) |
| `Dockerfile` | FFmpeg + Python3; CMD is `python3 -u main.py` — never override this in Render |
| `render.yaml` | Infrastructure-as-Code for the Render background worker service |
| `fix_db.js` | Inserts test `customers` rows into PostgreSQL for development |

### FFmpeg Rendering Details (worker/main.py)

- **Per scene:** Prescale image to 1620×2880 (1.5× target), apply `zoompan` Ken Burns (0.0006 zoom/frame), normalize audio to EBU R128 -16 LUFS, output H.264 + AAC at 25 fps
- **Concatenation:** Chained `xfade` (crossblur, 400 ms) + `acrossfade` filters across all scenes
- **Output:** H.264 with `movflags=+faststart` for streaming; final MP4 uploaded to B2

### Database Tables

- `customers` — subscription status, `videos_remaining` (decremented per job)
- `video_jobs` — job lifecycle: `pending → processing → completed/failed`, stores `b2_url`, `cost_breakdown` (JSONB), `generation_time_seconds`, `error_logs`
- `usage_tracking` — daily aggregate of video count and API costs

### n8n Workflow

The production workflow lives in n8n Cloud. `Reels Video Pipeline.production.json` is the exported copy. Use the n8n MCP tools (`mcp__n8n-mcp__*`) or the `@n8n/workflow-sdk` (via Node.js) to update it programmatically. The `setup/anthropic-http-template.json` defines the Anthropic HTTP node body with prompt caching enabled (`anthropic-beta: prompt-caching-2024-07-31`).

## Environment Variables

See `setup/env.render.example` for the full list. Critical ones:

| Variable | Purpose |
|---|---|
| `REDIS_URL` | Redis queue connection |
| `DATABASE_URL` | PostgreSQL connection |
| `N8N_CALLBACK_URL` | n8n webhook the worker POSTs to on completion |
| `B2_ENDPOINT` / `B2_APPLICATION_KEY_ID` / `B2_APPLICATION_KEY` / `B2_BUCKET_NAME` | Backblaze B2 storage |
| `REPLICATE_API_TOKEN` | Image generation |
| `ELEVENLABS_API_KEY` | Voice synthesis |
| `TEMP_DIR` | Scratch space for FFmpeg (default `/tmp/reels`, 5 GB disk on Render) |

## Deployment

Deploy via `render.yaml` — connect the repo to Render and it auto-configures the background worker. Set env vars marked `sync: false` manually in the Render dashboard. **Never override the Docker Command field** — the Dockerfile's CMD (`python3 -u main.py`) must run as-is.

The worker handles SIGTERM gracefully: it finishes the current job before exiting (important for Render deploys).
