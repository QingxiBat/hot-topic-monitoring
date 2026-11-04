import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlparse, urlencode, urlunparse

import feedparser
import httpx

from .config import CUSTOM_SOURCES, USER_AGENT
from .db import now_utc

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "yclid", "spm", "ref", "ref_src", "mc_cid", "mc_eid",
}

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
AI_HINT_RE = re.compile(
    r"(?:\bai\b|artificial intelligence|machine learning|deep learning|llm|gpt|claude|gemini|qwen|deepseek|"
    r"agent|mcp|copilot|cursor|ollama|hugging face|huggingface|transformer|diffusion|multimodal|openai|"
    r"anthropic|nvidia|gpu|大模型|人工智能|机器学习|深度学习|智能体|模型|算法|编程|代码|开发者|开源|芯片|算力|"
    r"多模态|机器人|自动驾驶|生成式|推理)",
    re.I,
)


@dataclass
class RawItem:
    title: str
    url: str
    summary: str = ""
    source: str = ""
    source_domain: str = ""
    published_at: str | None = None
    origin: str = "web"
    points: int = 0
    source_tier: str = "media"
    content_kind: str = "news"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "source": self.source,
            "source_domain": self.source_domain,
            "published_at": self.published_at,
            "origin": self.origin,
            "points": self.points,
            "source_tier": self.source_tier,
            "content_kind": self.content_kind,
        }


def matches_ai_hint(text: str) -> bool:
    return bool(AI_HINT_RE.search(text or ""))


def clean_html(value: str | None, limit: int = 600) -> str:
    if not value:
        return ""
    text = unescape(value)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = TAG_RE.sub(" ", text)
    text = unescape(text)
    text = SPACE_RE.sub(" ", text).strip()
    return text[:limit]


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    query = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        key: values for key, values in query.items() if key.lower() not in TRACKING_PARAMS
    }
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc.lower(),
            path,
            "",
            urlencode(filtered, doseq=True),
            "",
        )
    )


def decode_bing_link(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    params = parse_qs(parsed.query)
    target = params.get("url", [None])[0]
    return target or url


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    text = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,30}$", "", text)
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text


def title_tokens(title: str) -> set[str]:
    text = unicodedata.normalize("NFKC", title or "").lower()
    latin = set(re.findall(r"[a-z0-9][a-z0-9.+#-]{1,}", text))
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", text)
    grams = {cjk[i : i + 2] for i in range(max(0, len(cjk) - 1))}
    return latin | grams


def title_similarity(left: str, right: str) -> float:
    import difflib

    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 6 and (a in b or b in a):
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        extra = longer.replace(shorter, "", 1)
        if not re.search(r"\d", extra):
            return 0.92
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    tokens_a = title_tokens(left)
    tokens_b = title_tokens(right)
    if tokens_a and tokens_b:
        jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    else:
        jaccard = 0.0
    return round(seq * 0.6 + jaccard * 0.4, 4)


def _struct_to_iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _entry_published(entry: Any) -> str | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, key, None) or entry.get(key)
        iso = _struct_to_iso(value)
        if iso:
            return iso
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _is_fresh(published_at: str | None, hours: int) -> bool:
    if not published_at:
        return True
    try:
        parsed = datetime.fromisoformat(published_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= now_utc() - timedelta(hours=hours)


async def _get(
    url: str, timeout: float = 20.0, extra_headers: dict[str, str] | None = None
) -> httpx.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
    }
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response


