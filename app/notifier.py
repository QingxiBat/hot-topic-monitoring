import asyncio
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from . import db


class EventBroker:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data, "ts": datetime.now().isoformat()}
        for queue in list(self._clients):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


broker = EventBroker()


def detect_webhook_type(url: str, configured: str = "auto") -> str:
    if configured and configured != "auto":
        return configured
    host = urlparse(url).netloc.lower()
    if "feishu" in host or "larksuite" in host:
        return "feishu"
    if "dingtalk" in host:
        return "dingtalk"
    if "weixin" in host or "wecom" in host:
        return "wecom"
    return "generic"


def _webhook_payload(kind: str, text: str, context: dict[str, Any]) -> dict[str, Any]:
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if kind in {"wecom", "dingtalk"}:
        return {"msgtype": "text", "text": {"content": text}}
    return {
        "event": "hot_topic_alert",
        "text": text,
        "title": context.get("title", ""),
        "keyword": context.get("keyword", ""),
        "level": context.get("level", "info"),
        "verdict": context.get("verdict", ""),
        "url": context.get("url", ""),
        "summary": context.get("summary", ""),
        "published_at": context.get("published_at"),
        "created_at": context.get("created_at"),
    }


async def send_webhook(settings: dict[str, Any], text: str, context: dict[str, Any]) -> tuple[bool, str]:
    url = (settings.get("webhook_url") or "").strip()
    if not url:
        return False, "未配置 Webhook"
    if urlparse(url).scheme not in {"http", "https"}:
        return False, "Webhook 必须是 http/https 地址"
    kind = detect_webhook_type(url, settings.get("webhook_type", "auto"))
    payload = _webhook_payload(kind, text, context)
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.post(url, json=payload)
        body = response.text[:300]
        if response.status_code >= 300:
            return False, f"HTTP {response.status_code}: {body}"
        if body.strip().startswith("{"):
            try:
                data = json.loads(body)
                code = data.get("errcode", data.get("code", 0))
                if code not in (0, "0", None):
                    return False, f"平台返回错误 {code}: {body}"
            except json.JSONDecodeError:
                pass
        return True, f"HTTP {response.status_code}"
    except Exception as exc:
        return False, str(exc)


async def test_webhook(settings: dict[str, Any]) -> tuple[bool, str]:
    text = "【SignalRadar】Webhook 连通性测试成功。热点信号将在这里抵达。"
    return await send_webhook(settings, text, {"title": "Webhook 测试", "level": "info"})


def _level_label(level: str) -> str:
    return {"critical": "已核实", "warning": "高可信", "info": "待核实"}.get(level, "信号")


async def notify_alert(alert: dict[str, Any], article: dict[str, Any], settings: dict[str, Any]) -> bool:
    broker.publish(
        "alert",
        {
            "alert": alert,
            "article": {
                "id": article.get("id"),
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source"),
                "verdict": article.get("verdict"),
                "credibility": article.get("credibility"),
                "summary": article.get("ai_summary") or article.get("summary"),
            },
        },
    )
    if alert.get("level") == "info":
        db.mark_alert_pushed(alert["id"], False, "待核实内容仅站内提示")
        return False
    if float(article.get("credibility") or 0) < float(settings.get("alert_min_credibility", 60)):
        db.mark_alert_pushed(alert["id"], False, "低于推送可信度阈值")
        return False
    text = (
        f"【SignalRadar · {_level_label(alert['level'])}】{alert['keyword']}\n"
        f"{article['title']}\n"
        f"{(article.get('ai_summary') or article.get('summary') or '')[:120]}\n"
        f"{article['url']}"
    )
    ok, result = await send_webhook(
        settings,
        text,
        {
            "title": article["title"],
            "keyword": alert["keyword"],
            "level": alert["level"],
            "verdict": article.get("verdict"),
            "url": article.get("url"),
            "summary": article.get("ai_summary") or article.get("summary"),
            "published_at": article.get("published_at"),
            "created_at": alert.get("created_at"),
        },
    )
    db.mark_alert_pushed(alert["id"], ok, result)
    return ok
