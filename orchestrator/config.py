import os

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.environ.get("DATABASE_URL")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")

B2_ENDPOINT = os.environ.get("B2_ENDPOINT")
B2_APPLICATION_KEY_ID = os.environ.get("B2_APPLICATION_KEY_ID")
B2_APPLICATION_KEY = os.environ.get("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.environ.get("B2_BUCKET_NAME", "reels-output")

ORCHESTRATOR_CALLBACK_URL = os.environ.get("ORCHESTRATOR_CALLBACK_URL")  # worker posts here

QUEUE_NAME = "reels:jobs"
VOICE_ID = "p405EPtuTbD6LL5ZPhG3"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# ── Kling v1.6 Pro (image-to-video) ───────────────────────────────────────
# Replaces Flux-Dev image generation. Input: real listing photo URL.
# Output: 5s animated 9:16 MP4 with natural camera motion.
KLING_MODEL_URL = "https://api.replicate.com/v1/models/kwaivgi/kling-v1.6-pro/predictions"
KLING_POLL_INTERVAL = 3        # seconds
KLING_MAX_POLLS = 80           # Kling clips take 60-120s; 80 × 3s = 4 min max
KLING_CLIP_DURATION = 5        # seconds per clip (5 or 10)

# ── Legacy Flux (image generation) ────────────────────────────────────────
# Kept for compatibility with any legacy callers but no longer used by the
# real-estate pipeline. Safe to delete in a later pass.
REPLICATE_MODEL_URL = "https://api.replicate.com/v1/models/black-forest-labs/flux-dev/predictions"
REPLICATE_POLL_INTERVAL = 3
REPLICATE_MAX_POLLS = 60

SCENE_RATE_LIMIT_WAIT = 11    # seconds between scenes (matches n8n)
