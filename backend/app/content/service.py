"""Content workflow operations. Every status change goes through `move` so it is validated and logged."""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.state_machine import ContentStatus as S, transition
from app.models import ContentItem, ContentStatusHistory


def move(session: Session, item: ContentItem, dst: S, note: str = "") -> ContentItem:
    """Validate and apply a transition, recording it in history. Caller commits."""
    src = item.status
    item.status = transition(src, dst).value
    session.add(ContentStatusHistory(content_item_id=item.id, from_status=src, to_status=item.status, note=note))
    return item


def submit_for_approval(session: Session, item: ContentItem) -> ContentItem:
    return move(session, item, S.PENDING_APPROVAL, "submitted")


def approve(session: Session, item: ContentItem) -> ContentItem:
    return move(session, item, S.APPROVED, "approved")


def reject(session: Session, item: ContentItem, reason: str = "") -> ContentItem:
    return move(session, item, S.REJECTED, reason or "rejected")


def approve_all(session: Session, items: list[ContentItem]) -> dict:
    """Push every design-ready/pending item to APPROVED. Items in other states are reported, not failed."""
    approved, skipped = [], []
    for item in items:
        status = S(item.status)
        if status is S.APPROVED:
            continue
        if status is S.DESIGN_READY:
            submit_for_approval(session, item)
            status = S.PENDING_APPROVAL
        if status is S.PENDING_APPROVAL:
            approve(session, item)
            approved.append(item.id)
        else:
            skipped.append({"id": item.id, "status": status.value})
    return {"approved": approved, "skipped": skipped}


def history(session: Session, item_id: uuid.UUID) -> list[ContentStatusHistory]:
    return list(session.scalars(
        select(ContentStatusHistory).where(ContentStatusHistory.content_item_id == item_id)
        .order_by(ContentStatusHistory.at)
    ))
