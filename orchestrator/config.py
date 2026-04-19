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
REPLICATE_MODEL_URL = "https://api.replicate.com/v1/models/black-forest-labs/flux-dev/predictions"
REPLICATE_POLL_INTERVAL = 3   # seconds
REPLICATE_MAX_POLLS = 60      # flux-dev takes 15-25s vs schnell's 2-3s; 60 × 3s = 3 min max
SCENE_RATE_LIMIT_WAIT = 11    # seconds between scenes (matches n8n)
