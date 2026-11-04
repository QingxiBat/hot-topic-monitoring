import asyncio
import re
from typing import Any

from . import db
from .llm import OllamaClient
from .notifier import broker, notify_alert
from .sources import (
    RawItem,
    canonicalize_url,
    domain_of,
    fetch_custom_sources,
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
    "qwenlm.github.io",
]
TOP_MEDIA_DOMAINS = [
    "reuters.com", "bloomberg.com", "theverge.com", "techcrunch.com", "wired.com",
    "arstechnica.com", "nytimes.com", "wsj.com", "ft.com", "cnbc.com",
    "the-decoder.com", "venturebeat.com", "latent.space", "infoq.cn", "techmeme.com",
]
CN_MEDIA_DOMAINS = [
    "36kr.com", "jiqizhixin.com", "qbitai.com", "ithome.com", "huxiu.com",
    "tmtpost.com", "sina.com.cn", "163.com", "sohu.com", "thepaper.cn",
    "ifanr.com", "sspai.com", "geekpark.net", "oschina.net",
]
COMMUNITY_DOMAINS = [
    "news.ycombinator.com", "reddit.com", "simonwillison.net",
    "bilibili.com", "juejin.cn", "v2ex.com", "segmentfault.com", "csdn.net",
]

VERDICT_RANK = {"suspicious": 0, "unverified": 1, "likely": 2, "verified": 3}
TIER_RANK = {"heat": 0, "community": 1, "media": 2, "research": 2, "official": 3, "release": 3}
KIND_RANK = {"heat": 0, "discussion": 1, "video": 1, "news": 2, "release": 2, "research": 2}
TIER_LABELS = {
    "official": "官方/一手",
    "release": "官方项目发布",
    "media": "媒体",
    "community": "社区",
    "heat": "热榜信号",
    "research": "研究",
}
KIND_LABELS = {
    "news": "新闻",
    "research": "研究论文",
    "release": "项目更新",
    "discussion": "社区讨论",
    "video": "视频",
    "heat": "热榜信号",
}


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


def effective_tier(item: dict[str, Any]) -> str:
    tier = item.get("source_tier") or "media"
    if tier in {"research", "release"}:
        return tier
    domain = item.get("source_domain") or ""
    if domain and source_base_score(domain) >= 90:
        return "official"
    return tier


SOURCE_DEFAULT_SCOPES = {
    "OpenAI News": ["大模型", "AI 编程"],
    "Google AI Blog": ["大模型"],
    "Google DeepMind": ["大模型"],
    "Hugging Face Blog": ["开源 AI", "大模型"],
    "Cursor Changelog": ["AI 编程"],
    "Ollama Blog": ["AI 编程", "开源 AI"],
    "Ollama Releases": ["AI 编程", "开源 AI"],
    "vLLM Releases": ["开源 AI", "大模型"],
    "llama.cpp Releases": ["开源 AI"],
    "Transformers Releases": ["开源 AI", "大模型"],
    "LangChain Releases": ["AI Agent", "开源 AI"],
    "openai-python Releases": ["AI 编程"],
    "Qwen Blog": ["大模型"],
    "NVIDIA Blog": ["AI 芯片与算力", "大模型"],
    "HF Daily Papers": ["AI 研究前沿"],
}

SCOPE_HINTS = {
    "AI 编程": [
        "ai编程", "ai coding", "编程", "代码", "coding", "cursor", "copilot", "claude code",
        "代码生成", "developer", "sdk", "ide", "vibe coding",
    ],
    "大模型": [
        "大模型", "llm", "gpt", "claude", "gemini", "qwen", "deepseek", "llama", "mistral",
        "多模态", "token", "推理", "训练", "模型发布", "基础模型",
    ],
    "AI Agent": [
        "agent", "智能体", "mcp", "tool use", "工具调用", "workflow", "自动化", "autonomous",
    ],
    "开源 AI": [
        "开源", "open source", "huggingface", "hugging face", "github", "vllm", "transformers",
        "本地部署", "llama.cpp", "ollama", "release",
    ],
    "AI 芯片与算力": [
        "芯片", "gpu", "算力", "nvidia", "英伟达", "推理芯片", "数据中心", "cuda", "tpu",
    ],
    "多模态与生成式": [
        "多模态", "文生图", "文生视频", "图像生成", "视频生成", "sora", "midjourney",
        "语音生成", "生成式", "音频", "image generation", "video generation",
    ],
    "AI 产品与应用": [
        "ai应用", "ai 应用", "产品", "助手", "办公", "教育", "医疗", "客服", "商业化", "落地",
    ],
    "AI 安全与合规": [
        "ai安全", "ai 安全", "监管", "合规", "版权", "对齐", "风险", "政策", "法案", "alignment",
    ],
    "具身智能与机器人": [
        "具身", "机器人", "人形", "自动驾驶", "机械臂", "仿真", "embodied", "robotics",
    ],
    "AI 研究前沿": [
        "论文", "arxiv", "基准", "benchmark", "研究", "顶会", "neurips", "icml", "iclr",
    ],
}


def infer_scopes(item: dict[str, Any], scopes: list[dict[str, Any]]) -> list[str]:
    text = f"{item.get('title') or ''} {item.get('summary') or ''}".lower()
    matched: list[str] = []
    for scope in scopes:
        name = scope["name"]
        hints = SCOPE_HINTS.get(name)
        if not hints:
            hints = re.findall(r"[A-Za-z][A-Za-z0-9.+#-]{1,}|[\u4e00-\u9fff]{2,6}", scope.get("query", ""))
        if any(str(hint).lower() in text for hint in hints):
            matched.append(name)
    if not matched:
        defaults = SOURCE_DEFAULT_SCOPES.get(item.get("source") or "", [])
        available = {scope["name"] for scope in scopes}
        matched = [name for name in defaults if name in available]
    return matched[:3]


