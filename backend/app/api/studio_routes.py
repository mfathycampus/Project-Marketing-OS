from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.ai.provider import LLMProvider
from app.api.routes import get_llm
from app.studio import service as st

router = APIRouter(prefix="/studio")


class ScriptIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    aspect: str = "9:16"
    length: str = "medium"


@router.get("/options")
def options():
    return st.options()


@router.post("/script")
def write_script(body: ScriptIn, llm: LLMProvider = Depends(get_llm)):
    try:
        return st.write_script(llm, body.prompt, body.aspect, body.length)
    except st.StudioError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/videos", status_code=201)
def create_video(body: st.VideoSpec, studio: st.Studio = Depends(st.get_studio)):
    try:
        return studio.create(body)
    except st.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/videos")
def list_videos(studio: st.Studio = Depends(st.get_studio)):
    return studio.list()


@router.get("/videos/{vid}")
def get_video(vid: str, studio: st.Studio = Depends(st.get_studio)):
    try:
        return studio.get(vid)
    except LookupError as exc:
        raise HTTPException(404, "video not found") from exc


@router.delete("/videos/{vid}", status_code=204)
def delete_video(vid: str, studio: st.Studio = Depends(st.get_studio)):
    try:
        studio.delete(vid)
    except LookupError as exc:
        raise HTTPException(404, "video not found") from exc
    except st.StudioError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(status_code=204)


@router.get("/videos/{vid}/preview")
def preview(vid: str, studio: st.Studio = Depends(st.get_studio)):
    try:
        return FileResponse(studio.master(vid), media_type="video/mp4")
    except LookupError as exc:
        raise HTTPException(404, "video not found") from exc
    except st.StudioError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/videos/{vid}/export")
def export(vid: str, format: str = "mp4", quality: str = "1080p", studio: st.Studio = Depends(st.get_studio)):
    try:
        path = studio.export(vid, format, quality)
        title = studio.get(vid)["title"]
    except LookupError as exc:
        raise HTTPException(404, "video not found") from exc
    except st.StudioError as exc:
        raise HTTPException(422, str(exc)) from exc
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "video"
    name = f"{safe}.{format}" if format == "srt" else f"{safe} {quality}.{format}"
    return FileResponse(path, media_type=st.MIME[format], filename=name)
