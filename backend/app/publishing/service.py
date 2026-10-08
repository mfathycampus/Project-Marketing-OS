import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.content.state_machine import ContentStatus
from app.db import utcnow
from app.models import Campaign, ContentItem, DesignAsset, Project, Publication
from app.social.provider import PermanentPublishError, PublishPayload, get_social_provider
from app.storage.local import LocalStorage

ACTIVE = ("scheduled", "publishing", "awaiting_manual", "published")


class PublishingError(ValueError):
    pass


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_to_utc(day: date, hhmm: str, tz_name: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def platforms_of(item: ContentItem) -> list[str]:
    """The item's own platform first, then the platforms of its variants."""
    return [item.platform, *[v.platform for v in item.variants]]


def caption_for(item: ContentItem, platform: str) -> str:
    return next((v.caption for v in item.variants if v.platform == platform), item.caption)


def schedule(session: Session, item: ContentItem, when: datetime, platform: str | None = None) -> Publication:
    platform = platform or item.platform
    if item.status != ContentStatus.APPROVED.value:
        raise PublishingError("only approved content can be scheduled")
    if platform not in platforms_of(item):
        raise PublishingError(f"this content has no {platform} version; add the platform first")
    exists = session.scalar(select(Publication).where(
        Publication.content_item_id == item.id, Publication.platform == platform,
        Publication.status.in_(ACTIVE)))
    if exists:
        raise PublishingError("this content is already scheduled or published on this platform")
    provider = get_social_provider(platform, session, item.project_id)
    pub = Publication(content_item_id=item.id, project_id=item.project_id, platform=platform,
                      provider=provider.name, status="scheduled", scheduled_at=_aware(when))
    session.add(pub)
    return pub


def schedule_campaign(session: Session, campaign: Campaign, hhmm: str | None = None) -> dict:
    project = session.get(Project, campaign.project_id)
    hhmm = hhmm or settings.publish_default_time
    created, skipped = [], []
    for item in campaign.items:
        if item.status != ContentStatus.APPROVED.value or item.planned_date is None:
            skipped.append({"id": item.id, "reason": "not approved or no date"})
            continue
        for platform in platforms_of(item):
            try:
                created.append(schedule(session, item, local_to_utc(item.planned_date, hhmm, project.timezone), platform))
            except PublishingError as exc:
                skipped.append({"id": item.id, "reason": f"{platform}: {exc}"})
    return {"scheduled": len(created), "skipped": skipped}


def reschedule(pub: Publication, when: datetime) -> Publication:
    if pub.status not in ("scheduled", "failed"):
        raise PublishingError(f"cannot reschedule a {pub.status} publication")
    pub.scheduled_at, pub.status, pub.last_error = _aware(when), "scheduled", None
    return pub


def cancel(pub: Publication) -> Publication:
    if pub.status not in ("scheduled", "awaiting_manual", "failed"):
        raise PublishingError(f"cannot cancel a {pub.status} publication")
    pub.status = "cancelled"
    return pub


def retry(pub: Publication) -> Publication:
    if pub.status != "failed":
        raise PublishingError("only failed publications can be retried")
    pub.status, pub.scheduled_at, pub.attempts, pub.last_error = "scheduled", utcnow(), 0, None
    return pub


def mark_published(pub: Publication, url: str | None = None) -> Publication:
    if pub.status not in ("awaiting_manual", "scheduled", "failed"):
        raise PublishingError(f"cannot mark a {pub.status} publication as published")
    pub.status, pub.published_at, pub.external_url = "published", utcnow(), url or pub.external_url
    return pub


def claim_due(session: Session, now: datetime | None = None) -> uuid.UUID | None:
    """Atomically flip one due 'scheduled' publication to 'publishing'. Safe with several workers."""
    now = now or utcnow()
    pid = session.scalar(select(Publication.id).where(
        Publication.status == "scheduled", Publication.scheduled_at <= now
    ).order_by(Publication.scheduled_at).limit(1))
    if pid is None:
        return None
    claimed = session.execute(update(Publication).where(
        Publication.id == pid, Publication.status == "scheduled").values(status="publishing")).rowcount
    session.commit()
    return pid if claimed else None


def run_publication(session: Session, pub_id: uuid.UUID, storage: LocalStorage | None = None) -> Publication:
    pub = session.get(Publication, pub_id)
    item = session.get(ContentItem, pub.content_item_id)
    storage = storage or LocalStorage()
    asset = session.scalar(select(DesignAsset).where(DesignAsset.content_item_id == item.id)
                           .order_by(DesignAsset.created_at.desc()))
    image = None
    if asset is not None:
        try:
            image = storage.get(asset.storage_key)
        except OSError:
            image = None
    pub.attempts += 1
    try:
        provider = get_social_provider(pub.platform, session, pub.project_id)
        pub.provider = provider.name
        result = provider.publish(PublishPayload(
            platform=pub.platform, headline=item.headline, caption=caption_for(item, pub.platform), cta=item.cta, image=image,
            item_id=item.id, project_id=pub.project_id))
    except Exception as exc:  # noqa: BLE001 - recorded, retried with backoff
        pub.last_error = str(exc)[:1000]
        if isinstance(exc, PermanentPublishError):
            pub.status = "failed"  # retrying cannot help
        elif pub.attempts < settings.publish_max_attempts:
            pub.status, pub.scheduled_at = "scheduled", utcnow() + timedelta(minutes=5 * pub.attempts)
        else:
            pub.status = "failed"
        session.commit()
        return pub
    pub.status, pub.external_id, pub.external_url, pub.last_error = result.status, result.external_id, result.external_url, None
    if result.status == "published":
        pub.published_at = utcnow()
    session.commit()
    return pub


def recover_stuck(session: Session) -> int:
    n = session.execute(update(Publication).where(Publication.status == "publishing").values(status="scheduled")).rowcount
    session.commit()
    return n or 0
