import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.designs.canva.crypto import encrypt
from app.models import SocialConnection
from app.publishing import worker
from app.social import linkedin
from app.social.provider import PermanentPublishError, PublishPayload
from tests.test_publishing import approved  # noqa: F401

UTC = timezone.utc


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)
    monkeypatch.setattr(settings, "linkedin_client_id", "LID")
    monkeypatch.setattr(settings, "linkedin_client_secret", "LSEC")


def client_with(handler):
    return linkedin.LinkedInClient(httpx.Client(transport=httpx.MockTransport(handler)))


def conn(expires=None):
    return SocialConnection(project_id=uuid.uuid4(), platform="linkedin", account_id="777", account_name="Maeen",
                            token_enc=encrypt("TOK"), expires_at=expires)


def test_commentary_escaping_of_reserved_characters():
    assert linkedin.escape_commentary("خصم (50%) #معين @maeen_app [info]") == "خصم \\(50%\\) \\#معين \\@maeen\\_app \\[info\\]"
    assert linkedin.escape_commentary("مرحبا بكم") == "مرحبا بكم"


def test_state_roundtrip_tamper_and_meta_state_not_accepted(monkeypatch):
    from app.social import meta

    pid = uuid.uuid4()
    s = linkedin.make_state(pid)
    assert linkedin.read_state(s) == pid
    with pytest.raises(linkedin.LinkedInError):
        linkedin.read_state(s[:-4] + "AAAA")
    with pytest.raises(linkedin.LinkedInError):
        linkedin.read_state(meta.make_state(pid))  # a Facebook state must not work here
    monkeypatch.setattr(linkedin.time, "time", lambda: 9_999_999_999)
    with pytest.raises(linkedin.LinkedInError):
        linkedin.read_state(s)


def test_login_url_has_client_scopes_and_state():
    url = linkedin.login_url(uuid.uuid4())
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?") and "client_id=LID" in url
    assert "w_organization_social" in unquote(url).replace("+", " ") and "state=" in url


def test_admin_organizations_reads_acls_and_names():
    def handler(req: httpx.Request):
        assert req.headers["linkedin-version"] == settings.linkedin_api_version and req.headers["authorization"] == "Bearer T"
        if req.url.path.endswith("/organizationAcls"):
            assert req.url.params["role"] == "ADMINISTRATOR"
            return httpx.Response(200, json={"elements": [{"organization": "urn:li:organization:777"}, {"organization": "urn:li:organization:888"}]})
        return httpx.Response(200, json={"localizedName": "Maeen" if req.url.path.endswith("/777") else "Other"})

    assert client_with(handler).admin_organizations("T") == [{"id": "777", "name": "Maeen"}, {"id": "888", "name": "Other"}]


def test_post_with_image_uploads_then_posts_with_org_author(session):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/images"):
            assert json.loads(req.content)["initializeUploadRequest"]["owner"] == "urn:li:organization:777"
            return httpx.Response(200, json={"value": {"uploadUrl": "https://upload.example/u1", "image": "urn:li:image:IMG1"}})
        if req.url.host == "upload.example":
            assert req.content == b"PNGDATA"
            return httpx.Response(201)
        if req.url.path.endswith("/posts"):
            body = json.loads(req.content)
            assert body["author"] == "urn:li:organization:777" and body["visibility"] == "PUBLIC"
            assert body["content"]["media"]["id"] == "urn:li:image:IMG1" and body["lifecycleState"] == "PUBLISHED"
            assert body["commentary"] == "عرض \\(خاص\\)"
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:123"})
        return httpx.Response(404)

    out = linkedin.LinkedInProvider(conn(), session, client_with(handler)).publish(
        PublishPayload("linkedin", "h", "عرض (خاص)", "c", image=b"PNGDATA"))
    assert out.status == "published" and out.external_url == "https://www.linkedin.com/feed/update/urn:li:share:123"
    assert [m for m, _ in seen] == ["POST", "PUT", "POST"]


def test_text_only_post_skips_image_upload(session):
    def handler(req: httpx.Request):
        assert req.url.path.endswith("/posts") and "content" not in json.loads(req.content)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:9"})

    linkedin.LinkedInProvider(conn(), session, client_with(handler)).publish(PublishPayload("linkedin", "h", "نص", "c"))


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_marks_connection_and_is_permanent(session, status):
    c = conn()
    prov = linkedin.LinkedInProvider(c, session, client_with(lambda r: httpx.Response(status, json={"message": "ACCESS_DENIED org permission"})))
    with pytest.raises(PermanentPublishError):
        prov.publish(PublishPayload("linkedin", "h", "c", "c"))
    assert c.status == "expired" and "ACCESS_DENIED" in c.last_error


