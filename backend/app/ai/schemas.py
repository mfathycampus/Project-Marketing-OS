"""Structured output contract between Claude and the rest of the system."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Platform = Literal["instagram", "facebook", "tiktok", "linkedin", "x"]

CAPTION_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "facebook": 5000,
    "tiktok": 2200,
    "linkedin": 3000,
    "x": 280,
}
HEADLINE_MAX = 60  # must fit design templates


class DesignFields(BaseModel):
    """Explicit (closed) fields: structured outputs require closed objects, not free-form dicts."""

    price: str = Field(default="", description="Price text exactly as listed in products, or empty")
    description: str = Field(default="", description="Short line for the design, max 140 chars, or empty")


class DesignSpec(BaseModel):
    """Internal design schema. Template mappers translate this to Canva/HTML fields."""

    template_key: str = Field(description="One of the allowed template keys")
    fields: DesignFields = Field(default_factory=DesignFields)
    video_scenes: list[str] = Field(
        default_factory=list,
        description="Only when media_type is video: 3-5 short on-screen lines (max 70 chars each) telling the "
                    "story in order, starting with a strong hook. Do not repeat the cta; it is added as the last scene.")
    video_broll: list[str] = Field(
        default_factory=list,
        description="Only when media_type is video: one ENGLISH stock-footage search term (2-4 concrete visual words, "
                    "e.g. 'woman typing laptop office') per video scene, same order as video_scenes.")
    video_media: list[str] = Field(
        default_factory=list,
        description="Only when media_type is video and <media_library> is given: per video scene the id of the "
                    "library item (app screenshot/recording) that shows what the scene says, or an empty string.")
    video_stats: list[str] = Field(
        default_factory=list,
        description="Only when media_type is video: per video scene an animated statistic 'value|label' "
                    "(e.g. '5|ساعات توفرها أسبوعيًا') using ONLY numbers present in the context, or an empty string.")

    @field_validator("fields", mode="before")
    @classmethod
    def _lowercase_keys(cls, v):
        return {str(k).lower(): val for k, val in v.items()} if isinstance(v, dict) else v


class ContentItemOut(BaseModel):
    type: Literal["social_post"] = "social_post"
    platform: Platform
    day_offset: int = Field(ge=0, description="Days after campaign start, 0-based")
    headline: str = Field(max_length=HEADLINE_MAX)
    caption: str
    cta: str = Field(max_length=100)
    media_type: Literal["image", "video"] = Field(
        default="image", description="video = short vertical reel (Reels/TikTok/Shorts); image = single designed post")
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


VIDEO_SCENE_MAX = 70
VIDEO_SCENES_MAX = 6


class CampaignRequest(BaseModel):
    objective: str = Field(examples=["increase_orders"])
    duration_days: int = Field(default=7, ge=1, le=60)
    post_count: int = Field(default=5, ge=1, le=30)
    platforms: list[Platform] = Field(min_length=1)
    tone: str | None = None
    start_date: date | None = None  # defaults to today
    brief: str = ""  # free-text from the user, e.g. "weekend offer targeting families in Riyadh"
    video_count: int = Field(default=0, ge=0, le=30, description="How many of the posts should be short videos")


class AdaptedCaption(BaseModel):
    platform: Platform
    caption: str

    @model_validator(mode="after")
    def _fits(self) -> "AdaptedCaption":
        if len(self.caption) > CAPTION_LIMITS[self.platform]:
            raise ValueError(f"caption for {self.platform} exceeds {CAPTION_LIMITS[self.platform]} chars")
        return self


class AdaptOut(BaseModel):
    variants: list[AdaptedCaption] = Field(min_length=1)
