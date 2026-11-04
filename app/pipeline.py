import asyncio
from typing import Any

from . import db
from .llm import OllamaClient
from .notifier import broker, notify_alert
from .sources import (
    RawItem,
    canonicalize_url,
    domain_of,
    fetch_feed_batch,
    fetch_keyword_sources,
    fetch_scope_sources,
    title_similarity,
)

COLLECT_LOCK = asyncio.Lock()
KEYWORD_LOCK = asyncio.Lock()

OFFICIAL_DOMAINS = [
    "openai.com", "anthropic.com", "deepmind.google", "blog.google", "ai.google",
    "huggingface.co", "meta.com", "microsoft.com", "nvidia.com", "deepseek.com",
    "qwen.ai", "alibaba.com", "cursor.com", "ollama.com", "github.com",
]
TOP_MEDIA_DOMAINS = [
    "reuters.com", "bloomberg.com", "theverge.com", "techcrunch.com", "wired.com",
    "arstechnica.com", "nytimes.com", "wsj.com", "ft.com", "cnbc.com",
    "the-decoder.com", "venturebeat.com", "latent.space",
]
CN_MEDIA_DOMAINS = [
    "36kr.com", "jiqizhixin.com", "qbitai.com", "ithome.com", "huxiu.com",
    "tmtpost.com", "sina.com.cn", "163.com", "sohu.com", "thepaper.cn",
]
COMMUNITY_DOMAINS = ["news.ycombinator.com", "reddit.com", "simonwillison.net"]

VERDICT_RANK = {"suspicious": 0, "unverified": 1, "likely": 2, "verified": 3}


def _matches(domain: str, base: str) -> bool:
    return domain == base or domain.endswith("." + base)


def source_base_score(domain: str) -> float:
    domain = (domain or "").lower()
    for base in OFFICIAL_DOMAINS:
        if _matches(domain, base):
            return 92
    for base in TOP_MEDIA_DOMAINS:
        if _matches(domain, base):
            return 78
    for base in CN_MEDIA_DOMAINS:
        if _matches(domain, base):
            return 65
    for base in COMMUNITY_DOMAINS:
        if _matches(domain, base):
            return 60
    return 38


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _verdict_from_score(
    score: float, corroboration: int, claim_type: str, fake_risk: float = 0
) -> str:
    if fake_risk >= 65:
        return "suspicious"
    if claim_type == "rumor":
        return "unverified" if score < 55 else "likely"
    if score >= 78 and (corroboration >= 2 or claim_type == "official"):
        return "verified"
    if score >= 56:
        return "likely"
    if score >= 36:
        return "unverified"
    return "suspicious"


def evaluate_credibility(
    item: dict[str, Any], ai: dict[str, Any]
) -> dict[str, Any]:
    domains = item.get("also_sources") or [item.get("source_domain") or ""]
    domains = [domain for domain in dict.fromkeys(domains) if domain]
    base = max([source_base_score(domain) for domain in domains] or [38])
    ai_cred = float(ai.get("credibility") or 50)
    claim_type = ai.get("claim_type") or "unknown"
    corroboration = len(domains)
    score = base * 0.55 + ai_cred * 0.45 + min(12, max(0, corroboration - 1) * 6)
    if ai.get("sensational"):
        score -= 15
    if claim_type == "official" and base >= 75:
        score = max(score, 85)
    if claim_type == "rumor":
        score = min(score, 45)
    if claim_type in {"opinion", "marketing"}:
        score = min(score, 58)
    if int(item.get("points") or 0) >= 100:
        score += 4
    score = _clamp(score)
    verdict = _verdict_from_score(score, corroboration, claim_type)
    reasons = []
    if domains:
        reasons.append(f"来源 {domains[0]}")
    if corroboration >= 2:
        reasons.append(f"{corroboration} 个独立来源印证")
    claim_labels = {
        "official": "官方发布", "third_party": "媒体报道", "rumor": "传闻",
        "opinion": "观点", "marketing": "营销", "unknown": "来源类型不明",
    }
    reasons.append(claim_labels.get(claim_type, "来源类型不明"))
    if ai.get("sensational"):
        reasons.append("模型标记为标题党")
    if item.get("origin") == "hn":
        reasons.append("Hacker News 热度较高")
    return {
        "credibility": round(score, 1),
        "verdict": verdict,
        "verdict_reason": " · ".join(reasons),
    }


