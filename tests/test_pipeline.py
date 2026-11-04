from app import db
from app.pipeline import (
    evaluate_credibility,
    evaluate_keyword_credibility,
    infer_scopes,
    prepare_candidates,
    source_base_score,
)
from app.sources import RawItem


def test_source_base_scores():
    assert source_base_score("openai.com") == 92
    assert source_base_score("www.openai.com") == 92
    assert source_base_score("theverge.com") == 78
    assert source_base_score("qbitai.com") == 65
    assert source_base_score("unknown-blog.xyz") == 38


def test_official_item_is_verified():
    item = {"source_domain": "openai.com", "also_sources": ["openai.com"], "origin": "feed"}
    ai = {
        "credibility": 88,
        "claim_type": "official",
        "sensational": False,
    }
    result = evaluate_credibility(item, ai)
    assert result["verdict"] == "verified"
    assert result["credibility"] >= 78


def test_sensational_unknown_item_is_not_verified():
    item = {"source_domain": "unknown-blog.xyz", "also_sources": ["unknown-blog.xyz"]}
    ai = {
        "credibility": 30,
        "claim_type": "unknown",
        "sensational": True,
    }
    result = evaluate_credibility(item, ai)
    assert result["verdict"] in {"unverified", "suspicious"}


def test_corroboration_boosts_and_marks_likely():
    item = {
        "source_domain": "example.com",
        "also_sources": ["example.com", "another.com", "third.com"],
    }
    ai = {"credibility": 70, "claim_type": "third_party", "sensational": False}
    result = evaluate_credibility(item, ai)
    assert result["verdict"] in {"likely", "verified"}
    assert "3 个独立来源" in result["verdict_reason"]


def test_keyword_fake_risk_blocks_verification():
    item = {"source_domain": "unknown.xyz", "also_sources": ["unknown.xyz"]}
    judgement = {
        "relevance": 90,
        "fake_risk": 85,
        "credibility": 80,
        "claim_type": "official",
        "sensational": False,
        "verdict_reason": "疑似冒名账号",
    }
    result = evaluate_keyword_credibility(item, judgement)
    assert result["verdict"] == "suspicious"


def test_community_tier_capped_at_unverified():
    item = {
        "source_domain": "v2ex.com",
        "also_sources": ["v2ex.com"],
        "source_tier": "community",
        "content_kind": "discussion",
    }
    ai = {"credibility": 95, "claim_type": "official", "sensational": False}
    result = evaluate_credibility(item, ai)
    assert result["verdict"] == "unverified"
    assert "社区/热榜" in result["verdict_reason"]


def test_research_tier_capped_at_likely():
    item = {
        "source_domain": "huggingface.co",
        "also_sources": ["huggingface.co"],
        "source_tier": "research",
        "content_kind": "research",
    }
    ai = {"credibility": 95, "claim_type": "official", "sensational": False}
    result = evaluate_credibility(item, ai)
    assert result["verdict"] == "likely"
    assert "研究论文" in result["verdict_reason"]


def test_official_release_can_be_verified():
    item = {
        "source_domain": "github.com",
        "also_sources": ["github.com"],
        "source_tier": "release",
        "content_kind": "release",
    }
    ai = {"credibility": 92, "claim_type": "official", "sensational": False}
    result = evaluate_credibility(item, ai)
    assert result["verdict"] == "verified"
    assert "项目更新" in result["verdict_reason"]


def test_keyword_official_source_is_verified():
    item = {"source_domain": "openai.com", "also_sources": ["openai.com"]}
    judgement = {
        "relevance": 95,
        "fake_risk": 5,
        "credibility": 92,
        "claim_type": "official",
        "sensational": False,
        "verdict_reason": "官方博客发布",
    }
    result = evaluate_keyword_credibility(item, judgement)
    assert result["verdict"] == "verified"


def test_infer_scopes_for_release_source():
    scopes = [{"name": "AI 编程"}, {"name": "大模型"}, {"name": "开源 AI"}]
    item = {"source": "Ollama Releases", "title": "v0.40.15", "summary": ""}
    assert infer_scopes(item, scopes) == ["AI 编程", "开源 AI"]


def test_infer_scopes_for_chip_and_multimodal():
    scopes = [
        {"name": "AI 芯片与算力"},
        {"name": "多模态与生成式"},
        {"name": "大模型"},
    ]
    assert "AI 芯片与算力" in infer_scopes(
        {"source": "NVIDIA Blog", "title": "NVIDIA 发布新一代 GPU 推理芯片", "summary": ""}, scopes
    )
    assert infer_scopes(
        {"source": "Test", "title": "Sora 文生视频能力升级", "summary": ""}, scopes
    ) == ["多模态与生成式"]


def test_infer_scopes_for_research_title():
    scopes = [{"name": "AI 编程"}, {"name": "大模型"}, {"name": "AI Agent"}, {"name": "开源 AI"}]
    item = {
        "source": "HF Daily Papers",
        "title": "ToolSpace: Scaling Tool-Centric Environment Synthesis for Long-Horizon Agents",
        "summary": "",
    }
    assert infer_scopes(item, scopes) == ["AI Agent"]


def test_prepare_candidates_merges_similar_titles(temp_db):
    items = [
        RawItem(
            title="OpenAI releases GPT-5 for all users",
            url="https://a.com/story",
            source_domain="a.com",
        ),
        RawItem(
            title="OpenAI releases GPT-5 for all users today",
            url="https://b.com/story",
            source_domain="b.com",
        ),
    ]
    candidates = prepare_candidates(items)
    assert len(candidates) == 1
    assert set(candidates[0]["also_sources"]) == {"a.com", "b.com"}


def test_prepare_candidates_merges_url_duplicate(temp_db):
    items = [
        RawItem(title="Claude Code update", url="https://a.com/1?utm_source=x", source_domain="a.com"),
        RawItem(title="Different title", url="https://a.com/1", source_domain="a.com"),
    ]
    candidates = prepare_candidates(items)
    assert len(candidates) == 1


def test_prepare_candidates_keeps_existing_id_for_keywords(temp_db):
    article_id = db.insert_article(
        {
            "url": "https://a.com/1",
            "title": "OpenAI launches Codex",
            "source_domain": "a.com",
            "also_sources": ["a.com"],
            "verdict": "unverified",
            "credibility": 50,
        }
    )
    items = [
        RawItem(title="OpenAI launches Codex", url="https://b.com/2", source_domain="b.com"),
    ]
    candidates = prepare_candidates(items, keep_existing=True)
    assert len(candidates) == 1
    assert candidates[0]["existing_id"] == article_id
    updated = db.get_article(article_id)
    assert set(updated["also_sources"]) == {"a.com", "b.com"}
