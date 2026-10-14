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
    monkeypatch.setattr(settings, "voice_provider", "off")  # tests never call real voice services


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


# ---------- voiceover (Azure Speech) ----------
def _tone_wav(seconds: float) -> bytes:
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(int(seconds * 24000))))
    return buf.getvalue()


def test_azure_request_is_cached_and_errors_are_clear(monkeypatch, tmp_path):
    from app.video import voice

    monkeypatch.setattr(settings, "azure_speech_key", "KEY")
    monkeypatch.setattr(settings, "azure_speech_region", "westeurope")
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        return httpx.Response(200, content=_tone_wav(1.0))

    store = LocalStorage(str(tmp_path))
    http = httpx.Client(transport=httpx.MockTransport(handler))
    a = voice.synthesize("مرحبا <بكم>", "ar-SA-HamedNeural", http, store)
    b = voice.synthesize("مرحبا <بكم>", "ar-SA-HamedNeural", http, store)
    assert a == b and len(calls) == 1 and abs(voice.wav_seconds(a) - 1.0) < 0.01
    req = calls[0]
    assert req.url.host == "westeurope.tts.speech.microsoft.com" and req.headers["ocp-apim-subscription-key"] == "KEY"
    assert req.headers["x-microsoft-outputformat"] == "riff-24khz-16bit-mono-pcm"
    body = req.content.decode()
    assert "ar-SA-HamedNeural" in body and "&lt;بكم&gt;" in body and "xml:lang='ar-SA'" in body
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(voice.VoiceError, match="set-azure"):
        voice.synthesize("نص آخر", "ar-SA-HamedNeural", bad, store)


def test_voiceover_sets_scene_length_and_mixes_with_music(monkeypatch, tmp_path):
    import subprocess

    from app.video import voice

    monkeypatch.setattr(voice, "synthesize", lambda text, v=None, *a, **k: _tone_wav(4.0))
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                    str(music_dir / "t.wav")], check=True)
    monkeypatch.setattr(renderer, "MUSIC_DIR", music_dir)
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "ب"], voice="ar-SA-HamedNeural"))
    # each scene = 0.3 lead + 4.0 voice + 0.45 (> the 2.6s text minimum)
    assert abs(res.duration_ms - 2 * 4750) <= 300
    info = subprocess.run([renderer.ffmpeg_exe(), "-i", "-"], input=res.mp4, capture_output=True).stderr.decode(errors="replace")
    assert "aac" in info


def test_voice_provider_priority(monkeypatch):
    from app.designs.provider import DesignRequest
    from app.designs.service import DesignService
    from app.video import voice

    item = ContentItem(platform="instagram", cta="اطلب", design={}, media_type="video")
    for k in ("elevenlabs_api_key", "elevenlabs_voice_id", "azure_speech_key", "azure_speech_region"):
        monkeypatch.setattr(settings, k, "")
    monkeypatch.setattr(settings, "voice_provider", "auto")
    monkeypatch.setattr(voice, "_edge_available", lambda: True)
    assert voice.current_voice() == f"edge:{settings.tts_voice}"  # free default, no key
    monkeypatch.setattr(settings, "azure_speech_key", "K")
    monkeypatch.setattr(settings, "azure_speech_region", "eastus")
    assert voice.current_voice().startswith("azure:")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "E")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "VID")
    assert voice.current_voice() == "elevenlabs:VID"
    assert DesignService._video_request(item, {"headline": "ع"}, DesignRequest("offer_square", {})).voice == "elevenlabs:VID"
    monkeypatch.setattr(settings, "voice_provider", "off")
    assert voice.current_voice() == ""
    monkeypatch.setattr(settings, "voice_provider", "auto")
    monkeypatch.setattr(voice, "_edge_available", lambda: False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "azure_speech_key", "")
    assert voice.current_voice() == ""


def test_elevenlabs_request_and_errors(monkeypatch, tmp_path):
    from app.video import voice

    monkeypatch.setattr(settings, "elevenlabs_api_key", "XI")
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, content=b"\x01\x00" * 24000)  # 1 s of raw 16-bit PCM

    store = LocalStorage(str(tmp_path))
    wav = voice.synthesize("مرحبا", "elevenlabs:VID", httpx.Client(transport=httpx.MockTransport(handler)), store)
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/VID" and req.url.params["output_format"] == "pcm_24000"
    assert req.headers["xi-api-key"] == "XI" and b'"model_id"' in req.content
    assert abs(voice.wav_seconds(wav) - 1.0) < 0.01
    for status, body, match in ((401, "", "set-elevenlabs"), (404, "", "My Voices"),
                                (400, '{"detail":{"status":"quota_exceeded"}}', "رصيد")):
        bad = httpx.Client(transport=httpx.MockTransport(lambda r, s=status, b=body: httpx.Response(s, text=b)))
        with pytest.raises(voice.VoiceError, match=match):
            voice.synthesize(f"نص {status}", "elevenlabs:VID", bad, store)


def test_edge_voice_converts_mp3_to_wav(monkeypatch, tmp_path):
    import subprocess
    import sys
    import types

    from app.video import voice

    class Communicate:
        def __init__(self, text, v, rate="+0%"):
            assert v == "ar-SA-ZariyahNeural" and rate == settings.tts_rate

        async def save(self, path):
            subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=300:duration=1.5", path], check=True)

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    wav = voice.synthesize("نص", "edge:ar-SA-ZariyahNeural", storage=LocalStorage(str(tmp_path)))
    assert 1.4 < voice.wav_seconds(wav) < 1.7


