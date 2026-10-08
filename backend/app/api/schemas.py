import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BrandProfileIn(BaseModel):
    name: str
    description: str = ""
    mission: str = ""
    tone: str = ""
    language: str = "ar"
    dialect: str = ""
    primary_color: str = Field(default="#0F766E", pattern="^#[0-9A-Fa-f]{6}$")
    secondary_color: str = Field(default="#F59E0B", pattern="^#[0-9A-Fa-f]{6}$")


class AudienceIn(BaseModel):
    demographics: str = ""
    interests: list[str] = []
    pain_points: list[str] = []
    goals: list[str] = []


class ProductIn(BaseModel):
    name: str
    description: str = ""
    price: str = ""
    features: list[str] = []


class ProductOut(ProductIn, ORM):
    id: uuid.UUID


class RuleIn(BaseModel):
    kind: str = Field(pattern="^(forbidden_word|must_do|must_not)$")
    text: str = Field(max_length=500)


class RuleOut(RuleIn, ORM):
    id: uuid.UUID


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "Asia/Riyadh"


class ProjectPatch(BaseModel):
    name: str | None = None
    timezone: str | None = None


class ProjectOut(ORM):
    id: uuid.UUID
    name: str
    status: str
    timezone: str
    created_at: datetime


class ContentItemApi(ORM):
    id: uuid.UUID
    campaign_id: uuid.UUID
    platform: str
    day_offset: int
    planned_date: date | None = None
    headline: str
    caption: str
    cta: str
    design: dict
    status: str


class CampaignApi(ORM):
    id: uuid.UUID
    name: str
    objective: str
    strategy: str
    duration_days: int
    start_date: date | None = None
    items: list[ContentItemApi]


class CampaignSummary(ORM):
    id: uuid.UUID
    name: str
    objective: str
    start_date: date | None = None
    created_at: datetime


class ContentPatch(BaseModel):
    headline: str | None = Field(default=None, max_length=60)
    caption: str | None = None
    cta: str | None = Field(default=None, max_length=100)
    planned_date: date | None = None


class RejectIn(BaseModel):
    reason: str = ""


class CalendarItem(ContentItemApi):
    campaign_name: str = ""
