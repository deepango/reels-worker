# Setup Runbook (Anthropic + Render + n8n + Backblaze)

## 1. Accounts and Billing
1. Create or confirm accounts: Render, Backblaze B2, n8n Cloud, Anthropic, Replicate, ElevenLabs.
2. Set spend limits now:
   - Anthropic: $50/mo
   - Replicate: $100/mo
3. In Render, apply credits and set budget alert at $400 usage.

## 2. Backblaze B2
1. Create bucket: `reels-output`.
2. Set access as needed (public or private with signed links).
3. Create Application Key with bucket-level access.
4. Save these values into your env list:
   - `B2_BUCKET_NAME`
   - `B2_APPLICATION_KEY_ID`
   - `B2_APPLICATION_KEY`
   - `B2_ENDPOINT` (region-specific S3 endpoint)

## 3. Render Infrastructure
1. Create PostgreSQL service (Starter).
2. Create Redis service.
3. Create Background Worker named `reels-video-processor`.
4. Use image: `docker.io/jrottenberg/ffmpeg:7.1-ubuntu2404`.
5. Set disk:
   - Mount path: `/tmp/reels`
   - Size: `5 GB`

## 4. Environment Variables
1. Open `setup/env.render.example`.
2. Copy all variables to Render worker environment.
3. Replace every `replace_me` with real values.
4. Verify `B2_BUCKET_NAME` is `reels-output`.

## 5. Database Setup
1. Open your Render Postgres shell or connect with any SQL client.
2. Execute SQL in `setup/schema.sql`.
3. Confirm tables exist: `customers`, `video_jobs`, `usage_tracking`.

## 6. n8n Pipeline (MVP)
1. Build this sequence:
   - Webhook (input topic)
   - Anthropic HTTP node (script JSON)
   - Loop over scenes
   - Replicate image generation
   - ElevenLabs voice generation
   - Send job payload to Render worker
   - Receive callback and update status
2. Use `setup/anthropic-http-template.json` for the Anthropic request shape.
3. Store response metadata in `video_jobs.cost_breakdown` and `generation_time_seconds`.

## 7. Worker Command (Critical)
Current command is idle and must be replaced.
- Replace:
  - `bash -c "while true; do sleep 60; done"`
- With your real queue consumer command (example patterns):
  - Python: `python worker.py`
  - Node: `node worker.js`

Without this change, jobs will never process.

## 8. Monitoring
1. Add Sentry DSN to worker env.
2. Add n8n health-check workflow (every 6 hours).
3. Alert if:
   - no completed jobs in 6 hours
   - failed job ratio > 5%

## 9. First End-to-End Test
1. Trigger webhook with topic: `Luxury 2BHK in Pune`.
2. Verify sequence:
   - script from Anthropic
   - 5 images from Replicate
   - voice from ElevenLabs
   - ffmpeg output video
   - upload to B2
   - callback updates DB status to `completed`
3. Confirm final URL is stored in `video_jobs.b2_url`.

## 10. Pre-Launch Gate
Ship only if all are true:
1. End-to-end success rate >= 95% across 10 tests.
2. Average generation time < 5 minutes.
3. Cost per video stays in planned range.
4. Retry logic handles transient provider failures.
