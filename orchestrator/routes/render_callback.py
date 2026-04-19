import logging
from fastapi import APIRouter
from pydantic import BaseModel
import db

log = logging.getLogger(__name__)
router = APIRouter()


class RenderCallbackRequest(BaseModel):
    job_id: int
    status: str
    b2_url: str = None
    error: str = None


@router.post("/webhook/render-callback")
async def render_callback(body: RenderCallbackRequest):
    db.update_callback_received(body.job_id)
    log.info(f"Render callback received for job {body.job_id}, status={body.status}")
    return {"ok": True}
