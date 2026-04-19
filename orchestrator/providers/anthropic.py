import json
import re
import logging
import httpx
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an elite real estate reel director.\n"
    "Create a 5-scene script:\n"
    "SCENE 1—HOOK: max 10 words\n"
    "SCENE 2—DESIRE: max 12 words\n"
    "SCENE 3—LIFESTYLE: max 12 words\n"
    "SCENE 4—DETAIL: max 10 words\n"
    "SCENE 5—CTA: max 10 words\n"
    "Voiceover: 2nd person, present tense, sensory. No: stunning/beautiful/amazing/luxurious.\n"
    "Image prompts: shot type + lighting + Dezeen/AD India style + 9:16 portrait, no people/text/watermarks."
)

IMAGE_STYLE_SUFFIX = (
    ", Architectural Digest India editorial style, luxury real estate photography, "
    "ultra-sharp, 9:16 portrait format, no people, no text, no watermarks, photorealistic"
)


def _parse_scenes(text: str) -> list:
    text = text.strip()
    try:
        return json.loads(text)["scenes"]
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    return json.loads(m.group(1).strip() if m else text)["scenes"]


def generate_script(topic: str) -> list:
    """Calls Claude and returns list of scene dicts with scene_id, image_prompt, voiceover_text."""
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": (
                    f'Property: {topic}\n'
                    'Return ONLY raw JSON: {"scenes": [{"scene_id": 1, "image_prompt": "...", "voiceover_text": "..."}]}'
                ),
            }
        ],
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "prompt-caching-2024-07-31",
                "content-type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
    data = resp.json()
    cache_read = data.get("usage", {}).get("cache_read_input_tokens", 0)
    log.info(f"Claude usage: cache_read={cache_read} input_tokens={data.get('usage',{}).get('input_tokens',0)}")
    text = data["content"][0]["text"]
    scenes = _parse_scenes(text)
    for s in scenes:
        s["image_prompt"] = s["image_prompt"] + IMAGE_STYLE_SUFFIX
    return scenes
