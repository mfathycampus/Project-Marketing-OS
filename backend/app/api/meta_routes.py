import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session, utcnow
from app.designs.canva.crypto import decrypt
from app.models import MetaPending, Project, SocialConnection
from app.social import meta

router = APIRouter()


class SelectIn(BaseModel):
    pending_id: uuid.UUID
    page_id: str


def _ui(project_id, suffix: str) -> RedirectResponse:
    return RedirectResponse(f"/ui/#/p/{project_id}/publish?{suffix}")


def _configured() -> bool:
    return bool(settings.meta_app_id and settings.meta_app_secret)


@router.get("/meta/status")
def meta_status():
    return {"configured": _configured(), "redirect_uri": settings.meta_redirect_uri,
            "public_base_url_set": bool(settings.public_base_url),
            "instagram_ready": _configured() and bool(settings.public_base_url)}


@router.get("/meta/connect")
def meta_connect(project_id: uuid.UUID, session: Session = Depends(get_session)):
    if not _configured():
        raise HTTPException(400, "META_APP_ID / META_APP_SECRET are not set in .env")
    if session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    return RedirectResponse(meta.login_url(project_id))


@router.get("/meta/callback")
def meta_callback(state: str, code: str | None = None, error: str | None = None,
                  error_description: str | None = None, session: Session = Depends(get_session)):
    try:
        project_id = meta.read_state(state)
    except meta.MetaError as exc:
        raise HTTPException(400, str(exc))
    if error or not code:
        return _ui(project_id, "meta_error=" + quote(error_description or error or "cancelled"))
    client = meta.MetaClient()
    try:
        token = client.exchange_code(code)
        pages = client.pages(token)
    except (meta.MetaError, Exception) as exc:  # noqa: BLE001 - shown to the user, never contains tokens
        return _ui(project_id, "meta_error=" + quote(str(exc)[:300]))
    if not pages:
        who = client.whoami(token)
        hint = (f"لم تُرجع Meta أي صفحة. الحساب: {who['name'] or 'غير معروف'}. "
                f"الصلاحيات الممنوحة: {', '.join(who['granted']) or 'لا شيء'}")
        if "pages_show_list" not in who["granted"]:
            hint += " | ناقصة pages_show_list"
        return _ui(project_id, "meta_error=" + quote(hint))
    if len(pages) == 1:
        meta.save_selection(session, project_id, pages[0])
        session.commit()
        return _ui(project_id, "connected=1")
    pending = MetaPending(project_id=project_id, payload_enc=meta.pending_payload(pages), created_at=utcnow())
    session.add(pending)
    session.commit()
    return _ui(project_id, f"pick={pending.id}")


@router.get("/meta/pending/{pending_id}")
def pending_pages(pending_id: uuid.UUID, session: Session = Depends(get_session)):
    pending = session.get(MetaPending, pending_id)
    if pending is None:
        raise HTTPException(404, "selection expired, connect again")
    pages = json.loads(decrypt(pending.payload_enc))
    return [{"id": p["id"], "name": p.get("name", ""),
             "instagram": (p.get("instagram_business_account") or {}).get("username")} for p in pages]


@router.post("/meta/select")
def select_page(body: SelectIn, session: Session = Depends(get_session)):
    pending = session.get(MetaPending, body.pending_id)
    if pending is None:
        raise HTTPException(404, "selection expired, connect again")
    page = next((p for p in json.loads(decrypt(pending.payload_enc)) if p["id"] == body.page_id), None)
    if page is None:
        raise HTTPException(404, "page not in the list")
    conns = meta.save_selection(session, pending.project_id, page)
    session.delete(pending)
    session.commit()
    return [{"platform": c.platform, "account_name": c.account_name} for c in conns]


@router.get("/projects/{project_id}/social-connections")
def list_connections(project_id: uuid.UUID, session: Session = Depends(get_session)):
    rows = session.scalars(select(SocialConnection).where(SocialConnection.project_id == project_id))
    return [{"id": c.id, "platform": c.platform, "account_name": c.account_name, "status": c.status} for c in rows]


@router.delete("/projects/{project_id}/social-connections/{conn_id}", status_code=204)
def disconnect(project_id: uuid.UUID, conn_id: uuid.UUID, session: Session = Depends(get_session)):
    conn = session.get(SocialConnection, conn_id)
    if conn is None or conn.project_id != project_id:
        raise HTTPException(404, "connection not found")
    session.delete(conn)
    session.commit()


@router.get("/meta/check-public-url")
def check_public_url():
    """Can the internet reach this server? Instagram fetches images from PUBLIC_BASE_URL."""
    import httpx

    base = settings.public_base_url.rstrip("/")
    if not base:
        return {"ok": False, "detail": "PUBLIC_BASE_URL is not set"}
    try:
        r = httpx.get(f"{base}/health", timeout=15, follow_redirects=True)
        return {"ok": r.status_code == 200 and r.json().get("status") == "ok", "status": r.status_code,
                "detail": "reachable" if r.status_code == 200 else f"HTTP {r.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"not reachable: {exc}"}
