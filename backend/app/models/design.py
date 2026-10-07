import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, TimestampMixin


class DesignJob(Base, IdMixin, TimestampMixin):
    """Source of truth for design generation; works for sync (html) and async (canva) providers."""

    __tablename__ = "design_jobs"

    content_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|processing|completed|failed
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    external_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DesignAsset(Base, IdMixin, TimestampMixin):
    __tablename__ = "design_assets"

    content_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("content_items.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("design_jobs.id"))
    provider: Mapped[str] = mapped_column(String(30))
    template_key: Mapped[str] = mapped_column(String(50))
    storage_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(50), default="image/png")
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # e.g. Canva edit link


class CanvaConnection(Base, IdMixin, TimestampMixin):
    __tablename__ = "canva_connections"

    owner_id: Mapped[str] = mapped_column(String(100), unique=True, default="default")
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
