import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.orchestrator import AIGenerationError, AIOrchestrator
from app.ai.provider import LLMProvider, get_provider
from app.ai.schemas import CampaignRequest
from app.api import schemas as s
from app.content import service as content_service
from app.content.state_machine import ContentStatus, InvalidTransition
from app.db import get_session
from app.models import (AudienceProfile, BrandProfile, BrandRule, Campaign, ContentItem, ContentVariant,
                        Product, Project, Publication)

router = APIRouter()


def _project(session: Session, project_id: uuid.UUID) -> Project:
    p = session.get(Project, project_id)
    if p is None or p.status == "deleted":
        raise HTTPException(404, "project not found")
    return p


@router.post("/projects", response_model=s.ProjectOut, status_code=201)
def create_project(body: s.ProjectIn, session: Session = Depends(get_session)):
    p = Project(name=body.name, timezone=body.timezone)
    session.add(p)
    session.commit()
    return p


@router.get("/projects", response_model=list[s.ProjectOut])
def list_projects(include_archived: bool = False, session: Session = Depends(get_session)):
    q = select(Project).order_by(Project.created_at.desc())
    if not include_archived:
        q = q.where(Project.status == "active")
    return session.scalars(q).all()


@router.get("/projects/{project_id}", response_model=s.ProjectOut)
def get_project(project_id: uuid.UUID, session: Session = Depends(get_session)):
    return _project(session, project_id)


