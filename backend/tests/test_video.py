import copy
import uuid

import httpx
import pytest

from app.ai.orchestrator import AIOrchestrator
from app.ai.schemas import CampaignRequest
from app.config import settings
from app.designs.canva.crypto import encrypt
from app.designs.service import DesignService
from app.models import ContentItem, Publication, SocialConnection, VideoAsset
from app.publishing import service as pub_service
from app.social import meta
from app.social.provider import PermanentPublishError, PublishPayload
from app.storage.local import LocalStorage
from app.video import renderer
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider


@pytest.fixture(autouse=True)
def fast_video(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)  # keep tests quick; real videos are 30 fps
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)


def video_payload(n=2, videos=1):
    data = campaign_payload(n=n)
    for it in data["content_items"][:videos]:
        it["media_type"] = "video"
        it["design"] = {**copy.deepcopy(it["design"]), "video_scenes": ["مشهد أول قوي", "المشكلة", "الحل مع المذاق"]}
    return data


def test_scenes_fall_back_to_design_fields_and_end_with_cta():
    assert renderer.scenes_for("عنوان", "وصف", "49 ريال", "اطلب", None) == ["عنوان", "وصف", "49 ريال", "اطلب"]
    assert renderer.scenes_for("ع", "", "", "اطلب", ["١", " ", "٢"]) == ["١", "٢", "اطلب"]


def test_renderer_makes_mp4_with_audio_in_platform_size():
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["خصم اليوم", "اطلب الآن"], cta="اطلب الآن",
                                                                brand_name="المذاق", aspect="1:1"))
    assert b"ftyp" in res.mp4[:64] and (res.width, res.height) == (1080, 1080) and res.duration_ms > 4000
    probe = __import__("subprocess").run([renderer.ffmpeg_exe(), "-i", "-"], input=res.mp4, capture_output=True)
    info = probe.stderr.decode(errors="replace")
    assert "h264" in info and "aac" in info


def test_campaign_marks_video_items_and_validates_scene_count(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(video_payload())
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "video_count": 1}).json()
    kinds = [it["media_type"] for it in body["items"]]
    assert kinds.count("video") == 1 and body["items"][0]["design"]["video_scenes"]
    with pytest.raises(ValueError, match="video items"):
        AIOrchestrator._validate(video_payload(videos=0), CampaignRequest(**{**REQ, "video_count": 1}), [])
    bad = video_payload()
    bad["content_items"][0]["design"]["video_scenes"] = ["x" * 71]
    with pytest.raises(ValueError, match="70"):
        AIOrchestrator._validate(bad, CampaignRequest(**{**REQ, "video_count": 1}), [])


def test_video_item_gets_cover_and_mp4_and_endpoint_serves_it(client, fake_llm, session, tmp_path, monkeypatch):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload())
    item_id = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ).json()["items"][0]["id"]
    assert client.patch(f"/api/v1/content-items/{item_id}", json={"media_type": "video"}).json()["media_type"] == "video"
    storage = LocalStorage(str(tmp_path))
    monkeypatch.setattr("app.api.design_routes.LocalStorage", lambda: storage)
    DesignService(session, StubProvider(), storage).generate(uuid.UUID(item_id))
    v = session.query(VideoAsset).filter_by(content_item_id=uuid.UUID(item_id)).one()
    assert (v.width, v.height) == (1080, 1920)
    r = client.get(f"/api/v1/content-items/{item_id}/video")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and b"ftyp" in r.content[:64]
    assert session.get(ContentItem, uuid.UUID(item_id)).status == "design_ready"


def _video_publication(client, fake_llm, session, tmp_path, platform="instagram"):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload(n=1))
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1, "platforms": [platform]}).json()
    item_id = body["items"][0]["id"]
    client.patch(f"/api/v1/content-items/{item_id}", json={"media_type": "video"})
    storage = LocalStorage(str(tmp_path))
    DesignService(session, StubProvider(), storage).generate(uuid.UUID(item_id))
    client.post(f"/api/v1/projects/{pid}/campaigns/{body['id']}/approve-all")
    pub = client.post(f"/api/v1/content-items/{item_id}/schedule", json={"platform": platform}).json()
    return uuid.UUID(pid), uuid.UUID(pub["id"]), storage


