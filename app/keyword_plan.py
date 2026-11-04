import hashlib
import json
import re
from typing import Any

MATCH_LEVEL_RANK = {"none": 0, "weak": 1, "partial": 2, "strong": 3, "exact": 4}
MATCH_LEVELS = set(MATCH_LEVEL_RANK)
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.+#-]*|\d+(?:\.\d+)+|[\u4e00-\u9fff]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|[\r\n]+")
STOPWORDS = {
    "发布", "最新", "新闻", "消息", "更新", "模型", "版本", "上线", "推出", "报道",
    "release", "news", "update", "latest", "launch", "announcement",
}


def normalize(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("text") or item.get("value") or item.get("name") or ""
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,，、;；\n]", value) if part.strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _expand_cjk(token: str) -> list[str]:
    if (
        re.fullmatch(r"[\u4e00-\u9fff]+", token)
        and len(token) >= 4
        and len(token) % 2 == 0
    ):
        return [token[index : index + 2] for index in range(0, len(token), 2)]
    return [token]


def keyword_tokens(keyword: str) -> list[str]:
    tokens = [token.strip() for token in TOKEN_RE.findall(keyword or "") if token.strip()]
    filtered = [
        part
        for token in tokens
        if len(token) >= 2 or VERSION_RE.fullmatch(token)
        for part in _expand_cjk(token)
    ]
    meaningful = [token for token in filtered if normalize(token) not in STOPWORDS]
    return _unique(meaningful or filtered or [keyword.strip()])


def _build_alias(text: str, required: list[str]) -> dict[str, Any]:
    alias_norm = normalize(text)
    alias_required = [anchor for anchor in required if normalize(anchor) in alias_norm]
    return {"text": text.strip(), "required": alias_required}


def _fallback_queries(original: str, aliases: list[dict[str, Any]]) -> list[str]:
    queries = [f'"{original}"', original]
    for alias in aliases:
        text = alias["text"].strip()
        if text and normalize(text) != normalize(original):
            queries.append(f'"{text}"')
    return _unique(queries)[:5]


def deterministic_plan(keyword: str) -> dict[str, Any]:
    original = (keyword or "").strip()
    required = keyword_tokens(original)
    versions = [token for token in required if VERSION_RE.fullmatch(token)]
    aliases: list[dict[str, Any]] = [_build_alias(original, required)]
    tail = required[1:]
    if versions and tail and any(not VERSION_RE.fullmatch(token) for token in tail):
        aliases.append(_build_alias(" ".join(tail), required))
    aliases = [
        alias
        for alias in aliases
        if alias["text"] and alias["required"] and (
            not versions or all(version in VERSION_RE.findall(alias["text"]) for version in versions)
        )
    ]
    aliases = _unique_aliases(aliases) or [_build_alias(original, required)]
    return {
        "original": original,
        "entity_type": "unknown",
        "vendor": "",
        "product_family": "",
        "version": versions[0] if versions else "",
        "required_anchors": required,
        "optional_anchors": [],
        "aliases": aliases,
        "search_queries": _fallback_queries(original, aliases),
        "confusable_entities": [],
        "negative_terms": [],
        "source": "fallback",
    }


def _unique_aliases(aliases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for alias in aliases:
        key = normalize(alias.get("text"))
        if key and key not in seen:
            seen.add(key)
            result.append(alias)
    return result


def validate_plan(keyword: str, raw: Any) -> dict[str, Any]:
    fallback = deterministic_plan(keyword)
    if not isinstance(raw, dict):
        return fallback

    original = (keyword or "").strip()
    original_norm = normalize(original)
    base_required = keyword_tokens(original)
    versions = [token for token in base_required if VERSION_RE.fullmatch(token)]
    required: list[str] = []
    for anchor in _as_list(raw.get("required_anchors")):
        if len(keyword_tokens(anchor)) > 1:
            continue
        if normalize(anchor) in original_norm and normalize(anchor) not in {normalize(item) for item in required}:
            required.append(anchor)
    for token in base_required:
        if normalize(token) not in {normalize(item) for item in required}:
            required.append(token)
    if not required:
        required = base_required

    aliases: list[dict[str, Any]] = []
    for value in _as_list(raw.get("aliases")) + [original]:
        text = str(value).strip()
        if not text:
            continue
        alias_norm = normalize(text)
        alias_required = [anchor for anchor in required if normalize(anchor) in alias_norm]
        if not alias_required:
            continue
        non_version_required = [anchor for anchor in required if not VERSION_RE.fullmatch(anchor)]
        if non_version_required and not any(
            normalize(anchor) in alias_norm for anchor in non_version_required
        ):
            continue
        alias_versions = VERSION_RE.findall(text)
        if any(version not in versions for version in alias_versions):
            continue
        if versions and not all(version in alias_versions for version in versions):
            continue
        missing_required = [anchor for anchor in required if normalize(anchor) not in alias_norm]
        allowed_missing = {normalize(required[0])} if versions and required else set()
        if any(normalize(anchor) not in allowed_missing for anchor in missing_required):
            continue
        aliases.append({"text": text, "required": alias_required})
    aliases = _unique_aliases(aliases) or [_build_alias(original, required)]

    alias_texts = [alias["text"] for alias in aliases]
    queries: list[str] = []
    for value in _as_list(raw.get("search_queries")) + fallback["search_queries"]:
        query = str(value).strip()
        if not query or len(query) > 300:
            continue
        query_versions = VERSION_RE.findall(query)
        if versions and any(version not in versions for version in query_versions):
            continue
        query_norm = normalize(query)
        non_version_required = [anchor for anchor in required if not VERSION_RE.fullmatch(anchor)]
        if non_version_required and not any(
            normalize(anchor) in query_norm for anchor in non_version_required
        ):
            continue
        if not any(normalize(anchor) in query_norm for anchor in required) and not any(
            normalize(alias) in query_norm for alias in alias_texts
        ):
            continue
        if normalize(query) not in {normalize(item) for item in queries}:
            queries.append(query)
    if not queries:
        queries = fallback["search_queries"]

    return {
        "original": original,
        "entity_type": str(raw.get("entity_type") or "unknown")[:40],
        "vendor": str(raw.get("vendor") or "")[:80],
        "product_family": str(raw.get("product_family") or "")[:120],
        "version": str(raw.get("version") or (versions[0] if versions else ""))[:40],
        "required_anchors": required,
        "optional_anchors": _as_list(raw.get("optional_anchors"))[:10],
        "aliases": aliases,
        "search_queries": queries[:5],
        "confusable_entities": _as_list(raw.get("confusable_entities"))[:10],
        "negative_terms": _as_list(raw.get("negative_terms"))[:10],
        "source": "ai" if raw.get("source") != "fallback" else "fallback",
    }


def plan_hash(plan: dict[str, Any]) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def search_queries(plan: dict[str, Any] | None, keyword: str) -> list[str]:
    if isinstance(plan, dict):
        queries = _as_list(plan.get("search_queries"))
        if queries:
            return queries[:5]
    return [keyword]


def _find_evidence(text: str, terms: list[str]) -> str:
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text or "") if part.strip()]
    for sentence in sentences or [text]:
        sentence_norm = normalize(sentence)
        if any(normalize(term) in sentence_norm for term in terms if term):
            return sentence[:180]
    return (text or "").strip()[:180]


