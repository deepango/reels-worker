import time
import logging
import httpx
from config import REPLICATE_API_TOKEN, REPLICATE_MODEL_URL, REPLICATE_POLL_INTERVAL, REPLICATE_MAX_POLLS

log = logging.getLogger(__name__)


def generate_image(prompt: str) -> str:
    log.info(f"[replicate] creating prediction | prompt_len={len(prompt)} | prompt_preview='{prompt[:80]}'")
    log.debug(f"[replicate] token_prefix={REPLICATE_API_TOKEN[:8] if REPLICATE_API_TOKEN else 'MISSING'}")
    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "output_format": "jpg",
            "output_quality": 95,
            # flux-dev: full 50-step diffusion model. 35 steps is the sweet spot —
            # default 28 is too fast for marble/brass material detail, 50 is overkill.
            "num_inference_steps": 35,
            # guidance (CFG scale): 3.5 is flux-dev's default and works well for
            # architectural prompts. Lower = more creative, higher = stricter adherence.
            "guidance": 3.5,
            "num_outputs": 1,
            # Note: flux-dev does NOT support go_fast or disable_safety_checker
        }
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(REPLICATE_MODEL_URL, headers=headers, json=payload)
            log.info(f"[replicate] create response status={resp.status_code}")
            if resp.status_code != 201:
                log.error(f"[replicate] create error body: {resp.text[:500]}")
            resp.raise_for_status()
            prediction = resp.json()
    except httpx.HTTPStatusError as e:
        log.error(f"[replicate] create HTTP error {e.response.status_code}: {e.response.text[:500]}")
        raise
    except Exception as e:
        log.error(f"[replicate] create request failed: {type(e).__name__}: {e}")
        raise

    prediction_id = prediction.get("id")
    get_url = prediction["urls"]["get"]
    log.info(f"[replicate] prediction created | id={prediction_id} | status={prediction.get('status')}")

    poll_count = 0
    try:
        with httpx.Client(timeout=30) as client:
            while poll_count < REPLICATE_MAX_POLLS:
                time.sleep(REPLICATE_POLL_INTERVAL)
                resp = client.get(get_url, headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"})
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                log.debug(f"[replicate] poll {poll_count+1}/{REPLICATE_MAX_POLLS} | status={status}")
                if status == "succeeded":
                    image_url = data["output"][0]
                    log.info(f"[replicate] succeeded after {poll_count+1} polls | url={image_url}")
                    return image_url
                if status in ("failed", "canceled"):
                    err = data.get("error")
                    log.error(f"[replicate] prediction {status} | error={err}")
                    raise RuntimeError(f"Replicate prediction {status}: {err}")
                poll_count += 1
    except httpx.HTTPStatusError as e:
        log.error(f"[replicate] poll HTTP error {e.response.status_code}: {e.response.text[:300]}")
        raise
    except RuntimeError:
        raise
    except Exception as e:
        log.error(f"[replicate] poll failed: {type(e).__name__}: {e}")
        raise

    log.error(f"[replicate] timed out after {REPLICATE_MAX_POLLS} polls | prediction_id={prediction_id}")
    raise RuntimeError(f"Replicate prediction timed out after {REPLICATE_MAX_POLLS} polls")
