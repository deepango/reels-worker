"""Kling v1.6 Pro image-to-video via Replicate.

Each call animates one real listing photo into a 5- or 10-second 9:16 video
clip with natural camera motion (pan, push-in, parallax). This replaces
Flux-Dev image generation: we no longer create fake interiors — we animate
the agent's actual listing photos.

Kling 1.6 Pro on Replicate costs roughly $0.28–0.35 per 5-second clip at the
time of writing. 8–12 photos per reel → $2.24–4.20 in image-to-video costs.

Replicate polling pattern is identical to the old Flux flow:
  1. POST to predictions → get prediction ID + polling URL.
  2. GET polling URL every 3 s until status ∈ {succeeded, failed, canceled}.
  3. Kling takes ~60–120 s per clip (much longer than Flux) — polling
     budget extended accordingly.
"""

import time
import logging
import httpx

from config import (
    REPLICATE_API_TOKEN,
    KLING_MODEL_URL,
    KLING_POLL_INTERVAL,
    KLING_MAX_POLLS,
)

log = logging.getLogger(__name__)


def generate_clip(start_image_url: str, motion_prompt: str, duration: int = 5) -> str:
    """Animate a listing photo into a 5s / 10s 9:16 video clip.

    Args:
      start_image_url: Public URL of the source photo (B2 or similar).
      motion_prompt:   Short natural-language description of desired camera motion
                       (e.g., "slow push-in toward the kitchen island, subtle parallax").
      duration:        5 or 10 seconds. 5s is the default; 10s doubles the cost.

    Returns:
      Public URL of the generated MP4 clip.
    """
    if duration not in (5, 10):
        raise ValueError(f"Kling duration must be 5 or 10, got {duration}")

    log.info(
        f"[kling] create prediction | duration={duration}s | "
        f"image={start_image_url[:80]} | prompt='{motion_prompt[:80]}'"
    )
    log.debug(
        f"[kling] token_prefix={REPLICATE_API_TOKEN[:8] if REPLICATE_API_TOKEN else 'MISSING'}"
    )

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "start_image": start_image_url,
            "prompt": motion_prompt,
            "duration": duration,
            "aspect_ratio": "9:16",
            # Moderate prompt adherence — too high makes motion robotic, too low ignores the prompt.
            "cfg_scale": 0.5,
            # Block anything that would violate "no people, no text" real-estate requirement.
            "negative_prompt": "people, faces, hands, text, logos, signage, watermarks, low quality, distorted",
        }
    }

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(KLING_MODEL_URL, headers=headers, json=payload)
            log.info(f"[kling] create response status={resp.status_code}")
            if resp.status_code != 201:
                log.error(f"[kling] create error body: {resp.text[:500]}")
            resp.raise_for_status()
            prediction = resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"[kling] create HTTP error {e.response.status_code}: {e.response.text[:500]}")
        raise
    except Exception as e:
        log.error(f"[kling] create request failed: {type(e).__name__}: {e}")
        raise

    prediction_id = prediction.get("id")
    get_url = prediction["urls"]["get"]
    log.info(
        f"[kling] prediction created | id={prediction_id} | status={prediction.get('status')}"
    )

    poll_count = 0
    try:
        with httpx.Client(timeout=60) as client:
            while poll_count < KLING_MAX_POLLS:
                time.sleep(KLING_POLL_INTERVAL)
                resp = client.get(
                    get_url,
                    headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                log.debug(
                    f"[kling] poll {poll_count + 1}/{KLING_MAX_POLLS} | "
                    f"id={prediction_id} | status={status}"
                )
                if status == "succeeded":
                    # Kling returns a string URL or list depending on version; handle both.
                    out = data["output"]
                    clip_url = out if isinstance(out, str) else out[0]
                    log.info(
                        f"[kling] succeeded | id={prediction_id} | "
                        f"polls={poll_count + 1} | url={clip_url}"
                    )
                    return clip_url
                if status in ("failed", "canceled"):
                    err = data.get("error")
                    log.error(f"[kling] prediction {status} | id={prediction_id} | error={err}")
                    raise RuntimeError(f"Kling prediction {status}: {err}")
                poll_count += 1
    except httpx.HTTPStatusError as e:
        log.error(f"[kling] poll HTTP error {e.response.status_code}: {e.response.text[:300]}")
        raise
    except RuntimeError:
        raise
    except Exception as e:
        log.error(f"[kling] poll failed: {type(e).__name__}: {e}")
        raise

    log.error(
        f"[kling] timed out after {KLING_MAX_POLLS} polls "
        f"({KLING_MAX_POLLS * KLING_POLL_INTERVAL}s) | prediction_id={prediction_id}"
    )
    raise RuntimeError(
        f"Kling prediction timed out after {KLING_MAX_POLLS} polls "
        f"({KLING_MAX_POLLS * KLING_POLL_INTERVAL}s)"
    )
