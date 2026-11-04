from app import db


def test_index_and_static(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "热点雷达" in response.text
    assert 'id="filterToggle"' in response.text
    assert 'id="sortSelect"' in response.text
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "--paper" in css.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert '"composite"' in js.text


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
    multi_scope = client.get(
        "/api/topics?scope=AI%20%E7%BC%96%E7%A8%8B&scope=%E5%A4%A7%E6%A8%A1%E5%9E%8B"
    ).json()["items"]
    assert len(multi_scope) == 2
    verified = client.get("/api/topics?verdict=verified").json()["items"]
    assert len(verified) == 1
    assert client.get("/api/topics?verdict=hacked").status_code == 400


def test_topics_multi_filter_and_sort(client):
    official = _insert_article(
        url="https://example.com/official",
        title="Official GPT release",
        source_tier="official",
        content_kind="release",
        verdict="verified",
        credibility=92,
        relevance=80,
    )
    community = _insert_article(
        url="https://example.com/community",
        title="Community rumor",
        source_tier="community",
        content_kind="discussion",
        verdict="unverified",
        credibility=42,
        relevance=55,
        also_sources=["v2ex.com"],
    )
    research = _insert_article(
        url="https://example.com/research",
        title="Research paper benchmark",
        source_tier="research",
        content_kind="research",
        verdict="likely",
        credibility=72,
        relevance=95,
        tags=["LLM"],
        also_sources=["arxiv.org"],
    )

    multi_verdict = client.get("/api/topics?verdict=verified&verdict=likely").json()["items"]
    assert {item["id"] for item in multi_verdict} == {official, research}

    multi_tier = client.get("/api/topics?tier=official&tier=community").json()["items"]
    assert {item["id"] for item in multi_tier} == {official, community}

    multi_kind = client.get("/api/topics?kind=release&kind=research").json()["items"]
    assert {item["id"] for item in multi_kind} == {official, research}

    credibility = client.get("/api/topics?sort=credibility").json()["items"]
    assert credibility[0]["id"] == official

    relevance = client.get("/api/topics?sort=relevance").json()["items"]
    assert relevance[0]["id"] == research


def test_topics_range_source_keyword_and_search(client):
    single = _insert_article(
        url="https://example.com/single",
        title="Single source story",
        source_tier="media",
        credibility=65,
        relevance=70,
        verdict="unverified",
    )
    multi = _insert_article(
        url="https://example.com/multi",
        title="Claude Code launch",
        source_tier="official",
        credibility=88,
        relevance=90,
        verdict="verified",
        also_sources=["openai.com", "theverge.com", "techcrunch.com"],
        matched_keywords=["Claude"],
        tags=["编程工具"],
    )
    low = _insert_article(
        url="https://example.com/low",
        title="Low score marketing",
        source_tier="community",
        credibility=30,
        relevance=35,
        verdict="suspicious",
        also_sources=["bilibili.com"],
    )

    ranged = client.get("/api/topics?min_credibility=60&max_credibility=90").json()["items"]
    assert {item["id"] for item in ranged} == {single, multi}

    multi_only = client.get("/api/topics?min_sources=2").json()["items"]
    assert [item["id"] for item in multi_only] == [multi]

    single_only = client.get("/api/topics?single_source=true").json()["items"]
    assert {item["id"] for item in single_only} == {single, low}

    searched = client.get("/api/topics?q=Claude").json()["items"]
    assert [item["id"] for item in searched] == [multi]

    tagged = client.get("/api/topics?q=编程工具").json()["items"]
    assert [item["id"] for item in tagged] == [multi]

    keyworded = client.get("/api/topics?has_keyword=true").json()["items"]
    assert [item["id"] for item in keyworded] == [multi]

    specific = client.get("/api/topics?keyword=Claude").json()["items"]
    assert [item["id"] for item in specific] == [multi]

    source_sorted = client.get("/api/topics?sort=sources").json()["items"]
    assert source_sorted[0]["id"] == multi

    total = client.get("/api/topics?min_credibility=60").json()["total"]
    assert total == 2


def test_topics_default_composite_and_time_field(client):
    from datetime import timedelta

    now = db.now_utc()
    official = _insert_article(
        url="https://example.com/composite-official",
        title="High priority official",
        source_tier="official",
        credibility=95,
        relevance=90,
        verdict="verified",
        published_at=(now - timedelta(days=6)).isoformat(),
    )
    _insert_article(
        url="https://example.com/composite-community",
        title="Fresh community post",
        source_tier="community",
        credibility=50,
        relevance=50,
        verdict="unverified",
        published_at=now.isoformat(),
    )
    composite = client.get("/api/topics").json()["items"]
    assert composite[0]["id"] == official

    old = _insert_article(
        url="https://example.com/old-published",
        title="Old published but newly fetched",
        source_tier="media",
        credibility=70,
        relevance=70,
        verdict="likely",
        published_at="2020-01-01T00:00:00+00:00",
        fetched_at=now.isoformat(),
    )
    default_ids = {item["id"] for item in client.get("/api/topics?limit=100").json()["items"]}
    assert old not in default_ids
    fetched_ids = {
        item["id"]
        for item in client.get("/api/topics?limit=100&time_field=fetched").json()["items"]
    }
    assert old in fetched_ids


def test_topics_filter_validation(client):
    assert client.get("/api/topics?sort=random").status_code == 400
    assert client.get("/api/topics?time_field=unknown").status_code == 400
    assert client.get("/api/topics?tier=unknown").status_code == 400
    assert client.get("/api/topics?kind=unknown").status_code == 400
    assert client.get("/api/topics?match_level=unknown").status_code == 400
    assert client.get("/api/topics?min_credibility=80&max_credibility=60").status_code == 400
    assert client.get("/api/topics?single_source=true&min_sources=2").status_code == 400


def test_keyword_match_fields_and_filter(client):
    article_id = _insert_article(
        url="https://example.com/keyword-match",
        title="Anthropic launches Claude Sonnet 4.6",
        scope_names=["AI 编程"],
    )
    db.upsert_keyword_match(
        article_id,
        "Claude Sonnet 4.6",
        "strong",
        ["Claude", "Sonnet", "4.6"],
        [],
        "Anthropic launches Claude Sonnet 4.6",
        "命中全部核心锚点",
        "Anthropic 发布新模型。",
        '"Claude Sonnet 4.6"',
        "plan123",
        92,
        88,
    )
    db.create_alert(article_id, "Claude Sonnet 4.6", "critical", "Anthropic launches Claude Sonnet 4.6", "强匹配")

    strong = client.get("/api/topics?match_level=strong").json()["items"]
    assert [item["id"] for item in strong] == [article_id]
    assert strong[0]["best_match"]["match_level"] == "strong"

    weak = client.get("/api/topics?match_level=weak").json()["items"]
    assert weak == []

    detail = client.get(f"/api/topics/{article_id}").json()
    assert detail["best_match"]["match_level"] == "strong"
    assert detail["keyword_matches"][0]["matched_anchors"] == ["Claude", "Sonnet", "4.6"]

    alerts = client.get("/api/alerts").json()
    assert alerts[0]["match_level"] == "strong"
    assert alerts[0]["matched_anchors"] == ["Claude", "Sonnet", "4.6"]
    assert alerts[0]["match_evidence"] == "Anthropic launches Claude Sonnet 4.6"
    assert alerts[0]["match_reason"] == "命中全部核心锚点"


def test_match_level_filter_includes_keyword_only(client):
    article_id = _insert_article(
        url="https://example.com/keyword-only-match",
        title="Claude Sonnet 4.6 shipped",
        scope_names=[],
        matched_keywords=["Claude Sonnet 4.6"],
    )
    db.upsert_keyword_match(
        article_id,
        "Claude Sonnet 4.6",
        "strong",
        ["Claude", "Sonnet", "4.6"],
        [],
        "Claude Sonnet 4.6 shipped",
        "命中全部核心锚点",
        "摘要",
        '"Claude Sonnet 4.6"',
        "plan123",
        90,
        80,
    )
    items = client.get("/api/topics?match_level=strong").json()["items"]
    assert [item["id"] for item in items] == [article_id]
    default_items = client.get("/api/topics").json()["items"]
    assert default_items == []


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
