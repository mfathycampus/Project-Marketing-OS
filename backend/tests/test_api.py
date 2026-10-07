import copy

GOOD_ITEM = {
    "type": "social_post", "platform": "instagram", "day_offset": 0,
    "headline": "عرض نهاية الأسبوع", "caption": "تعالوا مع العائلة", "cta": "اطلب الآن",
    "design": {"template_key": "offer_square", "fields": {"PRICE": "49 ريال"}},
}


def campaign_payload(n=2, **over):
    items = []
    for i in range(n):
        it = copy.deepcopy(GOOD_ITEM)
        it["day_offset"] = i
        items.append(it)
    data = {"name": "حملة نهاية الأسبوع", "objective": "increase_orders", "strategy": "s", "content_items": items}
    data.update(over)
    return data


def make_project(client):
    pid = client.post("/api/v1/projects", json={"name": "مطاعم المذاق"}).json()["id"]
    client.put(f"/api/v1/projects/{pid}/brand", json={"name": "المذاق", "tone": "ودية", "language": "ar"})
    client.put(f"/api/v1/projects/{pid}/audience", json={"demographics": "عائلات الرياض"})
    client.post(f"/api/v1/projects/{pid}/products", json={"name": "وجبة عائلية", "price": "49 ريال"})
    client.post(f"/api/v1/projects/{pid}/rules", json={"kind": "forbidden_word", "text": "مجاني"})
    return pid


REQ = {"objective": "increase_orders", "duration_days": 7, "post_count": 2, "platforms": ["instagram"]}


def test_project_lifecycle(client):
    pid = make_project(client)
    assert client.patch(f"/api/v1/projects/{pid}", json={"name": "X"}).json()["name"] == "X"
    assert client.post(f"/api/v1/projects/{pid}/archive").json()["status"] == "archived"
    assert client.get("/api/v1/projects").json() == []
    assert client.delete(f"/api/v1/projects/{pid}").status_code == 204
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404


def test_generate_campaign(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload())
    r = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["items"]) == 2 and body["items"][0]["status"] == "ai_generated"
    sent = fake_llm.calls[0]
    assert "وجبة عائلية" in sent.user_message and "عائلات الرياض" in sent.stable_context


def test_invalid_output_is_repaired_once(client, fake_llm):
    pid = make_project(client)
    fake_llm.responses += [campaign_payload(n=1), campaign_payload()]
    r = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ)
    assert r.status_code == 201
    assert len(fake_llm.calls) == 2


def test_forbidden_word_rejected(client, fake_llm):
    pid = make_project(client)
    bad = campaign_payload()
    bad["content_items"][0]["caption"] = "توصيل مجاني"
    fake_llm.responses += [bad, bad]
    r = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ)
    assert r.status_code == 502


def test_platform_caption_limit(client, fake_llm):
    pid = make_project(client)
    bad = campaign_payload()
    bad["content_items"][0]["platform"] = "x"
    bad["content_items"][0]["caption"] = "a" * 300
    fake_llm.responses += [bad, bad]
    r = client.post(f"/api/v1/projects/{pid}/campaigns", json={**REQ, "platforms": ["x"]})
    assert r.status_code == 502


def test_ai_run_recorded_and_transition_api(client, fake_llm, session):
    from app.models import AIRun
    pid = make_project(client)
    fake_llm.responses.append(campaign_payload())
    body = client.post(f"/api/v1/projects/{pid}/campaigns", json=REQ).json()
    run = session.query(AIRun).one()
    assert run.status == "ok" and run.prompt_version == "campaign.v1"
    item = body["items"][0]["id"]
    assert client.post(f"/api/v1/content-items/{item}/transition", params={"to": "approved"}).status_code == 409
    assert client.post(f"/api/v1/content-items/{item}/transition", params={"to": "design_pending"}).status_code == 200
