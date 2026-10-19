"""Results of published content + the lessons Claude draws from them (the learning loop)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.provider import LLMProvider
from app.api.routes import get_llm
from app.db import get_session
from app.insights import lessons, metrics
from app.models import Project, Publication

router = APIRouter()


class MetricsIn(BaseModel):
    views: int | None = Field(default=None, ge=0)
    reach: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    avg_watch_s: float | None = Field(default=None, ge=0)
    completion: float | None = Field(default=None, ge=0, le=1)


def _project(session: Session, pid: uuid.UUID) -> Project:
    p = session.get(Project, pid)
    if p is None:
        raise HTTPException(404, "project not found")
    return p


@router.get("/projects/{project_id}/results")
def results(project_id: uuid.UUID, session: Session = Depends(get_session)):
    _project(session, project_id)
    return {"results": metrics.project_results(session, project_id),
            "lessons": lessons.out(lessons.latest(session, project_id))}


@router.post("/projects/{project_id}/results/refresh")
def refresh(project_id: uuid.UUID, session: Session = Depends(get_session)):
    _project(session, project_id)
    return metrics.refresh(session, project_id)


@router.put("/publications/{pub_id}/metrics")
def set_metrics(pub_id: uuid.UUID, body: MetricsIn, session: Session = Depends(get_session)):
    pub = session.get(Publication, pub_id)
    if pub is None:
        raise HTTPException(404, "publication not found")
    try:
        return metrics.as_dict(metrics.save_manual(session, pub, body.model_dump(exclude_unset=True)))
    except metrics.MetricsError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/projects/{project_id}/lessons")
def make_lessons(project_id: uuid.UUID, session: Session = Depends(get_session), llm: LLMProvider = Depends(get_llm)):
    _project(session, project_id)
    try:
        return metrics.project_lessons(session, llm, project_id)
    except lessons.LessonsError as exc:
        raise HTTPException(422, str(exc)) from exc
