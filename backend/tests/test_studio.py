import subprocess

import pytest

from app.config import settings
from app.storage.local import LocalStorage
from app.studio import service as st
from app.video import renderer, voice
from tests.test_video import _tone_wav


@pytest.fixture(autouse=True)
def fast_video(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)


# ---------- Edge voice + synced captions ----------
def _mp3(seconds: float) -> bytes:
    return subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
                           "-f", "mp3", "-"], capture_output=True, check=True).stdout


class FakeCommunicate:
    calls = []

    def __init__(self, text, voice, **kw):
        FakeCommunicate.calls.append((text, voice, kw))
        self.words = text.split()

    def stream_sync(self):
        yield {"type": "audio", "data": _mp3(2.0)}
        for i, w in enumerate(self.words):  # 0.5 s per word, in 100 ns ticks
            yield {"type": "WordBoundary", "offset": int(i * 0.5e7), "duration": int(0.4e7), "text": w}


@pytest.fixture
def edge(monkeypatch, tmp_path):
    import edge_tts

    FakeCommunicate.calls = []
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(settings, "voice_provider", "auto")
    monkeypatch.setattr(settings, "azure_speech_key", "")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    return LocalStorage(str(tmp_path))


def test_edge_voice_is_wav_with_cached_word_timings(edge):
    a = voice.synthesize("قهوة المذاق وصلت", "edge:ar-EG-SalmaNeural", storage=edge)
    b = voice.synthesize("قهوة المذاق وصلت", "edge:ar-EG-SalmaNeural", storage=edge)
    assert a == b and len(FakeCommunicate.calls) == 1 and a.startswith(b"RIFF")
    assert FakeCommunicate.calls[0][2]["boundary"] == "WordBoundary"
    assert abs(voice.wav_seconds(a) - 2.0) < 0.1
    times = voice.word_times("قهوة المذاق وصلت", "edge:ar-EG-SalmaNeural", 2.0, edge)
    assert [(round(s, 2), round(e, 2), w) for s, e, w in times] == [(0, 0.4, "قهوة"), (0.5, 0.9, "المذاق"), (1.0, 1.4, "وصلت")]


def test_word_times_map_mismatched_tokens_and_estimate_without_timings(edge, monkeypatch):
    voice.synthesize("خصم 50%", "edge:ar-EG-SalmaNeural", storage=edge)
    # the voice spoke "خصم" "50%" -> same count; now pretend it split the number into two words
    key = voice._key("edge", "ar-EG-SalmaNeural", "خصم 50%") + ".words.json"
    edge.put(key, '[[0,0.4,"خصم"],[0.5,0.9,"خمسين"],[1.0,1.4,"بالمية"]]'.encode())
    t = voice.word_times("خصم 50%", "edge:ar-EG-SalmaNeural", 2.0, edge)
    assert [w for *_, w in t] == ["خصم", "50%"] and t[1][0] == 0.5 and t[1][1] == 1.4
    est = voice.word_times("كلمة أخرى", "edge:ar-EG-SalmaNeural", 3.0, edge)  # nothing cached -> spread over 3 s
    assert est[0][0] == 0 and abs(est[-1][1] - 3.0) < 1e-9


def test_edge_errors_are_clear(edge, monkeypatch):
    import edge_tts

    class Down(FakeCommunicate):
        def stream_sync(self):
            raise OSError("no network")
            yield

    monkeypatch.setattr(edge_tts, "Communicate", Down)
    with pytest.raises(voice.VoiceError, match="edge-tts"):
        voice.synthesize("مرحبا", "edge:ar-EG-SalmaNeural", storage=edge)


def test_synced_captions_and_srt(edge):
    res = renderer.VideoRenderer().render(renderer.VideoRequest(
        ["أ ب ت ث ج ح", "اطلب"], cta="اطلب", voice="edge:ar-EG-SalmaNeural"))
    texts = [c[2] for c in res.captions]
    assert texts == ["أ ب ت ث", "ج ح", "اطلب"]  # groups of <= 4 words (fewer when wide), CTA stays whole
    assert res.captions[1][0] == pytest.approx(renderer.VOICE_LEAD + 2.0)  # 5th word starts at 2.0 s in the clip
    srt = res.srt()
    assert srt.startswith("1\n00:00:00,300 --> 00:00:02,300\nأ ب ت ث\n\n2\n")
    assert all(b <= res.duration_ms / 1000 + 0.002 and a <= b for a, b, _ in res.captions)
    # one continuous take for the whole script (natural intonation), not a clip per scene
    assert [c[0] for c in FakeCommunicate.calls] == ["أ ب ت ث ج ح. اطلب."]
    assert res.captions[2][0] == pytest.approx(renderer.VOICE_LEAD + 3.0 - 0.12)  # CTA cut follows its spoken word
    plain = renderer.VideoRenderer().render(renderer.VideoRequest(
        ["واحد اثنين"], voice="edge:ar-EG-SalmaNeural", captions=False))
    assert [c[2] for c in plain.captions] == ["واحد اثنين"]


