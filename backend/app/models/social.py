import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, IdMixin, TimestampMixin


class SocialConnection(Base, IdMixin, TimestampMixin):
    """A connected social account (Facebook Page or Instagram professional account) for a project."""

    __tablename__ = "social_connections"
    __table_args__ = (UniqueConstraint("project_id", "platform", name="uq_social_project_platform"),)

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))  # facebook | instagram
    account_id: Mapped[str] = mapped_column(String(100))
    account_name: Mapped[str] = mapped_column(String(200), default="")
    token_enc: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | expired


class MetaPending(Base, IdMixin):
    """Pages returned right after login, waiting for the user to pick one. Tokens stay encrypted."""

    __tablename__ = "meta_pending"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    payload_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
