import asyncio
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db
from .config import STATIC_DIR
from .llm import OllamaClient
from .notifier import broker, test_webhook
from .pipeline import run_collect, run_keyword_check
from .scheduler import schedule_jobs, scheduler_status, start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="SignalRadar", version="1.0.0", lifespan=lifespan)


class ScopeIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=300)


class ScopeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    query: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None


class KeywordIn(BaseModel):
    term: str = Field(min_length=1, max_length=80)

    @field_validator("term")
    @classmethod
    def clean_term(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("关键词不能为空")
        return cleaned


class SettingsIn(BaseModel):
    collect_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    keyword_interval_minutes: int | None = Field(default=None, ge=2, le=1440)
    relevance_threshold: float | None = Field(default=None, ge=0, le=100)
    alert_min_credibility: float | None = Field(default=None, ge=0, le=100)
    max_alerts_per_keyword_run: int | None = Field(default=None, ge=1, le=20)
    ollama_url: str | None = None
    ollama_model: str | None = Field(default=None, min_length=1, max_length=100)
    webhook_url: str | None = Field(default=None, max_length=500)
    webhook_type: str | None = None
    auto_collect: bool | None = None
    max_google_per_scope: int | None = Field(default=None, ge=1, le=50)
    max_bing_per_scope: int | None = Field(default=None, ge=1, le=30)
    max_hn_per_scope: int | None = Field(default=None, ge=1, le=30)
    max_feed_items: int | None = Field(default=None, ge=1, le=30)
    freshness_hours: int | None = Field(default=None, ge=1, le=720)
    keyword_freshness_hours: int | None = Field(default=None, ge=1, le=720)

    @field_validator("ollama_url")
    @classmethod
    def validate_ollama_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是合法的 http/https 地址")
        return value

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("必须是合法的 http/https 地址")
        return value

    @field_validator("webhook_type")
    @classmethod
    def validate_webhook_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in {"auto", "feishu", "wecom", "dingtalk", "generic"}:
            raise ValueError("不支持的 Webhook 类型")
        return value


@app.get("/")
def index():
    file = STATIC_DIR / "index.html"
    if not file.exists():
        raise HTTPException(status_code=404, detail="前端页面不存在")
    return FileResponse(file)


@app.get("/api/health")
async def health():
    settings = db.get_settings()
    client = OllamaClient(settings.get("ollama_url", "http://localhost:11434"), settings.get("ollama_model", "qwen3:8b"))
    ollama = await client.health()
    return {
        "ok": bool(ollama.get("ok")),
        "time": datetime.now(timezone.utc).isoformat(),
        "ollama": ollama,
        "scheduler": scheduler_status(),
        "stats": db.dashboard_stats(),
    }


@app.get("/api/dashboard")
def dashboard():
    return {
        "scopes": db.list_scopes(),
        "keywords": db.list_keywords(),
        "topics": db.list_topics(limit=30),
        "alerts": db.list_alerts(limit=20),
        "settings": db.get_settings(),
        "stats": db.dashboard_stats(),
        "runs": db.latest_runs(5),
    }


@app.get("/api/topics")
def topics(
    scope: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    since_hours: int = Query(default=168, ge=1, le=720),
    verdict: str | None = Query(default=None),
    include_keyword_only: bool = Query(default=False),
):
    if verdict and verdict not in {"verified", "likely", "unverified", "suspicious"}:
        raise HTTPException(status_code=400, detail="未知的可信度筛选")
    return {
        "items": db.list_topics(scope, limit, offset, since_hours, verdict, include_keyword_only),
        "offset": offset,
        "limit": limit,
    }


@app.get("/api/topics/{article_id}")
def topic_detail(article_id: int):
    article = db.get_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="热点不存在")
    return article


@app.get("/api/scopes")
def scopes():
    return db.list_scopes()


@app.post("/api/scopes", status_code=201)
def add_scope(payload: ScopeIn):
    try:
        return db.create_scope(payload.name, payload.query, payload.description)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"范围已存在或参数无效: {exc}") from exc


@app.put("/api/scopes/{scope_id}")
def edit_scope(scope_id: int, payload: ScopeUpdate):
    updates = payload.model_dump(exclude_none=True)
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    updated = db.update_scope(scope_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="范围不存在")
    return updated


@app.delete("/api/scopes/{scope_id}")
def remove_scope(scope_id: int):
    if not db.delete_scope(scope_id):
        raise HTTPException(status_code=404, detail="范围不存在")
    return {"ok": True}


@app.get("/api/keywords")
def keywords():
    return db.list_keywords()


@app.post("/api/keywords", status_code=201)
def add_keyword(payload: KeywordIn, background: BackgroundTasks):
    try:
        keyword = db.create_keyword(payload.term)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"关键词已存在或参数无效: {exc}") from exc
    background.add_task(run_keyword_check, "keyword-added")
    return keyword


@app.patch("/api/keywords/{keyword_id}")
def toggle_keyword(keyword_id: int, enabled: bool = Query(...)):
    if not db.set_keyword_enabled(keyword_id, enabled):
        raise HTTPException(status_code=404, detail="关键词不存在")
    return {"ok": True, "enabled": enabled}


@app.delete("/api/keywords/{keyword_id}")
def remove_keyword(keyword_id: int):
    if not db.delete_keyword(keyword_id):
        raise HTTPException(status_code=404, detail="关键词不存在")
    return {"ok": True}


@app.get("/api/alerts")
def alerts(limit: int = Query(default=30, ge=1, le=100), unread: bool = False):
    return db.list_alerts(limit, unread)


@app.post("/api/alerts/{alert_id}/read")
def read_alert(alert_id: int):
    if not db.mark_alert_read(alert_id):
        raise HTTPException(status_code=404, detail="告警不存在")
    return {"ok": True}


@app.post("/api/alerts/read-all")
def read_all_alerts():
    return {"ok": True, "count": db.mark_all_alerts_read()}


@app.get("/api/settings")
def settings():
    return db.get_settings()


@app.put("/api/settings")
def update_settings(payload: SettingsIn):
    updates = payload.model_dump(exclude_none=True)
    saved = db.save_settings(updates)
    schedule_jobs()
    return saved


@app.post("/api/webhook/test")
async def webhook_test():
    settings = db.get_settings()
    ok, message = await test_webhook(settings)
    return {"ok": ok, "message": message}


@app.post("/api/collect/run", status_code=202)
def trigger_collect(
    background: BackgroundTasks,
    scope: str | None = Query(default=None, max_length=60),
    feeds: bool = Query(default=True),
):
    background.add_task(run_collect, "manual", [scope] if scope else None, feeds)
    return {"status": "started", "kind": "collect"}


@app.post("/api/keywords/check", status_code=202)
def trigger_keyword_check(background: BackgroundTasks):
    background.add_task(run_keyword_check, "manual")
    return {"status": "started", "kind": "keyword"}


@app.get("/api/runs")
def runs(limit: int = Query(default=10, ge=1, le=50)):
    return db.latest_runs(limit)


@app.get("/api/feeds")
def feeds():
    return db.list_feeds(enabled_only=False)


@app.get("/api/stream", response_class=EventSourceResponse)
async def stream(request: Request) -> AsyncIterable[ServerSentEvent]:
    queue = broker.subscribe()
    try:
        yield ServerSentEvent(comment="connected", retry=3000)
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ServerSentEvent(comment="ping")
                continue
            yield ServerSentEvent(data=payload["data"], event=payload["event"])
    finally:
        broker.unsubscribe(queue)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
