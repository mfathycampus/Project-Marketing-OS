from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="Project Marketing OS")
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