# ---------- studio ----------
def test_write_script_uses_claude_output(fake_llm):
    fake_llm.responses.append({"title": "قهوة", "brand_name": "المذاق", "scenes": ["أ", " ", "ب"],
                               "broll": ["coffee"], "cta": "اطلب"})
    out = st.write_script(fake_llm, "فيديو لمحل قهوة", "9:16", "short")
    assert out == {"title": "قهوة", "brand_name": "المذاق", "scenes": ["أ", "ب"], "broll": ["coffee", ""], "cta": "اطلب",
                   "media": ["", ""]}
    assert "scene_count: 4" in fake_llm.calls[0].user_message
    with pytest.raises(st.StudioError, match="بنفسك"):
        st.write_script(fake_llm, "x", "9:16", "short")  # empty response fails validation


@pytest.fixture
def studio(client, tmp_path, monkeypatch):
    from app.main import app

    s = st.Studio(LocalStorage(str(tmp_path)))
    s._pool.submit = lambda fn, *a: fn(*a)  # render inline so the test sees the result
    app.dependency_overrides[st.get_studio] = lambda: s
    return s


def test_studio_api_renders_and_exports_formats(client, studio, fake_llm):
    fake_llm.responses.append({"title": "عرض", "scenes": ["مشهد"], "broll": ["x"], "cta": "اطلب"})
    script = client.post("/api/v1/studio/script", json={"prompt": "فيديو قصير", "aspect": "16:9"}).json()
    assert script["scenes"] == ["مشهد"]
    opts = client.get("/api/v1/studio/options").json()
    assert "16:9" in opts["aspects"] and set(opts["formats"]) == {"mp4", "webm", "mov", "gif", "mp3", "srt"}

    r = client.post("/api/v1/studio/videos", json={**script, "aspect": "16:9", "voice": ""})
    assert r.status_code == 201
    v = client.get(f"/api/v1/studio/videos/{r.json()['id']}").json()
    assert v["status"] == "ready" and (v["width"], v["height"]) == (1920, 1080)
    assert [x["id"] for x in client.get("/api/v1/studio/videos").json()] == [v["id"]]
    assert client.get(f"/api/v1/studio/videos/{v['id']}/preview").content[4:8] == b"ftyp"

    def probe(data):
        return subprocess.run([renderer.ffmpeg_exe(), "-i", "-"], input=data, capture_output=True).stderr.decode(errors="replace")

    mp4 = client.get(f"/api/v1/studio/videos/{v['id']}/export?format=mp4&quality=480p")
    assert mp4.status_code == 200 and "854x480" in probe(mp4.content)
    assert "480p.mp4" in mp4.headers["content-disposition"]
    webm = client.get(f"/api/v1/studio/videos/{v['id']}/export?format=webm&quality=720p")
    assert "vp9" in probe(webm.content) and "1280x720" in probe(webm.content)
    assert client.get(f"/api/v1/studio/videos/{v['id']}/export?format=gif&quality=480p").content[:3] == b"GIF"
    assert "mp3" in probe(client.get(f"/api/v1/studio/videos/{v['id']}/export?format=mp3&quality=720p").content)
    assert "مشهد" in client.get(f"/api/v1/studio/videos/{v['id']}/export?format=srt").text
    assert client.get(f"/api/v1/studio/videos/{v['id']}/export?format=avi").status_code == 422

    assert client.delete(f"/api/v1/studio/videos/{v['id']}").status_code == 204
    assert client.get(f"/api/v1/studio/videos/{v['id']}").status_code == 404


def test_studio_validates_input_and_reports_render_failure(client, studio, monkeypatch):
    assert client.post("/api/v1/studio/videos", json={"scenes": [" "]}).status_code == 422
    assert client.post("/api/v1/studio/videos", json={"scenes": ["أ"], "aspect": "3:2"}).status_code == 422
    assert client.get("/api/v1/studio/videos/../../etc").status_code == 404

    def boom(*a, **k):
        raise renderer.VideoError("فشل الترميز")

    monkeypatch.setattr(renderer.VideoRenderer, "render", boom)
    v = client.post("/api/v1/studio/videos", json={"scenes": ["أ"], "voice": ""}).json()
    assert v["status"] == "failed" and "فشل الترميز" in v["error"]
    assert client.get(f"/api/v1/studio/videos/{v['id']}/export?format=mp4").status_code == 422
