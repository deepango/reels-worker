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
    log.debug(f"[anthropic] raw response text ({len(text)} chars): {text[:300]}")
    try:
        scenes = json.loads(text)["scenes"]
        log.info(f"[anthropic] parsed {len(scenes)} scenes from raw JSON")
        return scenes
    except Exception as e:
        log.warning(f"[anthropic] direct JSON parse failed ({e}), trying fence extraction")
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        log.info("[anthropic] extracted JSON from markdown fence")
        return json.loads(m.group(1).strip())["scenes"]
    log.info("[anthropic] no fence found, parsing text directly as fallback")
    return json.loads(text)["scenes"]


def generate_script(topic: str) -> list:
    log.info(f"[anthropic] generating script | model={CLAUDE_MODEL} | topic='{topic}'")
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
    log.debug(f"[anthropic] POST api.anthropic.com/v1/messages | api_key_prefix={ANTHROPIC_API_KEY[:12] if ANTHROPIC_API_KEY else 'MISSING'}")
    try:
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
            log.info(f"[anthropic] response status={resp.status_code}")
            if resp.status_code != 200:
                log.error(f"[anthropic] error body: {resp.text[:500]}")
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.error(f"[anthropic] HTTP error {e.response.status_code}: {e.response.text[:500]}")
        raise
    except Exception as e:
        log.error(f"[anthropic] request failed: {type(e).__name__}: {e}")
        raise

    data = resp.json()
    usage = data.get("usage", {})
    log.info(
        f"[anthropic] usage | input_tokens={usage.get('input_tokens')} "
        f"output_tokens={usage.get('output_tokens')} "
        f"cache_read={usage.get('cache_read_input_tokens', 0)} "
        f"cache_write={usage.get('cache_creation_input_tokens', 0)}"
    )
    text = data["content"][0]["text"]
    scenes = _parse_scenes(text)
    for s in scenes:
        s["image_prompt"] = s["image_prompt"] + IMAGE_STYLE_SUFFIX
    log.info(f"[anthropic] script done | {len(scenes)} scenes | voiceovers: {[s['voiceover_text'][:40] for s in scenes]}")
    return scenes
