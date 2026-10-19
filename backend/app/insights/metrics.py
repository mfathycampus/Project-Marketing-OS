"""Results of published posts: fetched from Meta (Facebook / Instagram) or typed in by hand."""
import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.insights import lessons
from app.models import ContentItem, PostMetric, Publication, SocialConnection

log = logging.getLogger(__name__)
FIELDS = ("views", "reach", "likes", "comments", "shares", "saves", "clicks", "avg_watch_s", "completion")
FETCH_DAYS = 45  # Meta keeps changing numbers for a few weeks; older posts are left as they are


class MetricsError(RuntimeError):
    pass


def _row(session: Session, pub: Publication) -> PostMetric:
    row = session.scalar(select(PostMetric).where(PostMetric.publication_id == pub.id))
    if row is None:
        row = PostMetric(publication_id=pub.id, project_id=pub.project_id)
        session.add(row)
    return row


def as_dict(m: PostMetric | None) -> dict | None:
    if m is None:
        return None
    d = {k: getattr(m, k) for k in FIELDS}
    return d | {"source": m.source, "note": m.note, "fetched_at": m.fetched_at.isoformat() if m.fetched_at else None,
                "score": lessons.score(d)}


def save_manual(session: Session, pub: Publication, values: dict) -> PostMetric:
    if pub.status != "published":
        raise MetricsError("سجّل النتائج بعد نشر المنشور")
    row = _row(session, pub)
    for k in FIELDS:
        if k in values:
            v = values[k]
            setattr(row, k, None if v is None or v == "" else (float(v) if k in ("avg_watch_s", "completion") else int(v)))
    row.source, row.note, row.fetched_at = "manual", "", utcnow()
    session.commit()
    return row


def fetch_meta(session: Session, pub: Publication, client=None) -> PostMetric | None:
    """Pull the numbers of one Facebook / Instagram publication. None when it isn't a Meta post we can read."""
    from app.designs.canva.crypto import decrypt
    from app.social.meta import MetaClient, MetaError

    if pub.status != "published" or not pub.external_id or pub.platform not in ("facebook", "instagram"):
        return None
    conn = session.scalar(select(SocialConnection).where(
        SocialConnection.project_id == pub.project_id, SocialConnection.platform == pub.platform))
    if conn is None or conn.status == "expired":
        return None
    client = client or MetaClient()
    token = decrypt(conn.token_enc)
    item = session.get(ContentItem, pub.content_item_id)
    video = bool(item and item.media_type == "video")
    try:
        if pub.platform == "instagram":
            raw = client.ig_media_stats(pub.external_id, token)
        elif video:
            raw = client.fb_video_stats(pub.external_id, token)
        else:
            raw = client.fb_post_stats(pub.external_id, token)
    except (MetaError, httpx.HTTPError) as exc:
        log.warning("metrics for %s failed: %s", pub.id, exc)
        row = _row(session, pub)
        if row.source != "manual":  # never overwrite numbers the user typed in
            row.source, row.note, row.fetched_at = "meta", f"تعذّر جلب النتائج: {exc}"[:300], utcnow()
        session.commit()
        return row
    row = _row(session, pub)
    for k in ("views", "reach", "likes", "comments", "shares", "saves", "clicks"):
        if isinstance(raw.get(k), (int, float)):
            setattr(row, k, int(raw[k]))
    if isinstance(raw.get("avg_watch_ms"), (int, float)):
        row.avg_watch_s = round(raw["avg_watch_ms"] / 1000, 2)
    if raw.get("complete_views") and row.views:
        row.completion = round(min(1.0, raw["complete_views"] / row.views), 3)
    missing = [k for k in ("reach", "views") if getattr(row, k) is None]
    row.source, row.fetched_at = "meta", utcnow()
    row.note = ("بعض الأرقام غير متاحة: أعد ربط الحساب مع صلاحيات النتائج (instagram_manage_insights, read_insights)"
                if len(missing) == 2 else "")
    session.commit()
    return row


def refresh(session: Session, project_id: uuid.UUID | None = None, client=None) -> dict:
    """Fetch the latest numbers of recent Meta publications (one project, or all)."""
    since = utcnow() - timedelta(days=FETCH_DAYS)
    q = select(Publication).where(Publication.status == "published", Publication.published_at >= since,
                                  Publication.platform.in_(("facebook", "instagram")))
    if project_id:
        q = q.where(Publication.project_id == project_id)
    done = failed = 0
    for pub in session.scalars(q).all():
        try:
            row = fetch_meta(session, pub, client)
        except Exception:  # noqa: BLE001 - one bad post must not stop the rest
            log.exception("metrics refresh failed for %s", pub.id)
            session.rollback()
            failed += 1
            continue
        if row is not None:
            done += 1
            failed += 1 if row.note.startswith("تعذّر") else 0
    return {"updated": done, "failed": failed}


def project_results(session: Session, project_id: uuid.UUID) -> list[dict]:
    pubs = session.scalars(select(Publication).where(Publication.project_id == project_id,
                                                     Publication.status == "published")
                           .order_by(Publication.published_at.desc())).all()
    metrics = {m.publication_id: m for m in session.scalars(
        select(PostMetric).where(PostMetric.project_id == project_id)).all()}
    out = []
    for p in pubs:
        item = session.get(ContentItem, p.content_item_id)
        out.append({"publication_id": str(p.id), "platform": p.platform, "url": p.external_url,
                    "published_at": p.published_at.isoformat() if p.published_at else None,
                    "headline": item.headline if item else "", "media_type": item.media_type if item else "image",
                    "campaign_id": str(item.campaign_id) if item else None, "metrics": as_dict(metrics.get(p.id))})
    return out


def project_lessons(session: Session, llm, project_id: uuid.UUID) -> dict:
    rows = []
    for r in project_results(session, project_id):
        m = r["metrics"]
        if not m or not any(m.get(k) for k in ("views", "reach", "likes")):
            continue
        pub = session.get(Publication, uuid.UUID(r["publication_id"]))
        it = session.get(ContentItem, pub.content_item_id)
        rows.append({"platform": r["platform"], "type": r["media_type"], "headline": r["headline"],
                     "caption": (it.caption if it else "")[:300], "cta": it.cta if it else "",
                     "published": r["published_at"], **{k: m[k] for k in (*FIELDS, "score") if m.get(k) is not None}})
    data = lessons.analyse(llm, rows)
    return lessons.out(lessons.save(session, project_id, data, len(rows)))
