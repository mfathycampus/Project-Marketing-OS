import io
import subprocess
import uuid

import pytest
from PIL import Image

from app.designs.service import DesignService
from app.storage.local import LocalStorage
from app.studio import service as st
from app.video import renderer
from app.video.renderer import _Browser, _browser_layout, _media_size, parse_compare
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)


def png_bytes(w, h, color="white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_parse_compare():
    assert parse_compare("يدوي|تلقائي") == ("يدوي", "تلقائي")
    assert parse_compare("") is None and parse_compare("بدون فاصل") is None and parse_compare("|ب") is None


@pytest.mark.parametrize("style", ["modern", "calm", "energetic", "corporate"])
def test_every_theme_renders_devices_compare_and_stats(tmp_path, style):
    wide, tall = tmp_path / "wide.png", tmp_path / "tall.png"
    wide.write_bytes(png_bytes(1920, 1080))
    tall.write_bytes(png_bytes(1080, 2400))
    res = renderer.VideoRenderer().render(renderer.VideoRequest(
        ["افتتاحية *مهمة*", "شاشة عريضة", "شاشة طولية", "مقارنة", "رقم", "اطلب"], cta="اطلب", style=style,
        media=["", str(wide), str(tall), "", "", ""], media_focus=[None, (0.2, 0.1), (0.5, 0.8), None, None, None],
        compare=["", "", "", "قبل شيء|بعد شيء", "", ""], stats=["", "", "", "", "5|ساعات", ""]))
    assert b"ftyp" in res.mp4[:64] and (res.width, res.height) == (1080, 1920)


def test_wide_media_uses_browser_and_tall_uses_phone(tmp_path, monkeypatch):
    wide, tall = tmp_path / "wide.png", tmp_path / "tall.png"
    wide.write_bytes(png_bytes(1600, 900))
    tall.write_bytes(png_bytes(900, 1800))
    made = []
    for cls in (renderer._Browser, renderer._Phone):
        orig = cls.__init__

        def init(self, *a, _orig=orig, _name=cls.__name__, **k):
            made.append(_name)
            _orig(self, *a, **k)
        monkeypatch.setattr(cls, "__init__", init)
    renderer.VideoRenderer().render(renderer.VideoRequest(["أ", "ب"], media=[str(wide), str(tall)]))
    assert made == ["_Browser", "_Phone"]


def test_browser_zooms_towards_the_focus_and_points_at_it(tmp_path):
    shot = tmp_path / "wide.png"
    im = Image.new("RGB", (1920, 1080), "white")
    im.paste((255, 0, 0), (180, 60, 330, 140))  # the "button" at ~ (0.133, 0.093)
    im.save(shot)
    geo = _browser_layout(1080, 1920, 90, 1920 / 1080)
    b = _Browser(str(shot), geo, 4.0, focus=(255 / 1920, 100 / 1080))
    frame, target = b._screen(3.9)  # fully zoomed
    x, y = int(target[0]), int(target[1])
    assert frame.getpixel((min(x, frame.width - 1), min(y, frame.height - 1)))[1] < 60  # the pointer sits on red
    assert target[0] > b.sw * 0.1 and target[1] > b.sh * 0.1  # zoom moved the button away from the corner


def test_media_size_of_a_video(tmp_path):
    out = tmp_path / "r.mp4"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10",
                    "-t", "1", str(out)], check=True)
    assert _media_size(str(out)) == (640, 360) and _media_size(str(tmp_path / "missing.png")) is None


def test_focus_point_api(client):
    mid = client.post("/api/v1/media", files={"file": ("a.png", png_bytes(800, 400), "image/png")},
                      data={"description": "لوحة"}).json()["id"]
    assert client.patch(f"/api/v1/media/{mid}", json={"focus": [0.25, 0.5]}).json()["focus"] == [0.25, 0.5]
    assert client.get("/api/v1/media").json()[0]["description"] == "لوحة"  # focus update kept the description
    assert client.patch(f"/api/v1/media/{mid}", json={"focus": [1.5, 0.5]}).status_code == 422
    assert client.patch(f"/api/v1/media/{mid}", json={"clear_focus": True}).json()["focus"] is None


def test_style_compare_and_focus_reach_campaign_videos(client, fake_llm, session, tmp_path, isolated_media_library,
                                                       monkeypatch):
    m = isolated_media_library.add("wide.png", png_bytes(1600, 900), "لوحة")
    isolated_media_library.update(m["id"], focus=(0.3, 0.4))
    pid = make_project(client)
    brand = client.get(f"/api/v1/projects/{pid}/brand-brain").json()["brand"]
    assert client.put(f"/api/v1/projects/{pid}/brand", json={**brand, "video_style": "corporate"}).status_code == 200
    data = campaign_payload(n=1)
    it = data["content_items"][0]
    it["media_type"] = "video"
    it["design"] = {**it["design"], "video_scenes": ["أ", "ب"], "video_media": [m["id"], ""],
                    "video_compare": ["", "يدوي|تلقائي"]}
    fake_llm.responses.append(data)
    item = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1, "video_count": 1}).json()["items"][0]
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen.update(style=req.style, compare=req.compare, focus=req.media_focus)
        return renderer.VideoResult(mp4=b"\x00\x00\x00\x18ftypmp42", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    DesignService(session, StubProvider(), LocalStorage(str(tmp_path))).generate(uuid.UUID(item["id"]))
    assert seen == {"style": "corporate", "compare": ["", "يدوي|تلقائي"], "focus": [(0.3, 0.4), None]}
    assert client.put(f"/api/v1/projects/{pid}/brand", json={**brand, "video_style": "neon"}).status_code == 422


def test_studio_style_compare_focus(tmp_path, isolated_media_library, monkeypatch):
    m = isolated_media_library.add("wide.png", png_bytes(1600, 900), "لوحة")
    isolated_media_library.update(m["id"], focus=(0.6, 0.2))
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen.update(style=req.style, compare=req.compare, focus=req.media_focus)
        return renderer.VideoResult(mp4=b"x", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    studio = st.Studio(LocalStorage(str(tmp_path)))
    studio.create(st.VideoSpec(scenes=["أ", "ب"], media=[m["id"], ""], compare=["", "x|y"], style="energetic"),
                  run_async=False)
    assert seen == {"style": "energetic", "compare": ["", "x|y"], "focus": [(0.6, 0.2), None]}
    assert set(st.options()["styles"]) == {"modern", "calm", "energetic", "corporate"}
