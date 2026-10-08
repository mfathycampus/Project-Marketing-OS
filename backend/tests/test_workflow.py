import uuid
from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

from app.designs import worker
from app.designs.service import DesignService
from app.models import ContentItem, DesignJob
from app.storage.local import LocalStorage
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import PNG, StubProvider


@pytest.fixture
def campaign(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload(n=3))
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 3, "start_date": "2026-11-02"}).json()
    return pid, body


def ready(session, storage, item_id):
    DesignService(session, StubProvider(), storage).generate(item_id)


def test_planned_dates_come_from_start_date_and_offset(campaign):
    _, body = campaign
    assert body["start_date"] == "2026-11-02"
    assert [i["planned_date"] for i in body["items"]] == ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_approval_flow_and_history(client, campaign, session, tmp_path):
    _, body = campaign
    item_id = uuid.UUID(body["items"][0]["id"])
    iid = str(item_id)
    # cannot approve before a design exists
    assert client.post(f"/api/v1/content-items/{iid}/approve").status_code == 409
    ready(session, LocalStorage(str(tmp_path)), item_id)
    assert client.post(f"/api/v1/content-items/{iid}/submit").json()["status"] == "pending_approval"
    assert client.post(f"/api/v1/content-items/{iid}/approve").json()["status"] == "approved"
    # approved content is locked
    assert client.patch(f"/api/v1/content-items/{iid}", json={"headline": "x"}).status_code == 409
    steps = [(h["from"], h["to"]) for h in client.get(f"/api/v1/content-items/{iid}/history").json()]
    assert steps[-2:] == [("design_ready", "pending_approval"), ("pending_approval", "approved")]


def test_reject_returns_to_draft(client, campaign, session, tmp_path):
    _, body = campaign
    item_id = uuid.UUID(body["items"][0]["id"])
    ready(session, LocalStorage(str(tmp_path)), item_id)
    client.post(f"/api/v1/content-items/{item_id}/submit")
    r = client.post(f"/api/v1/content-items/{item_id}/reject", json={"reason": "السعر خاطئ"})
    assert r.json()["status"] == "rejected"
    assert client.post(f"/api/v1/content-items/{item_id}/transition", params={"to": "draft"}).json()["status"] == "draft"


def test_patch_content_item(client, campaign):
    _, body = campaign
    iid = body["items"][1]["id"]
    r = client.patch(f"/api/v1/content-items/{iid}", json={"headline": "عنوان جديد", "planned_date": "2026-11-10"})
    assert r.json()["headline"] == "عنوان جديد" and r.json()["planned_date"] == "2026-11-10"
    assert client.patch(f"/api/v1/content-items/{iid}", json={"headline": "x" * 80}).status_code == 422


def test_approve_all_skips_items_without_design(client, campaign, session, tmp_path):
    pid, body = campaign
    storage = LocalStorage(str(tmp_path))
    ready(session, storage, uuid.UUID(body["items"][0]["id"]))
    ready(session, storage, uuid.UUID(body["items"][1]["id"]))
    r = client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/approve-all").json()
    assert r["approved"] == 2 and len(r["skipped"]) == 1 and r["skipped"][0]["status"] == "ai_generated"


def test_calendar_range_filter(client, campaign):
    pid, body = campaign
    r = client.get(f"/api/v1/projects/{pid}/calendar", params={"start": "2026-11-01", "end": "2026-11-03"}).json()
    assert [i["planned_date"] for i in r] == ["2026-11-02", "2026-11-03"]
    assert r[0]["campaign_name"] == body["name"]
    assert client.get(f"/api/v1/projects/{pid}/calendar", params={"start": "2026-12-01", "end": "2026-12-31"}).json() == []


def test_queue_designs_and_worker_drains_queue(client, campaign, engine, tmp_path, monkeypatch):
    from app.api.design_routes import get_designer
    from app.config import settings
    from app.main import app

    pid, body = campaign
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    app.dependency_overrides[get_designer] = lambda: StubProvider()
    r = client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/designs")
    assert r.status_code == 202 and r.json()["queued"] == 3
    states = {i["status"] for i in client.get(f"/api/v1/projects/{pid}/campaigns/{body['id']}").json()["items"]}
    assert states == {"design_pending"}
    # queuing twice does not duplicate jobs
    client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/designs")

    maker = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", maker)
    monkeypatch.setattr(worker, "get_design_provider", lambda s: StubProvider())
    while worker.process_one():
        pass
    items = client.get(f"/api/v1/projects/{pid}/campaigns/{body['id']}").json()["items"]
    assert {i["status"] for i in items} == {"design_ready"}
    with maker() as s:
        assert s.query(DesignJob).count() == 3


def test_worker_recovers_stuck_jobs(client, campaign, engine, tmp_path, monkeypatch):
    from app.models import DesignJob

    _, body = campaign
    maker = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", maker)
    with maker() as s:
        s.add(DesignJob(content_item_id=uuid.UUID(body["items"][0]["id"]), provider="stub",
                        idempotency_key="k", status="processing"))
        s.commit()
    assert worker.recover_stuck_jobs() == 1
    with maker() as s:
        assert s.query(DesignJob).one().status == "pending"


def test_failed_job_returns_item_to_ai_generated(client, campaign, engine, monkeypatch):
    pid, body = campaign
    maker = sessionmaker(engine, expire_on_commit=False)
    iid = uuid.UUID(body["items"][0]["id"])
    with maker() as s:
        DesignService(s, StubProvider(fail=True)).enqueue(iid)
    monkeypatch.setattr(worker, "SessionLocal", maker)
    monkeypatch.setattr(worker, "get_design_provider", lambda s: StubProvider(fail=True))
    assert worker.process_one() is True
    with maker() as s:
        assert s.get(ContentItem, iid).status == "ai_generated"
        assert s.query(DesignJob).one().status == "failed"


def test_brand_brain_read_and_delete(client):
    pid = make_project(client)
    bb = client.get(f"/api/v1/projects/{pid}/brand-brain").json()
    assert bb["brand"]["name"] == "المذاق" and len(bb["products"]) == 1 and bb["rules"][0]["text"] == "مجاني"
    assert client.delete(f"/api/v1/projects/{pid}/rules/{bb['rules'][0]['id']}").status_code == 204
    assert client.delete(f"/api/v1/projects/{pid}/products/{bb['products'][0]['id']}").status_code == 204
    bb = client.get(f"/api/v1/projects/{pid}/brand-brain").json()
    assert bb["rules"] == [] and bb["products"] == []
