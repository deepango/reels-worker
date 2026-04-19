import time
import logging
import httpx
from config import REPLICATE_API_TOKEN, REPLICATE_MODEL_URL, REPLICATE_POLL_INTERVAL, REPLICATE_MAX_POLLS

log = logging.getLogger(__name__)


def generate_image(prompt: str) -> str:
    """Creates a Replicate prediction and polls until succeeded. Returns image URL."""
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
            "go_fast": True,
            "num_outputs": 1,
        }
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(REPLICATE_MODEL_URL, headers=headers, json=payload)
        resp.raise_for_status()
        prediction = resp.json()

    get_url = prediction["urls"]["get"]
    poll_count = 0

    with httpx.Client(timeout=30) as client:
        while poll_count < REPLICATE_MAX_POLLS:
            time.sleep(REPLICATE_POLL_INTERVAL)
            resp = client.get(get_url, headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"})
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "succeeded":
                log.info(f"Replicate succeeded after {poll_count + 1} polls")
                return data["output"][0]
            if status in ("failed", "canceled"):
                raise RuntimeError(f"Replicate prediction {status}: {data.get('error')}")
            poll_count += 1

    raise RuntimeError(f"Replicate prediction timed out after {REPLICATE_MAX_POLLS} polls")
