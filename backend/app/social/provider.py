"""Social platform adapters. The core knows only this interface, never Meta/TikTok/etc."""
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


@dataclass
class PublishPayload:
    platform: str
    headline: str
    caption: str
    cta: str
    image: bytes | None = None


@dataclass
class PublishResult:
    status: str  # "published" | "awaiting_manual"
    external_id: str | None = None
    external_url: str | None = None


class SocialProvider(Protocol):
    name: str

    def publish(self, payload: PublishPayload) -> PublishResult: ...


class ManualProvider:
    """No API access: at the scheduled time the post is queued for a person to publish by hand."""

    name = "manual"

    def publish(self, payload: PublishPayload) -> PublishResult:
        return PublishResult(status="awaiting_manual")


class DryRunProvider:
    """Pretends to publish. For demos and tests."""

    name = "dryrun"

    def publish(self, payload: PublishPayload) -> PublishResult:
        return PublishResult(status="published", external_id=f"dryrun-{uuid.uuid4().hex[:8]}")


_PROVIDERS = {"manual": ManualProvider, "dryrun": DryRunProvider}


def get_social_provider(platform: str) -> SocialProvider:
    # Real adapters (Meta, TikTok, LinkedIn, X) register here keyed by platform once their apps are approved.
    return _PROVIDERS.get(settings.publish_provider, ManualProvider)()
