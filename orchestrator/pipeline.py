"""Reel generation pipeline — real-estate photos → Kling animation → Reel.

Pipeline flow:

  1. Claude produces one voiceover + one motion_prompt per uploaded photo.
  2. For each (photo, voiceover, motion_prompt) — serialised with 11 s rate
     limit to respect Replicate's burst cap:
       a. Kling v1.6 Pro animates the photo into a 5 s 9:16 video clip.
       b. ElevenLabs (cached on B2 by MD5) produces the voiceover MP3.
  3. Aggregated scene list is pushed to Redis. The worker concatenates the
     clips with beat-style crossfades, mixes voiceover + music, burns
     kinetic captions, and uploads the final MP4 to B2.

The orchestrator never renders video itself — all FFmpeg work happens in
worker/main.py. We just prepare assets and hand them off.
"""

import time
import logging

from config import SCENE_RATE_LIMIT_WAIT, KLING_CLIP_DURATION
import db
from job_queue import push_job
from providers import anthropic, kling, elevenlabs

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# TEST-MODE hardcoded scene list (no AI calls). Used by the /webhook test path
# to smoke-test the Redis → worker → B2 leg end to end.
# ──────────────────────────────────────────────────────────────────────────

TEST_SCENES = [
    {
        "scene_id": 1,
        "video_url": "https://picsum.photos/seed/reels1/1080/1920",  # worker must accept static image fallback in test mode
        "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3",
        "voiceover_text": "Welcome to this stunning property.",
    },
    {
        "scene_id": 2,
        "video_url": "https://picsum.photos/seed/reels2/1080/1920",
        "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3",
        "voiceover_text": "Spacious living areas with natural light.",
    },
    {
        "scene_id": 3,
        "video_url": "https://picsum.photos/seed/reels3/1080/1920",
        "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3",
        "voiceover_text": "A kitchen designed for modern living.",
    },
    {
        "scene_id": 4,
        "video_url": "https://picsum.photos/seed/reels4/1080/1920",
        "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3",
        "voiceover_text": "Luxurious bedrooms with panoramic views.",
    },
    {
        "scene_id": 5,
        "video_url": "https://picsum.photos/seed/reels5/1080/1920",
        "audio_url": "https://reels-output.s3.us-east-005.backblazeb2.com/test/short.mp3",
        "voiceover_text": "Your dream home awaits.",
    },
]


def run_test_job(job_id: int, topic: str, customer_id: int):
    """Fast smoke-test: hardcoded scenes, no AI, no Kling."""
    log.info(
        f"[pipeline] ====== TEST JOB {job_id} START | "
        f"customer={customer_id} | topic='{topic}' ======"
    )
    try:
        payload = {
            "job_id": job_id,
            "topic": topic,
            "customer_id": customer_id,
            "scenes": TEST_SCENES,
        }
        log.info(f"[pipeline] pushing test payload ({len(TEST_SCENES)} scenes) to Redis queue")
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(f"[pipeline] ====== TEST JOB {job_id} QUEUED ======")
    except Exception as e:
        log.error(f"[pipeline] TEST JOB {job_id} FAILED: {type(e).__name__}: {e}", exc_info=True)
        db.mark_failed(job_id, str(e))
        raise


def run_real_job(
    job_id: int,
    topic: str,
    customer_id: int,
    photo_urls: list[str],
    brand_profile: dict | None = None,
):
    """Full pipeline — photos → Kling clips + ElevenLabs voiceovers → Redis.

    The worker downstream concatenates the clips, mixes voice + music, burns
    captions, and uploads the final reel.
    """
    log.info(
        f"[pipeline] ====== REAL JOB {job_id} START | "
        f"customer={customer_id} | topic='{topic}' | photos={len(photo_urls)} ======"
    )
    t_start = time.time()
    try:
        # ── 1. Claude: voiceover + motion prompt per photo ────────────────
        log.info(
            f"[pipeline] step 1/3 — Claude script generation "
            f"({len(photo_urls)} scenes requested)"
        )
        scenes = anthropic.generate_script(
            topic=topic,
            photo_count=len(photo_urls),
            brand_profile=brand_profile,
        )
        log.info(f"[pipeline] script done in {time.time() - t_start:.1f}s")

        # ── 2. Per-photo: Kling image-to-video + ElevenLabs TTS ───────────
        scene_assets = []
        for i, (photo_url, scene) in enumerate(zip(photo_urls, scenes)):
            sid = scene.get("scene_id", i + 1)
            voiceover = scene.get("voiceover_text", "")
            motion = scene.get("motion_prompt", "slow push-in with subtle parallax")

            if i > 0:
                log.info(f"[pipeline] rate-limit wait {SCENE_RATE_LIMIT_WAIT}s before scene {sid}")
                time.sleep(SCENE_RATE_LIMIT_WAIT)

            t_scene = time.time()
            log.info(
                f"[pipeline] scene {sid}/{len(photo_urls)} — step 2a: Kling animate "
                f"(motion='{motion[:60]}')"
            )
            video_url = kling.generate_clip(
                start_image_url=photo_url,
                motion_prompt=motion,
                duration=KLING_CLIP_DURATION,
            )
            log.info(
                f"[pipeline] scene {sid} video done in {time.time() - t_scene:.1f}s | "
                f"url={video_url}"
            )

            log.info(f"[pipeline] scene {sid}/{len(photo_urls)} — step 2b: ElevenLabs TTS")
            audio_url = elevenlabs.get_or_generate_audio(voiceover) if voiceover else None
            log.info(f"[pipeline] scene {sid} audio done | url={audio_url}")

            scene_assets.append({
                "scene_id": sid,
                "video_url": video_url,
                "audio_url": audio_url,
                "voiceover_text": voiceover,
                "source_photo_url": photo_url,
            })
            log.info(
                f"[pipeline] scene {sid} complete | elapsed={time.time() - t_start:.1f}s"
            )

        # ── 3. Hand off to worker via Redis ──────────────────────────────
        payload = {
            "job_id": job_id,
            "topic": topic,
            "customer_id": customer_id,
            "brand_profile": brand_profile or {},
            "scenes": scene_assets,
        }
        log.info(
            f"[pipeline] all {len(scene_assets)} scenes done | pushing to Redis"
        )
        push_job(payload)
        db.mark_processing(job_id)
        db.decrement_quota(customer_id)
        log.info(
            f"[pipeline] ====== REAL JOB {job_id} QUEUED | "
            f"total_elapsed={time.time() - t_start:.1f}s ======"
        )

    except Exception as e:
        log.error(
            f"[pipeline] ====== REAL JOB {job_id} FAILED after "
            f"{time.time() - t_start:.1f}s: {type(e).__name__}: {e} ======",
            exc_info=True,
        )
        db.mark_failed(job_id, f"{type(e).__name__}: {e}")
        raise
