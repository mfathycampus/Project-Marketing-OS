import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.orchestrator import AIGenerationError, AIOrchestrator
from app.ai.provider import LLMProvider, get_provider
from app.ai.schemas import CampaignRequest
from app.api import schemas as s
from app.content.state_machine import ContentStatus, InvalidTransition, transition
from app.db import get_session
from app.models import AudienceProfile, BrandProfile, BrandRule, Campaign, ContentItem, Product, Project

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


@router.post("/content-items/{item_id}/transition", response_model=s.ContentItemApi)
def transition_item(item_id: uuid.UUID, to: ContentStatus, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(404, "content item not found")
    try:
        item.status = transition(item.status, to).value
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    session.commit()
    return item
