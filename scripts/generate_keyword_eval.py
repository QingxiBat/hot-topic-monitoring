#!/usr/bin/env python
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.keyword_plan import (
    deterministic_plan,
    evaluate_text,
    is_alertable,
    validate_plan,
)
from app.llm import OllamaClient

CATEGORIES = [
    "true_positive_exact",
    "true_positive_alias",
    "true_positive_chinese",
    "true_positive_reordered",
    "true_positive_with_confusable",
    "borderline_missing_version",
    "borderline_missing_product",
    "hard_negative_confusable",
    "hard_negative_old_version",
    "hard_negative_generic_ai",
    "hard_negative_vendor_only",
]


async def build_cases(keyword: str, count: int, ollama_url: str, model: str) -> list[dict]:
    client = OllamaClient(ollama_url, model)
    try:
        raw_plan = await client.build_query_plan(keyword)
        plan = validate_plan(keyword, raw_plan)
    except Exception:
        plan = deterministic_plan(keyword)

    prompt = f"""你是搜索相关性测试集生成器。关键词是「{keyword}」。
查询计划：{json.dumps(plan, ensure_ascii=False)}

请生成 {count} 条真实风格的新闻标题与摘要，覆盖以下类别：
{", ".join(CATEGORIES)}

要求：
1. true_positive 类必须包含关键词的核心锚点或别名。
2. hard_negative 类不得包含完整的核心锚点组合，尤其是 OpenClaw、旧版本、同厂商其他产品、泛 AI 新闻。
3. borderline 类只包含部分锚点，例如缺少版本号或缺少产品名。
4. 标题和摘要要像真实新闻，不要出现“测试样本”之类的字样。
5. 每条给出 category 和 rationale。

只输出 JSON：
{{"items":[{{"category":"true_positive_exact","title":"...","summary":"...","rationale":"..."}}]}}
不要输出解释。"""
    payload = await client.generate_json(prompt, num_predict=3200)
    items = payload.get("items", [])
    cases = []
    for index, item in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if category not in CATEGORIES or not title:
            continue
        lexical = evaluate_text(plan, title, summary)
        level = lexical["match_level"]
        if category.startswith("true_positive") and not is_alertable(level):
            continue
        if category.startswith("hard_negative") and is_alertable(level):
            continue
        if category.startswith("borderline") and level not in {"partial", "weak"}:
            continue
        cases.append(
            {
                "id": f"ai-{index}-{category}",
                "keyword": keyword,
                "category": category,
                "title": title,
                "summary": summary,
                "expected_related": is_alertable(level),
                "expected_match_level": level,
                "expected_anchors": lexical["matched_anchors"],
                "rationale": str(item.get("rationale") or lexical["reason"])[:160],
            }
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="用 AI 生成关键词评估样本，并用规则校验标签")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--ollama", default=os.getenv("RADAR_OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--model", default=os.getenv("RADAR_OLLAMA_MODEL", "qwen3:8b"))
    args = parser.parse_args()

    cases = asyncio.run(build_cases(args.keyword, args.count, args.ollama, args.model))
    if not cases:
        print("没有生成通过规则校验的样本", file=sys.stderr)
        return 1
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"已写入 {len(cases)} 条样本到 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
