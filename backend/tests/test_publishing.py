import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Publication
from app.publishing import service, worker
from app.social import provider as social
from app.storage.local import LocalStorage
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider
from app.designs.service import DesignService

UTC = timezone.utc


@pytest.fixture
def approved(client, fake_llm, session, tmp_path):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload(n=2))
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 2, "start_date": "2026-11-02"}).json()
    storage = LocalStorage(str(tmp_path))
    for it in body["items"]:
        DesignService(session, StubProvider(), storage).generate(uuid.UUID(it["id"]))
    client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/approve-all")
    return pid, body


def test_only_approved_content_can_be_scheduled(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload(n=1))
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1}).json()
    r = client.post(f"/api/v1/content-items/{body['items'][0]['id']}/schedule", json={})
    assert r.status_code == 409


def test_schedule_all_uses_project_timezone(client, approved):
    pid, body = approved
    r = client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/schedule-all", json={"time": "19:00"}).json()
    assert r["scheduled"] == 2
    pubs = client.get(f"/api/v1/projects/{pid}/publications").json()
    # Asia/Riyadh is UTC+3, so 19:00 local = 16:00 UTC
    assert pubs[0]["scheduled_at"].startswith("2026-11-02T16:00:00")
    assert {p["status"] for p in pubs} == {"scheduled"}
    # scheduling twice is refused per item/platform
    again = client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/schedule-all", json={}).json()
    assert again["scheduled"] == 0 and len(again["skipped"]) == 2


def test_manual_provider_queues_for_hand_posting_then_mark_published(client, approved, engine, monkeypatch):
    pid, body = approved
    monkeypatch.setattr(settings, "publish_provider", "manual")
    iid = body["items"][0]["id"]
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"scheduled_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    maker = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", maker)
    assert worker.process_due() == 1
    pub = client.get(f"/api/v1/projects/{pid}/publications").json()[0]
    assert pub["status"] == "awaiting_manual"
    r = client.post(f"/api/v1/publications/{pub['id']}/mark-published", json={"url": "https://x.com/post/1"}).json()
    assert r["status"] == "published" and r["external_url"] == "https://x.com/post/1"
    assert client.post(f"/api/v1/publications/{pub['id']}/cancel").status_code == 409


def test_dryrun_provider_publishes_and_future_items_wait(client, approved, engine, monkeypatch):
    pid, body = approved
    monkeypatch.setattr(settings, "publish_provider", "dryrun")
    now = datetime.now(UTC)
    client.post(f"/api/v1/content-items/{body['items'][0]['id']}/schedule", json={"scheduled_at": (now - timedelta(minutes=1)).isoformat()})
    client.post(f"/api/v1/content-items/{body['items'][1]['id']}/schedule", json={"scheduled_at": (now + timedelta(days=1)).isoformat()})
    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    assert worker.process_due() == 1 and worker.process_due() == 0
    statuses = sorted(p["status"] for p in client.get(f"/api/v1/projects/{pid}/publications").json())
    assert statuses == ["published", "scheduled"]


def test_failure_retries_with_backoff_then_fails(client, approved, engine, monkeypatch):
    pid, body = approved

    class Boom:
        name = "boom"

        def publish(self, payload):
            raise RuntimeError("api down")

    monkeypatch.setattr(service, "get_social_provider", lambda *a, **k: Boom())
    monkeypatch.setattr(settings, "publish_max_attempts", 2)
    client.post(f"/api/v1/content-items/{body['items'][0]['id']}/schedule", json={"scheduled_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    maker = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", maker)
    worker.process_due()
    with maker() as s:
        p = s.query(Publication).one()
        assert p.status == "scheduled" and p.attempts == 1 and p.last_error == "api down"
        assert p.scheduled_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=4)  # backoff
        p.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    worker.process_due()
    pub = client.get(f"/api/v1/projects/{pid}/publications").json()[0]
    assert pub["status"] == "failed"
    assert client.post(f"/api/v1/publications/{pub['id']}/retry").json()["status"] == "scheduled"


def test_claim_is_exclusive_and_crash_recovery(client, approved, engine, monkeypatch):
    pid, body = approved
    client.post(f"/api/v1/content-items/{body['items'][0]['id']}/schedule", json={"scheduled_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s1, maker() as s2:
        first = service.claim_due(s1)
        assert first is not None and service.claim_due(s2) is None  # second worker gets nothing
        assert service.recover_stuck(s2) == 1  # simulated crash: back to scheduled
        assert service.claim_due(s2) == first


def test_cancel_and_reschedule(client, approved):
    pid, body = approved
    iid = body["items"][0]["id"]
    pub = client.post(f"/api/v1/content-items/{iid}/schedule", json={"scheduled_at": "2030-01-01T10:00:00+00:00"}).json()
    r = client.post(f"/api/v1/publications/{pub['id']}/reschedule", json={"scheduled_at": "2030-01-02T10:00:00+00:00"}).json()
    assert r["scheduled_at"].startswith("2030-01-02")
    assert client.post(f"/api/v1/publications/{pub['id']}/cancel").json()["status"] == "cancelled"
    # after cancelling, the item can be scheduled again
    assert client.post(f"/api/v1/content-items/{iid}/schedule", json={"scheduled_at": "2030-01-03T10:00:00+00:00"}).status_code == 201
