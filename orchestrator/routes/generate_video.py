import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import db
import pipeline

log = logging.getLogger(__name__)
router = APIRouter()


class GenerateVideoRequest(BaseModel):
    customer_id: int
    topic: str
    test: bool = False


@router.post("/webhook/generate-video")
async def generate_video(body: GenerateVideoRequest, background_tasks: BackgroundTasks):
    log.info(f"[route] POST /webhook/generate-video | customer_id={body.customer_id} | topic='{body.topic}' | test={body.test}")
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
        raise HTTPException(status_code=402, detail="No videos remaining. Please upgrade your plan.")

    try:
        job = db.insert_video_job(body.customer_id, body.topic)
    except Exception as e:
        log.error(f"[route] DB error inserting job: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create job")

    job_id = job["id"]
    if body.test:
        background_tasks.add_task(pipeline.run_test_job, job_id, body.topic, body.customer_id)
    else:
        background_tasks.add_task(pipeline.run_real_job, job_id, body.topic, body.customer_id)

    log.info(f"[route] job {job_id} scheduled | mode={'test' if body.test else 'real'} | returning immediately")
    return {"ok": True, "job_id": job_id, "status": "queued"}
