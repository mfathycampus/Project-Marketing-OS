import json
import uuid

import httpx
import pytest

from app.config import settings
from app.designs.canva import oauth
from app.designs.canva.client import CanvaClient, CanvaError
from app.designs.canva.provider import CanvaProvider
from app.designs.html_renderer import HtmlRenderer, render_html
from app.designs.provider import DesignRequest, DesignResult
from app.designs.registry import DesignSpecError, build_values
from app.designs.service import DesignService
from app.models import ContentItem, DesignJob
from app.storage.local import LocalStorage
from tests.test_api import REQ, campaign_payload, make_project

PNG = b"\x89PNG\r\n\x1a\n"


class StubProvider:
    name = "stub"

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def render(self, req):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return DesignResult(png=PNG + b"x", width=1080, height=1080)


@pytest.fixture
def item_id(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload())
    return uuid.UUID(client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ).json()["items"][0]["id"])


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(str(tmp_path))


def test_build_values_trims_and_validates():
    v = build_values("offer_square", "H", "CTA", {"PRICE": "49 ريال", "description": "x" * 500})
    assert v["price"] == "49 ريال" and len(v["description"]) == 140
    with pytest.raises(DesignSpecError):
        build_values("offer_square", "", "CTA", {})
    with pytest.raises(DesignSpecError):
        build_values("nope", "H", "C", {})


def test_html_is_escaped_and_rtl():
    html = render_html(DesignRequest("offer_square", {"headline": "<script>x</script>", "cta": "اطلب"}))
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html and 'dir="rtl"' in html


def test_html_renderer_makes_real_png():
    r = HtmlRenderer().render(DesignRequest("offer_square", {"headline": "عرض نهاية الأسبوع", "cta": "اطلب الآن", "price": "49 ريال"}))
    assert r.png.startswith(PNG) and (r.width, r.height) == (1080, 1080)


def test_service_happy_path_and_idempotency(client, session, item_id, storage):
    provider = StubProvider()
    svc = DesignService(session, provider, storage)
    a1 = svc.generate(item_id)
    assert session.get(ContentItem, item_id).status == "design_ready"
    # regenerate with identical inputs: no second render
    session.get(ContentItem, item_id).status = "design_ready"
    a2 = svc.generate(item_id)
    assert a1.id == a2.id and provider.calls == 1
    assert storage.get(a1.storage_key).startswith(PNG)


def test_service_failure_rolls_back_state(client, session, item_id, storage):
    with pytest.raises(RuntimeError):
        DesignService(session, StubProvider(fail=True), storage).generate(item_id)
    assert session.get(ContentItem, item_id).status == "ai_generated"
    assert session.query(DesignJob).one().status == "failed"


def test_design_api_end_to_end(client, item_id, monkeypatch, tmp_path):
    from app.api.design_routes import get_designer
    from app.main import app

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    app.dependency_overrides[get_designer] = lambda: StubProvider()
    r = client.post(f"/api/v1/content-items/{item_id}/design")
    assert r.status_code == 200, r.text
    img = client.get(r.json()["image_url"])
    assert img.status_code == 200 and img.content.startswith(PNG)


def test_pkce_challenge_matches_verifier():
    import base64, hashlib
    v, c = oauth.generate_pkce()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert "code_challenge_method=s256" in oauth.authorize_url("st", c)


def canva_client(caps):
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/users/me/capabilities"):
            return httpx.Response(200, json={"capabilities": caps})
        if p.endswith("/dataset"):
            return httpx.Response(200, json={"dataset": {"HEADLINE": {"type": "text"}, "CTA": {"type": "text"}}})
        if p.endswith("/autofills") and request.method == "POST":
            body = json.loads(request.content)
            assert body["data"]["HEADLINE"] == {"type": "text", "text": "H"}
            return httpx.Response(200, json={"job": {"id": "af1", "status": "in_progress"}})
        if p.endswith("/autofills/af1"):
            return httpx.Response(200, json={"job": {"id": "af1", "status": "success", "result": {"design": {"id": "D1", "url": "https://canva/d1"}}}})
        if p.endswith("/exports") and request.method == "POST":
            return httpx.Response(200, json={"job": {"id": "ex1", "status": "in_progress"}})
        if p.endswith("/exports/ex1"):
            return httpx.Response(200, json={"job": {"id": "ex1", "status": "success", "urls": ["https://files/x.png"]}})
        return httpx.Response(404)

    return CanvaClient("tok", httpx.Client(transport=httpx.MockTransport(handler)))


MAP = {"offer_square": {"brand_template_id": "BT1", "fields": {"headline": "HEADLINE", "cta": "CTA"}}}


def test_canva_blocks_without_autofill_capability():
    p = CanvaProvider(canva_client(["design"]), MAP)
    with pytest.raises(CanvaError, match="Autofill"):
        p.render(DesignRequest("offer_square", {"headline": "H", "cta": "C"}))


def test_canva_autofill_flow(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, content=PNG + b"canva"))
    p = CanvaProvider(canva_client(["autofill"]), MAP, interval_s=0)
    r = p.render(DesignRequest("offer_square", {"headline": "H", "cta": "C"}))
    assert r.png.endswith(b"canva") and r.external_url == "https://canva/d1" and r.external_job_id == "af1"