def _parse_entries(
    content: bytes,
    source_name: str,
    source_domain: str = "",
    default_domain: str = "",
    limit: int = 20,
    freshness_hours: int = 168,
    origin: str = "rss",
    decode_link=None,
    source_tier: str = "media",
    content_kind: str = "news",
    filter_ai: bool = False,
) -> list[RawItem]:
    feed = feedparser.parse(content)
    items: list[RawItem] = []
    for entry in feed.entries:
        title = clean_html(entry.get("title"), 300)
        link = entry.get("link") or entry.get("id") or ""
        if decode_link:
            link = decode_link(link)
        link = canonicalize_url(link)
        if not title or not link:
            continue
        published = _entry_published(entry)
        if not _is_fresh(published, freshness_hours):
            continue
        entry_source = entry.get("source") or {}
        if isinstance(entry_source, dict):
            entry_source_name = entry_source.get("title") or ""
            entry_source_href = entry_source.get("href") or ""
        else:
            entry_source_name = str(entry_source)
            entry_source_href = ""
        domain = source_domain or domain_of(entry_source_href) or domain_of(link) or default_domain
        summary = clean_html(entry.get("summary") or entry.get("description"), 600)
        if filter_ai and not matches_ai_hint(f"{title} {summary}"):
            continue
        items.append(
            RawItem(
                title=title,
                url=link,
                summary=summary,
                source=entry_source_name or source_name,
                source_domain=domain,
                published_at=published,
                origin=origin,
                source_tier=source_tier,
                content_kind=content_kind,
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_google_news(
    query: str, limit: int = 12, freshness_hours: int = 168, when: str = "7d"
) -> list[RawItem]:
    scoped_query = f"{query} when:{when}" if when else query
    url = (
        "https://news.google.com/rss/search?q="
        + quote(scoped_query)
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    response = await _get(url)
    return _parse_entries(
        response.content,
        "Google News",
        source_domain="",
        default_domain="news.google.com",
        limit=limit,
        freshness_hours=freshness_hours,
        origin="google",
        source_tier="media",
        content_kind="news",
    )


async def fetch_bing_news(query: str, limit: int = 6, freshness_hours: int = 168) -> list[RawItem]:
    encoded = quote(query)
    variants = [
        f"https://www.bing.com/news/search?q={encoded}&format=rss&count={max(limit, 10)}",
        f"https://www.bing.com/news/search?q={encoded}&format=RSS",
    ]
    last_error: Exception | None = None
    for url in variants:
        try:
            response = await _get(url)
            items = _parse_entries(
                response.content,
                "Bing News",
                default_domain="bing.com",
                limit=limit,
                freshness_hours=freshness_hours,
                origin="bing",
                decode_link=decode_bing_link,
                source_tier="media",
                content_kind="news",
            )
            if items:
                return items
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


async def fetch_hn(query: str, limit: int = 8, freshness_hours: int = 168) -> list[RawItem]:
    since = int((now_utc() - timedelta(hours=freshness_hours)).timestamp())
    url = (
        "https://hn.algolia.com/api/v1/search?query="
        + quote(query)
        + f"&tags=story&hitsPerPage={max(limit * 2, 20)}"
        + f"&numericFilters=created_at_i>{since}"
    )
    response = await _get(url)
    payload = response.json()
    items: list[RawItem] = []
    for hit in payload.get("hits", []):
        title = clean_html(hit.get("title"), 300)
        if not title:
            continue
        points = int(hit.get("points") or 0)
        if points < 5:
            continue
        article_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        published = hit.get("created_at")
        items.append(
            RawItem(
                title=title,
                url=canonicalize_url(article_url),
                summary=f"Hacker News 热度 {points} · 评论 {hit.get('num_comments') or 0}",
                source="Hacker News",
                source_domain=domain_of(article_url) or "news.ycombinator.com",
                published_at=published,
                origin="hn",
                points=points,
                source_tier="community",
                content_kind="discussion",
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_feed(feed: dict[str, Any], limit: int = 6, freshness_hours: int = 168) -> list[RawItem]:
    response = await _get(feed["url"], timeout=25.0)
    return _parse_entries(
        response.content,
        feed["name"],
        default_domain=domain_of(feed["url"]),
        limit=int(feed.get("max_items") or limit),
        freshness_hours=freshness_hours,
        origin="feed",
        source_tier=feed.get("tier", "media"),
        content_kind=feed.get("content_kind", "news"),
        filter_ai=bool(feed.get("filter_ai")),
    )


async def fetch_feed_batch(
    feeds: list[dict[str, Any]], limit: int = 6, freshness_hours: int = 168
) -> tuple[list[RawItem], list[str]]:
    tasks = [fetch_feed(feed, limit, freshness_hours) for feed in feeds]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[RawItem] = []
    errors: list[str] = []
    for feed, result in zip(feeds, results):
        if isinstance(result, Exception):
            errors.append(f"{feed['name']}: {result}")
        else:
            items.extend(result)
    return items, errors


async def fetch_custom_sources(settings: dict[str, Any]) -> tuple[list[RawItem], list[str]]:
    adapter_map = {
        "bilibili_ranking": fetch_bilibili_ranking,
        "juejin_hot": fetch_juejin_hot,
        "v2ex_hot": fetch_v2ex_hot,
        "hf_daily_papers": fetch_hf_daily_papers,
    }
    freshness = int(settings.get("freshness_hours", 168))
    tasks = []
    labels = []
    for source in CUSTOM_SOURCES:
        if not source.get("enabled", True):
            continue
        adapter = adapter_map.get(source.get("adapter"))
        if not adapter:
            continue
        tasks.append(adapter(int(source.get("limit", 10)), freshness))
        labels.append(source["name"])
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[RawItem] = []
    errors: list[str] = []
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")
        else:
            items.extend(result)
    return items, errors


def _timestamp_iso(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


async def fetch_bilibili_ranking(limit: int = 10, freshness_hours: int = 168) -> list[RawItem]:
    urls = [
        "https://api.bilibili.com/x/web-interface/ranking/v2?rid=188&type=all",
        "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all",
        "https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1",
    ]
    found: dict[str, RawItem] = {}
    for url in urls:
        try:
            response = await _get(
                url,
                extra_headers={"Accept": "application/json", "Referer": "https://www.bilibili.com/"},
            )
            payload = response.json()
        except Exception:
            continue
        for entry in payload.get("data", {}).get("list", []):
            title = clean_html(entry.get("title"), 300)
            desc = clean_html(entry.get("desc"), 400)
            bvid = entry.get("bvid")
            if not title or not bvid or bvid in found:
                continue
            if not matches_ai_hint(f"{title} {desc}"):
                continue
            published = _timestamp_iso(entry.get("pubdate"))
            if not _is_fresh(published, freshness_hours):
                continue
            stat = entry.get("stat") or {}
            owner = (entry.get("owner") or {}).get("name") or ""
            summary = (
                f"{desc} | 播放 {stat.get('view', 0)} · 点赞 {stat.get('like', 0)} · UP {owner}"
            ).strip(" |")
            found[bvid] = RawItem(
                title=title,
                url=f"https://www.bilibili.com/video/{bvid}",
                summary=summary,
                source="B站科技区排行",
                source_domain="bilibili.com",
                published_at=published,
                origin="bilibili",
                points=0,
                source_tier="community",
                content_kind="video",
            )
            if len(found) >= limit:
                return list(found.values())
    return list(found.values())


async def fetch_juejin_hot(limit: int = 10, freshness_hours: int = 168) -> list[RawItem]:
    url = (
        "https://api.juejin.cn/content_api/v1/content/article_rank"
        "?category_id=1&type=hot&cursor=0&limit=30"
    )
    response = await _get(url, extra_headers={"Accept": "application/json"})
    payload = response.json()
    items: list[RawItem] = []
    for entry in payload.get("data", []):
        content = entry.get("content") or {}
        title = clean_html(content.get("title"), 300)
        brief = clean_html(content.get("brief"), 400)
        content_id = content.get("content_id")
        if not title or not content_id:
            continue
        if not matches_ai_hint(f"{title} {brief}"):
            continue
        counter = entry.get("content_counter") or {}
        summary = f"{brief} | 阅读 {counter.get('view', 0)} · 点赞 {counter.get('like', 0)}".strip(" |")
        items.append(
            RawItem(
                title=title,
                url=f"https://juejin.cn/post/{content_id}",
                summary=summary,
                source="掘金热榜",
                source_domain="juejin.cn",
                published_at=_timestamp_iso(content.get("ctime")),
                origin="juejin",
                source_tier="community",
                content_kind="discussion",
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_v2ex_hot(limit: int = 10, freshness_hours: int = 168) -> list[RawItem]:
    url = "https://www.v2ex.com/api/topics/hot.json"
    response = await _get(url, extra_headers={"Accept": "application/json"})
    payload = response.json()
    items: list[RawItem] = []
    for entry in payload:
        title = clean_html(entry.get("title"), 300)
        link = entry.get("url") or ""
        content = clean_html(entry.get("content"), 400)
        if not title or not link:
            continue
        if not matches_ai_hint(f"{title} {content}"):
            continue
        node = (entry.get("node") or {}).get("title") or ""
        summary = f"{content} | {node} · 回复 {entry.get('replies', 0)}".strip(" |")
        items.append(
            RawItem(
                title=title,
                url=canonicalize_url(link),
                summary=summary,
                source="V2EX 热榜",
                source_domain="v2ex.com",
                published_at=_timestamp_iso(entry.get("created")),
                origin="v2ex",
                source_tier="community",
                content_kind="discussion",
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_hf_daily_papers(limit: int = 5, freshness_hours: int = 168) -> list[RawItem]:
    url = f"https://huggingface.co/api/daily_papers?limit={max(limit, 10)}"
    response = await _get(url, extra_headers={"Accept": "application/json"})
    payload = response.json()
    items: list[RawItem] = []
    for entry in payload:
        paper = entry.get("paper") or {}
        paper_id = paper.get("id")
        title = clean_html(entry.get("title") or paper.get("title"), 300)
        if not paper_id or not title:
            continue
        summary = clean_html(
            entry.get("summary") or paper.get("ai_summary") or paper.get("summary"), 600
        )
        authors = ", ".join(
            str(author.get("name") or "") for author in (paper.get("authors") or [])[:4]
        )
        items.append(
            RawItem(
                title=title,
                url=f"https://huggingface.co/papers/{paper_id}",
                summary=f"{summary} | 作者：{authors}".strip(" |"),
                source="HF Daily Papers",
                source_domain="huggingface.co",
                published_at=entry.get("publishedAt") or paper.get("publishedAt"),
                origin="hf",
                source_tier="research",
                content_kind="research",
            )
        )
        if len(items) >= limit:
            break
    return items


def _scope_hn_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9.+#-]{1,}", query)
    return tokens[0] if tokens else query


async def fetch_scope_sources(
    scope: dict[str, Any], settings: dict[str, Any]
) -> tuple[list[RawItem], list[str]]:
    freshness = int(settings.get("freshness_hours", 168))
    tasks = [
        fetch_google_news(
            scope["query"], int(settings.get("max_google_per_scope", 12)), freshness
        ),
        fetch_bing_news(
            scope["query"], int(settings.get("max_bing_per_scope", 6)), freshness
        ),
        fetch_hn(
            _scope_hn_query(scope["query"]),
            int(settings.get("max_hn_per_scope", 8)),
            freshness,
        ),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[RawItem] = []
    errors: list[str] = []
    labels = ["Google News", "Bing News", "Hacker News"]
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            errors.append(f"{scope['name']} / {label}: {result}")
        else:
            items.extend(result)
    return items, errors


async def fetch_keyword_sources(
    keyword: str, settings: dict[str, Any]
) -> tuple[list[RawItem], list[str]]:
    freshness = int(settings.get("keyword_freshness_hours", 48))
    tasks = [
        fetch_google_news(keyword, 10, freshness, when="3d"),
        fetch_bing_news(keyword, 6, freshness),
        fetch_hn(keyword, 6, freshness),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[RawItem] = []
    errors: list[str] = []
    labels = ["Google News", "Bing News", "Hacker News"]
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            errors.append(f"{keyword} / {label}: {result}")
        else:
            items.extend(result)
    return items, errors
