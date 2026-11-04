import asyncio

from app import db
from app.pipeline import ingest_scope_items, run_collect, run_keyword_check
from app.sources import RawItem


class FakeOllama:
    def __init__(self, *args, **kwargs):
        pass

    async def build_query_plan(self, keyword):
        return {}

    async def classify_items(self, items, scopes):
        return [
            {
                "scopes": ["AI 编程"],
                "relevance": 88,
                "credibility": 86,
                "sensational": False,
                "claim_type": "official",
                "summary": "一条测试摘要",
                "tags": ["测试"],
            }
            for _ in items
        ]

    async def judge_keyword_items(self, keyword, items, plan=None):
        return [
            {
                "related": True,
                "match_level": "strong",
                "matched_anchors": [keyword],
                "missing_anchors": [],
                "evidence": item.get("title", ""),
                "content_summary": "关键词命中摘要",
                "match_reason": "标题命中关键词",
                "relevance": 92,
                "fake_risk": 5,
                "credibility": 92,
                "sensational": False,
                "claim_type": "official",
                "verdict_reason": "官方发布",
                "summary": "关键词命中摘要",
            }
            for item in items
        ]


def test_run_collect_persists_topics(temp_db, monkeypatch):
    import app.pipeline as pipeline

    async def fake_scope_sources(scope, settings):
        return (
            [
                RawItem(
                    title="OpenAI releases a new coding model",
                    url="https://openai.com/index/new-coding-model",
                    summary="Official announcement",
                    source="OpenAI News",
                    source_domain="openai.com",
                    published_at=db.now_iso(),
                    origin="feed",
                )
            ],
            [],
        )

    async def fake_feed_batch(feeds, limit, freshness):
        return ([], [])

    async def fake_custom_sources(settings):
        return ([], [])

    monkeypatch.setattr(pipeline, "fetch_scope_sources", fake_scope_sources)
    monkeypatch.setattr(pipeline, "fetch_feed_batch", fake_feed_batch)
    monkeypatch.setattr(pipeline, "fetch_custom_sources", fake_custom_sources)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    result = asyncio.run(run_collect("test"))
    assert result["status"] == "success"
    assert result["inserted"] == 1
    topics = db.list_topics()
    assert len(topics) == 1
    assert topics[0]["scope_names"] == ["AI 编程"]
    assert topics[0]["verdict"] == "verified"


def test_ingest_scope_items_preserves_source_metadata(temp_db, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)
    item = RawItem(
        title="AI Agent 实战视频",
        url="https://www.bilibili.com/video/BVtest",
        summary="社区视频",
        source="B站科技区排行",
        source_domain="bilibili.com",
        published_at=db.now_iso(),
        source_tier="community",
        content_kind="video",
    )
    stats = asyncio.run(
        ingest_scope_items([item], db.list_scopes(enabled_only=True), db.get_settings(), "test")
    )
    assert stats["inserted"] == 1
    topic = db.list_topics(include_keyword_only=True)[0]
    assert topic["source_tier"] == "community"
    assert topic["content_kind"] == "video"
    assert topic["verdict"] == "unverified"


