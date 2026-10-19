import io
import subprocess
import uuid

import pytest
from PIL import Image

from app.designs.service import DesignService
from app.models import ContentItem
from app.studio import service as st
from app.video import renderer
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider


def png(w=540, h=1200, color="teal") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def mp4(tmp_path, size="360x780", seconds=1) -> bytes:
    out = tmp_path / "rec.mp4"
    subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=15", "-t", str(seconds), str(out)], check=True)
    return out.read_bytes()


def test_media_library_api(client, tmp_path):
    r = client.post("/api/v1/media", files={"file": ("home.png", png(), "image/png")},
                    data={"description": "الشاشة الرئيسية: قائمة المستندات"})
    assert r.status_code == 201, r.text
    img = r.json()
    assert img["kind"] == "image" and (img["width"], img["height"]) == (540, 1200)
    r = client.post("/api/v1/media", files={"file": ("demo.mp4", mp4(tmp_path), "video/mp4")}, data={"description": "تسجيل"})
    vid = r.json()
    assert vid["kind"] == "video" and (vid["width"], vid["height"]) == (360, 780) and 900 <= vid["duration_ms"] <= 1200
    assert [m["id"] for m in client.get("/api/v1/media").json()] == [vid["id"], img["id"]]
    assert client.get(f"/api/v1/media/{vid['id']}/poster").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/v1/media/{img['id']}/file").content.startswith(b"\x89PNG")
    assert client.patch(f"/api/v1/media/{img['id']}", json={"description": "جديد"}).json()["description"] == "جديد"
    bad = client.post("/api/v1/media", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 422 and "غير مدعوم" in bad.json()["detail"]
    assert client.delete(f"/api/v1/media/{img['id']}").status_code == 204
    assert client.get(f"/api/v1/media/{img['id']}/file").status_code == 404
    assert client.get("/api/v1/media/..%2F..%2Fetc/file").status_code == 404


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)


@pytest.mark.parametrize("aspect", ["9:16", "1:1", "16:9", "4:5"])
def test_phone_mockup_with_screenshot_and_recording(tmp_path, aspect):
    shot = tmp_path / "s.png"
    shot.write_bytes(png(1080, 3000, "white"))
    rec = tmp_path / "r.mp4"
    rec.write_bytes(mp4(tmp_path))
    res = renderer.VideoRenderer().render(renderer.VideoRequest(
        ["افتتاحية", "شاشة التطبيق", "تسجيل الشاشة", "اطلب"], cta="اطلب", aspect=aspect,
        media=["", str(shot), str(rec), ""]))
    w, h = renderer.SIZES[aspect]
    assert (res.width, res.height) == (w, h) and b"ftyp" in res.mp4[:64]


def test_phone_shows_the_screenshot(tmp_path):
    shot = tmp_path / "red.png"
    shot.write_bytes(png(1080, 2300, (255, 0, 0)))
    res = renderer.VideoRenderer().render(renderer.VideoRequest(["أ"], media=[str(shot)]))
    geo = renderer._phone_layout(1080, 1920, 90, True)
    px, py, pw, ph = geo["phone"]
    frame = subprocess.run([renderer.ffmpeg_exe(), "-v", "error", "-i", "-", "-vf", "select=eq(n\\,6)", "-vframes", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], input=res.mp4, capture_output=True).stdout
    x, y = px + pw // 2, py + ph // 2
    r, g, b = frame[(y * 1080 + x) * 3:(y * 1080 + x) * 3 + 3]
    assert r > 200 and g < 60 and b < 60  # the red screenshot is inside the phone


def test_studio_script_gets_library_and_drops_unknown_ids(fake_llm, isolated_media_library):
    m = isolated_media_library.add("home.png", png(), "الشاشة الرئيسية")
    fake_llm.responses.append({"title": "t", "brand_name": "", "scenes": ["أ", "ب", "ج"], "broll": ["", "", ""],
                               "cta": "اطلب", "media": ["", m["id"], "made-up-id"]})
    out = st.write_script(fake_llm, "فيديو لتطبيق معين", "9:16", "short")
    assert out["media"] == ["", m["id"], ""]
    assert "<media_library>" in fake_llm.calls[0].user_message and m["id"] in fake_llm.calls[0].user_message


def test_campaign_video_uses_library_media(client, fake_llm, session, tmp_path, isolated_media_library, monkeypatch):
    m = isolated_media_library.add("home.png", png(), "الشاشة الرئيسية")
    pid = make_project(client)
    data = campaign_payload(n=1)
    it = data["content_items"][0]
    it["media_type"] = "video"
    it["design"] = {**it["design"], "video_scenes": ["أ", "ب"], "video_media": [m["id"], "bogus"]}
    fake_llm.responses.append(data)
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1, "video_count": 1}).json()
    assert "<media_library>" in fake_llm.calls[0].user_message
    item = body["items"][0]
    assert item["design"]["video_media"] == [m["id"], ""]
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen["media"] = req.media
        return renderer.VideoResult(mp4=b"\x00\x00\x00\x18ftypmp42", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    from app.storage.local import LocalStorage

    DesignService(session, StubProvider(), LocalStorage(str(tmp_path))).generate(uuid.UUID(item["id"]))
    assert seen["media"][0].endswith("source.png") and seen["media"][1] == ""
    # the media of a scene can be changed from the edit dialog (positional, empty slots kept)
    r = client.patch(f"/api/v1/content-items/{item['id']}", json={"video_media": ["", m["id"]]})
    assert r.json()["design"]["video_media"] == ["", m["id"]]