def test_expired_token_is_not_sent(session):
    c = conn(expires=datetime.now(UTC) - timedelta(days=1))
    prov = linkedin.LinkedInProvider(c, session, client_with(lambda r: pytest.fail("must not call the API")))
    with pytest.raises(PermanentPublishError):
        prov.publish(PublishPayload("linkedin", "h", "c", "c"))
    assert c.status == "expired"


def test_connect_flow_single_org_and_multi_org_selection(client, monkeypatch):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    state = linkedin.make_state(uuid.UUID(pid))
    monkeypatch.setattr(linkedin.LinkedInClient, "exchange_code", lambda self, code: {"access_token": "T", "expires_in": 5184000})
    monkeypatch.setattr(linkedin.LinkedInClient, "admin_organizations", lambda self, tok: [{"id": "777", "name": "Maeen"}])
    r = client.get("/api/v1/linkedin/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert r.headers["location"].endswith("publish?connected=1")
    conns = client.get(f"/api/v1/projects/{pid}/social-connections").json()
    assert conns[0]["platform"] == "linkedin" and conns[0]["account_name"] == "Maeen" and conns[0]["expires_at"]
    assert "token" not in json.dumps(conns)

    monkeypatch.setattr(linkedin.LinkedInClient, "admin_organizations", lambda self, tok: [{"id": "777", "name": "Maeen"}, {"id": "888", "name": "Maeen EN"}])
    loc = client.get("/api/v1/linkedin/callback", params={"state": state, "code": "c"}, follow_redirects=False).headers["location"]
    pending_id = loc.split("pick_li=")[1]
    listing = client.get(f"/api/v1/linkedin/pending/{pending_id}").json()
    assert [o["id"] for o in listing] == ["777", "888"] and "TOK" not in json.dumps(listing) and "token" not in json.dumps(listing)
    assert client.post("/api/v1/linkedin/select", json={"pending_id": pending_id, "org_id": "888"}).json()["account_name"] == "Maeen EN"
    assert client.get(f"/api/v1/linkedin/pending/{pending_id}").status_code == 404


def test_callback_errors_are_readable(client, monkeypatch):
    assert client.get("/api/v1/linkedin/callback", params={"state": "garbage", "code": "c"}).status_code == 400
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["id"]
    state = linkedin.make_state(uuid.UUID(pid))
    r = client.get("/api/v1/linkedin/callback", params={"state": state, "error": "user_cancelled_authorize"}, follow_redirects=False)
    assert "li_error=" in r.headers["location"]
    monkeypatch.setattr(linkedin.LinkedInClient, "exchange_code", lambda self, code: {"access_token": "T"})
    monkeypatch.setattr(linkedin.LinkedInClient, "admin_organizations", lambda self, tok: [])
    msg = unquote(client.get("/api/v1/linkedin/callback", params={"state": state, "code": "c"}, follow_redirects=False).headers["location"])
    assert "Community Management API" in msg


def test_status_and_connect_require_configuration(client, monkeypatch):
    assert client.get("/api/v1/linkedin/status").json()["configured"] is True
    monkeypatch.setattr(settings, "linkedin_client_id", "")
    assert client.get("/api/v1/linkedin/status").json()["configured"] is False
    assert client.get(f"/api/v1/linkedin/connect?project_id={uuid.uuid4()}").status_code == 400


def test_scheduler_publishes_to_linkedin_when_connected(client, approved, engine, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    pid, body = approved
    iid = body["items"][0]["id"]
    client.post(f"/api/v1/content-items/{iid}/variants", json={"platforms": ["linkedin"], "use_ai": False})
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as s:
        s.add(SocialConnection(project_id=uuid.UUID(pid), platform="linkedin", account_id="777", account_name="Maeen",
                               token_enc=encrypt("T")))
        s.commit()

    def handler(req: httpx.Request):
        if req.url.path.endswith("/images"):
            return httpx.Response(200, json={"value": {"uploadUrl": "https://up.example/x", "image": "urn:li:image:I"}})
        if req.url.host == "up.example":
            return httpx.Response(201)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:55"})

    real = linkedin.LinkedInClient
    monkeypatch.setattr(linkedin, "LinkedInClient", lambda: real(httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(worker, "SessionLocal", maker)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    client.post(f"/api/v1/content-items/{iid}/schedule", json={"platform": "linkedin", "scheduled_at": past})
    assert worker.process_due() == 1
    pub = next(p for p in client.get(f"/api/v1/projects/{pid}/publications").json() if p["platform"] == "linkedin")
    assert pub["status"] == "published" and pub["provider"] == "linkedin" and pub["external_url"].endswith("urn:li:share:55")
