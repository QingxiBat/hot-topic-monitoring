import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import DB_PATH, DATA_DIR, DEFAULT_FEEDS, DEFAULT_SCOPES, DEFAULT_SETTINGS

TOPIC_SORTS = {"composite", "newest", "fetched", "credibility", "relevance", "sources"}
TIME_FIELDS = {"published", "fetched"}

SOURCE_COUNT_SQL = (
    "CASE WHEN json_valid(also_sources) "
    "THEN MAX(1, json_array_length(also_sources)) "
    "ELSE CASE WHEN source_domain != '' THEN 1 ELSE 0 END END"
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scopes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL,
                description TEXT DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL UNIQUE COLLATE NOCASE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_checked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                tier TEXT NOT NULL DEFAULT 'media',
                content_kind TEXT NOT NULL DEFAULT 'news',
                filter_ai INTEGER NOT NULL DEFAULT 0,
                max_items INTEGER NOT NULL DEFAULT 6
            );
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                source TEXT DEFAULT '',
                source_domain TEXT DEFAULT '',
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                scope_names TEXT NOT NULL DEFAULT '[]',
                tags TEXT NOT NULL DEFAULT '[]',
                relevance REAL NOT NULL DEFAULT 0,
                credibility REAL NOT NULL DEFAULT 0,
                verdict TEXT NOT NULL DEFAULT 'unverified',
                verdict_reason TEXT DEFAULT '',
                ai_summary TEXT DEFAULT '',
                claim_type TEXT DEFAULT 'unknown',
                sensational INTEGER NOT NULL DEFAULT 0,
                matched_keywords TEXT NOT NULL DEFAULT '[]',
                also_sources TEXT NOT NULL DEFAULT '[]',
                source_tier TEXT NOT NULL DEFAULT 'media',
                content_kind TEXT NOT NULL DEFAULT 'news',
                trigger TEXT DEFAULT 'schedule',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read INTEGER NOT NULL DEFAULT 0,
                pushed INTEGER NOT NULL DEFAULT 0,
                push_result TEXT DEFAULT '',
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'collect',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                fetched INTEGER NOT NULL DEFAULT 0,
                inserted INTEGER NOT NULL DEFAULT 0,
                alerts INTEGER NOT NULL DEFAULT 0,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_credibility ON articles(credibility DESC);
            CREATE INDEX IF NOT EXISTS idx_articles_relevance ON articles(relevance DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_article_keyword ON alerts(article_id, keyword);
            """
        )
        _ensure_column(conn, "feeds", "tier", "TEXT NOT NULL DEFAULT 'media'")
        _ensure_column(conn, "feeds", "content_kind", "TEXT NOT NULL DEFAULT 'news'")
        _ensure_column(conn, "feeds", "filter_ai", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "feeds", "max_items", "INTEGER NOT NULL DEFAULT 6")
        _ensure_column(conn, "articles", "source_tier", "TEXT NOT NULL DEFAULT 'media'")
        _ensure_column(conn, "articles", "content_kind", "TEXT NOT NULL DEFAULT 'news'")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, json_dumps(value)),
            )
        if conn.execute("SELECT COUNT(*) AS c FROM scopes").fetchone()["c"] == 0:
            for scope in DEFAULT_SCOPES:
                conn.execute(
                    "INSERT INTO scopes(name, query, description, enabled, created_at) VALUES(?, ?, ?, 1, ?)",
                    (scope["name"], scope["query"], scope["description"], now_iso()),
                )
        for feed in DEFAULT_FEEDS:
            conn.execute(
                "INSERT OR IGNORE INTO feeds(name, url, enabled, tier, content_kind, filter_ai, max_items) "
                "VALUES(?, ?, 1, ?, ?, ?, ?)",
                (
                    feed["name"],
                    feed["url"],
                    feed.get("tier", "media"),
                    feed.get("content_kind", "news"),
                    1 if feed.get("filter_ai") else 0,
                    int(feed.get("max_items", 6)),
                ),
            )
            conn.execute(
                "UPDATE feeds SET name = ?, tier = ?, content_kind = ?, filter_ai = ?, max_items = ? "
                "WHERE url = ?",
                (
                    feed["name"],
                    feed.get("tier", "media"),
                    feed.get("content_kind", "news"),
                    1 if feed.get("filter_ai") else 0,
                    int(feed.get("max_items", 6)),
                    feed["url"],
                ),
            )
        conn.commit()


def get_settings() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        settings[row["key"]] = json_loads(row["value"], row["value"])
    return settings


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(DEFAULT_SETTINGS.keys())
    with connect() as conn:
        for key, value in updates.items():
            if key not in allowed:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json_dumps(value)),
            )
        conn.commit()
    return get_settings()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def list_scopes(enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM scopes"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(sql).fetchall()]


def create_scope(name: str, query: str, description: str = "") -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO scopes(name, query, description, enabled, created_at) VALUES(?, ?, ?, 1, ?)",
            (name.strip(), query.strip(), description.strip(), now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scopes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


def update_scope(scope_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    fields = []
    values = []
    for key in ("name", "query", "description", "enabled"):
        if key in updates:
            fields.append(f"{key} = ?")
            values.append(updates[key])
    if not fields:
        return None
    values.append(scope_id)
    with connect() as conn:
        conn.execute(f"UPDATE scopes SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM scopes WHERE id = ?", (scope_id,)).fetchone()
    return row_to_dict(row) if row else None


def delete_scope(scope_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM scopes WHERE id = ?", (scope_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_keywords(enabled_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM keywords"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(sql).fetchall()]


def create_keyword(term: str) -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO keywords(term, enabled, created_at) VALUES(?, 1, ?)",
            (term.strip(), now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM keywords WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


def set_keyword_enabled(keyword_id: int, enabled: bool) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE keywords SET enabled = ? WHERE id = ?", (1 if enabled else 0, keyword_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def touch_keyword(keyword_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE keywords SET last_checked_at = ? WHERE id = ?", (now_iso(), keyword_id)
        )
        conn.commit()


def delete_keyword(keyword_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_feeds(enabled_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM feeds"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id"
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(sql).fetchall()]


def find_recent_articles(hours: int = 72) -> list[dict[str, Any]]:
    since = (now_utc() - timedelta(hours=hours)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM articles WHERE fetched_at >= ? ORDER BY fetched_at DESC", (since,)
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def find_article_by_url(url: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM articles WHERE url = ?", (url,)).fetchone()
    return row_to_dict(row) if row else None


def insert_article(article: dict[str, Any]) -> int:
    fields = (
        "url", "title", "summary", "source", "source_domain", "published_at", "fetched_at",
        "scope_names", "tags", "relevance", "credibility", "verdict", "verdict_reason",
        "ai_summary", "claim_type", "sensational", "matched_keywords", "also_sources",
        "source_tier", "content_kind", "trigger", "created_at",
    )
    values = [
        article.get("url"),
        article.get("title"),
        article.get("summary", ""),
        article.get("source", ""),
        article.get("source_domain", ""),
        article.get("published_at"),
        article.get("fetched_at") or now_iso(),
        json_dumps(article.get("scope_names", [])),
        json_dumps(article.get("tags", [])),
        float(article.get("relevance", 0)),
        float(article.get("credibility", 0)),
        article.get("verdict", "unverified"),
        article.get("verdict_reason", ""),
        article.get("ai_summary", ""),
        article.get("claim_type", "unknown"),
        1 if article.get("sensational") else 0,
        json_dumps(article.get("matched_keywords", [])),
        json_dumps(article.get("also_sources", [])),
        article.get("source_tier", "media"),
        article.get("content_kind", "news"),
        article.get("trigger", "schedule"),
        article.get("created_at") or now_iso(),
    ]
    with connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO articles({', '.join(fields)}) VALUES({', '.join('?' for _ in fields)})",
            values,
        )
        conn.commit()
    return int(cursor.lastrowid)


def update_article(article_id: int, updates: dict[str, Any]) -> None:
    mapping = {
        "scope_names": json_dumps,
        "tags": json_dumps,
        "matched_keywords": json_dumps,
        "also_sources": json_dumps,
    }
    fields = []
    values = []
    for key, value in updates.items():
        if key not in {
            "title", "summary", "credibility", "verdict", "verdict_reason", "ai_summary",
            "claim_type", "sensational", "scope_names", "tags", "matched_keywords",
            "also_sources", "published_at", "source", "source_domain",
        }:
            continue
        fields.append(f"{key} = ?")
        values.append(mapping[key](value) if key in mapping else value)
    if not fields:
        return
    values.append(article_id)
    with connect() as conn:
        conn.execute(f"UPDATE articles SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()


def _as_filter_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).split(",")
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _topic_filter_clauses(
    since_hours: int,
    scope: str | list[str] | None,
    verdict: str | list[str] | None,
    include_keyword_only: bool,
    tiers: str | list[str] | None,
    kinds: str | list[str] | None,
    min_credibility: float | None,
    max_credibility: float | None,
    min_relevance: float | None,
    max_relevance: float | None,
    min_sources: int | None,
    single_source: bool,
    keywords: str | list[str] | None,
    query: str | None,
    time_field: str,
    has_keyword: bool,
) -> tuple[list[str], list[Any]]:
    since = (now_utc() - timedelta(hours=since_hours)).isoformat()
    clauses: list[str] = []
    values: list[Any] = []

    if time_field == "fetched":
        clauses.append("fetched_at >= ?")
    else:
        clauses.append("COALESCE(published_at, fetched_at) >= ?")
    values.append(since)

    if not include_keyword_only:
        clauses.append("scope_names != '[]'")

    scopes = _as_filter_list(scope)
    if scopes:
        clauses.append("(" + " OR ".join("scope_names LIKE ?" for _ in scopes) + ")")
        values.extend(f'%"{item}"%' for item in scopes)

    verdicts = _as_filter_list(verdict)
    if verdicts:
        clauses.append("verdict IN (" + ", ".join("?" for _ in verdicts) + ")")
        values.extend(verdicts)

    tier_values = _as_filter_list(tiers)
    if tier_values:
        clauses.append("source_tier IN (" + ", ".join("?" for _ in tier_values) + ")")
        values.extend(tier_values)

    kind_values = _as_filter_list(kinds)
    if kind_values:
        clauses.append("content_kind IN (" + ", ".join("?" for _ in kind_values) + ")")
        values.extend(kind_values)

    if min_credibility is not None:
        clauses.append("credibility >= ?")
        values.append(float(min_credibility))
    if max_credibility is not None:
        clauses.append("credibility <= ?")
        values.append(float(max_credibility))
    if min_relevance is not None:
        clauses.append("relevance >= ?")
        values.append(float(min_relevance))
    if max_relevance is not None:
        clauses.append("relevance <= ?")
        values.append(float(max_relevance))

    if single_source:
        clauses.append(f"{SOURCE_COUNT_SQL} <= 1")
    elif min_sources is not None:
        clauses.append(f"{SOURCE_COUNT_SQL} >= ?")
        values.append(max(1, int(min_sources)))

    keyword_values = _as_filter_list(keywords)
    if keyword_values:
        clauses.append(
            "(" + " OR ".join("matched_keywords LIKE ?" for _ in keyword_values) + ")"
        )
        values.extend(f'%"{item}"%' for item in keyword_values)
    if has_keyword:
        clauses.append("matched_keywords != '[]'")

    if query:
        term = f"%{_like_escape(query.strip())}%"
        clauses.append(
            "(title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' "
            "OR ai_summary LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"
        )
        values.extend([term, term, term, term])

    return clauses, values


def _topic_order_sql(sort: str) -> str:
    if sort == "newest":
        return "COALESCE(published_at, fetched_at) DESC, relevance DESC, id DESC"
    if sort == "fetched":
        return "fetched_at DESC, id DESC"
    if sort == "credibility":
        return "credibility DESC, COALESCE(published_at, fetched_at) DESC, id DESC"
    if sort == "relevance":
        return "relevance DESC, credibility DESC, COALESCE(published_at, fetched_at) DESC, id DESC"
    if sort == "sources":
        return (
            f"{SOURCE_COUNT_SQL} DESC, credibility DESC, "
            "COALESCE(published_at, fetched_at) DESC, id DESC"
        )

    recency_hours = (
        "(julianday('now') - julianday(COALESCE(published_at, fetched_at))) * 24"
    )
    priority = (
        "("
        "credibility * 0.40 + relevance * 0.25 "
        f"+ MIN({SOURCE_COUNT_SQL}, 4) * 5 "
        "+ CASE source_tier "
        "WHEN 'official' THEN 8 WHEN 'release' THEN 8 "
        "WHEN 'research' THEN 5 WHEN 'media' THEN 2 ELSE 0 END "
        f"+ CASE WHEN {recency_hours} <= 24 THEN 8 "
        f"WHEN {recency_hours} <= 72 THEN 5 "
        f"WHEN {recency_hours} <= 168 THEN 2 ELSE 0 END "
        "- CASE WHEN sensational = 1 THEN 8 ELSE 0 END"
        ")"
    )
    return (
        f"{priority} DESC, COALESCE(published_at, fetched_at) DESC, "
        "credibility DESC, id DESC"
    )


def query_topics(
    scope: str | list[str] | None = None,
    limit: int = 40,
    offset: int = 0,
    since_hours: int = 168,
    verdict: str | list[str] | None = None,
    include_keyword_only: bool = False,
    *,
    tiers: str | list[str] | None = None,
    kinds: str | list[str] | None = None,
    min_credibility: float | None = None,
    max_credibility: float | None = None,
    min_relevance: float | None = None,
    max_relevance: float | None = None,
    min_sources: int | None = None,
    single_source: bool = False,
    keywords: str | list[str] | None = None,
    query: str | None = None,
    time_field: str = "published",
    sort: str = "composite",
    has_keyword: bool = False,
) -> dict[str, Any]:
    if time_field not in TIME_FIELDS:
        time_field = "published"
    if sort not in TOPIC_SORTS:
        sort = "composite"
    clauses, values = _topic_filter_clauses(
        since_hours=since_hours,
        scope=scope,
        verdict=verdict,
        include_keyword_only=include_keyword_only,
        tiers=tiers,
        kinds=kinds,
        min_credibility=min_credibility,
        max_credibility=max_credibility,
        min_relevance=min_relevance,
        max_relevance=max_relevance,
        min_sources=min_sources,
        single_source=single_source,
        keywords=keywords,
        query=query,
        time_field=time_field,
        has_keyword=has_keyword,
    )
    where = " AND ".join(clauses)
    order = _topic_order_sql(sort)
    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM articles WHERE {where}", values
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM articles WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*values, limit, offset],
        ).fetchall()
    return {
        "items": [_serialize_article(row_to_dict(row)) for row in rows],
        "total": int(total),
    }


def list_topics(
    scope: str | list[str] | None = None,
    limit: int = 40,
    offset: int = 0,
    since_hours: int = 168,
    verdict: str | list[str] | None = None,
    include_keyword_only: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    return query_topics(
        scope=scope,
        limit=limit,
        offset=offset,
        since_hours=since_hours,
        verdict=verdict,
        include_keyword_only=include_keyword_only,
        **filters,
    )["items"]


def get_article(article_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    return _serialize_article(row_to_dict(row)) if row else None


def _serialize_article(article: dict[str, Any]) -> dict[str, Any]:
    article["scope_names"] = json_loads(article.get("scope_names"), [])
    article["tags"] = json_loads(article.get("tags"), [])
    article["matched_keywords"] = json_loads(article.get("matched_keywords"), [])
    article["also_sources"] = json_loads(article.get("also_sources"), [])
    article["sensational"] = bool(article.get("sensational"))
    return article


def create_alert(
    article_id: int, keyword: str, level: str, title: str, message: str
) -> dict[str, Any] | None:
    with connect() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO alerts(article_id, keyword, level, title, message, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (article_id, keyword, level, title, message, now_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return row_to_dict(row)


def list_alerts(limit: int = 30, unread_only: bool = False) -> list[dict[str, Any]]:
    sql = (
        "SELECT alerts.*, articles.url AS article_url, articles.source AS source, "
        "articles.verdict AS verdict, articles.credibility AS credibility "
        "FROM alerts LEFT JOIN articles ON articles.id = alerts.article_id"
    )
    if unread_only:
        sql += " WHERE alerts.read = 0"
    sql += " ORDER BY alerts.created_at DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    alerts = []
    for row in rows:
        item = row_to_dict(row)
        item["read"] = bool(item["read"])
        item["pushed"] = bool(item["pushed"])
        alerts.append(item)
    return alerts


def mark_alert_read(alert_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute("UPDATE alerts SET read = 1 WHERE id = ?", (alert_id,))
        conn.commit()
    return cursor.rowcount > 0


def mark_all_alerts_read() -> int:
    with connect() as conn:
        cursor = conn.execute("UPDATE alerts SET read = 1 WHERE read = 0")
        conn.commit()
    return cursor.rowcount


def mark_alert_pushed(alert_id: int, pushed: bool, result: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE alerts SET pushed = ?, push_result = ? WHERE id = ?",
            (1 if pushed else 0, result[:500], alert_id),
        )
        conn.commit()


def start_run(trigger: str, kind: str = "collect") -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO runs(trigger, kind, started_at, status) VALUES(?, ?, ?, 'running')",
            (trigger, kind, now_iso()),
        )
        conn.commit()
    return int(cursor.lastrowid)


def finish_run(run_id: int, status: str, fetched: int, inserted: int, alerts: int, error: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, fetched = ?, inserted = ?, alerts = ?, error = ? "
            "WHERE id = ?",
            (now_iso(), status, fetched, inserted, alerts, error[:1000], run_id),
        )
        conn.commit()


def latest_runs(limit: int = 10) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_dict(row) for row in rows]


def dashboard_stats() -> dict[str, Any]:
    with connect() as conn:
        topics = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
        verified = conn.execute(
            "SELECT COUNT(*) AS c FROM articles WHERE verdict = 'verified'"
        ).fetchone()["c"]
        unread = conn.execute("SELECT COUNT(*) AS c FROM alerts WHERE read = 0").fetchone()["c"]
        keywords = conn.execute(
            "SELECT COUNT(*) AS c FROM keywords WHERE enabled = 1"
        ).fetchone()["c"]
        latest = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "topics": topics,
        "verified": verified,
        "unread_alerts": unread,
        "keywords": keywords,
        "latest_run": row_to_dict(latest) if latest else None,
    }
