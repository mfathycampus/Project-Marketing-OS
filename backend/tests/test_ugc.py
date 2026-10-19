"""'Your face' videos: jump cuts, Claude's overlay plan, the background job."""
import io
import subprocess

import pytest

from app.storage.local import LocalStorage
from app.studio import service as st
from app.video import renderer, transcribe, ugc

WORDS = [(0.5, 0.9, "مرحبا"), (1.0, 1.4, "هذا"), (1.5, 2.0, "معين"), (3.5, 4.0, "يوفر"), (4.1, 4.6, "خمس"),
         (4.7, 5.2, "ساعات"), (5.3, 5.8, "أسبوعيا"), (7.0, 7.5, "جربه"), (7.6, 8.2, "الآن")]


@pytest.fixture(autouse=True)
def fast_video(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)
    monkeypatch.setattr(ugc, "FPS", 4)


@pytest.fixture(scope="module")
def talking(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("ugc") / "talk.mp4"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=12",
                    "-f", "lavfi", "-i", "sine=frequency=300", "-t", "9", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True, capture_output=True)
    return str(path)


def test_keep_segments_cut_long_pauses_only():
    segs = ugc.keep_segments(WORDS, 9.0)
    assert len(segs) == 3  # pauses of 1.5 s and 1.2 s are cut, short gaps between words are kept
    assert segs[0][0] == pytest.approx(0.43) and segs[-1][1] == pytest.approx(8.32)
    assert ugc.keep_segments(WORDS, 9.0, jump_cuts=False) == [(0.0, 9.0)]
    assert ugc.remap(3.5, segs) == pytest.approx(segs[0][1] - segs[0][0] + 3.5 - segs[1][0])
    assert ugc.remap(2.8, segs) == pytest.approx(segs[0][1] - segs[0][0])  # inside a cut: snaps to the join


def test_clean_plan_keeps_only_safe_overlays():
    raw = {"title": "t", "hook_text": "وفّر *ساعات*", "cta": "جرّب", "overlays": [
        {"start_word": 0, "end_word": 2, "kind": "text", "value": "يغطي الافتتاحية"},  # over the hook -> dropped
        {"start_word": 3, "end_word": 6, "kind": "stat", "value": "5|ساعات أسبوعيًا"},
        {"start_word": 5, "end_word": 6, "kind": "text", "value": "متداخل"},  # overlaps the stat -> dropped
        {"start_word": 7, "end_word": 7, "kind": "media", "value": "nope"},  # unknown id -> dropped
        {"start_word": 7, "end_word": 8, "kind": "text", "value": "على آخر كلمة"},  # covers the payoff -> dropped
        {"start_word": 3, "end_word": 4, "kind": "stat", "value": "بدون رقم"},
    ]}
    plan = st.clean_plan(raw, WORDS, {"m1"})
    assert plan["hook_text"] == "وفّر *ساعات*"
    assert plan["overlays"] == [{"start": 3.5, "end": 5.8, "kind": "stat", "value": "5|ساعات أسبوعيًا"}]


def test_render_ugc_cuts_pauses_and_adds_end_card(talking):
    req = ugc.UgcRequest(video=talking, words=WORDS, hook_text="وفّر *ساعات*", cta="جرّب الآن", end_url="maeen.app",
                         music="none", overlays=[{"start": 3.5, "end": 5.8, "kind": "stat", "value": "5|ساعات"}])
    res = ugc.render_ugc(req)
    cut = sum(b - a for a, b in ugc.keep_segments(WORDS, 9.0))
    assert res.duration_ms == pytest.approx((cut + ugc.END_CARD_S) * 1000, abs=60)
    assert cut < 6.5  # 9 s of raw talk lost its pauses
    assert res.captions and res.captions[0][0] < 0.5
    info = subprocess.run([renderer.ffmpeg_exe(), "-i", "-"], input=res.mp4, capture_output=True).stderr.decode()
    assert "Audio:" in info and "1080x1920" in info