def evaluate_keyword_credibility(
    item: dict[str, Any], judgement: dict[str, Any]
) -> dict[str, Any]:
    domains = item.get("also_sources") or [item.get("source_domain") or ""]
    domains = [domain for domain in dict.fromkeys(domains) if domain]
    base = max([source_base_score(domain) for domain in domains] or [38])
    ai_cred = float(judgement.get("credibility") or 50)
    fake_risk = float(judgement.get("fake_risk") or 50)
    claim_type = judgement.get("claim_type") or "unknown"
    corroboration = len(domains)
    score = base * 0.45 + ai_cred * 0.45 - fake_risk * 0.25
    score += min(18, max(0, corroboration - 1) * 6)
    if judgement.get("sensational"):
        score -= 10
    if claim_type == "official" and base >= 75:
        score = max(score, 86)
    if claim_type == "rumor":
        score = min(score, 42)
    if claim_type in {"opinion", "marketing"}:
        score = min(score, 55)
    score = _clamp(score)
    verdict = _verdict_from_score(score, corroboration, claim_type, fake_risk)
    reason = judgement.get("verdict_reason") or "模型未给出具体依据"
    parts = [f"来源 {domains[0]}" if domains else "来源未知"]
    if corroboration >= 2:
        parts.append(f"{corroboration} 个独立来源印证")
    parts.append(f"假冒风险 {int(fake_risk)}")
    parts.append(reason)
    return {
        "credibility": round(score, 1),
        "verdict": verdict,
        "verdict_reason": " · ".join(parts)[:300],
    }


def _merge_domains(target: dict[str, Any], item: dict[str, Any]) -> bool:
    domains = list(target.get("also_sources") or [])
    if target.get("source_domain") and target["source_domain"] not in domains:
        domains.append(target["source_domain"])
    changed = False
    domain = item.get("source_domain")
    if domain and domain not in domains:
        domains.append(domain)
        changed = True
    target["also_sources"] = domains
    return changed


