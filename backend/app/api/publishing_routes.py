import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Campaign, ContentItem, Project, Publication
from app.publishing import service

router = APIRouter()


class ScheduleIn(BaseModel):
    scheduled_at: datetime | None = None  # default: planned date at the default local time


class ScheduleAllIn(BaseModel):
    time: str | None = None  # "HH:MM" in the project's timezone


class MarkIn(BaseModel):
    url: str | None = None


def _pub(session: Session, pub_id: uuid.UUID) -> Publication:
    pub = session.get(Publication, pub_id)
    if pub is None:
        raise HTTPException(404, "publication not found")
    return pub


def _out(session: Session, p: Publication) -> dict:
    item = session.get(ContentItem, p.content_item_id)
    campaign = session.get(Campaign, item.campaign_id)
    return {
        "id": p.id, "content_item_id": p.content_item_id, "project_id": p.project_id, "platform": p.platform,
        "provider": p.provider, "status": p.status, "scheduled_at": service._aware(p.scheduled_at),
        "published_at": service._aware(p.published_at) if p.published_at else None,
        "external_url": p.external_url, "attempts": p.attempts, "last_error": p.last_error,
        "headline": item.headline, "caption": item.caption, "cta": item.cta, "campaign_name": campaign.name,
    }


def _guard(fn, *args):
    try:
        return fn(*args)
    except service.PublishingError as exc:
        raise HTTPException(409, str(exc))


@router.post("/content-items/{item_id}/schedule", status_code=201)
def schedule_item(item_id: uuid.UUID, body: ScheduleIn, session: Session = Depends(get_session)):
    item = session.get(ContentItem, item_id)
    if item is None:
        raise HTTPException(404, "content item not found")
    when = body.scheduled_at
    if when is None:
        if item.planned_date is None:
            raise HTTPException(422, "scheduled_at is required when the item has no planned date")
        project = session.get(Project, item.project_id)
        when = service.local_to_utc(item.planned_date, service.settings.publish_default_time, project.timezone)
    pub = _guard(service.schedule, session, item, when)
    session.commit()
    return _out(session, pub)


@router.post("/projects/{project_id}/campaigns/{campaign_id}/schedule-all")
def schedule_campaign(project_id: uuid.UUID, campaign_id: uuid.UUID, body: ScheduleAllIn,
                      session: Session = Depends(get_session)):
    c = session.get(Campaign, campaign_id)
    if c is None or c.project_id != project_id:
        raise HTTPException(404, "campaign not found")
    result = service.schedule_campaign(session, c, body.time)
    session.commit()
    return {"scheduled": result["scheduled"], "skipped": [{"id": str(x["id"]), "reason": x["reason"]} for x in result["skipped"]]}


@router.get("/projects/{project_id}/publications")
def list_publications(project_id: uuid.UUID, status: str | None = None, session: Session = Depends(get_session)):
    q = select(Publication).where(Publication.project_id == project_id).order_by(Publication.scheduled_at)
    if status:
        q = q.where(Publication.status == status)
    return [_out(session, p) for p in session.scalars(q)]


@router.post("/publications/{pub_id}/cancel")
def cancel_publication(pub_id: uuid.UUID, session: Session = Depends(get_session)):
    pub = _guard(service.cancel, _pub(session, pub_id))
    session.commit()
    return _out(session, pub)


@router.post("/publications/{pub_id}/retry")
def retry_publication(pub_id: uuid.UUID, session: Session = Depends(get_session)):
    pub = _guard(service.retry, _pub(session, pub_id))
    session.commit()
    return _out(session, pub)


@router.post("/publications/{pub_id}/reschedule")
def reschedule_publication(pub_id: uuid.UUID, body: ScheduleIn, session: Session = Depends(get_session)):
    if body.scheduled_at is None:
        raise HTTPException(422, "scheduled_at is required")
    pub = _guard(service.reschedule, _pub(session, pub_id), body.scheduled_at)
    session.commit()
    return _out(session, pub)


@router.post("/publications/{pub_id}/mark-published")
def mark_published(pub_id: uuid.UUID, body: MarkIn, session: Session = Depends(get_session)):
    pub = _guard(service.mark_published, _pub(session, pub_id), body.url)
    session.commit()
    return _out(session, pub)
