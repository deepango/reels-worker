import time
import logging
from config import SCENE_RATE_LIMIT_WAIT
import db
from job_queue import push_job
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
    log.info(f"[pipeline] ====== TEST JOB {job_id} START | customer={customer_id} | topic='{topic}' ======")
    try:
        payload = {"job_id": job_id, "topic": topic, "customer_id": customer_id, "scenes": TEST_SCENES}
        log.info(f"[pipeline] pushing test payload to Redis queue")
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(f"[pipeline] ====== TEST JOB {job_id} QUEUED ======")
    except Exception as e:
        log.error(f"[pipeline] TEST JOB {job_id} FAILED: {type(e).__name__}: {e}", exc_info=True)
        db.mark_failed(job_id, str(e))
        raise


def run_real_job(job_id: int, topic: str, customer_id: int):
    log.info(f"[pipeline] ====== REAL JOB {job_id} START | customer={customer_id} | topic='{topic}' ======")
    t_start = time.time()
    try:
        log.info(f"[pipeline] step 1/3 — Claude script generation")
        scenes = anthropic.generate_script(topic)
        log.info(f"[pipeline] script done in {time.time()-t_start:.1f}s | {len(scenes)} scenes")

        scene_assets = []
        for i, scene in enumerate(scenes):
            sid = scene["scene_id"]
            if i > 0:
                log.info(f"[pipeline] rate-limit wait {SCENE_RATE_LIMIT_WAIT}s before scene {sid}")
                time.sleep(SCENE_RATE_LIMIT_WAIT)

            t_scene = time.time()
            log.info(f"[pipeline] scene {sid}/{len(scenes)} — step 2: Replicate image")
            image_url = replicate.generate_image(scene["image_prompt"])
            log.info(f"[pipeline] scene {sid} image done in {time.time()-t_scene:.1f}s | url={image_url}")

            log.info(f"[pipeline] scene {sid}/{len(scenes)} — step 3: ElevenLabs audio")
            audio_url = elevenlabs.get_or_generate_audio(scene["voiceover_text"])
            log.info(f"[pipeline] scene {sid} audio done | url={audio_url}")

            scene_assets.append({
                "scene_id": sid,
                "image_url": image_url,
                "audio_url": audio_url,
                "voiceover_text": scene["voiceover_text"],
            })
            log.info(f"[pipeline] scene {sid} complete | elapsed={time.time()-t_start:.1f}s")

        payload = {"job_id": job_id, "topic": topic, "customer_id": customer_id, "scenes": scene_assets}
        log.info(f"[pipeline] all {len(scene_assets)} scenes done | pushing to Redis")
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(f"[pipeline] ====== REAL JOB {job_id} QUEUED | total_elapsed={time.time()-t_start:.1f}s ======")

    except Exception as e:
        log.error(f"[pipeline] ====== REAL JOB {job_id} FAILED after {time.time()-t_start:.1f}s: {type(e).__name__}: {e} ======", exc_info=True)
        db.mark_failed(job_id, f"{type(e).__name__}: {e}")
        raise
