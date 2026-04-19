import time
import logging
from config import SCENE_RATE_LIMIT_WAIT
import db
from queue import push_job
from providers import anthropic, replicate, elevenlabs

log = logging.getLogger(__name__)

TEST_SCENES = [
    {"scene_id": 1, "image_url": "https://picsum.photos/seed/reels1/1080/1920", "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3", "voiceover_text": "Welcome to this stunning property."},
    {"scene_id": 2, "image_url": "https://picsum.photos/seed/reels2/1080/1920", "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3", "voiceover_text": "Spacious living areas with natural light."},
    {"scene_id": 3, "image_url": "https://picsum.photos/seed/reels3/1080/1920", "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3", "voiceover_text": "A kitchen designed for modern living."},
    {"scene_id": 4, "image_url": "https://picsum.photos/seed/reels4/1080/1920", "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3", "voiceover_text": "Luxurious bedrooms with panoramic views."},
    {"scene_id": 5, "image_url": "https://picsum.photos/seed/reels5/1080/1920", "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3", "voiceover_text": "Your dream home awaits."},
]


def run_test_job(job_id: int, topic: str, customer_id: int):
    """Push hardcoded test scenes directly to Redis — no AI calls."""
    try:
        payload = {"job_id": job_id, "topic": topic, "customer_id": customer_id, "scenes": TEST_SCENES}
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(f"Test job {job_id} pushed to queue")
    except Exception as e:
        log.error(f"Test job {job_id} failed: {e}")
        db.mark_failed(job_id, str(e))
        raise


def run_real_job(job_id: int, topic: str, customer_id: int):
    """Full pipeline: Claude script → per-scene Replicate + ElevenLabs → Redis."""
    try:
        log.info(f"Job {job_id}: generating script for topic='{topic}'")
        scenes = anthropic.generate_script(topic)

        scene_assets = []
        for i, scene in enumerate(scenes):
            if i > 0:
                log.info(f"Job {job_id}: waiting {SCENE_RATE_LIMIT_WAIT}s before scene {i+1}")
                time.sleep(SCENE_RATE_LIMIT_WAIT)

            log.info(f"Job {job_id}: scene {scene['scene_id']} — generating image")
            image_url = replicate.generate_image(scene["image_prompt"])

            log.info(f"Job {job_id}: scene {scene['scene_id']} — generating audio")
            audio_url = elevenlabs.get_or_generate_audio(scene["voiceover_text"])

            scene_assets.append({
                "scene_id": scene["scene_id"],
                "image_url": image_url,
                "audio_url": audio_url,
                "voiceover_text": scene["voiceover_text"],
            })

        payload = {"job_id": job_id, "topic": topic, "customer_id": customer_id, "scenes": scene_assets}
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(f"Job {job_id}: pushed to queue, {len(scene_assets)} scenes")

    except Exception as e:
        log.error(f"Job {job_id} failed: {e}")
        db.mark_failed(job_id, str(e))
        raise
