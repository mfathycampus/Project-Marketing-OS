import io
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from PIL import Image
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.designs.canva.crypto import encrypt
from app.models import ContentVariant, SocialConnection
from app.publishing import worker
from app.social import meta
from app.social.provider import PermanentPublishError, PublishPayload
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_publishing import approved  # noqa: F401

UTC = timezone.utc


def adapt_payload(*pairs):
    return {"variants": [{"platform": p, "caption": c} for p, c in pairs]}


@pytest.fixture
def item(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload(n=1))
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1}).json()
    return pid, body["items"][0]


def test_ai_adaptation_creates_variants_and_logs_run(client, fake_llm, item, session):
    from app.models import AIRun

    _, it = item
    fake_llm.responses.append(adapt_payload(("facebook", "نسخة فيسبوك"), ("linkedin", "نسخة لينكدإن")))
    r = client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["facebook", "linkedin"]})
    assert r.status_code == 201 and {v["platform"] for v in r.json()} == {"facebook", "linkedin"}
    sent = fake_llm.calls[-1]
    assert "facebook, linkedin" in sent.user_message and "تعالوا مع العائلة" in sent.user_message  # source caption
    assert session.query(AIRun).filter_by(operation="adapt_content", status="ok").count() == 1
    again = client.get(f"/api/v1/projects/{item[0]}/campaigns/{it['campaign_id']}").json()["items"][0]
    assert [v["platform"] for v in again["variants"]] == ["facebook", "linkedin"]


def test_own_platform_and_existing_variants_are_skipped(client, fake_llm, item):
    _, it = item
    fake_llm.responses.append(adapt_payload(("facebook", "ف")))
    client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["facebook"]})
    n_calls = len(fake_llm.calls)
    r = client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["instagram", "facebook"]})  # item itself is instagram
    assert r.status_code == 201 and r.json() == [] and len(fake_llm.calls) == n_calls  # nothing to do, no LLM call


def test_adaptation_over_limit_is_repaired_once(client, fake_llm, item):
    _, it = item
    before = len(fake_llm.calls)
    fake_llm.responses += [adapt_payload(("x", "x" * 300)), adapt_payload(("x", "قصير"))]  # x allows 280 chars
    r = client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["x"]})
    assert r.status_code == 201 and r.json()[0]["caption"] == "قصير"
    assert len(fake_llm.calls) - before == 2
    assert "invalid" in fake_llm.calls[-1].history[-1]["content"]


def test_adaptation_rejects_forbidden_word_and_wrong_platform_set(client, fake_llm, item):
    _, it = item
    fake_llm.responses += [adapt_payload(("linkedin", "هذا مجاني")), adapt_payload(("linkedin", "هذا مجاني"))]
    assert client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["linkedin"]}).status_code == 502
    fake_llm.responses += [adapt_payload(("tiktok", "a")), adapt_payload(("tiktok", "a"))]  # asked for facebook, got tiktok twice
    assert client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["facebook"]}).status_code == 502


def test_copy_mode_without_ai_and_validation(client, item, fake_llm):
    _, it = item
    r = client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["facebook"], "use_ai": False}).json()
    assert r[0]["caption"] == it["caption"] and not fake_llm.calls[1:]
    assert client.post(f"/api/v1/content-items/{it['id']}/variants", json={"platforms": ["myspace"], "use_ai": False}).status_code == 422


def test_variant_edit_delete_and_lock_when_scheduled(client, approved):  # noqa: F811
    pid, body = approved
    iid = body["items"][0]["id"]
    v = client.post(f"/api/v1/content-items/{iid}/variants", json={"platforms": ["facebook"], "use_ai": False}).json()[0]
    assert client.patch(f"/api/v1/variants/{v['id']}", json={"caption": "نص مخصص"}).json()["caption"] == "نص مخصص"
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"platform": "facebook", "scheduled_at": "2030-01-01T10:00:00+00:00"})
    assert client.patch(f"/api/v1/variants/{v['id']}", json={"caption": "x"}).status_code == 409
    assert client.delete(f"/api/v1/variants/{v['id']}").status_code == 409


def test_schedule_all_covers_every_platform_and_uses_variant_caption(client, approved, engine, monkeypatch):  # noqa: F811
    pid, body = approved
    iid = body["items"][0]["id"]
    client.post(f"/api/v1/content-items/{iid}/variants", json={"platforms": ["facebook"], "use_ai": False})
    client.patch(f"/api/v1/variants/{client.get(f'/api/v1/projects/{pid}/campaigns/{body['id']}').json()['items'][0]['variants'][0]['id']}", json={"caption": "نص فيسبوك فقط"})
    r = client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/schedule-all", json={}).json()
    assert r["scheduled"] == 3  # item 1: instagram + facebook, item 2: instagram
    pubs = client.get(f"/api/v1/projects/{pid}/publications").json()
    fb = next(p for p in pubs if p["platform"] == "facebook")
    ig = next(p for p in pubs if p["platform"] == "instagram" and p["content_item_id"] == iid)
    assert fb["caption"] == "نص فيسبوك فقط" and ig["caption"] != "نص فيسبوك فقط"


def test_schedule_requires_an_existing_version(client, approved):  # noqa: F811
    _, body = approved
    r = client.post(f"/api/v1/content-items/{body['items'][0]['id']}/schedule", json={"platform": "linkedin", "scheduled_at": "2030-01-01T10:00:00+00:00"})
    assert r.status_code == 409 and "linkedin" in r.json()["detail"]


def test_each_platform_publishes_through_its_own_connection(client, approved, engine, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    pid, body = approved
    iid = body["items"][0]["id"]
    client.post(f"/api/v1/content-items/{iid}/variants", json={"platforms": ["facebook"], "use_ai": False})
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        s.add(SocialConnection(project_id=uuid.UUID(pid), platform="facebook", account_id="PG", account_name="n", token_enc=encrypt("T")))
        s.commit()
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        return httpx.Response(200, json={"id": "P1", "post_id": "PG_1"})

    real = meta.MetaClient
    monkeypatch.setattr(meta, "MetaClient", lambda: real(httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(worker, "SessionLocal", maker)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"platform": "facebook", "scheduled_at": past})
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"platform": "instagram", "scheduled_at": past})
    assert worker.process_due() == 2
    by = {p["platform"]: p for p in client.get(f"/api/v1/projects/{pid}/publications").json() if p["content_item_id"] == iid}
    assert by["facebook"]["status"] == "published" and by["facebook"]["provider"] == "facebook"
    assert by["instagram"]["status"] == "awaiting_manual"  # no instagram connection -> manual fallback
    assert seen == ["/" + settings.meta_graph_version + "/PG/photos"]


def test_instagram_rejects_story_shaped_images(session, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)
    monkeypatch.setattr(settings, "public_base_url", "https://pub.example.com")
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1920)).save(buf, "PNG")
    conn = SocialConnection(project_id=uuid.uuid4(), platform="instagram", account_id="I", account_name="n", token_enc=encrypt("T"))
    prov = meta.MetaProvider(conn, session, meta.MetaClient(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))))
    with pytest.raises(PermanentPublishError, match="1080x1920"):
        prov.publish(PublishPayload("instagram", "h", "c", "c", image=buf.getvalue(), item_id=uuid.uuid4()))


def test_check_public_url(client, monkeypatch):
    assert client.get("/api/v1/meta/check-public-url").json()["ok"] is False
    monkeypatch.setattr(settings, "public_base_url", "https://pub.example.com")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, json={"status": "ok"}, request=httpx.Request("GET", a[0])))
    assert client.get("/api/v1/meta/check-public-url").json()["ok"] is True
