from datetime import UTC, datetime, timedelta

import pytest

from searcharis import models as models_module
from searcharis.storage.memory import InMemoryStateStore


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _claim_kwargs() -> dict[str, object]:
    return {
        "operation": "open",
        "incident_id": "i" * 64,
        "marker": "<!-- searcharis-action:test-key -->",
        "lease_seconds": 120,
    }


@pytest.mark.asyncio
async def test_first_action_claim_is_acquired_and_second_is_leased():
    action_claim_type = getattr(models_module, "ActionClaim", None)
    assert action_claim_type is not None, "ActionClaim model is missing"

    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)

    first = await store.claim_action("key-1", **_claim_kwargs())
    second = await store.claim_action("key-1", **_claim_kwargs())

    assert first == action_claim_type(acquired=True, stale_takeover=False, completed=False)
    assert second.acquired is False
    assert second.stale_takeover is False
    assert second.completed is False


@pytest.mark.asyncio
async def test_expired_action_claim_can_be_taken_over():
    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)

    await store.claim_action("key-1", **_claim_kwargs())
    clock.advance(121)
    takeover = await store.claim_action("key-1", **_claim_kwargs())

    assert takeover.acquired is True
    assert takeover.stale_takeover is True
    assert takeover.completed is False


@pytest.mark.asyncio
async def test_completed_action_claim_returns_saved_result_and_never_reopens():
    clock = MutableClock(datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    store = InMemoryStateStore(clock=clock)

    await store.claim_action("key-1", **_claim_kwargs())
    await store.complete_action("key-1", {"issue_number": 42})
    clock.advance(3600)

    existing = await store.claim_action("key-1", **_claim_kwargs())

    assert existing.acquired is False
    assert existing.completed is True
    assert existing.result == {"issue_number": 42}