def _level_from_counts(required: list[str], matched: list[str]) -> str:
    if not matched:
        return "none"
    if len(matched) >= len(required):
        return "strong"
    if len(matched) >= 2:
        return "partial"
    return "weak"


def evaluate_text(
    plan: dict[str, Any] | None, title: str, summary: str = ""
) -> dict[str, Any]:
    text = f"{title or ''} {summary or ''}".strip()
    if not isinstance(plan, dict) or not plan.get("required_anchors"):
        return {
            "match_level": "none",
            "matched_anchors": [],
            "missing_anchors": [],
            "evidence": "",
            "alias": "",
            "confusable_hits": [],
            "reason": "缺少查询计划",
        }

    text_norm = normalize(text)
    required = _as_list(plan.get("required_anchors")) or keyword_tokens(plan.get("original") or title)
    global_matched = [anchor for anchor in required if normalize(anchor) in text_norm]
    global_missing = [anchor for anchor in required if normalize(anchor) not in text_norm]
    best_level = _level_from_counts(required, global_matched)
    best_matched = global_matched
    best_missing = global_missing
    best_alias = ""

    for alias in plan.get("aliases") or []:
        alias_text = str(alias.get("text") or "").strip()
        alias_required = _as_list(alias.get("required")) or required
        if not alias_text or not alias_required:
            continue
        alias_norm = normalize(alias_text)
        matched = [anchor for anchor in alias_required if normalize(anchor) in text_norm]
        missing = [anchor for anchor in alias_required if normalize(anchor) not in text_norm]
        if alias_norm in text_norm:
            level = "exact"
        else:
            level = _level_from_counts(alias_required, matched)
        if MATCH_LEVEL_RANK[level] > MATCH_LEVEL_RANK[best_level]:
            best_level = level
            best_matched = matched
            best_missing = missing
            best_alias = alias_text

    terms = [best_alias] if best_alias else []
    terms.extend(best_matched)
    evidence = _find_evidence(text, terms) if best_level != "none" else ""
    confusable_hits = [
        item
        for item in _as_list(plan.get("confusable_entities"))
        if normalize(item) in text_norm
    ]
    if best_level == "exact":
        reason = f"完整命中「{best_alias or plan.get('original') or title}」"
    elif best_level == "strong":
        reason = f"命中全部核心锚点：{'、'.join(best_matched)}"
    elif best_level == "partial":
        reason = f"部分命中：{'、'.join(best_matched)}；缺少：{'、'.join(best_missing)}"
    elif best_level == "weak":
        reason = f"仅命中泛化锚点：{'、'.join(best_matched)}；缺少：{'、'.join(best_missing)}"
    else:
        reason = "未命中核心锚点"

    return {
        "match_level": best_level,
        "matched_anchors": best_matched,
        "missing_anchors": best_missing,
        "evidence": evidence,
        "alias": best_alias,
        "confusable_hits": confusable_hits,
        "reason": reason,
    }


def merged_level(lexical_level: str, ai_level: str) -> str:
    lexical = lexical_level if lexical_level in MATCH_LEVELS else "none"
    ai = ai_level if ai_level in MATCH_LEVELS else lexical
    if lexical in {"exact", "strong"}:
        return lexical
    return lexical if MATCH_LEVEL_RANK[lexical] <= MATCH_LEVEL_RANK[ai] else ai


def is_alertable(level: str) -> bool:
    return MATCH_LEVEL_RANK.get(level, 0) >= MATCH_LEVEL_RANK["strong"]


def relevance_cap(level: str) -> float:
    return {
        "exact": 100.0,
        "strong": 100.0,
        "partial": 65.0,
        "weak": 40.0,
        "none": 0.0,
    }.get(level, 40.0)
