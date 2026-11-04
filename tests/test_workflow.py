import asyncio

from app import db
from app.pipeline import run_collect, run_keyword_check
from app.sources import RawItem


class FakeOllama:
    def __init__(self, *args, **kwargs):
        pass

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

    async def judge_keyword_items(self, keyword, items):
        return [
            {
                "related": True,
                "relevance": 92,
                "fake_risk": 5,
                "credibility": 92,
                "sensational": False,
                "claim_type": "official",
                "verdict_reason": "官方发布",
                "summary": "关键词命中摘要",
            }
            for _ in items
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

    monkeypatch.setattr(pipeline, "fetch_scope_sources", fake_scope_sources)
    monkeypatch.setattr(pipeline, "fetch_feed_batch", fake_feed_batch)
    monkeypatch.setattr(pipeline, "OllamaClient", FakeOllama)

    result = asyncio.run(run_collect("test"))
    assert result["status"] == "success"
    assert result["inserted"] == 1
    topics = db.list_topics()
    assert len(topics) == 1
    assert topics[0]["scope_names"] == ["AI 编程"]
    assert topics[0]["verdict"] == "verified"


def test_run_keyword_check_creates_alert(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.create_keyword("Claude Code")

    async def fake_keyword_sources(keyword, settings):
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


def test_keyword_alert_cap(temp_db, monkeypatch):
    import app.pipeline as pipeline

    db.save_settings({"max_alerts_per_keyword_run": 1})
    db.create_keyword("Claude")

    async def fake_keyword_sources(keyword, settings):
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
