from fastapi import FastAPI

from app.api.design_routes import router as design_router
from app.api.routes import router

app = FastAPI(title="Project Marketing OS")
app.include_router(router, prefix="/api/v1")
app.include_router(design_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
