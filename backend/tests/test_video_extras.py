import io
import uuid

import pytest
from PIL import Image

from app.designs.service import DesignService
from app.storage.local import LocalStorage
from app.studio import service as st
from app.video import renderer
from app.video.renderer import _stat_text, load_logo, parse_emphasis, parse_stat
from tests.test_api import REQ, campaign_payload, make_project
from tests.test_designs import StubProvider


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 4)


def test_emphasis_is_removed_for_voice_and_kept_for_colour():
    assert parse_emphasis("وفّر *ساعتين* يوميًا") == ("وفّر ساعتين يوميًا", {"ساعتين"})
    assert parse_emphasis("*مرة واحدة* فقط!") == ("مرة واحدة فقط!", {"مرة", "واحدة"})
    assert parse_emphasis("بدون تمييز") == ("بدون تمييز", set())


def test_stat_parsing_and_count_up():
    s = parse_stat("+80%|أسرع في التوثيق")
    assert s[:4] == ("+", 80.0, 0, "%") and s[4] == "أسرع في التوثيق"
    assert _stat_text(s, 0) == "+0%" and _stat_text(s, 0.5) == "+40%" and _stat_text(s, 1) == "+80%"
    assert _stat_text(parse_stat("٥|ساعات"), 1) == "٥"  # Arabic-Indic digits stay Arabic-Indic
    assert _stat_text(parse_stat("4.5|نجوم"), 1) == "4.5"
    assert _stat_text(parse_stat("12000|مستخدم"), 1) == "12,000"
    assert parse_stat("") is None and parse_stat("بدون رقم|x") is None


def test_logo_badge_for_opaque_and_transparent_logos(tmp_path):
    opaque = tmp_path / "o.jpg"
    Image.new("RGB", (800, 400), "red").save(opaque)
    lg = load_logo(str(opaque), 300, 120)
    assert lg.width <= 340 and lg.getpixel((20, 20))[3] < 255  # rounded corner is transparent
    assert load_logo(str(tmp_path / "missing.png"), 300, 120) is None


def test_video_with_stat_emphasis_and_logo(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (400, 160), (245, 158, 11, 255)).save(logo)
    res = renderer.VideoRenderer().render(renderer.VideoRequest(
        ["كم *ساعة* تضيع؟", "معين يوفّر عليك", "اطلب"], cta="اطلب", brand_name="معين", logo=str(logo),
        stats=["", "5|ساعات أسبوعيًا", ""]))
    assert b"ftyp" in res.mp4[:64]
    assert [c[2] for c in res.captions] == ["كم ساعة تضيع؟", "معين يوفّر عليك", "اطلب"]  # no asterisks in SRT


def test_brand_logo_and_stats_reach_the_campaign_video(client, fake_llm, session, tmp_path, isolated_media_library,
                                                       monkeypatch):
    buf = io.BytesIO()
    Image.new("RGBA", (300, 100), "orange").save(buf, "PNG")
    logo = isolated_media_library.add("logo.png", buf.getvalue(), "شعار معين")
    pid = make_project(client)
    brand = client.get(f"/api/v1/projects/{pid}/brand-brain").json()["brand"]
    assert client.put(f"/api/v1/projects/{pid}/brand", json={**brand, "logo_media_id": logo["id"]}).status_code == 200
    data = campaign_payload(n=1)
    it = data["content_items"][0]
    it["media_type"] = "video"
    it["design"] = {**it["design"], "video_scenes": ["وفّر *وقتك*", "ب"], "video_stats": ["", "5|ساعات"]}
    fake_llm.responses.append(data)
    item = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "post_count": 1, "video_count": 1}).json()["items"][0]
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen.update(stats=req.stats, logo=req.logo, scenes=req.scenes)
        return renderer.VideoResult(mp4=b"\x00\x00\x00\x18ftypmp42", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    DesignService(session, StubProvider(), LocalStorage(str(tmp_path))).generate(uuid.UUID(item["id"]))
    assert seen["stats"] == ["", "5|ساعات"] and seen["logo"].endswith("source.png")
    assert seen["scenes"][0] == "وفّر *وقتك*"  # marks reach the renderer, which strips them for the voice
    r = client.patch(f"/api/v1/content-items/{item['id']}", json={"video_stats": ["7|أيام", ""]})
    assert r.json()["design"]["video_stats"] == ["7|أيام", ""]


def test_studio_passes_stats_and_logo(tmp_path, isolated_media_library, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGBA", (300, 100), "orange").save(buf, "PNG")
    logo = isolated_media_library.add("logo.png", buf.getvalue(), "logo")
    seen = {}

    def fake_render(self, req, on_progress=None):
        seen.update(stats=req.stats, logo=req.logo)
        return renderer.VideoResult(mp4=b"x", width=1080, height=1920, duration_ms=1000)

    monkeypatch.setattr(renderer.VideoRenderer, "render", fake_render)
    studio = st.Studio(LocalStorage(str(tmp_path)))
    meta = studio.create(st.VideoSpec(scenes=["أ", "ب"], stats=["", "3|أيام"], logo=logo["id"]), run_async=False)
    assert meta["status"] == "ready" and seen["stats"] == ["", "3|أيام"] and seen["logo"].endswith("source.png")
