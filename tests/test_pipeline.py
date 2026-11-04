from app import db
from app.pipeline import (
    evaluate_credibility,
    evaluate_keyword_credibility,
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
