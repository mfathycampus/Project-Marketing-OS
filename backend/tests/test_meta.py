import io
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.designs.canva.crypto import encrypt
from app.models import MetaPending, Publication, SocialConnection
from app.publishing import service, worker
from app.social import meta
from app.social.provider import PermanentPublishError, PublishPayload
from tests.test_publishing import approved  # noqa: F401  (fixture)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)
    monkeypatch.setattr(settings, "meta_app_id", "123")
    monkeypatch.setattr(settings, "meta_app_secret", "sec")
    monkeypatch.setattr(settings, "public_base_url", "https://pub.example.com")


def fake_client(handler):
    return meta.MetaClient(httpx.Client(transport=httpx.MockTransport(handler)))


def conn(platform="facebook", account="PAGE1"):
    return SocialConnection(project_id=uuid.uuid4(), platform=platform, account_id=account, account_name="n",
                            token_enc=encrypt("PAGE_TOKEN"), status="active")


def test_state_roundtrip_and_tamper_and_expiry(monkeypatch):
    pid = uuid.uuid4()
    s = meta.make_state(pid)
    assert meta.read_state(s) == pid
    with pytest.raises(meta.MetaError):
        meta.read_state(s[:-4] + "AAAA")
    monkeypatch.setattr(meta.time, "time", lambda: 9_999_999_999)
    with pytest.raises(meta.MetaError):
        meta.read_state(s)


def test_login_url_scopes_and_config_id(monkeypatch):
    url = meta.login_url(uuid.uuid4())
    assert "client_id=123" in url and "pages_manage_posts" in url and "instagram_content_publish" in url
    monkeypatch.setattr(settings, "meta_config_id", "CFG")
    assert "config_id=CFG" in meta.login_url(uuid.uuid4()) and "scope=" not in meta.login_url(uuid.uuid4())


def test_facebook_photo_post_sends_bytes_and_returns_url(session):
    seen = {}

    def handler(req: httpx.Request):
        seen["path"], seen["ctype"] = req.url.path, req.headers["content-type"]
        seen["body"] = req.content
        return httpx.Response(200, json={"id": "PHOTO1", "post_id": "PAGE1_999"})

    prov = meta.MetaProvider(conn(), session, fake_client(handler))
    out = prov.publish(PublishPayload("facebook", "h", "نص المنشور", "c", image=b"\x89PNGdata"))
    assert seen["path"].endswith("/PAGE1/photos") and seen["ctype"].startswith("multipart/form-data")
    assert b"PNGdata" in seen["body"] and "نص المنشور".encode() in seen["body"]
    assert out.status == "published" and out.external_url == "https://www.facebook.com/PAGE1_999"


