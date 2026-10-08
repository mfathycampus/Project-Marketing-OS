from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.design_routes import router as design_router
from app.api.publishing_routes import router as publishing_router
from app.api.routes import router
from app.config import settings
from app.designs.worker import DesignWorker
from app.publishing.worker import PublishingWorker
from app.migrate import auto_migrate_if_sqlite


@asynccontextmanager
async def lifespan(_: FastAPI):
    auto_migrate_if_sqlite()
    workers = [DesignWorker(), PublishingWorker()] if settings.worker_enabled else []
    for w in workers:
        w.start()
    yield
    for w in workers:
        w.stop()


app = FastAPI(title="Project Marketing OS", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
app.include_router(design_router, prefix="/api/v1")
app.include_router(publishing_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")


_WEB = Path(__file__).resolve().parents[2] / "frontend"
if _WEB.exists():
    app.mount("/ui", StaticFiles(directory=_WEB, html=True), name="ui")
