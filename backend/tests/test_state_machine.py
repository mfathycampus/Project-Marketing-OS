import pytest

from app.content.state_machine import ContentStatus as S, InvalidTransition, transition


def test_happy_path():
    st = S.AI_GENERATED
    for nxt in (S.DESIGN_PENDING, S.DESIGN_READY, S.PENDING_APPROVAL, S.APPROVED):
        st = transition(st, nxt)
    assert st is S.APPROVED


def test_rejected_returns_to_draft():
    assert transition(S.REJECTED, S.DRAFT) is S.DRAFT


@pytest.mark.parametrize("src,dst", [
    (S.APPROVED, S.PENDING_APPROVAL), (S.ARCHIVED, S.DRAFT), (S.DRAFT, S.APPROVED),
])
def test_invalid(src, dst):
    with pytest.raises(InvalidTransition):
        transition(src, dst)