def test_platform_without_video_support_goes_to_manual(client, fake_llm, session, tmp_path, monkeypatch):
    pid, pub_id, storage = _video_publication(client, fake_llm, session, tmp_path)

    class NoVideo:
        name = "linkedin"

        def publish(self, payload):
            raise AssertionError("must not be called for video")

    monkeypatch.setattr(pub_service, "get_social_provider", lambda *a, **k: NoVideo())
    pub = pub_service.run_publication(session, pub_id, storage)
    assert pub.status == "awaiting_manual" and pub.provider == "manual"


def test_facebook_video_upload(session):
    seen = {}

    def handler(req: httpx.Request):
        seen["host"], seen["path"] = req.url.host, req.url.path
        assert b"video/mp4" in req.content
        return httpx.Response(200, json={"id": "V1"})

    conn = SocialConnection(project_id=uuid.uuid4(), platform="facebook", account_id="PG", account_name="n",
                            token_enc=_enc(), status="active")
    out = meta.MetaProvider(conn, session, meta.MetaClient(httpx.Client(transport=httpx.MockTransport(handler)))).publish(
        PublishPayload("facebook", "h", "cap", "c", image=b"img", video=b"\x00\x00ftypmp4"))
    assert seen["host"] == "graph-video.facebook.com" and seen["path"].endswith("/PG/videos")
    assert out.status == "published" and out.external_url.endswith("/PG/videos/V1")


def _enc():
    return encrypt("TOKEN")


def test_instagram_reel_resumable_upload(session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "")
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.host, req.url.path))
        if req.url.path.endswith("/IG1/media"):
            body = req.content.decode()
            assert "REELS" in body and "resumable" in body
            return httpx.Response(200, json={"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v/C1"})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["authorization"] == "OAuth TOKEN" and req.headers["offset"] == "0"
            assert req.headers["file_size"] == str(len(req.content))
            return httpx.Response(200, json={"success": True})
        if req.url.path.endswith("/C1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path.endswith("/IG1/media_publish"):
            return httpx.Response(200, json={"id": "M1"})
        return httpx.Response(200, json={"permalink": "https://instagram.com/reel/x"})

    conn = SocialConnection(project_id=uuid.uuid4(), platform="instagram", account_id="IG1", account_name="n",
                            token_enc=_enc(), status="active")
    out = meta.MetaProvider(conn, session, meta.MetaClient(httpx.Client(transport=httpx.MockTransport(handler)))).publish(
        PublishPayload("instagram", "h", "cap", "c", image=b"img", video=b"MP4DATA", item_id=uuid.uuid4()))
    assert out.external_url == "https://instagram.com/reel/x"
    assert any(h == "rupload.facebook.com" for _, h, _ in calls)


def test_instagram_reel_upload_rejected_explains_public_url(session, monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "")

    def handler(req: httpx.Request):
        if req.url.host == "rupload.facebook.com":
            return httpx.Response(400, json={"debug_info": {"message": "ProcessingFailedError"}})
        return httpx.Response(200, json={"id": "C1"})

    conn = SocialConnection(project_id=uuid.uuid4(), platform="instagram", account_id="IG1", account_name="n",
                            token_enc=_enc(), status="active")
    with pytest.raises(PermanentPublishError, match="PUBLIC_BASE_URL"):
        meta.MetaProvider(conn, session, meta.MetaClient(httpx.Client(transport=httpx.MockTransport(handler)))).publish(
            PublishPayload("instagram", "h", "cap", "c", video=b"MP4", item_id=uuid.uuid4()))


def test_video_item_without_video_fails_clearly(client, fake_llm, session, tmp_path):
    pid, pub_id, storage = _video_publication(client, fake_llm, session, tmp_path, platform="instagram")
    session.query(VideoAsset).delete()
    session.commit()
    pub = pub_service.run_publication(session, pub_id, storage)
    assert pub.status == "failed" and "فيديو" in pub.last_error
