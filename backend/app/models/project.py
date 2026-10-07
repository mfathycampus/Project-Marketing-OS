import uuid

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, TimestampMixin


class Project(Base, IdMixin, TimestampMixin):
    __tablename__ = "projects"

    owner_id: Mapped[str] = mapped_column(String(100), index=True, default="default")
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Riyadh")

    brand_profile: Mapped["BrandProfile | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    audience_profile: Mapped["AudienceProfile | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    rules: Mapped[list["BrandRule"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class BrandProfile(Base, IdMixin, TimestampMixin):
    __tablename__ = "brand_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    mission: Mapped[str] = mapped_column(Text, default="")
    tone: Mapped[str] = mapped_column(String(200), default="")
    language: Mapped[str] = mapped_column(String(10), default="ar")
    dialect: Mapped[str] = mapped_column(String(50), default="")  # e.g. khaleeji, msa
    primary_color: Mapped[str] = mapped_column(String(9), default="#0F766E")
    secondary_color: Mapped[str] = mapped_column(String(9), default="#F59E0B")

    project: Mapped[Project] = relationship(back_populates="brand_profile")


class AudienceProfile(Base, IdMixin, TimestampMixin):
    __tablename__ = "audience_profiles"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), unique=True)
    demographics: Mapped[str] = mapped_column(Text, default="")
    interests: Mapped[list] = mapped_column(JSON, default=list)
    pain_points: Mapped[list] = mapped_column(JSON, default=list)
    goals: Mapped[list] = mapped_column(JSON, default=list)

    project: Mapped[Project] = relationship(back_populates="audience_profile")


class Product(Base, IdMixin, TimestampMixin):
    __tablename__ = "products"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[str] = mapped_column(String(50), default="")
    features: Mapped[list] = mapped_column(JSON, default=list)

    project: Mapped[Project] = relationship(back_populates="products")


class BrandRule(Base, IdMixin, TimestampMixin):
    __tablename__ = "brand_rules"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # forbidden_word | must_do | must_not
    text: Mapped[str] = mapped_column(String(500))

    project: Mapped[Project] = relationship(back_populates="rules")