def _find_similar(entries: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    best = None
    best_score = 0.0
    for entry in entries:
        score = title_similarity(title, entry.get("title") or "")
        if score > best_score:
            best_score = score
            best = entry
    return best if best_score >= 0.86 else None


def _merge_into_db(existing: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    previous_domains = list(existing.get("also_sources") or [])
    changed = _merge_domains(existing, item)
    if changed:
        boost = min(95.0, float(existing.get("credibility") or 0) + 5)
        verdict = existing.get("verdict") or "unverified"
        if len(existing["also_sources"]) >= 2 and VERDICT_RANK.get(verdict, 0) < 2 and boost >= 60:
            verdict = "likely"
        db.update_article(
            existing["id"],
            {
                "also_sources": existing["also_sources"],
                "credibility": boost,
                "verdict": verdict,
                "verdict_reason": (
                    (existing.get("verdict_reason") or "")
                    + f" · 新增 {item.get('source_domain') or '来源'} 印证"
                )[:300],
            },
        )
        existing["credibility"] = boost
        existing["verdict"] = verdict
    if previous_domains != existing.get("also_sources"):
        broker.publish("article_update", {"id": existing["id"], "credibility": existing.get("credibility")})
    return existing


def prepare_candidates(
    items: list[RawItem], keep_existing: bool = False, hours: int = 72
) -> list[dict[str, Any]]:
    recent = db.find_recent_articles(hours)
    for article in recent:
        article["also_sources"] = db.json_loads(article.get("also_sources"), [])
        if not article["also_sources"]:
            article["also_sources"] = [article.get("source_domain")] if article.get("source_domain") else []
    seen_urls = {article.get("url") for article in recent if article.get("url")}
    candidates: list[dict[str, Any]] = []
    for raw in items:
        item = raw.to_dict()
        url = canonicalize_url(item.get("url") or "")
        item["url"] = url
        if not url:
            continue
        domain = item.get("source_domain") or domain_of(url)
        item["source_domain"] = domain
        if url in seen_urls:
            existing = next((a for a in recent if a.get("url") == url), None)
            if existing:
                _merge_into_db(existing, item)
                if keep_existing:
                    item["existing_id"] = existing["id"]
                    item["also_sources"] = existing.get("also_sources") or [domain]
                    candidates.append(item)
            continue
        similar = _find_similar(recent, item["title"])
        if similar:
            _merge_into_db(similar, item)
            if keep_existing:
                item["existing_id"] = similar["id"]
                item["also_sources"] = similar.get("also_sources") or [domain]
                candidates.append(item)
            continue
        batch_similar = _find_similar(candidates, item["title"])
        if batch_similar:
            _merge_domains(batch_similar, item)
            continue
        item["also_sources"] = [domain] if domain else []
        candidates.append(item)
        seen_urls.add(url)
    return candidates


async def ingest_scope_items(
    items: list[RawItem], scopes: list[dict[str, Any]], settings: dict[str, Any], trigger: str
) -> dict[str, Any]:
    candidates = prepare_candidates(items, keep_existing=False)
    if not candidates:
        return {"fetched": len(items), "inserted": 0, "alerts": 0}
    client = OllamaClient(settings.get("ollama_url", "http://localhost:11434"), settings.get("ollama_model", "qwen3:8b"))
    classifications = await client.classify_items(candidates, scopes)
    inserted = 0
    for item, ai in zip(candidates, classifications):
        if not ai.get("scopes") or float(ai.get("relevance") or 0) < 40:
            continue
        evaluation = evaluate_credibility(item, ai)
        article = {
            "url": item["url"],
            "title": item["title"],
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
            "source_domain": item.get("source_domain", ""),
            "published_at": item.get("published_at"),
            "fetched_at": db.now_iso(),
            "scope_names": ai.get("scopes", []),
            "tags": ai.get("tags", []),
            "relevance": ai.get("relevance", 0),
            "credibility": evaluation["credibility"],
            "verdict": evaluation["verdict"],
            "verdict_reason": evaluation["verdict_reason"],
            "ai_summary": ai.get("summary", ""),
            "claim_type": ai.get("claim_type", "unknown"),
            "sensational": ai.get("sensational", False),
            "matched_keywords": [],
            "also_sources": item.get("also_sources", []),
            "trigger": trigger,
            "created_at": db.now_iso(),
        }
        try:
            db.insert_article(article)
            inserted += 1
        except Exception:
            continue
    return {"fetched": len(items), "inserted": inserted, "alerts": 0}


async def run_collect(
    trigger: str = "manual",
    scope_names: list[str] | None = None,
    include_feeds: bool = True,
) -> dict[str, Any]:
    if COLLECT_LOCK.locked():
        return {"status": "skipped", "reason": "采集任务正在运行"}
    async with COLLECT_LOCK:
        run_id = db.start_run(trigger, "collect")
        settings = db.get_settings()
        scopes = db.list_scopes(enabled_only=True)
        if scope_names:
            scopes = [scope for scope in scopes if scope["name"] in scope_names]
        feeds = db.list_feeds(enabled_only=True)
        fetched = 0
        inserted = 0
        errors: list[str] = []
        try:
            tasks = [fetch_scope_sources(scope, settings) for scope in scopes]
            if include_feeds:
                tasks.append(
                    fetch_feed_batch(
                        feeds,
                        int(settings.get("max_feed_items", 6)),
                        int(settings.get("freshness_hours", 168)),
                    )
                )
            results = await asyncio.gather(*tasks, return_exceptions=True)
            items: list[RawItem] = []
            for result in results:
                if isinstance(result, Exception):
                    errors.append(str(result))
                    continue
                source_items, source_errors = result
                items.extend(source_items)
                errors.extend(source_errors)
            fetched = len(items)
            stats = await ingest_scope_items(items, scopes, settings, trigger)
            inserted = stats["inserted"]
            db.finish_run(run_id, "success", fetched, inserted, 0, " | ".join(errors)[:1000])
            result = {
                "status": "success",
                "run_id": run_id,
                "fetched": fetched,
                "inserted": inserted,
                "errors": errors[:10],
            }
            broker.publish("topics", result)
            return result
        except Exception as exc:
            db.finish_run(run_id, "failed", fetched, inserted, 0, str(exc))
            broker.publish("error", {"message": f"采集失败：{exc}"})
            return {"status": "failed", "run_id": run_id, "error": str(exc)}


async def run_keyword_check(trigger: str = "manual") -> dict[str, Any]:
    if KEYWORD_LOCK.locked():
        return {"status": "skipped", "reason": "关键词检查正在运行"}
    async with KEYWORD_LOCK:
        run_id = db.start_run(trigger, "keyword")
        settings = db.get_settings()
        keywords = db.list_keywords(enabled_only=True)
        fetched = 0
        alerts_created = 0
        errors: list[str] = []
        try:
            fetch_results = await asyncio.gather(
                *[fetch_keyword_sources(keyword["term"], settings) for keyword in keywords],
                return_exceptions=True,
            )
            for keyword, result in zip(keywords, fetch_results):
                if isinstance(result, Exception):
                    errors.append(f"{keyword['term']}: {result}")
                    continue
                items, source_errors = result
                errors.extend(source_errors)
                fetched += len(items)
                candidates = prepare_candidates(items, keep_existing=True)
                if not candidates:
                    db.touch_keyword(keyword["id"])
                    continue
                client = OllamaClient(
                    settings.get("ollama_url", "http://localhost:11434"),
                    settings.get("ollama_model", "qwen3:8b"),
                )
                judgements = await client.judge_keyword_items(keyword["term"], candidates)
                alert_candidates: list[dict[str, Any]] = []
                threshold = float(settings.get("relevance_threshold", 55))
                for item, judgement in zip(candidates, judgements):
                    if not judgement.get("related") or float(judgement.get("relevance") or 0) < threshold:
                        continue
                    evaluation = evaluate_keyword_credibility(item, judgement)
                    level = {
                        "verified": "critical",
                        "likely": "warning",
                    }.get(evaluation["verdict"], "info")
                    message = (
                        f"{judgement.get('verdict_reason') or ''} "
                        f"| AI 相关度 {int(judgement.get('relevance', 0))}"
                    ).strip()
                    article_id = item.get("existing_id")
                    if article_id:
                        existing = db.get_article(int(article_id))
                        if not existing:
                            continue
                        matched = list(existing.get("matched_keywords") or [])
                        if keyword["term"] in matched:
                            continue
                        matched.append(keyword["term"])
                        new_credibility = max(
                            float(existing.get("credibility") or 0), evaluation["credibility"]
                        )
                        new_verdict = (
                            evaluation["verdict"]
                            if VERDICT_RANK.get(evaluation["verdict"], 0)
                            > VERDICT_RANK.get(existing.get("verdict"), 0)
                            else existing.get("verdict")
                        )
                        db.update_article(
                            existing["id"],
                            {
                                "matched_keywords": matched,
                                "credibility": new_credibility,
                                "verdict": new_verdict,
                                "verdict_reason": evaluation["verdict_reason"],
                            },
                        )
                        existing = db.get_article(existing["id"])
                        article = existing
                    else:
                        article = {
                            "url": item["url"],
                            "title": item["title"],
                            "summary": item.get("summary", ""),
                            "source": item.get("source", ""),
                            "source_domain": item.get("source_domain", ""),
                            "published_at": item.get("published_at"),
                            "fetched_at": db.now_iso(),
                            "scope_names": [],
                            "tags": [],
                            "relevance": judgement.get("relevance", 0),
                            "credibility": evaluation["credibility"],
                            "verdict": evaluation["verdict"],
                            "verdict_reason": evaluation["verdict_reason"],
                            "ai_summary": judgement.get("summary") or "",
                            "claim_type": judgement.get("claim_type", "unknown"),
                            "sensational": judgement.get("sensational", False),
                            "matched_keywords": [keyword["term"]],
                            "also_sources": item.get("also_sources", []),
                            "trigger": trigger,
                            "created_at": db.now_iso(),
                        }
                        try:
                            article_id = db.insert_article(article)
                        except Exception:
                            continue
                        article = db.get_article(article_id)
                    if not article:
                        continue
                    alert_candidates.append(
                        {
                            "article": article,
                            "level": level,
                            "message": message,
                            "relevance": float(judgement.get("relevance") or 0),
                            "credibility": float(article.get("credibility") or 0),
                        }
                    )
                max_alerts = max(1, int(settings.get("max_alerts_per_keyword_run", 3)))
                alert_candidates.sort(
                    key=lambda entry: (
                        VERDICT_RANK.get(entry["article"].get("verdict"), 0),
                        entry["relevance"],
                        entry["credibility"],
                    ),
                    reverse=True,
                )
                pending = []
                for entry in alert_candidates[:max_alerts]:
                    alert = db.create_alert(
                        entry["article"]["id"],
                        keyword["term"],
                        entry["level"],
                        entry["article"]["title"],
                        entry["message"],
                    )
                    if alert:
                        alerts_created += 1
                        pending.append(notify_alert(alert, entry["article"], settings))
                db.touch_keyword(keyword["id"])
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            db.finish_run(run_id, "success", fetched, 0, alerts_created, " | ".join(errors)[:1000])
            result = {
                "status": "success",
                "run_id": run_id,
                "fetched": fetched,
                "alerts": alerts_created,
                "errors": errors[:10],
            }
            broker.publish("keywords", result)
            return result
        except Exception as exc:
            db.finish_run(run_id, "failed", fetched, 0, alerts_created, str(exc))
            broker.publish("error", {"message": f"关键词检查失败：{exc}"})
            return {"status": "failed", "run_id": run_id, "error": str(exc)}
