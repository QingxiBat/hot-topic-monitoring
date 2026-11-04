from app import db


def test_index_and_static(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "热点雷达" in response.text
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "--paper" in css.text


def test_dashboard_defaults(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert len(data["scopes"]) == 10
    assert data["settings"]["ollama_model"] == "qwen3:8b"
    assert "stats" in data


def test_scope_crud(client):
    created = client.post(
        "/api/scopes",
        json={"name": "AI 芯片", "query": "AI chip NVIDIA", "description": "芯片动态"},
    )
    assert created.status_code == 201
    scope_id = created.json()["id"]
    duplicate = client.post("/api/scopes", json={"name": "AI 芯片", "query": "x"})
    assert duplicate.status_code == 409
    updated = client.put(f"/api/scopes/{scope_id}", json={"description": "更新后的描述"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "更新后的描述"
    deleted = client.delete(f"/api/scopes/{scope_id}")
    assert deleted.status_code == 200


def test_keyword_crud(client):
    created = client.post("/api/keywords", json={"term": "GPT-5"})
    assert created.status_code == 201
    keyword_id = created.json()["id"]
    assert any(item["term"] == "GPT-5" for item in client.get("/api/keywords").json())
    duplicate = client.post("/api/keywords", json={"term": "gpt-5"})
    assert duplicate.status_code == 409
    toggled = client.patch(f"/api/keywords/{keyword_id}?enabled=false")
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False
    deleted = client.delete(f"/api/keywords/{keyword_id}")
    assert deleted.status_code == 200


def test_settings_validation_and_update(client):
    updated = client.put(
        "/api/settings",
        json={
            "collect_interval_minutes": 12,
            "relevance_threshold": 70,
            "max_alerts_per_keyword_run": 2,
            "auto_collect": False,
        },
    )
    assert updated.status_code == 200
    data = updated.json()
    assert data["collect_interval_minutes"] == 12
    assert data["relevance_threshold"] == 70
    assert data["auto_collect"] is False
    invalid_url = client.put("/api/settings", json={"webhook_url": "ftp://example.com"})
    assert invalid_url.status_code == 422
    invalid_type = client.put("/api/settings", json={"webhook_type": "slack"})
    assert invalid_type.status_code == 422
    invalid_interval = client.put("/api/settings", json={"collect_interval_minutes": 1})
    assert invalid_interval.status_code == 422


def _insert_article(**overrides):
    article = {
        "url": "https://example.com/story",
        "title": "Example story",
        "summary": "Summary",
        "source": "Example",
        "source_domain": "example.com",
        "published_at": db.now_iso(),
        "fetched_at": db.now_iso(),
        "scope_names": ["AI 编程"],
        "tags": ["AI"],
        "relevance": 85,
        "credibility": 80,
        "verdict": "verified",
        "verdict_reason": "官方发布",
        "ai_summary": "AI 摘要",
        "claim_type": "official",
        "matched_keywords": [],
        "also_sources": ["example.com"],
        "created_at": db.now_iso(),
    }
    article.update(overrides)
    return db.insert_article(article)


def test_topics_exclude_keyword_only(client):
    scope_id = _insert_article(url="https://example.com/scope", title="Scope topic")
    keyword_id = _insert_article(
        url="https://example.com/keyword",
        title="Keyword topic",
        scope_names=[],
        matched_keywords=["Claude"],
    )
    response = client.get("/api/topics")
    items = response.json()["items"]
    assert [item["id"] for item in items] == [scope_id]
    response_all = client.get("/api/topics?include_keyword_only=true")
    ids = {item["id"] for item in response_all.json()["items"]}
    assert ids == {scope_id, keyword_id}


def test_topic_scope_and_verdict_filter(client):
    _insert_article(url="https://example.com/1", title="AI coding", verdict="verified")
    _insert_article(
        url="https://example.com/2",
        title="Other scope",
        scope_names=["大模型"],
        verdict="suspicious",
    )
    filtered = client.get("/api/topics?scope=AI%20%E7%BC%96%E7%A8%8B").json()["items"]
    assert len(filtered) == 1
    verified = client.get("/api/topics?verdict=verified").json()["items"]
    assert len(verified) == 1
    assert client.get("/api/topics?verdict=hacked").status_code == 400


def test_alerts_join_and_mark_read(client):
    article_id = _insert_article()
    alert = db.create_alert(article_id, "Claude", "critical", "Example story", "官方发布")
    assert alert is not None
    alerts = client.get("/api/alerts").json()
    assert alerts[0]["article_url"] == "https://example.com/story"
    assert alerts[0]["credibility"] == 80
    assert alerts[0]["read"] is False
    marked = client.post(f"/api/alerts/{alert['id']}/read")
    assert marked.status_code == 200
    assert client.get("/api/alerts").json()[0]["read"] is True
    read_all = client.post("/api/alerts/read-all")
    assert read_all.status_code == 200


def test_webhook_test_without_url(client):
    response = client.post("/api/webhook/test")
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_manual_triggers(client):
    assert client.post("/api/collect/run").status_code == 202
    assert client.post("/api/keywords/check").status_code == 202
