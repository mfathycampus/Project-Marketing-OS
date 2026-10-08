"""Social platform adapters. The core knows only this interface, never Meta/TikTok/etc."""
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


class PermanentPublishError(RuntimeError):
    """Retrying will not help (expired token, missing config, rejected content)."""


@dataclass
class PublishPayload:
    platform: str
    headline: str
    caption: str
    cta: str
    image: bytes | None = None
    item_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


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


def get_social_provider(platform: str, session=None, project_id=None) -> SocialProvider:
    """A connected account for this project+platform wins; otherwise the configured default (manual)."""
    if session is not None and project_id is not None and platform in ("facebook", "instagram"):
        from app.social.meta import provider_for

        connected = provider_for(session, project_id, platform)
        if connected is not None:
            return connected
    return _PROVIDERS.get(settings.publish_provider, ManualProvider)()