def test_render_ugc_rejects_silent_video(tmp_path):
    path = tmp_path / "mute.mp4"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x284:rate=8", "-t", "1",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    with pytest.raises(renderer.VideoError):
        ugc.render_ugc(ugc.UgcRequest(video=str(path)))


def test_studio_ugc_job_transcribes_plans_and_renders(talking, tmp_path, monkeypatch, fake_llm, isolated_media_library):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 800), "white").save(buf, "PNG")
    shot = isolated_media_library.add("screen.png", buf.getvalue(), "شاشة التقارير")
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None, on_progress=None: WORDS)
    fake_llm.responses.append({"title": "معين يوفر وقتك", "hook_text": "وفّر *ساعات*", "cta": "جرّبه", "overlays": [
        {"start_word": 3, "end_word": 6, "kind": "media", "value": shot["id"]}]})
    studio = st.Studio(LocalStorage(str(tmp_path)))
    with open(talking, "rb") as f:
        meta = studio.create_ugc(st.UgcSpec(notes="تطبيق معين", music="none", end_url="maeen.app"), "me.mp4", f,
                                 fake_llm, run_async=False)
    assert meta["status"] == "ready", meta["error"]
    assert meta["kind"] == "ugc" and meta["title"] == "معين يوفر وقتك"
    assert meta["plan"]["overlays"][0]["kind"] == "media" and meta["transcript"].startswith("مرحبا")
    assert "<transcript>" in fake_llm.calls[0].user_message and "[3]يوفر" in fake_llm.calls[0].user_message
    assert studio.master(meta["id"]).stat().st_size > 1000
    assert studio.export(meta["id"], "srt", "1080p").read_text(encoding="utf-8").startswith("1\n")


def test_studio_ugc_without_ai_plan_still_edits(talking, tmp_path, monkeypatch, fake_llm):
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None, on_progress=None: WORDS)
    studio = st.Studio(LocalStorage(str(tmp_path)))
    with open(talking, "rb") as f:  # the fake LLM returns {} -> invalid plan -> cuts + captions only
        meta = studio.create_ugc(st.UgcSpec(music="none"), "me.mov", f, fake_llm, run_async=False)
    assert meta["status"] == "ready" and meta["plan"]["overlays"] == [] and meta.get("warning")


def test_studio_ugc_reports_missing_speech_and_bad_files(talking, tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None, on_progress=None: [])
    studio = st.Studio(LocalStorage(str(tmp_path)))
    with open(talking, "rb") as f:
        meta = studio.create_ugc(st.UgcSpec(smart_edit=False), "me.mp4", f, None, run_async=False)
    assert meta["status"] == "failed" and "كلام" in meta["error"]
    with pytest.raises(st.StudioError):
        studio.create_ugc(st.UgcSpec(), "notes.txt", io.BytesIO(b"x"), None, run_async=False)


def test_ugc_api_upload_validation_and_talk_script(client, fake_llm):
    r = client.post("/api/v1/studio/ugc", files={"file": ("a.txt", b"x")}, data={"options": "{}"})
    assert r.status_code == 422
    r = client.post("/api/v1/studio/ugc", files={"file": ("a.mp4", b"x")}, data={"options": "{bad"})
    assert r.status_code == 422
    fake_llm.responses.append({"title": "ساعات ضايعة", "lines": ["  بتضيع كام ساعة؟ ", "", "جرب معين"], "tips": ["اتكلم بحماس"]})
    out = client.post("/api/v1/studio/ugc/script", json={"prompt": "تطبيق معين لتفريغ الاجتماعات", "seconds": 30}).json()
    assert out["lines"] == ["بتضيع كام ساعة؟", "جرب معين"] and out["tips"] == ["اتكلم بحماس"]


def test_transcribe_reports_missing_package(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "faster_whisper":
            raise ImportError
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.setattr(transcribe, "_models", {})
    assert transcribe.available() is False
    with pytest.raises(transcribe.TranscribeError):
        transcribe.transcribe("x.mp4")
