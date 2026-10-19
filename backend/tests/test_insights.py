"""The learning loop: results of published posts -> lessons -> fed into the next campaign / video."""
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.ai.context import build_campaign_context
from app.config import settings
from app.designs.canva.crypto import encrypt
from app.insights import lessons, metrics
from app.models import PostMetric, Project, Publication, SocialConnection
from app.social import meta
from app.storage.local import LocalStorage
from app.studio import service as st
from tests.test_publishing import approved  # noqa: F401  (fixture)

LESSON = {"summary": "الفيديوهات التي تبدأ بسؤال تفوقت", "do": ["ابدأ بسؤال عن الألم"], "avoid": ["المقدمات الطويلة"],
          "winning_hooks": ["بتضيع كام ساعة؟"]}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "k" * 40)


def _published(session, pid, item_id, platform="instagram", ext="M1") -> Publication:
    pub = Publication(content_item_id=uuid.UUID(item_id), project_id=uuid.UUID(pid), platform=platform,
                      provider=platform, status="published", scheduled_at=datetime.now(timezone.utc),
                      published_at=datetime.now(timezone.utc), external_id=ext)
    session.add(pub)
    session.commit()
    return pub


def test_score_weighs_saves_and_shares():
    assert lessons.score({"reach": 1000, "likes": 50, "comments": 5, "shares": 2, "saves": 4}) == 7.8
    assert lessons.score({"likes": 3}) is None


def test_insights_fall_back_to_one_metric_at_a_time():
    asked = []

    def handler(req: httpx.Request):
        m = req.url.params.get("metric")
        if m is None:
            return httpx.Response(200, json={"like_count": 9, "comments_count": 2, "media_product_type": "REELS"})
        asked.append(m)
        if "," in m or m == "views":  # one unsupported metric fails the whole batch
            return httpx.Response(400, json={"error": {"message": "bad metric", "code": 100}})
        val = {"reach": 500, "saved": 7, "shares": 3, "ig_reels_avg_watch_time": 4200}[m]
        return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": val}]}]})

    client = meta.MetaClient(httpx.Client(transport=httpx.MockTransport(handler)))
    out = client.ig_media_stats("M1", "T")
    assert out == {"likes": 9, "comments": 2, "reach": 500, "saves": 7, "shares": 3, "avg_watch_ms": 4200}
    assert "," in asked[0] and len(asked) == 6


class StubMeta:
    def __init__(self, fail=False):
        self.fail = fail

    def ig_media_stats(self, media_id, token):
        if self.fail:
            raise meta.MetaError("down")
        return {"likes": 40, "comments": 4, "reach": 800, "views": 1200, "saves": 6, "avg_watch_ms": 5100}


def test_fetch_meta_saves_numbers_and_never_overwrites_manual_ones(client, approved, session):  # noqa: F811
    pid, body = approved
    session.add(SocialConnection(project_id=uuid.UUID(pid), platform="instagram", account_id="IG1", account_name="x",
                                 token_enc=encrypt("T")))
    session.commit()
    pub = _published(session, pid, body["items"][0]["id"])
    row = metrics.fetch_meta(session, pub, StubMeta())
    assert (row.reach, row.likes, row.avg_watch_s, row.source) == (800, 40, 5.1, "meta")
    assert metrics.refresh(session, uuid.UUID(pid), StubMeta()) == {"updated": 1, "failed": 0}

    metrics.save_manual(session, pub, {"reach": 999})
    metrics.fetch_meta(session, pub, StubMeta(fail=True))
    row = session.get(PostMetric, row.id)
    assert row.reach == 999 and row.source == "manual"


def test_results_manual_entry_lessons_and_campaign_context(client, approved, session, fake_llm):  # noqa: F811
    pid, body = approved
    pubs = [_published(session, pid, it["id"], "linkedin", f"L{i}") for i, it in enumerate(body["items"])]
    r = client.put(f"/api/v1/publications/{pubs[0].id}/metrics", json={"views": 2000, "reach": 1500, "likes": 90, "saves": 12})
    assert r.status_code == 200 and r.json()["score"] == pytest.approx(8.4)
    # one result is not enough evidence
    assert client.post(f"/api/v1/projects/{pid}/lessons").status_code == 422
    client.put(f"/api/v1/publications/{pubs[1].id}/metrics", json={"reach": 1000, "likes": 10})
    res = client.get(f"/api/v1/projects/{pid}/results").json()
    assert len(res["results"]) == 2 and res["lessons"] is None

    fake_llm.responses.append(LESSON)
    out = client.post(f"/api/v1/projects/{pid}/lessons").json()
    assert out["do"] == ["ابدأ بسؤال عن الألم"] and out["based_on"] == 2
    assert '"score": 8.4' in fake_llm.calls[-1].user_message
    assert client.get(f"/api/v1/projects/{pid}/results").json()["lessons"]["summary"] == LESSON["summary"]

    project = session.get(Project, uuid.UUID(pid))
    ctx = build_campaign_context(session, project, "حملة جديدة")
    assert "<performance_lessons>" in ctx.stable and "بتضيع كام ساعة؟" in ctx.stable


def test_manual_metrics_need_a_published_post(client, approved, session):  # noqa: F811
    pid, body = approved
    pub = _published(session, pid, body["items"][0]["id"])
    pub.status = "scheduled"
    session.commit()
    assert client.put(f"/api/v1/publications/{pub.id}/metrics", json={"likes": 1}).status_code == 409


def test_studio_results_and_lessons_feed_the_next_script(client, fake_llm, tmp_path, monkeypatch):
    studio = st.Studio(LocalStorage(str(tmp_path)))
    monkeypatch.setattr(st, "_studio", studio)
    ids = []
    for k in range(2):
        vid = f"v{k}"
        studio._save({"id": vid, "title": f"t{k}", "status": "ready", "progress": 1, "error": None, "created_at": k,
                      "group": "g", "variant": "AB"[k], "spec": {"scenes": [f"افتتاحية {k}"], "style": "modern", "aspect": "9:16"},
                      "width": 1080, "height": 1920, "duration_ms": 20000})
        ids.append(vid)
    r = client.put(f"/api/v1/studio/videos/{ids[0]}/metrics", json={"views": 3000, "likes": 120, "completion": 0.4})
    assert r.status_code == 200 and r.json()["metrics"]["score"] == 4.0
    client.put(f"/api/v1/studio/videos/{ids[1]}/metrics", json={"views": 1000, "likes": 10, "completion": 0.2})
    fake_llm.responses.append(LESSON)
    assert client.post("/api/v1/studio/lessons").json()["winning_hooks"] == ["بتضيع كام ساعة؟"]
    sent = fake_llm.calls[-1].user_message
    assert '"variant": "A"' in sent and "افتتاحية 0" in sent
    assert client.get("/api/v1/studio/lessons").json()["lessons"]["based_on"] == 2

    fake_llm.responses.append({"title": "x", "scenes": ["a"], "broll": ["b"], "cta": "c"})
    client.post("/api/v1/studio/script", json={"prompt": "فيديو لمعين"})
    assert "<performance_lessons>" in fake_llm.calls[-1].stable_context