def test_instagram_flow_uses_public_jpeg_url_and_waits_for_container(session):
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/IG1/media"):
            assert "https://pub.example.com/api/v1/content-items/" in req.content.decode() or "pub.example.com" in req.content.decode().replace("%3A", ":").replace("%2F", "/")
            return httpx.Response(200, json={"id": "CONT1"})
        if req.url.path.endswith("/CONT1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path.endswith("/IG1/media_publish"):
            return httpx.Response(200, json={"id": "MEDIA1"})
        return httpx.Response(200, json={"permalink": "https://instagram.com/p/abc"})

    prov = meta.MetaProvider(conn("instagram", "IG1"), session, fake_client(handler))
    out = prov.publish(PublishPayload("instagram", "h", "cap", "c", image=b"x", item_id=uuid.uuid4()))
    assert out.external_url == "https://instagram.com/p/abc"
    assert [m for m, _ in calls][:3] == ["POST", "GET", "POST"]


def test_instagram_requires_public_base_url_and_image(session, monkeypatch):
    prov = meta.MetaProvider(conn("instagram", "IG1"), session, fake_client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(PermanentPublishError, match="صورة"):
        prov.publish(PublishPayload("instagram", "h", "c", "c", image=None, item_id=uuid.uuid4()))
    monkeypatch.setattr(settings, "public_base_url", "")
    with pytest.raises(PermanentPublishError, match="PUBLIC_BASE_URL"):
        prov.publish(PublishPayload("instagram", "h", "c", "c", image=b"x", item_id=uuid.uuid4()))


def test_expired_token_marks_connection_and_is_permanent(session):
    c = conn()
    prov = meta.MetaProvider(c, session, fake_client(lambda r: httpx.Response(400, json={"error": {"message": "Session has expired", "code": 190}})))
    with pytest.raises(PermanentPublishError):
        prov.publish(PublishPayload("facebook", "h", "c", "c"))
    assert c.status == "expired"


def test_connect_flow_single_page_connects_and_multi_page_asks(client, session, monkeypatch):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    state = meta.make_state(uuid.UUID(pid))
    one = [{"id": "P1", "name": "صفحتي", "access_token": "T", "instagram_business_account": {"id": "I1", "username": "my_ig"}}]
    two = one + [{"id": "P2", "name": "ثانية", "access_token": "T2"}]
    monkeypatch.setattr(meta.MetaClient, "exchange_code", lambda self, code: "LONG")
    monkeypatch.setattr(meta.MetaClient, "pages", lambda self, tok: one)
    r = client.get("/api/v1/meta/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert r.headers["location"].endswith("publish?connected=1")
    conns = client.get(f"/api/v1/projects/{pid}/social-connections").json()
    assert {(c["platform"], c["account_name"]) for c in conns} == {("facebook", "صفحتي"), ("instagram", "my_ig")}
    assert "token" not in json.dumps(conns)

    monkeypatch.setattr(meta.MetaClient, "pages", lambda self, tok: two)
    loc = client.get("/api/v1/meta/callback", params={"state": state, "code": "c"}, follow_redirects=False).headers["location"]
    pending_id = loc.split("pick=")[1]
    listing = client.get(f"/api/v1/meta/pending/{pending_id}").json()
    assert [p["id"] for p in listing] == ["P1", "P2"] and "access_token" not in json.dumps(listing)
    r = client.post("/api/v1/meta/select", json={"pending_id": pending_id, "page_id": "P2"}).json()
    assert [x["platform"] for x in r] == ["facebook"]
    assert client.get(f"/api/v1/meta/pending/{pending_id}").status_code == 404


def test_callback_rejects_bad_state_and_reports_denial(client):
    assert client.get("/api/v1/meta/callback", params={"state": "garbage", "code": "c"}).status_code == 400
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    r = client.get("/api/v1/meta/callback", params={"state": meta.make_state(uuid.UUID(pid)), "error": "access_denied"}, follow_redirects=False)
    assert "meta_error=" in r.headers["location"]


def test_scheduler_publishes_through_meta_when_connected(client, approved, engine, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))  # where the `approved` fixture stored designs
    pid, body = approved
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        s.add(SocialConnection(project_id=uuid.UUID(pid), platform="instagram", account_id="IG1", account_name="x",
                               token_enc=encrypt("T")))
        s.commit()

    def handler(req: httpx.Request):
        if req.url.path.endswith("/IG1/media"):
            return httpx.Response(200, json={"id": "C"})
        if req.url.path.endswith("/C"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M"})
        return httpx.Response(200, json={"permalink": "https://instagram.com/p/z"})

    real = meta.MetaClient
    monkeypatch.setattr(meta, "MetaClient", lambda: real(httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(worker, "SessionLocal", maker)
    iid = body["items"][0]["id"]  # test payload items are platform=instagram
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"scheduled_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()})
    assert worker.process_due() == 1
    pub = client.get(f"/api/v1/projects/{pid}/publications").json()[0]
    assert pub["status"] == "published" and pub["provider"] == "instagram" and pub["external_url"] == "https://instagram.com/p/z"


def test_jpeg_variant_of_design_image(client, approved, session, monkeypatch, tmp_path):  # noqa: F811
    from PIL import Image

    from app.models import DesignAsset

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    _, body = approved
    iid = uuid.UUID(body["items"][0]["id"])
    asset = session.query(DesignAsset).filter_by(content_item_id=iid).one()
    buf = io.BytesIO()
    Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(buf, "PNG")
    (tmp_path / asset.storage_key).write_bytes(buf.getvalue())
    r = client.get(f"/api/v1/content-items/{iid}/design/image", params={"format": "jpeg"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/v1/content-items/{iid}/design/image").headers["content-type"] == "image/png"
