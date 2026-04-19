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
    remaining = db.check_quota(body.customer_id)
    if remaining <= 0:
        raise HTTPException(status_code=402, detail="No videos remaining. Please upgrade your plan.")

    job = db.insert_video_job(body.customer_id, body.topic)
    job_id = job["id"]

    if body.test:
        background_tasks.add_task(pipeline.run_test_job, job_id, body.topic, body.customer_id)
    else:
        background_tasks.add_task(pipeline.run_real_job, job_id, body.topic, body.customer_id)

    log.info(f"Job {job_id} queued for customer {body.customer_id}, test={body.test}")
    return {"ok": True, "job_id": job_id, "status": "queued"}
