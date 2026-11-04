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
    merged_level,
    normalize,
)
from app.llm import OllamaClient


def load_cases(path: Path, keyword: str) -> list[dict]:
    cases = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            if case.get("keyword", keyword) != keyword:
                continue
            cases.append(case)
    return cases


def predicted_from_lexical(case: dict, plan: dict) -> dict:
    result = evaluate_text(plan, case.get("title", ""), case.get("summary", ""))
    return {
        "match_level": result["match_level"],
        "matched_anchors": result["matched_anchors"],
        "missing_anchors": result["missing_anchors"],
        "evidence": result["evidence"],
        "match_reason": result["reason"],
    }


async def predicted_from_ai(
    keyword: str, cases: list[dict], plan: dict, ollama_url: str, model: str
) -> list[dict]:
    client = OllamaClient(ollama_url, model)
    items = [
        {"title": case.get("title", ""), "summary": case.get("summary", ""), "source": "eval"}
        for case in cases
    ]
    judgements = await client.judge_keyword_items(keyword, items, plan)
    predictions = []
    for case, judgement in zip(cases, judgements):
        lexical = evaluate_text(plan, case.get("title", ""), case.get("summary", ""))
        final_level = merged_level(lexical["match_level"], judgement.get("match_level") or "none")
        predictions.append(
            {
                "match_level": final_level,
                "matched_anchors": lexical["matched_anchors"] or judgement.get("matched_anchors") or [],
                "missing_anchors": lexical["missing_anchors"] or judgement.get("missing_anchors") or [],
                "evidence": judgement.get("evidence") or lexical["evidence"],
                "match_reason": judgement.get("match_reason") or lexical["reason"],
            }
        )
    return predictions


def evaluate(cases: list[dict], predictions: list[dict], mode: str) -> dict:
    tp = fp = fn = tn = 0
    level_hits = 0
    hard_negative_count = 0
    hard_negative_fp = 0
    evidence_checked = 0
    evidence_supported = 0
    errors = []
    for case, prediction in zip(cases, predictions):
        expected_related = bool(case.get("expected_related"))
        predicted_related = is_alertable(prediction["match_level"])
        if predicted_related and expected_related:
            tp += 1
        elif predicted_related and not expected_related:
            fp += 1
        elif not predicted_related and expected_related:
            fn += 1
        else:
            tn += 1
        if prediction["match_level"] == case.get("expected_match_level"):
            level_hits += 1
        else:
            errors.append(
                {
                    "id": case.get("id"),
                    "expected": case.get("expected_match_level"),
                    "predicted": prediction["match_level"],
                    "title": case.get("title"),
                }
            )
        category = str(case.get("category") or "")
        if category.startswith("hard_negative"):
            hard_negative_count += 1
            if predicted_related:
                hard_negative_fp += 1
        if mode == "ai" and predicted_related:
            evidence_checked += 1
            text = normalize(f'{case.get("title", "")} {case.get("summary", "")}')
            if prediction["evidence"] and normalize(prediction["evidence"]) in text:
                evidence_supported += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "mode": mode,
        "cases": len(cases),
        "alert_precision": round(precision, 4),
        "alert_recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "hard_negative_fp_rate": round(hard_negative_fp / hard_negative_count, 4)
        if hard_negative_count
        else 0.0,
        "match_level_accuracy": round(level_hits / len(cases), 4) if cases else 0.0,
        "evidence_support_rate": round(evidence_supported / evidence_checked, 4)
        if evidence_checked
        else 1.0,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评估关键词相关性与匹配等级")
    parser.add_argument("--keyword", default="Claude Sonnet 4.6")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "evals" / "keywords" / "claude-sonnet-4-6.jsonl"),
    )
    parser.add_argument("--ai", action="store_true", help="调用本地 Ollama 进行 AI 判断")
    parser.add_argument("--ollama", default=os.getenv("RADAR_OLLAMA_URL", "http://localhost:11434"))
    parser.add_argument("--model", default=os.getenv("RADAR_OLLAMA_MODEL", "qwen3:8b"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="低于建议门槛时返回非零退出码")
    args = parser.parse_args()

    cases = load_cases(Path(args.dataset), args.keyword)
    if not cases:
        print(f"没有找到 {args.keyword} 的评估样本", file=sys.stderr)
        return 1

    plan = deterministic_plan(args.keyword)
    if args.ai:
        predictions = asyncio.run(
            predicted_from_ai(args.keyword, cases, plan, args.ollama, args.model)
        )
    else:
        predictions = [predicted_from_lexical(case, plan) for case in cases]
    report = evaluate(cases, predictions, "ai" if args.ai else "lexical")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"关键词：{args.keyword}")
        print(f"模式：{report['mode']} · 样本：{report['cases']}")
        print(f"告警精确率：{report['alert_precision']:.2%}")
        print(f"告警召回率：{report['alert_recall']:.2%}")
        print(f"F1：{report['f1']:.2%}")
        print(f"强负例误报率：{report['hard_negative_fp_rate']:.2%}")
        print(f"匹配等级准确率：{report['match_level_accuracy']:.2%}")
        print(f"证据支持率：{report['evidence_support_rate']:.2%}")
        if report["errors"]:
            print("等级不一致：")
            for error in report["errors"][:10]:
                print(
                    f"  - {error['id']}: 期望 {error['expected']}，实际 {error['predicted']} · {error['title']}"
                )

    failed = (
        report["alert_precision"] < 0.95
        or report["hard_negative_fp_rate"] > 0.02
        or report["evidence_support_rate"] < 1.0
        or report["alert_recall"] < 0.90
    )
    return 1 if args.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
