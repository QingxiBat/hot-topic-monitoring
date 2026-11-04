from app.keyword_plan import (
    deterministic_plan,
    evaluate_text,
    is_alertable,
    merged_level,
    validate_plan,
)


def test_deterministic_plan_for_model_version():
    plan = deterministic_plan("Claude Sonnet 4.6")
    assert plan["required_anchors"] == ["Claude", "Sonnet", "4.6"]
    assert plan["version"] == "4.6"
    assert any(alias["text"] == "Sonnet 4.6" for alias in plan["aliases"])
    assert plan["search_queries"][0] == '"Claude Sonnet 4.6"'


def test_match_levels_for_claude_sonnet():
    plan = deterministic_plan("Claude Sonnet 4.6")

    exact = evaluate_text(plan, "Anthropic launches Claude Sonnet 4.6", "")
    assert exact["match_level"] == "exact"
    assert exact["matched_anchors"] == ["Claude", "Sonnet", "4.6"]

    alias = evaluate_text(plan, "Sonnet 4.6 released today", "")
    assert alias["match_level"] == "exact"

    partial = evaluate_text(plan, "Claude Sonnet gets an update", "")
    assert partial["match_level"] == "partial"
    assert "4.6" in partial["missing_anchors"]

    weak = evaluate_text(plan, "Claude Code update", "")
    assert weak["match_level"] == "weak"
    assert weak["matched_anchors"] == ["Claude"]

    none = evaluate_text(plan, "OpenClaw is a new agent", "")
    assert none["match_level"] == "none"


def test_confusable_entity_does_not_block_real_match():
    plan = deterministic_plan("Claude Sonnet 4.6")
    plan["confusable_entities"] = ["OpenClaw"]
    result = evaluate_text(plan, "OpenClaw compares with Claude Sonnet 4.6", "")
    assert result["match_level"] == "exact"
    assert result["confusable_hits"] == ["OpenClaw"]


def test_validate_plan_drops_hallucinated_version_and_alias():
    raw = {
        "required_anchors": ["Claude", "Sonnet", "4.6"],
        "aliases": ["Claude Sonnet 4.6", "Sonnet 4.6", "Claude Sonnet 5.0", "Claude"],
        "search_queries": ['"Claude Sonnet 4.6"', '"Claude Sonnet 5.0"'],
    }
    plan = validate_plan("Claude Sonnet 4.6", raw)
    alias_texts = {alias["text"] for alias in plan["aliases"]}
    assert "Sonnet 4.6" in alias_texts
    assert "Claude Sonnet 5.0" not in alias_texts
    assert all("5.0" not in query for query in plan["search_queries"])


def test_version_only_alias_is_rejected():
    plan = deterministic_plan("Claude 4.6")
    assert [alias["text"] for alias in plan["aliases"]] == ["Claude 4.6"]
    validated = validate_plan(
        "Claude 4.6",
        {"aliases": ["4.6", "Claude 4.6"], "search_queries": ["4.6", '"Claude 4.6"']},
    )
    assert [alias["text"] for alias in validated["aliases"]] == ["Claude 4.6"]
    assert validated["search_queries"] == ['"Claude 4.6"', "Claude 4.6"]


def test_chinese_phrase_is_split_into_anchors():
    plan = deterministic_plan("小米汽车")
    assert plan["required_anchors"] == ["小米", "汽车"]
    assert evaluate_text(plan, "小米发布汽车新功能", "")["match_level"] == "strong"
    assert evaluate_text(plan, "小米手机发布", "")["match_level"] == "weak"
    assert evaluate_text(plan, "小米汽车新款发布", "")["match_level"] == "exact"


def test_validate_cached_plan_keeps_aliases():
    plan = deterministic_plan("Claude Sonnet 4.6")
    revalidated = validate_plan("Claude Sonnet 4.6", plan)
    alias_texts = {alias["text"] for alias in revalidated["aliases"]}
    assert "Claude Sonnet 4.6" in alias_texts
    assert "Sonnet 4.6" in alias_texts


def test_alertable_policy_and_merge():
    assert is_alertable("exact") is True
    assert is_alertable("strong") is True
    assert is_alertable("partial") is False
    assert is_alertable("weak") is False
    assert merged_level("strong", "exact") == "strong"
    assert merged_level("exact", "weak") == "exact"
    assert merged_level("partial", "strong") == "partial"
    assert merged_level("weak", "strong") == "weak"
