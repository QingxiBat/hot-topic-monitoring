import asyncio

from app import db, notifier


def test_detect_webhook_type():
    assert notifier.detect_webhook_type("https://open.feishu.cn/open-apis/bot/v2/hook/x") == "feishu"
    assert notifier.detect_webhook_type("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x") == "wecom"
    assert notifier.detect_webhook_type("https://oapi.dingtalk.com/robot/send?access_token=x") == "dingtalk"
    assert notifier.detect_webhook_type("https://example.com/hook") == "generic"
    assert notifier.detect_webhook_type("https://example.com/hook", "feishu") == "feishu"


def test_webhook_payload_shapes():
    feishu = notifier._webhook_payload("feishu", "hello", {})
    assert feishu["msg_type"] == "text"
    assert feishu["content"]["text"] == "hello"
    wecom = notifier._webhook_payload("wecom", "hello", {})
    assert wecom["msgtype"] == "text"
    assert wecom["text"]["content"] == "hello"
    generic = notifier._webhook_payload(
        "generic", "hello", {"title": "T", "keyword": "K", "url": "https://e.com"}
    )
    assert generic["event"] == "hot_topic_alert"
    assert generic["keyword"] == "K"
    assert generic["url"] == "https://e.com"


def test_notify_alert_pushes_critical(temp_db, monkeypatch):
    calls = []

    async def fake_send(settings, text, context):
        calls.append((settings, text, context))
        return True, "ok"

    monkeypatch.setattr(notifier, "send_webhook", fake_send)
    article_id = db.insert_article(
        {
            "url": "https://openai.com/index/x",
            "title": "OpenAI ships something",
            "source_domain": "openai.com",
            "credibility": 90,
            "verdict": "verified",
            "also_sources": ["openai.com"],
        }
    )
    alert = db.create_alert(article_id, "OpenAI", "critical", "OpenAI ships something", "官方发布")
    article = db.get_article(article_id)
    settings = db.get_settings()
    ok = asyncio.run(notifier.notify_alert(alert, article, settings))
    assert ok is True
    assert calls and "OpenAI" in calls[0][1]
    stored = db.list_alerts()[0]
    assert stored["pushed"] is True


def test_notify_alert_info_stays_in_app(temp_db, monkeypatch):
    calls = []

    async def fake_send(settings, text, context):
        calls.append(text)
        return True, "ok"

    monkeypatch.setattr(notifier, "send_webhook", fake_send)
    article_id = db.insert_article(
        {
            "url": "https://example.com/x",
            "title": "Unclear rumor",
            "source_domain": "example.com",
            "credibility": 55,
            "verdict": "unverified",
            "also_sources": ["example.com"],
        }
    )
    alert = db.create_alert(article_id, "Rumor", "info", "Unclear rumor", "待核实")
    article = db.get_article(article_id)
    ok = asyncio.run(notifier.notify_alert(alert, article, db.get_settings()))
    assert ok is False
    assert calls == []
    assert db.list_alerts()[0]["pushed"] is False
