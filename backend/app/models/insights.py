import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, TimestampMixin, utcnow


class PostMetric(Base, IdMixin, TimestampMixin):
    """Latest numbers of one published post: fetched from Meta, or typed in by hand for other platforms."""

    __tablename__ = "post_metrics"

    publication_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("publications.id"), unique=True, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # meta | manual
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_watch_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1 share of viewers who watched to the end
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(String(300), default="")  # e.g. why some numbers are missing


class Lesson(Base, IdMixin, TimestampMixin):
    """What Claude learned from the results; the latest one is fed back into new campaigns / videos.
    project_id NULL = the video studio's lessons."""

    __tablename__ = "lessons"

    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # {summary, do, avoid, winning_hooks}
    based_on: Mapped[int] = mapped_column(Integer, default=0)  # how many posts / videos it was drawn from
    ai_note: Mapped[str] = mapped_column(Text, default="")
