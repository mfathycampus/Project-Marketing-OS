import io
import subprocess
import wave

import pytest
from PIL import Image

from app.media.library import describe_for_ai
from app.storage.local import LocalStorage
from app.studio import service as st
from app.video import backgrounds, music_gen, renderer


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)


def test_background_plan_varies_per_scene_and_theme():
    p = backgrounds.plan("modern", 5)
    assert len(set(p)) == 5 and all(a != b for a, b in zip(p, p[1:]))
    assert set(backgrounds.plan("corporate", 4)) <= set(backgrounds.LIGHT_KINDS)
    assert backgrounds.plan("modern", 3, vary=False) == ["gradient"] * 3


def test_scenes_get_different_backgrounds(monkeypatch):
    built = []
    orig = backgrounds.build
    monkeypatch.setattr(backgrounds, "build", lambda k, *a, **kw: built.append(k) or orig(k, *a, **kw))
    renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "ب", "ج"]))
    assert len(set(built)) == 3


@pytest.mark.real_music
def test_generated_music_is_a_real_track():
    data = music_gen.generate(6, "energetic", seed=3)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(6, abs=0.05)
        frames = w.readframes(w.getnframes())
    assert max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 200)) > 10000
    assert music_gen.generate(2, "calm", seed=1) == music_gen.generate(2, "calm", seed=1)  # deterministic


def has_audio(mp4: bytes) -> float:
    out = subprocess.run([renderer.ffmpeg_exe(), "-i", "-", "-af", "volumedetect", "-f", "null", "-"], input=mp4,
                         capture_output=True).stderr.decode(errors="replace")
    return float(out.split("max_volume:")[1].split("dB")[0]) if "max_volume:" in out else -99.0


def test_music_choices(tmp_path, monkeypatch):
    tone = tmp_path / "track.wav"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
                    str(tone)], check=True)
    monkeypatch.setattr(renderer, "MUSIC_DIR", tmp_path / "empty")
    used = []
    monkeypatch.setattr(music_gen, "generate", lambda sec, style="modern", seed=0: used.append(style) or tone.read_bytes())
    assert has_audio(renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], music="none")).mp4) < -80
    renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], style="calm"))  # auto, no tracks -> generated
    assert used == ["calm"]
    loud = has_audio(renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], music=str(tone))).mp4)
    assert loud > -30 and used == ["calm"]  # the chosen file, not generated


def test_audio_in_media_library(client, tmp_path):
    tone = tmp_path / "bed.mp3"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                    str(tone)], check=True)
    r = client.post("/api/v1/media", files={"file": ("bed.mp3", tone.read_bytes(), "audio/mpeg")},
                    data={"description": "موسيقى هادئة"})
    m = r.json()
    assert r.status_code == 201 and m["kind"] == "audio" and 1800 <= m["duration_ms"] <= 2300
    assert describe_for_ai([m]) == ""  # Claude picks screens, not music
    assert m["id"] in st.options()["music"]


def test_hook_variants_make_grouped_videos(tmp_path, monkeypatch):
    seen = []

    def fake_render(self, req, on_progress=None):
        seen.append((req.scenes[0], list(req.stats)))
        return renderer.VideoResult(mp4=b"x", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    studio = st.Studio(LocalStorage(str(tmp_path)))
    studio.create(st.VideoSpec(title="معين", scenes=["81 دقيقة منسية", "ب"], stats=["81|دقيقة", ""],
                               hooks=["كم مرة *نسيت* تسجيلًا؟", "التسجيل بلا *فائدة*؟"]), run_async=False)
    vids = studio.list()
    assert sorted(v["variant"] for v in vids) == ["A", "B", "C"] and len({v["group"] for v in vids}) == 1
    assert sorted(v["title"] for v in vids) == ["معين (A)", "معين (B)", "معين (C)"]
    assert ("81 دقيقة منسية", ["81|دقيقة", ""]) in seen  # A keeps the original hook and its number
    assert ("كم مرة *نسيت* تسجيلًا؟", ["", ""]) in seen  # variants: new hook, no number on it


def test_script_offers_alternative_hooks(fake_llm):
    fake_llm.responses.append({"title": "t", "scenes": ["أ", "ب"], "broll": ["", ""], "cta": "اطلب",
                               "hooks": ["بديل *واحد*", "أ", "بديل *اثنان*", "زائد"]})
    out = st.write_script(fake_llm, "فيديو", "9:16", "short")
    assert out["hooks"] == ["بديل *واحد*", "بديل *اثنان*"]  # duplicates of scene 1 dropped, max 2