def test_edge_failure_renders_video_without_voice(monkeypatch):
    from app.video import voice

    def boom(*a, **k):
        raise voice.VoiceError("offline")

    monkeypatch.setattr(voice, "synthesize", boom)
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], voice="edge:ar-SA-HamedNeural"))
    assert res.duration_ms >= 2600
    with pytest.raises(voice.VoiceError):  # a configured paid provider failing is reported, not hidden
        renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], voice="elevenlabs:VID"))


# ---------- Pexels footage ----------
PEXELS_RESULT = {"videos": [
    {"id": 1, "duration": 3, "url": "https://pexels.com/v/1", "user": {"name": "A"}, "video_files": [
        {"id": 11, "file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://cdn/short.mp4"}]},
    {"id": 2, "duration": 12, "url": "https://pexels.com/v/2", "user": {"name": "B"}, "video_files": [
        {"id": 21, "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://cdn/landscape.mp4"},
        {"id": 22, "file_type": "video/mp4", "width": 2160, "height": 3840, "link": "https://cdn/4k.mp4"},
        {"id": 23, "file_type": "video/mp4", "width": 720, "height": 1280, "link": "https://cdn/hd.mp4"}]},
]}


def test_pexels_picks_long_enough_portrait_file_and_caches(monkeypatch, tmp_path):
    from app.video import broll

    monkeypatch.setattr(settings, "pexels_api_key", "PK")
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.host == "api.pexels.com":
            assert req.headers["authorization"] == "PK" and req.url.params["orientation"] == "portrait"
            return httpx.Response(200, json=PEXELS_RESULT)
        return httpx.Response(200, content=b"MP4BYTES")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    store = LocalStorage(str(tmp_path))
    path = broll.fetch("woman office laptop", 1080, 1920, 4.0, "seed", http, store)
    # video 1 is too short; of video 2 the smallest portrait file that covers ~2/3 of 1080x1920 is 720x1280
    assert path.read_bytes() == b"MP4BYTES" and path.name == "pexels-2-23.mp4"
    assert calls[-1] == "https://cdn/hd.mp4"
    n = len(calls)
    assert broll.fetch("woman office laptop", 1080, 1920, 4.0, "seed", http, store) == path
    assert len([c for c in calls[n:] if "cdn" in c]) == 0  # cached: no second download


def test_pexels_failures_fall_back_to_gradient(monkeypatch, tmp_path):
    from app.video import broll

    store = LocalStorage(str(tmp_path))
    monkeypatch.setattr(settings, "pexels_api_key", "")
    assert broll.fetch("x", 1080, 1920, 3, "s", storage=store) is None  # no key
    monkeypatch.setattr(settings, "pexels_api_key", "PK")
    for resp in (httpx.Response(401), httpx.Response(200, json={"videos": []}), httpx.Response(500)):
        http = httpx.Client(transport=httpx.MockTransport(lambda r, resp=resp: resp))
        assert broll.fetch("x", 1080, 1920, 3, "s", http, store) is None


def test_footage_is_drawn_behind_the_text(monkeypatch, tmp_path):
    import subprocess

    from app.video import broll

    clip = tmp_path / "red.mp4"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=360x640:d=1",
                    str(clip)], check=True)
    monkeypatch.setattr(broll, "fetch", lambda *a, **k: clip)
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], broll=["red"], primary_color="#0000ff"))
    frame = subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-i", "-", "-vframes", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], input=res.mp4, capture_output=True).stdout
    r, g, b = frame[(100 * 1080 + 100) * 3:(100 * 1080 + 100) * 3 + 3]
    assert r > 40 and r > g  # red footage shows through the brand-tinted shade


def test_edit_video_scenes_and_search_terms(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload())
    item_id = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ).json()["items"][0]["id"]
    body = client.patch(f"/api/v1/content-items/{item_id}", json={
        "media_type": "video", "video_scenes": [" مشهد ١ ", "", "مشهد ٢"], "video_broll": ["office laptop"]}).json()
    assert body["design"]["video_scenes"] == ["مشهد ١", "مشهد ٢"] and body["design"]["video_broll"] == ["office laptop"]
    assert body["design"]["template_key"]  # rest of the design kept


PIXABAY_RESULT = {"hits": [
    {"id": 7, "duration": 2, "videos": {"large": {"url": "https://px/short.mp4", "width": 1920, "height": 1080}}},
    {"id": 8, "duration": 15, "pageURL": "https://pixabay.com/v/8", "user": "U", "videos": {
        "large": {"url": "https://px/8-large.mp4", "width": 3840, "height": 2160},
        "medium": {"url": "https://px/8-medium.mp4", "width": 1920, "height": 1080},
        "small": {"url": "https://px/8-small.mp4", "width": 1280, "height": 720},
        "tiny": {"url": "", "width": 0, "height": 0}}},
]}


def test_pixabay_is_used_when_pexels_has_no_key(monkeypatch, tmp_path):
    from app.video import broll

    monkeypatch.setattr(settings, "pexels_api_key", "")
    monkeypatch.setattr(settings, "pixabay_api_key", "PB")
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.host == "pixabay.com":
            assert req.url.params["key"] == "PB" and req.url.params["q"] == "office laptop"
            return httpx.Response(200, json=PIXABAY_RESULT)
        return httpx.Response(200, content=b"PXB")

    path = broll.fetch("office laptop", 1080, 1920, 4.0, "s", httpx.Client(transport=httpx.MockTransport(handler)),
                       LocalStorage(str(tmp_path)))
    # short clip skipped; 1080p chosen over 4K (too heavy) and 720p (too small once cropped to vertical)
    assert path.name == "pixabay-8-medium.mp4" and seen[-1] == "https://px/8-medium.mp4"
    assert broll.provider_name() == "Pixabay"
