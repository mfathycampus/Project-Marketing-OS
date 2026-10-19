import io
import math
import struct
import subprocess
import uuid
import wave

import pytest
from PIL import Image, ImageDraw

from app.designs.service import DesignService
from app.storage.local import LocalStorage
from app.video import renderer, voice
from app.video.renderer import _auto_focus
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider


def wav(segments):
    """segments: [(seconds, loud?)] -> 24 kHz mono WAV."""
    frames = b""
    for sec, loud in segments:
        n = int(sec * 24000)
        frames += b"".join(struct.pack("<h", int(9000 * math.sin(i / 7)) if loud else 0) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(frames)
    return buf.getvalue()


def test_compress_pauses_shortens_gaps_and_shifts_words():
    data = wav([(0.5, False), (1.0, True), (1.2, False), (1.0, True), (0.8, False)])  # 4.5 s
    words = [(0.5, 1.5, "أ"), (2.7, 3.7, "ب")]
    out, new = voice.compress_pauses(data, words, max_gap=0.24)
    assert voice.wav_seconds(out) == pytest.approx(0.06 + 1.0 + 0.24 + 1.0 + 0.15, abs=0.02)
    assert new[0][0] == pytest.approx(0.06, abs=0.01) and new[1][0] - new[0][1] == pytest.approx(0.24, abs=0.01)


def test_trim_silence_keeps_the_speech():
    out = voice.trim_silence(wav([(0.7, False), (1.0, True), (0.9, False)]))
    assert 1.0 <= voice.wav_seconds(out) <= 1.25


def test_auto_focus_finds_the_busy_area():
    im = Image.new("RGB", (1600, 900), "white")
    d = ImageDraw.Draw(im)
    for i in range(30):  # dense "table" in the lower-left area
        d.line((150, 520 + i * 10, 650, 520 + i * 10), fill="black", width=2)
    fx, fy = _auto_focus(im)
    assert fx < 0.5 and fy > 0.5


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 6)


def first_frame(mp4: bytes) -> Image.Image:
    raw = subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-i", "-", "-vframes", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], input=mp4, capture_output=True).stdout
    return Image.frombytes("RGB", (1080, 1920), raw)


def test_hook_is_visible_on_the_very_first_frame():
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["سؤال الافتتاحية القوي؟", "ب"],
                                                                primary_color="#000000", secondary_color="#000000"))
    im = first_frame(res.mp4).convert("L")
    assert max(im.crop((90, 700, 990, 1200)).getdata()) > 200  # white text already on screen (the cover frame)


def test_end_card_adds_link_and_holds_longer():
    base = renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "اطلب"], cta="اطلب"))
    card = renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "اطلب"], cta="اطلب", end_url="https://maeen.app/",
                                                                 end_offer="جرّب مجانًا"))
    assert card.duration_ms - base.duration_ms == pytest.approx(1200, abs=200)


def test_brand_website_and_offer_reach_the_video(client, fake_llm, session, tmp_path, monkeypatch):
    pid = make_project(client)
    brand = client.get(f"/api/v1/projects/{pid}/brand-brain").json()["brand"]
    client.put(f"/api/v1/projects/{pid}/brand", json={**brand, "website": "maeen.app", "offer": "مجانًا 7 أيام"})
    data = campaign_payload(n=1)
    data["content_items"][0]["media_type"] = "video"
    data["content_items"][0]["design"] = {**data["content_items"][0]["design"], "video_scenes": ["أ"]}
    fake_llm.responses.append(data)
    item = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1, "video_count": 1}).json()["items"][0]
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen.update(url=req.end_url, offer=req.end_offer)
        return renderer.VideoResult(mp4=b"\x00\x00\x00\x18ftypmp42", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    DesignService(session, StubProvider(), LocalStorage(str(tmp_path))).generate(uuid.UUID(item["id"]))
    assert seen == {"url": "maeen.app", "offer": "مجانًا 7 أيام"}


def test_hook_stat_shows_a_number_on_the_first_frame():
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "ب"], stats=["81|دقيقة", ""],
                                                                primary_color="#000000", secondary_color="#000000"))
    im = first_frame(res.mp4).convert("L")
    assert max(im.crop((300, 600, 780, 1100)).getdata()) > 200  # the number is on the cover frame
