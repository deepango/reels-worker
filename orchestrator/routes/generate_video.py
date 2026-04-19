"""POST /webhook/generate-video — entry point for reel creation.

Request shape (real-estate v2):

    {
      "customer_id": 1,
      "topic":        "3BR condo in Austin TX, $620k, South Lamar",
      "photo_urls":   ["https://.../pic1.jpg", ...],   # 1..20, 8-12 recommended
      "brand_profile": {                                  # optional
         "agent_name":  "Jane Doe",
         "market":      "Austin TX",
         "brokerage":   "Keller Williams"
      },
      "test":          false                              # when true, skip AI and use hardcoded scenes
    }

The call returns immediately ({ok, job_id, status: queued}); the AI pipeline
runs in a FastAPI BackgroundTask.
"""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

import db
import pipeline

log = logging.getLogger(__name__)
router = APIRouter()


class BrandProfile(BaseModel):
    agent_name: Optional[str] = None
    market: Optional[str] = None
    brokerage: Optional[str] = None


class GenerateVideoRequest(BaseModel):
    customer_id: int
    topic: str
    photo_urls: list[str] = Field(default_factory=list)
    brand_profile: Optional[BrandProfile] = None
    test: bool = False


@router.post("/webhook/generate-video")
async def generate_video(body: GenerateVideoRequest, background_tasks: BackgroundTasks):
    log.info(
        f"[route] POST /webhook/generate-video | "
        f"customer_id={body.customer_id} | topic='{body.topic}' | "
        f"photos={len(body.photo_urls)} | brand={body.brand_profile} | test={body.test}"
    )

    # ── Input validation (skip photo check for test mode) ─────────────────
    if not body.test:
        if len(body.photo_urls) < 1:
            raise HTTPException(status_code=400, detail="photo_urls must not be empty")
        if len(body.photo_urls) > 20:
            raise HTTPException(status_code=400, detail="photo_urls must not exceed 20")

    # ── Quota gate ────────────────────────────────────────────────────────
    try:
        remaining = db.check_quota(body.customer_id)
    except ValueError as e:
        log.warning(f"[route] customer lookup failed: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log.error(f"[route] DB error during quota check: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Database error")

    if remaining <= 0:
        log.warning(f"[route] customer {body.customer_id} has no quota remaining")
        raise HTTPException(
            status_code=402,
            detail="No videos remaining. Please upgrade your plan.",
        )

    # ── Insert job row (status=pending) ───────────────────────────────────
    try:
        job = db.insert_video_job(body.customer_id, body.topic)
    except Exception as e:
        log.error(f"[route] DB error inserting job: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create job")

    job_id = job["id"]

    # ── Schedule the orchestration background task ────────────────────────
    if body.test:
        background_tasks.add_task(
            pipeline.run_test_job,
            job_id,
            body.topic,
            body.customer_id,
        )
    else:
        background_tasks.add_task(
            pipeline.run_real_job,
            job_id,
            body.topic,
            body.customer_id,
            body.photo_urls,
            body.brand_profile.model_dump() if body.brand_profile else None,
        )

    log.info(
        f"[route] job {job_id} scheduled | "
        f"mode={'test' if body.test else 'real'} | returning immediately"
    )
    return {"ok": True, "job_id": job_id, "status": "queued"}