@router.patch("/projects/{project_id}", response_model=s.ProjectOut)
def update_project(project_id: uuid.UUID, body: s.ProjectPatch, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    session.commit()
    return p


@router.post("/projects/{project_id}/archive", response_model=s.ProjectOut)
def archive_project(project_id: uuid.UUID, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    p.status = "archived"
    session.commit()
    return p


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, session: Session = Depends(get_session)):
    session.delete(_project(session, project_id))
    session.commit()
    return Response(status_code=204)


# ---- Brand Brain ----
@router.put("/projects/{project_id}/brand", response_model=s.BrandProfileIn)
def put_brand(project_id: uuid.UUID, body: s.BrandProfileIn, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    if p.brand_profile is None:
        p.brand_profile = BrandProfile(**body.model_dump())
    else:
        for k, v in body.model_dump().items():
            setattr(p.brand_profile, k, v)
    session.commit()
    return p.brand_profile


@router.put("/projects/{project_id}/audience", response_model=s.AudienceIn)
def put_audience(project_id: uuid.UUID, body: s.AudienceIn, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    if p.audience_profile is None:
        p.audience_profile = AudienceProfile(**body.model_dump())
    else:
        for k, v in body.model_dump().items():
            setattr(p.audience_profile, k, v)
    session.commit()
    return p.audience_profile


@router.post("/projects/{project_id}/products", response_model=s.ProductOut, status_code=201)
def add_product(project_id: uuid.UUID, body: s.ProductIn, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    prod = Product(**body.model_dump())
    p.products.append(prod)
    session.commit()
    return prod


@router.get("/projects/{project_id}/products", response_model=list[s.ProductOut])
def list_products(project_id: uuid.UUID, session: Session = Depends(get_session)):
    return _project(session, project_id).products


@router.get("/projects/{project_id}/brand-brain")
def brand_brain(project_id: uuid.UUID, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    return {
        "brand": s.BrandProfileIn.model_validate(p.brand_profile, from_attributes=True) if p.brand_profile else None,
        "audience": s.AudienceIn.model_validate(p.audience_profile, from_attributes=True) if p.audience_profile else None,
        "products": [s.ProductOut.model_validate(x, from_attributes=True) for x in p.products],
        "rules": [s.RuleOut.model_validate(x, from_attributes=True) for x in p.rules],
    }


@router.delete("/projects/{project_id}/products/{product_id}", status_code=204)
def delete_product(project_id: uuid.UUID, product_id: uuid.UUID, session: Session = Depends(get_session)):
    prod = session.get(Product, product_id)
    if prod is None or prod.project_id != project_id:
        raise HTTPException(404, "product not found")
    session.delete(prod)
    session.commit()
    return Response(status_code=204)


@router.delete("/projects/{project_id}/rules/{rule_id}", status_code=204)
def delete_rule(project_id: uuid.UUID, rule_id: uuid.UUID, session: Session = Depends(get_session)):
    rule = session.get(BrandRule, rule_id)
    if rule is None or rule.project_id != project_id:
        raise HTTPException(404, "rule not found")
    session.delete(rule)
    session.commit()
    return Response(status_code=204)


@router.post("/projects/{project_id}/rules", response_model=s.RuleOut, status_code=201)
def add_rule(project_id: uuid.UUID, body: s.RuleIn, session: Session = Depends(get_session)):
    p = _project(session, project_id)
    rule = BrandRule(**body.model_dump())
    p.rules.append(rule)
    session.commit()
    return rule


# ---- Campaigns ----
def get_llm() -> LLMProvider:
    return get_provider()


@router.post("/projects/{project_id}/campaigns", response_model=s.CampaignApi, status_code=201)
def create_campaign(
    project_id: uuid.UUID,
    body: CampaignRequest,
    session: Session = Depends(get_session),
    llm: LLMProvider = Depends(get_llm),
):
    _project(session, project_id)
    try:
        return AIOrchestrator(session, llm).generate_campaign(project_id, body)
    except AIGenerationError as exc:
        raise HTTPException(502, f"AI generation failed: {exc}")


@router.get("/projects/{project_id}/campaigns/{campaign_id}", response_model=s.CampaignApi)
def get_campaign(project_id: uuid.UUID, campaign_id: uuid.UUID, session: Session = Depends(get_session)):
    c = session.get(Campaign, campaign_id)
    if c is None or c.project_id != project_id:
        raise HTTPException(404, "campaign not found")
    return c


def _item(session: Session, item_id: uuid.UUID) -> ContentItem:
    item = session.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(404, "content item not found")
    return item


def _apply(session: Session, fn, item: ContentItem, *args):
    try:
        fn(session, item, *args)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    session.commit()
    return item


@router.post("/content-items/{item_id}/transition", response_model=s.ContentItemApi)
def transition_item(item_id: uuid.UUID, to: ContentStatus, session: Session = Depends(get_session)):
    return _apply(session, lambda ss, it: content_service.move(ss, it, to), _item(session, item_id))


@router.post("/content-items/{item_id}/submit", response_model=s.ContentItemApi)
def submit_item(item_id: uuid.UUID, session: Session = Depends(get_session)):
    return _apply(session, content_service.submit_for_approval, _item(session, item_id))


@router.post("/content-items/{item_id}/approve", response_model=s.ContentItemApi)
def approve_item(item_id: uuid.UUID, session: Session = Depends(get_session)):
    return _apply(session, content_service.approve, _item(session, item_id))


@router.post("/content-items/{item_id}/reject", response_model=s.ContentItemApi)
def reject_item(item_id: uuid.UUID, body: s.RejectIn, session: Session = Depends(get_session)):
    return _apply(session, content_service.reject, _item(session, item_id), body.reason)


@router.patch("/content-items/{item_id}", response_model=s.ContentItemApi)
def patch_item(item_id: uuid.UUID, body: s.ContentPatch, session: Session = Depends(get_session)):
    item = _item(session, item_id)
    if item.status in (ContentStatus.APPROVED.value, ContentStatus.ARCHIVED.value):
        raise HTTPException(409, "approved or archived content cannot be edited")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(item, k, v)
    session.commit()
    return item


@router.get("/content-items/{item_id}/history")
def item_history(item_id: uuid.UUID, session: Session = Depends(get_session)):
    _item(session, item_id)
    return [{"from": h.from_status, "to": h.to_status, "note": h.note, "at": h.at}
            for h in content_service.history(session, item_id)]


@router.get("/projects/{project_id}/campaigns", response_model=list[s.CampaignSummary])
def list_campaigns(project_id: uuid.UUID, session: Session = Depends(get_session)):
    _project(session, project_id)
    return session.scalars(
        select(Campaign).where(Campaign.project_id == project_id).order_by(Campaign.created_at.desc())
    ).all()


@router.post("/projects/{project_id}/campaigns/{campaign_id}/approve-all")
def approve_all(project_id: uuid.UUID, campaign_id: uuid.UUID, session: Session = Depends(get_session)):
    c = session.get(Campaign, campaign_id)
    if c is None or c.project_id != project_id:
        raise HTTPException(404, "campaign not found")
    result = content_service.approve_all(session, c.items)
    session.commit()
    return {"approved": len(result["approved"]), "skipped": result["skipped"]}


@router.get("/projects/{project_id}/calendar", response_model=list[s.CalendarItem])
def calendar(project_id: uuid.UUID, start: date, end: date, session: Session = Depends(get_session)):
    _project(session, project_id)
    rows = session.execute(
        select(ContentItem, Campaign.name).join(Campaign, Campaign.id == ContentItem.campaign_id)
        .where(ContentItem.project_id == project_id, ContentItem.planned_date >= start,
               ContentItem.planned_date <= end, ContentItem.status != ContentStatus.ARCHIVED.value)
        .order_by(ContentItem.planned_date)
    ).all()
    out = []
    for item, cname in rows:
        d = s.CalendarItem.model_validate(item)
        d.campaign_name = cname
        out.append(d)
    return out


# ---- Platform variants ----
PLATFORM_KEYS = ("instagram", "facebook", "tiktok", "linkedin", "x")


def _variant(session: Session, variant_id: uuid.UUID) -> ContentVariant:
    v = session.get(ContentVariant, variant_id)
    if v is None:
        raise HTTPException(404, "variant not found")
    return v


def _has_live_publication(session: Session, item_id: uuid.UUID, platform: str) -> bool:
    return session.scalar(select(Publication.id).where(
        Publication.content_item_id == item_id, Publication.platform == platform,
        Publication.status.in_(("scheduled", "publishing", "awaiting_manual", "published")))) is not None


@router.post("/content-items/{item_id}/variants", response_model=list[s.VariantApi], status_code=201)
def add_variants(item_id: uuid.UUID, body: s.VariantsIn, session: Session = Depends(get_session),
                 llm: LLMProvider = Depends(get_llm)):
    item = _item(session, item_id)
    bad = [p for p in body.platforms if p not in PLATFORM_KEYS]
    if bad:
        raise HTTPException(422, f"unknown platforms: {bad}")
    if body.use_ai:
        try:
            return AIOrchestrator(session, llm).adapt_content(item_id, body.platforms)
        except AIGenerationError as exc:
            raise HTTPException(502, f"AI generation failed: {exc}")
    existing = {v.platform for v in item.variants} | {item.platform}
    created = [ContentVariant(content_item_id=item.id, platform=p, caption=item.caption)
               for p in dict.fromkeys(body.platforms) if p not in existing]
    session.add_all(created)
    session.commit()
    return created


@router.patch("/variants/{variant_id}", response_model=s.VariantApi)
def patch_variant(variant_id: uuid.UUID, body: s.VariantPatch, session: Session = Depends(get_session)):
    v = _variant(session, variant_id)
    if _has_live_publication(session, v.content_item_id, v.platform):
        raise HTTPException(409, "this version is already scheduled or published; cancel its publication first")
    v.caption = body.caption
    session.commit()
    return v


@router.delete("/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: uuid.UUID, session: Session = Depends(get_session)):
    v = _variant(session, variant_id)
    if _has_live_publication(session, v.content_item_id, v.platform):
        raise HTTPException(409, "this version is already scheduled or published; cancel its publication first")
    session.delete(v)
    session.commit()
    return Response(status_code=204)
