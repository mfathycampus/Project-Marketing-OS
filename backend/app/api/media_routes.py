from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.media.library import MediaError, MediaLibrary, get_library

router = APIRouter(prefix="/media")


class MediaPatch(BaseModel):
    description: str = Field(default="", max_length=300)


@router.get("")
def list_media(lib: MediaLibrary = Depends(get_library)):
    return lib.list()


@router.post("", status_code=201)
async def upload(file: UploadFile = File(...), description: str = Form(""), lib: MediaLibrary = Depends(get_library)):
    try:
        return lib.add(file.filename or "", await file.read(), description)
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/{mid}")
def update(mid: str, body: MediaPatch, lib: MediaLibrary = Depends(get_library)):
    try:
        return lib.update(mid, body.description)
    except LookupError as exc:
        raise HTTPException(404, "media not found") from exc


@router.delete("/{mid}", status_code=204)
def delete(mid: str, lib: MediaLibrary = Depends(get_library)):
    try:
        lib.delete(mid)
    except LookupError as exc:
        raise HTTPException(404, "media not found") from exc
    return Response(status_code=204)


@router.get("/{mid}/file")
def media_file(mid: str, lib: MediaLibrary = Depends(get_library)):
    try:
        return FileResponse(lib.file(mid))
    except LookupError as exc:
        raise HTTPException(404, "media not found") from exc


@router.get("/{mid}/poster")
def media_poster(mid: str, lib: MediaLibrary = Depends(get_library)):
    try:
        return FileResponse(lib.poster(mid), media_type="image/jpeg")
    except LookupError as exc:
        raise HTTPException(404, "media not found") from exc
