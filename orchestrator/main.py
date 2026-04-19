import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Reels orchestrator starting up")
    yield
    log.info("Reels orchestrator shutting down")


app = FastAPI(title="Reels Orchestrator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True, "service": "reels-orchestrator"}


try:
    from routes.generate_video import router as generate_video_router
    from routes.render_callback import router as render_callback_router
    app.include_router(generate_video_router)
    app.include_router(render_callback_router)
    log.info("Routes registered")
except ImportError as e:
    log.warning(f"Routes not yet available: {e}")
