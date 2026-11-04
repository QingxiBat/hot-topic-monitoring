import asyncio

from app.llm import OllamaClient, _clamp, _extract_json


def test_extract_json_plain():
    assert _extract_json('{"items": []}') == {"items": []}


def test_extract_json_code_fence():
    assert _extract_json('```json\n{"items": [1]}\n```') == {"items": [1]}


def test_extract_json_surrounding_text():
    assert _extract_json('好的，结果如下：{"items": [1, 2]} 完毕') == {"items": [1, 2]}


def test_clamp():
    assert _clamp("120") == 100
    assert _clamp(-5) == 0
    assert _clamp("bad", default=42) == 42


def test_normalize_classification_filters_scopes():
    client = OllamaClient("http://localhost:11434", "qwen3:8b")
    result = client._normalize_classification(
        {
            "scopes": ["AI 编程", "不存在的范围"],
            "relevance": 88,
            "credibility": 80,
            "sensational": "false",
            "claim_type": "official",
            "summary": "摘要",
            "tags": "标签1，标签2",
        },
        ["AI 编程", "大模型"],
        {"title": "Title", "summary": "Raw"},
    )
    assert result["scopes"] == ["AI 编程"]
    assert result["claim_type"] == "official"
    assert result["tags"] == ["标签1", "标签2"]
    assert result["sensational"] is False


def test_batch_classification_uses_positional_order(monkeypatch):
    client = OllamaClient("http://localhost:11434", "qwen3:8b")
    items = [
        {"title": "A", "summary": "", "source": "s"},
        {"title": "B", "summary": "", "source": "s"},
    ]
    scopes = [{"name": "AI 编程", "query": "AI", "description": ""}]

    async def fake_generate_json(prompt, num_predict=1400):
        return {
            "items": [
                {"index": 2, "scopes": ["AI 编程"], "summary": "summary-a", "relevance": 80},
                {"index": 1, "scopes": ["AI 编程"], "summary": "summary-b", "relevance": 80},
            ]
        }

    monkeypatch.setattr(client, "generate_json", fake_generate_json)
    results = asyncio.run(client.classify_items(items, scopes))
    assert results[0]["summary"] == "summary-a"
    assert results[1]["summary"] == "summary-b"


def test_batch_keyword_positional_order(monkeypatch):
    client = OllamaClient("http://localhost:11434", "qwen3:8b")
    items = [
        {"title": "A", "summary": "", "source": "s"},
        {"title": "B", "summary": "", "source": "s"},
    ]

    async def fake_generate_json(prompt, num_predict=1400):
        return {
            "items": [
                {"index": 2, "related": True, "summary": "summary-a", "relevance": 80},
                {"index": 1, "related": False, "summary": "summary-b", "relevance": 20},
            ]
        }

    monkeypatch.setattr(client, "generate_json", fake_generate_json)
    results = asyncio.run(client.judge_keyword_items("Claude", items))
    assert results[0]["summary"] == "summary-a"
    assert results[0]["related"] is True
    assert results[1]["summary"] == "summary-b"
    assert results[1]["related"] is False
