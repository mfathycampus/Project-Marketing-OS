from typing import Literal
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
    logo_media_id: str | None = Field(default=None, max_length=40)  # image from the media library
    video_style: Literal["modern", "calm", "energetic", "corporate"] = "modern"
    website: str = Field(default="", max_length=120)
    offer: str = Field(default="", max_length=120)


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


class VariantApi(ORM):
    id: uuid.UUID
    platform: str
    caption: str


class VariantsIn(BaseModel):
    platforms: list[str] = Field(min_length=1)
    use_ai: bool = True  # False: copy the original caption unchanged


class VariantPatch(BaseModel):
    caption: str


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
    media_type: str = "image"
    has_video: bool = False
    status: str
    variants: list[VariantApi] = []


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
    media_type: Literal["image", "video"] | None = None
    video_scenes: list[str] | None = Field(default=None, max_length=6)  # on-screen lines (stored in design)
    video_broll: list[str] | None = Field(default=None, max_length=6)  # English Pexels search terms per scene
    video_media: list[str] | None = Field(default=None, max_length=6)  # media-library id per scene ('' = none)
    video_stats: list[str] | None = Field(default=None, max_length=6)  # "value|label" per scene ('' = none)
    video_compare: list[str] | None = Field(default=None, max_length=6)  # "before|after" per scene ('' = none)


class RejectIn(BaseModel):
    reason: str = ""


class CalendarItem(ContentItemApi):
    campaign_name: str = ""