def test_run_keyword_check_creates_alert(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.create_keyword("Claude Code")

    async def fake_keyword_sources(keyword, settings, plan=None):
        return (
            [
                RawItem(
                    title="Anthropic announces Claude Code update",
                    url="https://openai.com/index/claude-code-update",
                    summary="Official announcement",
                    source="OpenAI News",
                    source_domain="openai.com",
                    published_at=db.now_iso(),
                    origin="feed",
                )
            ],
            [],
        )

    monkeypatch.setattr(pipeline, "fetch_keyword_sources", fake_keyword_sources)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    result = asyncio.run(run_keyword_check("test"))
    assert result["status"] == "success"
    assert result["alerts"] == 1
    alerts = db.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["level"] == "critical"
    assert alerts[0]["keyword"] == "Claude Code"


def test_keyword_query_expansion_filters_unrelated_content(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.create_keyword("Claude Sonnet 4.6")

    async def fake_keyword_sources(keyword, settings, plan=None):
        return (
            [
                RawItem(
                    title="Anthropic announces Claude Sonnet 4.6",
                    url="https://openai.com/index/sonnet",
                    summary="New model release",
                    source="OpenAI News",
                    source_domain="openai.com",
                    published_at=db.now_iso(),
                ),
                RawItem(
                    title="OpenClaw is a new agent",
                    url="https://example.com/openclaw",
                    summary="A separate open-source agent project",
                    source="Example",
                    source_domain="example.com",
                    published_at=db.now_iso(),
                ),
                RawItem(
                    title="Claude Code update",
                    url="https://example.com/claude-code",
                    summary="A coding tool update",
                    source="Example",
                    source_domain="example.com",
                    published_at=db.now_iso(),
                ),
                RawItem(
                    title="Claude Sonnet gets an update",
                    url="https://example.com/sonnet-update",
                    summary="The model family gets a minor update",
                    source="Example",
                    source_domain="example.com",
                    published_at=db.now_iso(),
                ),
            ],
            [],
        )

    monkeypatch.setattr(pipeline, "fetch_keyword_sources", fake_keyword_sources)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    result = asyncio.run(run_keyword_check("test"))
    assert result["status"] == "success"
    assert result["alerts"] == 1
    assert result["filtered"] == 1
    assert result["weak"] == 1

    topics = db.list_topics(include_keyword_only=True, limit=100)
    levels = {
        topic["title"]: (topic.get("best_match") or {}).get("match_level")
        for topic in topics
    }
    assert "OpenClaw is a new agent" not in levels
    assert levels["Claude Code update"] == "weak"
    assert levels["Claude Sonnet gets an update"] == "partial"
    assert levels["Anthropic announces Claude Sonnet 4.6"] == "exact"

    alerts = db.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["match_level"] == "exact"
    assert "Claude" in alerts[0]["matched_anchors"]
    assert alerts[0]["match_evidence"]


def test_keyword_repeat_run_does_not_consume_alert_slots(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.create_keyword("Claude")
    state = {"items": [RawItem(title="Claude update one", url="https://example.com/one", source="Example", source_domain="example.com", published_at=db.now_iso())]}

    async def fake_keyword_sources(keyword, settings, plan=None):
        return (list(state["items"]), [])

    monkeypatch.setattr(pipeline, "fetch_keyword_sources", fake_keyword_sources)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    first = asyncio.run(run_keyword_check("test"))
    assert first["alerts"] == 1
    state["items"].append(
        RawItem(title="Claude update two", url="https://example.com/two", source="Example", source_domain="example.com", published_at=db.now_iso())
    )
    second = asyncio.run(run_keyword_check("test"))
    assert second["alerts"] == 1
    alerts = db.list_alerts()
    assert len(alerts) == 2
    assert {alert["title"] for alert in alerts} == {"Claude update one", "Claude update two"}


def test_keyword_alert_cap(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.save_settings({"max_alerts_per_keyword_run": 1})
    db.create_keyword("Claude")

    async def fake_keyword_sources(keyword, settings, plan=None):
        return (
            [
                RawItem(
                    title="Claude Code update one",
                    url="https://openai.com/index/one",
                    source="OpenAI News",
                    source_domain="openai.com",
                    published_at=db.now_iso(),
                ),
                RawItem(
                    title="Claude Code update two",
                    url="https://openai.com/index/two",
                    source="OpenAI News",
                    source_domain="openai.com",
                    published_at=db.now_iso(),
                ),
            ],
            [],
        )

    monkeypatch.setattr(pipeline, "fetch_keyword_sources", fake_keyword_sources)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    result = asyncio.run(run_keyword_check("test"))
    assert result["alerts"] == 1
    assert len(db.list_alerts()) == 1
    assert len(db.list_topics(include_keyword_only=True)) == 2
