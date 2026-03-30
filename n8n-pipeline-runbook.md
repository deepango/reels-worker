# Runbook: n8n Pipeline Integration

## 6.1 Preparation
1. Start an n8n Cloud trial if you haven't already.
2. In n8n, create a new workflow and name it `Reels Video Pipeline`.
3. Add your credentials to n8n:
    * Replicate API Key (as Header Auth: `Authorization: Token [KEY]`)
    * ElevenLabs API Key (as Header Auth: `xi-api-key: [KEY]`)
    * Anthropic API Key (as Header Auth: `x-api-key: [KEY]`, also add header `anthropic-version: 2023-06-01`)
    * PostgreSQL Credentials (Host, Port, DB, User, Password from your Render external connection)
    * Redis Credentials (URL from Render)

## 6.2 The Input Phase
1. Add a **Webhook Node**.
    * Path: `generate-video`
    * HTTP Method: `POST`
    * Response Mode: `On Received`
    * Test the webhook using Postman or cURL to send JSON: `{"topic": "Luxury 2BHK in Pune", "customer_id": 1}`.
2. Add a **PostgreSQL Node** (Insert).
    * Table: `video_jobs`
    * Map the `customer_id` and `topic` from the webhook body.
    * Set `status` to `pending`.
    * Output the new generic `job_id`.

## 6.3 The AI Script Generation Phase (Anthropic)
1. Add an **HTTP Request Node**.
    * Method: `POST`
    * URL: `https://api.anthropic.com/v1/messages`
    * Authentication: Select your Anthropic credentials.
    * Body shape: Use the template from `setup/anthropic-http-template.json`.
    * Inject `topic` dynamically from the Webhook node into the prompt.
2. Add an **Edit Fields Node** (JSON Parse).
    * Extract the JSON script payload returned by Anthropic (it contains the `scenes` array).

## 6.4 The Generation Loop (Images & Audio)
1. Add a **Loop Node** (formerly Item Lists node).
    * Configure to iterate over the `scenes` array from the Anthropic response.
2. Inside the loop, branch to two parallel operations:

    **Branch A: Replicate Image Generation**
    * Add an **HTTP Request Node**.
    * URL: `https://api.replicate.com/v1/predictions`
    * Body: Send the `image_prompt` for the specific scene. (Model: e.g., `stability-ai/sdxl`).
    * Add a **Wait Node** (Wait for Replicate to finish, or use a polling sub-workflow).
    * Retrieve the generated image URL.

    **Branch B: ElevenLabs Audio Generation**
    * Add an **HTTP Request Node**.
    * URL: `https://api.Elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}`
    * Body: Send the `voiceover_text` for the specific scene.
    * Output: Binary audio file.

3. Add a **Merge Node** to reunite Branch A and Branch B.
4. Output the combined Object (Scene ID, Image URL, Audio Binary/URL) back to the **Loop Node**.

## 6.5 Queue Dispatch & Database Update
1. After the Loop Node finishes all scenes, aggregate the results into a single list of scenes.
2. Add a **Redis Node** (or HTTP request if using a custom queue API).
    * Action: `RPUSH` (or add to your specific queue system, like BullMQ).
    * Key: `reels:jobs`
    * Value: A JSON object containing the `job_id`, the full list of scenes (with images/audio), and `topic`.
3. Add a **PostgreSQL Node** (Update).
    * Table: `video_jobs`
    * Update condition: `id = {{job_id}}`
    * Set `status` to `processing`.
    * Set `cost_breakdown` with initial estimated costs from Anthropic/Replicate nodes.

## 6.6 The Render Callback Webhook
1. This is a separate trigger in the same workflow, or a separate workflow entirely.
2. Add a new **Webhook Node**.
    * Path: `render-callback`
    * Method: `POST`
3. Add a **PostgreSQL Node** (Update).
    * Find job by the `job_id` sent in the callback body.
    * Set `status` to `completed` (or `failed`).
    * Set `b2_url` to the final video URL.
    * Calculate and update `generation_time_seconds` based on `created_at` timestamp.
4. Update the `N8N_CALLBACK_URL` in your Render environment variables with this exact Production Webhook URL.