def _cap_verdict(verdict: str, source_tier: str, content_kind: str) -> str:
    if source_tier in {"community", "heat"}:
        cap = "unverified"
    elif content_kind == "research":
        cap = "likely"
    else:
        cap = None
    if cap and VERDICT_RANK.get(verdict, 0) > VERDICT_RANK.get(cap, 0):
        return cap
    return verdict


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
    source_tier = effective_tier(item)
    content_kind = item.get("content_kind") or "news"
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
    if source_tier in {"community", "heat"}:
        score = min(score, 60)
    if content_kind == "research":
        score = min(score, 90)
    score = _clamp(score)
    verdict = _cap_verdict(
        _verdict_from_score(score, corroboration, claim_type), source_tier, content_kind
    )
    reasons = []
    if domains:
        reasons.append(f"来源 {domains[0]}")
    reasons.append(f"来源层级 {TIER_LABELS.get(source_tier, source_tier)}")
    if content_kind != "news":
        reasons.append(f"内容类型 {KIND_LABELS.get(content_kind, content_kind)}")
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
    if source_tier in {"community", "heat"}:
        reasons.append("社区/热榜内容最高按待核实处理")
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
    source_tier = effective_tier(item)
    content_kind = item.get("content_kind") or "news"
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
    if source_tier in {"community", "heat"}:
        score = min(score, 60)
    if content_kind == "research":
        score = min(score, 90)
    score = _clamp(score)
    verdict = _cap_verdict(
        _verdict_from_score(score, corroboration, claim_type, fake_risk),
        source_tier,
        content_kind,
    )
    reason = judgement.get("verdict_reason") or "模型未给出具体依据"
    parts = [f"来源 {domains[0]}" if domains else "来源未知"]
    parts.append(f"来源层级 {TIER_LABELS.get(source_tier, source_tier)}")
    if content_kind != "news":
        parts.append(f"内容类型 {KIND_LABELS.get(content_kind, content_kind)}")
    if corroboration >= 2:
        parts.append(f"{corroboration} 个独立来源印证")
    parts.append(f"假冒风险 {int(fake_risk)}")
    parts.append(reason)
    if source_tier in {"community", "heat"}:
        parts.append("社区/热榜内容不自动推送")
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


def _merge_meta(target: dict[str, Any], item: dict[str, Any]) -> bool:
    changed = False
    tier = item.get("source_tier") or "media"
    if TIER_RANK.get(tier, 0) > TIER_RANK.get(target.get("source_tier") or "media", 0):
        target["source_tier"] = tier
        changed = True
    kind = item.get("content_kind") or "news"
    if KIND_RANK.get(kind, 0) > KIND_RANK.get(target.get("content_kind") or "news", 0):
        target["content_kind"] = kind
        changed = True
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
    domains_changed = _merge_domains(existing, item)
    meta_changed = _merge_meta(existing, item)
    changed = domains_changed or meta_changed
    if changed:
        boost = min(
            95.0,
            float(existing.get("credibility") or 0) + (5 if domains_changed else 0),
        )
        verdict = existing.get("verdict") or "unverified"
        if domains_changed and len(existing["also_sources"]) >= 2 and VERDICT_RANK.get(verdict, 0) < 2 and boost >= 60:
            verdict = "likely"
        verdict = _cap_verdict(
            verdict,
            effective_tier(existing),
            existing.get("content_kind") or "news",
        )
        reason = existing.get("verdict_reason") or ""
        if domains_changed:
            reason += f" · 新增 {item.get('source_domain') or '来源'} 印证"
        if meta_changed:
            reason += " · 来源类型升级"
        db.update_article(
            existing["id"],
            {
                "also_sources": existing["also_sources"],
                "credibility": boost,
                "verdict": verdict,
                "verdict_reason": reason[:300],
                "source_tier": existing.get("source_tier") or "media",
                "content_kind": existing.get("content_kind") or "news",
            },
        )
        existing["credibility"] = boost
        existing["verdict"] = verdict
    if domains_changed:
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
        item["source_tier"] = effective_tier(item)
        item["content_kind"] = item.get("content_kind") or "news"
        if url in seen_urls:
            existing = next((a for a in recent if a.get("url") == url), None)
            if existing:
                _merge_into_db(existing, item)
                if keep_existing:
                    item["existing_id"] = existing["id"]
                    item["also_sources"] = existing.get("also_sources") or [domain]
                    item["source_tier"] = existing.get("source_tier") or item["source_tier"]
                    item["content_kind"] = existing.get("content_kind") or item["content_kind"]
                    candidates.append(item)
            continue
        similar = _find_similar(recent, item["title"])
        if similar:
            _merge_into_db(similar, item)
            if keep_existing:
                item["existing_id"] = similar["id"]
                item["also_sources"] = similar.get("also_sources") or [domain]
                item["source_tier"] = similar.get("source_tier") or item["source_tier"]
                item["content_kind"] = similar.get("content_kind") or item["content_kind"]
                candidates.append(item)
            continue
        batch_similar = _find_similar(candidates, item["title"])
        if batch_similar:
            _merge_domains(batch_similar, item)
            _merge_meta(batch_similar, item)
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
        if not ai.get("scopes"):
            inferred = infer_scopes(item, scopes)
            if inferred:
                ai["scopes"] = inferred
                ai["relevance"] = max(float(ai.get("relevance") or 0), 45)
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
            "source_tier": item.get("source_tier", "media"),
            "content_kind": item.get("content_kind", "news"),
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
                tasks.append(fetch_custom_sources(settings))
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
                            "source_tier": item.get("source_tier", "media"),
                            "content_kind": item.get("content_kind", "news"),
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
