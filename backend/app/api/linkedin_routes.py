import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session, utcnow
from app.designs.canva.crypto import decrypt
from app.models import MetaPending, Project
from app.social import linkedin

router = APIRouter()


class SelectIn(BaseModel):
    pending_id: uuid.UUID
    org_id: str


def _ui(project_id, suffix: str) -> RedirectResponse:
    return RedirectResponse(f"/ui/#/p/{project_id}/publish?{suffix}")


def _configured() -> bool:
    return bool(settings.linkedin_client_id and settings.linkedin_client_secret)


@router.get("/linkedin/status")
def linkedin_status():
    return {"configured": _configured(), "redirect_uri": settings.linkedin_redirect_uri,
            "scopes": settings.linkedin_scopes.split()}


@router.get("/linkedin/connect")
def linkedin_connect(project_id: uuid.UUID, session: Session = Depends(get_session)):
    if not _configured():
        raise HTTPException(400, "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET are not set in .env")
    if session.get(Project, project_id) is None:
        raise HTTPException(404, "project not found")
    return RedirectResponse(linkedin.login_url(project_id))


@router.get("/linkedin/callback")
def linkedin_callback(state: str, code: str | None = None, error: str | None = None,
                      error_description: str | None = None, session: Session = Depends(get_session)):
    try:
        project_id = linkedin.read_state(state)
    except linkedin.LinkedInError as exc:
        raise HTTPException(400, str(exc))
    if error or not code:
        msg = error_description or error or "cancelled"
        if "scope" in msg.lower():
            msg += (" — صلاحيات صفحة الشركة غير مفعّلة لتطبيقك بعد: اطلب منتج Community Management API من تبويب "
                    "Products في تطبيق LinkedIn، وبعد الموافقة تظهر الصلاحيات في تبويب Auth (OAuth 2.0 scopes)")
        return _ui(project_id, "li_error=" + quote(msg))
    client = linkedin.LinkedInClient()
    try:
        tokens = client.exchange_code(code)
        token, expires_in = tokens["access_token"], int(tokens.get("expires_in", 5184000))
        orgs = client.admin_organizations(token)
    except Exception as exc:  # noqa: BLE001 - shown to the user, never contains tokens
        return _ui(project_id, "li_error=" + quote(str(exc)[:300]))
    if not orgs:
        return _ui(project_id, "li_error=" + quote(
            "لم نجد صفحة شركة تديرها بهذا الحساب. تأكد أنك Super admin أو Content admin للصفحة، "
            "وأن المنتج Community Management API معتمد لتطبيقك"))
    if len(orgs) == 1:
        linkedin.save_selection(session, project_id, token, expires_in, orgs[0])
        session.commit()
        return _ui(project_id, "connected=1")
    pending = MetaPending(project_id=project_id, payload_enc=linkedin.pending_payload(token, expires_in, orgs),
                          created_at=utcnow())
    session.add(pending)
    session.commit()
    return _ui(project_id, f"pick_li={pending.id}")


@router.get("/linkedin/pending/{pending_id}")
def pending_orgs(pending_id: uuid.UUID, session: Session = Depends(get_session)):
    pending = session.get(MetaPending, pending_id)
    if pending is None:
        raise HTTPException(404, "selection expired, connect again")
    return json.loads(decrypt(pending.payload_enc)).get("orgs", [])  # never includes the token


@router.post("/linkedin/select")
def select_org(body: SelectIn, session: Session = Depends(get_session)):
    pending = session.get(MetaPending, body.pending_id)
    if pending is None:
        raise HTTPException(404, "selection expired, connect again")
    data = json.loads(decrypt(pending.payload_enc))
    org = next((o for o in data.get("orgs", []) if o["id"] == body.org_id), None)
    if org is None:
        raise HTTPException(404, "organization not in the list")
    conn = linkedin.save_selection(session, pending.project_id, data["token"], int(data["expires_in"]), org)
    session.delete(pending)
    session.commit()
    return {"platform": conn.platform, "account_name": conn.account_name}
