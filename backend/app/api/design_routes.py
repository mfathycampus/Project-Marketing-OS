import secrets
import uuid

import httpx

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.designs import factory
from app.designs.canva import oauth
from app.designs.canva import service as canva_service
from app.designs.canva.client import CanvaError
from app.designs.provider import DesignProvider
from app.designs.registry import REGISTRY, DesignSpecError
from app.designs.service import DesignService
from app.models import ContentItem
from app.storage.local import LocalStorage

router = APIRouter()
_pending_oauth: dict[str, str] = {}  # state -> verifier (single-process V1; move to DB/Redis with workers)


def get_designer(session: Session = Depends(get_session)) -> DesignProvider:
    return factory.get_design_provider(session)


@router.post("/content-items/{item_id}/design")
def generate_design(item_id: uuid.UUID, session: Session = Depends(get_session),
                    provider: DesignProvider = Depends(get_designer)):
    try:
        asset = DesignService(session, provider).generate(item_id)
    except LookupError:
        raise HTTPException(404, "content item not found")
    except DesignSpecError as exc:
        raise HTTPException(422, str(exc))
    except CanvaError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        raise HTTPException(409 if "not allowed" in str(exc) else 500, str(exc))
    return {"asset_id": asset.id, "provider": asset.provider, "width": asset.width, "height": asset.height,
            "image_url": f"/api/v1/content-items/{item_id}/design/image", "external_url": asset.external_url}


@router.get("/content-items/{item_id}/design/image")
def design_image(item_id: uuid.UUID, session: Session = Depends(get_session)):
    if session.get(ContentItem, item_id) is None:
        raise HTTPException(404, "content item not found")
    asset = DesignService(session, None).latest_asset(item_id)  # type: ignore[arg-type]
    if asset is None:
        raise HTTPException(404, "no design yet")
    return Response(LocalStorage().get(asset.storage_key), media_type=asset.mime_type)


@router.get("/canva/connect")
def canva_connect():
    verifier, challenge = oauth.generate_pkce()
    state = secrets.token_urlsafe(24)
    _pending_oauth[state] = verifier
    return RedirectResponse(oauth.authorize_url(state, challenge))


@router.get("/canva/callback")
def canva_callback(code: str, state: str, session: Session = Depends(get_session)):
    verifier = _pending_oauth.pop(state, None)
    if verifier is None:
        raise HTTPException(400, "invalid or expired state")
    try:
        tokens = oauth.exchange_code(code, verifier)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"Canva token exchange failed: {exc.response.status_code} {exc.response.text[:300]}")
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Canva unreachable: {exc}")
    try:
        canva_service.save_tokens(session, tokens)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, f"Could not store Canva tokens, check ENCRYPTION_KEY in .env: {exc}")
    return canva_service.status(session)


@router.get("/canva/status")
def canva_status(session: Session = Depends(get_session)):
    return canva_service.status(session)


@router.get("/canva/brand-templates")
def canva_brand_templates(session: Session = Depends(get_session)):
    """Brand templates that have autofill data fields (id + title only)."""
    try:
        items = canva_service.get_client(session).list_brand_templates()
    except CanvaError as exc:
        raise HTTPException(502, str(exc))
    return [{"id": t.get("id"), "title": t.get("title")} for t in items]


@router.get("/canva/brand-templates/{template_id}/dataset")
def canva_template_dataset(template_id: str, template_key: str | None = None,
                           session: Session = Depends(get_session)):
    """Data fields of a template plus a ready-to-paste CANVA_TEMPLATE_MAP entry."""
    try:
        dataset = canva_service.get_client(session).brand_template_dataset(template_id)
    except CanvaError as exc:
        raise HTTPException(502, str(exc))
    names = {k.lower(): k for k in dataset}
    spec = REGISTRY.get(template_key or "")
    fields = {f.name: names[f.name] for f in spec.fields if f.name in names} if spec else {}
    entry = {template_key: {"brand_template_id": template_id, "fields": fields}} if spec else None
    return {"fields": {k: v.get("type") for k, v in dataset.items()}, "template_map_entry": entry}
