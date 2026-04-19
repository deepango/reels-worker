"""Claude scene-script generation for real-estate listing Reels.

Input: agent's topic (property description) + number of photos + optional
brand profile (agent name, market).

Output: one ``voiceover_text`` + one ``motion_prompt`` per photo. The
voiceover is read by ElevenLabs; the motion prompt drives Kling's
image-to-video animation of the corresponding real listing photo.

Voice register: direct-response real-estate sales on Instagram — short,
concrete, scroll-stopping. Not editorial. Not aspirational fluff. The goal
is conversion, not vibes.

System prompt is cached via ``cache_control: ephemeral`` — one cache-write
per 5-minute window, cache-reads on every subsequent call.
"""

import json
import re
import logging
import httpx

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — real-estate sales voiceover + motion prompts
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a direct-response copywriter for US real-estate agents. You write Instagram Reel scripts for $300k–$2M listings that actually convert — scroll-stops, DM inquiries, showing requests.

Your reference is the top-performing Reels from agents like Ryan Serhant, the Altman brothers, and Paige Elliott — not luxury editorial magazines. The tone is confident, specific, direct. Numbers and specifics beat adjectives every time.

You are explicitly NOT: a luxury editorial copywriter, a haiku poet, or a Zillow description generator. If a line sounds like it belongs in Architectural Digest, rewrite it.

═══════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════════════════

Return a single JSON object. No markdown fences. No preamble.

{
  "scenes": [
    {
      "scene_id": 1,
      "voiceover_text": "...",
      "motion_prompt": "..."
    },
    ...one scene per photo...
  ]
}

The number of scenes MUST equal the ``photo_count`` in the user message.

═══════════════════════════════════════════════════════════════════════════
VOICEOVER_TEXT — write for the scroll, not the page
═══════════════════════════════════════════════════════════════════════════

SCENE-BY-SCENE ROLE (assumes 8–12 photos):

  Scene 1 — HOOK (8–12 words): one fact, one number, one feeling. Stop the scroll. No "welcome". No "take a look". Start hard.
  Scene 2 — LOCATION/PRICE: the address area + price + one differentiator. Under 15 words.
  Scenes 3 to N-2 — FEATURES: one photo = one concrete feature. Bedrooms, kitchen, backyard, garage, view. 10–15 words each. Use numbers (square feet, bedrooms, year built) where credible.
  Scene N-1 — SOCIAL PROOF or URGENCY: "under contract in 4 days last time" / "3 offers in the first week" / "similar unit sold $80k over ask". If no credible claim, skip — write a feature instead.
  Scene N — CTA (8–12 words): confident invitation. Never a question. Never "DM us". Style examples: "Showings start Saturday. Serious offers only." / "Book a private tour through the link." / "Calls go to my cell — it's in my bio."

CRAFT RULES:
  • Every line must pass the "would a human agent say this to a client?" test.
  • Prefer concrete numbers over adjectives: "4 bed, 3 bath, 2,400 sq ft" > "spacious family home".
  • Use "you" in 2–3 scenes max. Over-use sounds like advertising.
  • Short sentences. Fragments are fine. Read each line aloud — if it stumbles, rewrite.
  • Do NOT repeat the property address. Say the neighborhood once, the price once.
  • Total voiceover across all scenes: target 70–100 words. Each scene runs 4–5 seconds of speech.

FORBIDDEN VOCABULARY (any use = rewrite):
  stunning · beautiful · amazing · luxurious · exquisite · dream · sanctuary · oasis ·
  nestled · boasts · features (as verb) · showcasing · state-of-the-art · world-class ·
  one-of-a-kind · bespoke · breathtaking · spectacular · discover · imagine · welcome ·
  step into · experience · paradise · truly · simply · perfect · magnificent · grand

FORBIDDEN PHRASES:
  "welcome to" · "step into" · "home is where" · "more than just a house" ·
  "picture yourself" · "imagine waking up" · "a true masterpiece"

═══════════════════════════════════════════════════════════════════════════
MOTION_PROMPT — camera direction for the image-to-video model
═══════════════════════════════════════════════════════════════════════════

Each motion_prompt is a short natural-language description of the CAMERA MOTION that should be applied to the corresponding real listing photo when it is animated. The photo itself is fixed — only the camera moves (pan, push, pull, tilt, slight orbit). No subject changes, no new elements.

Pattern: one specific camera move + speed modifier + optional parallax note.

