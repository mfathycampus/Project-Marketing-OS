import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.content.state_machine import ContentStatus
from app.db import Base, IdMixin, TimestampMixin, utcnow


class Campaign(Base, IdMixin, TimestampMixin):
    __tablename__ = "campaigns"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(String(100))
    brief: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[str] = mapped_column(Text, default="")
    duration_days: Mapped[int] = mapped_column(default=7)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_runs.id"), nullable=True)

    items: Mapped[list["ContentItem"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="ContentItem.day_offset"
    )


class ContentItem(Base, IdMixin, TimestampMixin):
    """Platform-neutral content. Per-platform publication lives elsewhere (Sprint 3+)."""

    __tablename__ = "content_items"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), index=True)
    type: Mapped[str] = mapped_column(String(30), default="social_post")
    platform: Mapped[str] = mapped_column(String(30))
    day_offset: Mapped[int] = mapped_column(default=0)
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    headline: Mapped[str] = mapped_column(String(300), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    cta: Mapped[str] = mapped_column(String(200), default="")
    design: Mapped[dict] = mapped_column(JSON, default=dict)  # internal design schema
    status: Mapped[str] = mapped_column(String(30), default=ContentStatus.AI_GENERATED.value)

    campaign: Mapped[Campaign] = relationship(back_populates="items")


class ContentStatusHistory(Base, IdMixin):
    __tablename__ = "content_status_history"

    content_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), index=True)
    from_status: Mapped[str] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(String(300), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
