"""Structured output contract between Claude and the rest of the system."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Platform = Literal["instagram", "facebook", "tiktok", "linkedin", "x"]

CAPTION_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "facebook": 5000,
    "tiktok": 2200,
    "linkedin": 3000,
    "x": 280,
}
HEADLINE_MAX = 60  # must fit design templates


class DesignSpec(BaseModel):
    """Internal design schema. Template mappers translate this to Canva/HTML fields."""

    template_key: str = Field(description="One of the allowed template keys")
    fields: dict[str, str] = Field(default_factory=dict)


class ContentItemOut(BaseModel):
    type: Literal["social_post"] = "social_post"
    platform: Platform
    day_offset: int = Field(ge=0, description="Days after campaign start, 0-based")
    headline: str = Field(max_length=HEADLINE_MAX)
    caption: str
    cta: str = Field(max_length=100)
    design: DesignSpec

    @model_validator(mode="after")
    def _caption_fits_platform(self) -> "ContentItemOut":
        limit = CAPTION_LIMITS[self.platform]
        if len(self.caption) > limit:
            raise ValueError(f"caption for {self.platform} exceeds {limit} chars")
        return self


class CampaignOut(BaseModel):
    name: str
    objective: str
    strategy: str = Field(description="Short strategy summary, 2-5 sentences")
    content_items: list[ContentItemOut] = Field(min_length=1)


class CampaignRequest(BaseModel):
    objective: str = Field(examples=["increase_orders"])
    duration_days: int = Field(default=7, ge=1, le=60)
    post_count: int = Field(default=5, ge=1, le=30)
    platforms: list[Platform] = Field(min_length=1)
    tone: str | None = None
    start_date: date | None = None  # defaults to today
    brief: str = ""  # free-text from the user, e.g. "weekend offer targeting families in Riyadh"
