import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, TimestampMixin


class Publication(Base, IdMixin, TimestampMixin):
    """One content item published to one platform. Content state ends at APPROVED; this owns what comes after."""

    __tablename__ = "publications"

    content_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
