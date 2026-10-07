"""Content lifecycle.

Content state ends at APPROVED. Per-platform publishing state (scheduled, published,
failed) belongs to the publication model so one content item can be published on
Instagram and failed on LinkedIn independently.
"""
from enum import Enum


class ContentStatus(str, Enum):
    DRAFT = "draft"
    AI_GENERATED = "ai_generated"
    DESIGN_PENDING = "design_pending"
    DESIGN_READY = "design_ready"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


S = ContentStatus

TRANSITIONS: dict[ContentStatus, frozenset[ContentStatus]] = {
    S.DRAFT: frozenset({S.AI_GENERATED, S.PENDING_APPROVAL, S.ARCHIVED}),
    S.AI_GENERATED: frozenset({S.DESIGN_PENDING, S.PENDING_APPROVAL, S.DRAFT, S.ARCHIVED}),
    S.DESIGN_PENDING: frozenset({S.DESIGN_READY, S.AI_GENERATED}),  # back on design failure
    S.DESIGN_READY: frozenset({S.PENDING_APPROVAL, S.DESIGN_PENDING, S.ARCHIVED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED}),
    S.REJECTED: frozenset({S.DRAFT, S.ARCHIVED}),
    S.APPROVED: frozenset({S.ARCHIVED, S.DRAFT}),
    S.ARCHIVED: frozenset(),
}


class InvalidTransition(ValueError):
    pass


def can_transition(src: ContentStatus, dst: ContentStatus) -> bool:
    return dst in TRANSITIONS[src]


def transition(src: ContentStatus | str, dst: ContentStatus | str) -> ContentStatus:
    src, dst = ContentStatus(src), ContentStatus(dst)
    if not can_transition(src, dst):
        raise InvalidTransition(f"{src.value} -> {dst.value} is not allowed")
    return dst