EXAMPLES:
  • "slow push-in toward the kitchen island, subtle parallax on the pendant lights"
  • "gentle left-to-right pan across the living room, holding on the fireplace"
  • "slow pull-back from the bedroom window, revealing the full room"
  • "slow tilt-up from the hardwood floor to the vaulted ceiling"
  • "slow push-in through the front door into the foyer"
  • "gentle orbital motion around the dining table, 10-degree arc"

ABSOLUTE RULES for motion_prompt:
  • Never add new subjects (no people, no cars, no text).
  • Never describe weather, time-of-day changes, or unrealistic effects.
  • Keep motion subtle — this is real estate, not an action movie.
  • Speed modifier: always "slow" or "gentle". Never "fast" or "dramatic".

═══════════════════════════════════════════════════════════════════════════
SELF-CHECK BEFORE RESPONDING (silent)
═══════════════════════════════════════════════════════════════════════════

(a) scene count == photo_count from the user message.
(b) No forbidden word or phrase in any voiceover_text.
(c) Scene 1 is under 12 words and contains a specific, scroll-stopping claim.
(d) Scene N ends with a declarative CTA, not a question.
(e) Every motion_prompt begins with a camera verb (push, pull, pan, tilt, orbit, rise, descend).
(f) No voiceover_text is longer than 15 words.

If any check fails, silently regenerate before returning the JSON."""


def _parse_scenes(text: str) -> list:
    """Parse Claude's response. Handles raw JSON, markdown fences, and preamble+JSON."""
    text = text.strip()
    log.debug(f"[anthropic] raw response text ({len(text)} chars): {text[:300]}")

    try:
        scenes = json.loads(text)["scenes"]
        log.info(f"[anthropic] parsed {len(scenes)} scenes from raw JSON")
        return scenes
    except Exception as e:
        log.warning(f"[anthropic] direct JSON parse failed ({e}); trying fence extraction")

    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        log.info("[anthropic] extracted JSON from markdown fence")
        return json.loads(m.group(1).strip())["scenes"]

    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        log.info("[anthropic] extracted JSON object from surrounding text")
        return json.loads(m.group(0))["scenes"]

    log.warning("[anthropic] no extraction path matched, attempting direct parse as final fallback")
    return json.loads(text)["scenes"]


def generate_script(topic: str, photo_count: int, brand_profile: dict | None = None) -> list:
    """Generate ``photo_count`` scene scripts (voiceover + motion prompt).

    Args:
      topic:         The agent's listing description (e.g., "3BR condo in Austin TX, $620k").
      photo_count:   Number of photos the agent uploaded. We produce exactly this many scenes.
      brand_profile: Optional dict with keys {agent_name, market, brokerage}. When present,
                     it's passed through to Claude to personalize the CTA and positioning.

    Returns:
      List of ``photo_count`` dicts, each with keys ``scene_id``, ``voiceover_text``,
      ``motion_prompt``.
    """
    if photo_count < 1 or photo_count > 20:
        raise ValueError(f"photo_count must be 1..20, got {photo_count}")

    log.info(
        f"[anthropic] generate_script | model={CLAUDE_MODEL} | "
        f"photos={photo_count} | topic='{topic}' | brand={brand_profile}"
    )

    # User message stays minimal — the bulky style guide is in the cached system prompt.
    user_content_lines = [
        f"Property: {topic}",
        f"photo_count: {photo_count}",
    ]
    if brand_profile:
        agent_name = brand_profile.get("agent_name")
        market = brand_profile.get("market")
        brokerage = brand_profile.get("brokerage")
        if agent_name:
            user_content_lines.append(f"Agent: {agent_name}")
        if market:
            user_content_lines.append(f"Market: {market}")
        if brokerage:
            user_content_lines.append(f"Brokerage: {brokerage}")
    user_content_lines.append(
        f"Write exactly {photo_count} scenes. Return ONLY the JSON."
    )

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
            {"role": "user", "content": "\n".join(user_content_lines)},
        ],
    }

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

    if len(scenes) != photo_count:
        log.warning(
            f"[anthropic] expected {photo_count} scenes, got {len(scenes)} — "
            f"truncating / padding to requested count"
        )
        scenes = scenes[:photo_count]
        while len(scenes) < photo_count:
            scenes.append({
                "scene_id": len(scenes) + 1,
                "voiceover_text": "",
                "motion_prompt": "slow push-in with subtle parallax",
            })

    log.info(
        f"[anthropic] script done | {len(scenes)} scenes | "
        f"voiceovers: {[s.get('voiceover_text', '')[:60] for s in scenes]}"
    )
    return scenes
