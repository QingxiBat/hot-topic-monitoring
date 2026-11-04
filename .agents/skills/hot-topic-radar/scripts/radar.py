#!/usr/bin/env python
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(
    os.getenv("RADAR_PROJECT_ROOT", Path(__file__).resolve().parents[4])
).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from app import db
from app.llm import OllamaClient
from app.pipeline import run_collect, run_keyword_check


def output(data, as_json, human_lines=None):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif human_lines:
        print("\n".join(human_lines))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def fail(message, as_json):
    if as_json:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2))
    else:
        print(f"错误：{message}", file=sys.stderr)
    raise SystemExit(1)


def client_for(settings=None):
    settings = settings or db.get_settings()
    return OllamaClient(
        settings.get("ollama_url", "http://localhost:11434"),
        settings.get("ollama_model", "qwen3:8b"),
    )


def api_request(base, method, path, **kwargs):
    url = base.rstrip("/") + path
    with httpx.Client(timeout=kwargs.pop("timeout", 60)) as client:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()


def wait_for_run(base, kind, since, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = api_request(base, "GET", "/api/runs?limit=10")
        for run in runs:
            if run.get("kind") != kind:
                continue
            if run.get("started_at", "") < since:
                continue
            if run.get("status") != "running":
                return run
        time.sleep(2)
    return None


def cmd_status(args):
    if args.api:
        health = api_request(args.api, "GET", "/api/health")
        dashboard = api_request(args.api, "GET", "/api/dashboard")
        data = {
            "ok": health.get("ok"),
            "mode": "api",
            "api": args.api,
            "ollama": health.get("ollama"),
            "stats": dashboard.get("stats"),
            "scopes": [scope["name"] for scope in dashboard.get("scopes", [])],
            "keywords": [kw["term"] for kw in dashboard.get("keywords", [])],
            "latest_run": dashboard.get("stats", {}).get("latest_run"),
        }
    else:
        db.init_db()
        settings = db.get_settings()
        ollama = asyncio.run(client_for(settings).health())
        stats = db.dashboard_stats()
        data = {
            "ok": True,
            "mode": "direct",
            "db_path": str(db.DB_PATH),
            "ollama": ollama,
            "stats": stats,
            "scopes": [scope["name"] for scope in db.list_scopes()],
            "keywords": [kw["term"] for kw in db.list_keywords()],
            "latest_run": stats.get("latest_run"),
        }
    output(
        data,
        args.json,
        [
            f"模式：{data['mode']}",
            f"Ollama：{'在线' if data['ollama'].get('ok') else '离线'} · {data['ollama'].get('model')}",
            f"范围：{', '.join(data['scopes']) or '无'}",
            f"关键词：{', '.join(data['keywords']) or '无'}",
            f"热点：{data['stats'].get('topics', 0)} 条 · 电报：{data['stats'].get('unread_alerts', 0)} 条未读",
        ],
    )


def resolve_scope(args):
    if args.api:
        scopes = api_request(args.api, "GET", "/api/scopes")
        for scope in scopes:
            if str(scope["id"]) == str(args.id) or scope["name"] == args.name:
                return scope
        return None
    db.init_db()
    for scope in db.list_scopes():
        if str(scope["id"]) == str(args.id) or scope["name"] == args.name:
            return scope
    return None


def cmd_scopes(args):
    if args.action == "list":
        if args.api:
            scopes = api_request(args.api, "GET", "/api/scopes")
        else:
            db.init_db()
            scopes = db.list_scopes()
        output(
            {"ok": True, "scopes": scopes},
            args.json,
            [f"{scope['id']:>3}  {scope['name']}  |  {scope['query']}" for scope in scopes],
        )
        return
    if args.action == "add":
        if not args.name or not args.query:
            fail("添加范围需要 --name 和 --query", args.json)
        if args.api:
            data = api_request(
                args.api,
                "POST",
                "/api/scopes",
                json={"name": args.name, "query": args.query, "description": args.description or ""},
            )
        else:
            db.init_db()
            data = db.create_scope(args.name, args.query, args.description or "")
        output({"ok": True, "scope": data}, args.json, [f"已添加范围：{data['name']}"])
        return
    if args.action == "remove":
        scope = resolve_scope(args)
        if not scope:
            fail("没有找到该范围", args.json)
        if args.api:
            api_request(args.api, "DELETE", f"/api/scopes/{scope['id']}")
        else:
            db.delete_scope(scope["id"])
        output({"ok": True, "removed": scope["name"]}, args.json, [f"已删除范围：{scope['name']}"])
        return
    fail("未知操作", args.json)


def keyword_check_impl(args):
    if args.api:
        since = datetime.now(timezone.utc).isoformat()
        api_request(args.api, "POST", "/api/keywords/check")
        return wait_for_run(args.api, "keyword", since, args.timeout) if args.wait else {"status": "started"}
    db.init_db()
    return asyncio.run(run_keyword_check("skill"))


def cmd_keywords(args):
    if args.action == "list":
        if args.api:
            keywords = api_request(args.api, "GET", "/api/keywords")
        else:
            db.init_db()
            keywords = db.list_keywords()
        output(
            {"ok": True, "keywords": keywords},
            args.json,
            [f"{kw['id']:>3}  {kw['term']}  {'启用' if kw['enabled'] else '暂停'}" for kw in keywords],
        )
        return
    if args.action == "add":
        if not args.term:
            fail("添加关键词需要 --term", args.json)
        if args.api:
            data = api_request(args.api, "POST", "/api/keywords", json={"term": args.term})
        else:
            db.init_db()
            data = db.create_keyword(args.term)
        output({"ok": True, "keyword": data}, args.json, [f"已布防关键词：{data['term']}"])
        return
    if args.action == "remove":
        if args.api:
            keywords = api_request(args.api, "GET", "/api/keywords")
        else:
            db.init_db()
            keywords = db.list_keywords()
        target = None
        for kw in keywords:
            if str(kw["id"]) == str(args.id) or kw["term"].lower() == (args.term or "").lower():
                target = kw
                break
        if not target:
            fail("没有找到该关键词", args.json)
        if args.api:
            api_request(args.api, "DELETE", f"/api/keywords/{target['id']}")
        else:
            db.delete_keyword(target["id"])
        output({"ok": True, "removed": target["term"]}, args.json, [f"已撤防关键词：{target['term']}"])
        return
    if args.action == "check":
        result = keyword_check_impl(args)
        output(result or {"status": "timeout"}, args.json)
        return
    fail("未知操作", args.json)


def _scope_value(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def topics_impl(args):
    scopes = getattr(args, "scope", None) or None
    if args.single_source and args.min_sources:
        fail("--single-source 与 --min-sources 不能同时使用", args.json)
    if (
        args.min_credibility is not None
        and args.max_credibility is not None
        and args.min_credibility > args.max_credibility
    ):
        fail("可信度区间不合法", args.json)
    if (
        args.min_relevance is not None
        and args.max_relevance is not None
        and args.min_relevance > args.max_relevance
    ):
        fail("相关度区间不合法", args.json)
    if args.api:
        params = {
            "limit": args.limit,
            "since_hours": args.hours,
            "time_field": args.time_field,
            "sort": args.sort,
            "include_keyword_only": str(args.include_keyword_only).lower(),
            "has_keyword": str(args.has_keyword).lower(),
            "single_source": str(args.single_source).lower(),
        }
        if args.match_level:
            params["match_level"] = args.match_level
        if scopes:
            params["scope"] = scopes
        if args.verdict:
            params["verdict"] = args.verdict
        if args.tier:
            params["tier"] = args.tier
        if args.kind:
            params["kind"] = args.kind
        if args.min_credibility is not None:
            params["min_credibility"] = args.min_credibility
        if args.max_credibility is not None:
            params["max_credibility"] = args.max_credibility
        if args.min_relevance is not None:
            params["min_relevance"] = args.min_relevance
        if args.max_relevance is not None:
            params["max_relevance"] = args.max_relevance
        if args.min_sources is not None:
            params["min_sources"] = args.min_sources
        if args.keyword:
            params["keyword"] = args.keyword
        if args.q:
            params["q"] = args.q
        data = api_request(args.api, "GET", "/api/topics", params=params)
        return data.get("items", [])
    db.init_db()
    return db.list_topics(
        scope=scopes,
        limit=args.limit,
        since_hours=args.hours,
        verdict=args.verdict,
        include_keyword_only=args.include_keyword_only,
        tiers=args.tier,
        kinds=args.kind,
        min_credibility=args.min_credibility,
        max_credibility=args.max_credibility,
        min_relevance=args.min_relevance,
        max_relevance=args.max_relevance,
        min_sources=args.min_sources,
        single_source=args.single_source,
        keywords=args.keyword,
        query=args.q,
        time_field=args.time_field,
        sort=args.sort,
        has_keyword=args.has_keyword,
        match_levels=args.match_level,
    )


def _topic_line(item):
    match = item.get("best_match") or {}
    level = match.get("match_level")
    prefix = f"{level} " if level else ""
    return f"[{prefix}{item['verdict']} {round(float(item['credibility']))}] {item['title']}\n    {item['url']}"


def cmd_topics(args):
    items = topics_impl(args)
    output(
        {"ok": True, "count": len(items), "items": items},
        args.json,
        [_topic_line(item) for item in items],
    )


def cmd_alerts(args):
    if args.api:
        params = {"limit": args.limit, "unread": str(args.unread).lower()}
        alerts = api_request(args.api, "GET", "/api/alerts", params=params)
    else:
        db.init_db()
        alerts = db.list_alerts(args.limit, args.unread)
    output(
        {"ok": True, "count": len(alerts), "alerts": alerts},
        args.json,
        [
            f"[{alert['level']}] {alert['keyword']} · {alert['title']}\n    {alert.get('article_url') or ''}"
            for alert in alerts
        ],
    )


def collect_impl(args):
    if args.api:
        since = datetime.now(timezone.utc).isoformat()
        params = {"feeds": str(not args.no_feeds).lower()}
        if args.scope:
            params["scope"] = _scope_value(args.scope)
        api_request(args.api, "POST", "/api/collect/run", params=params)
        return wait_for_run(args.api, "collect", since, args.timeout) if args.wait else {"status": "started"}
    db.init_db()
    return asyncio.run(
        run_collect("skill", scope_names=args.scope or None, include_feeds=not args.no_feeds)
    )


def cmd_collect(args):
    result = collect_impl(args)
    output(result or {"status": "timeout"}, args.json)


def cmd_scan(args):
    collect_result = None if args.no_collect else collect_impl(args)
    items = topics_impl(args)
    output(
        {
            "ok": True,
            "collect": collect_result,
            "count": len(items),
            "items": items,
        },
        args.json,
        [_topic_line(item) for item in items],
    )


def cmd_verify(args):
    if not args.title:
        fail("verify 需要 --title", args.json)
    db.init_db()
    settings = db.get_settings()
    client = client_for(settings)
    prompt = f"""你是一名严谨的 AI 科技事实核查编辑。请评估下面这条内容是否真实可信。
需要特别警惕：标题党、夸张措辞、无明确信源、匿名爆料、旧闻翻炒、同名蹭热度、把观点包装成事实。

标题：{args.title}
来源：{args.source or "未知"}
链接：{args.url or "无"}
摘要：{args.summary or "无"}
用户关注的关键词：{args.keyword or "无"}

只输出 JSON 对象：
{{"verdict":"verified|likely|unverified|suspicious","credibility":0,"fake_risk":0,"reason":"不超过80字的中文判断依据","what_to_check":["建议人工核实的点"]}}
不要输出解释或思考过程。"""
    try:
        assessment = asyncio.run(client.generate_json(prompt, num_predict=700))
    except Exception as exc:
        fail(f"AI 核查失败：{exc}", args.json)
    assessment = dict(assessment)
    try:
        credibility = max(0, min(100, float(assessment.get("credibility", 50))))
    except (TypeError, ValueError):
        credibility = 50
    try:
        fake_risk = max(0, min(100, float(assessment.get("fake_risk", 50))))
    except (TypeError, ValueError):
        fake_risk = 50
    verdict = str(assessment.get("verdict") or "unverified").lower()
    if verdict == "suspicious":
        fake_risk = max(fake_risk, 65)
        credibility = min(credibility, 40)
    elif verdict == "unverified":
        fake_risk = max(fake_risk, 40)
        credibility = min(credibility, 60)
    elif verdict == "likely":
        fake_risk = min(fake_risk, 50)
        credibility = max(credibility, 55)
    elif verdict == "verified":
        fake_risk = min(fake_risk, 25)
        credibility = max(credibility, 70)
    assessment["verdict"] = verdict
    assessment["credibility"] = round(credibility, 1)
    assessment["fake_risk"] = round(fake_risk, 1)
    result = {
        "ok": True,
        "assessment": assessment,
        "disclaimer": "AI 判断仅作参考，发布前请核对原始信源。",
    }
    output(
        result,
        args.json,
        [
            f"结论：{assessment.get('verdict')} · 可信度 {assessment.get('credibility')} · 假冒风险 {assessment.get('fake_risk')}",
            f"理由：{assessment.get('reason')}",
        ],
    )


def alerts_impl(args):
    if args.api:
        params = {"limit": args.limit, "unread": str(args.unread).lower()}
        return api_request(args.api, "GET", "/api/alerts", params=params)
    db.init_db()
    return db.list_alerts(args.limit, args.unread)


def cmd_watch(args):
    if not args.term:
        fail("watch 需要 --term", args.json)
    if args.api:
        keyword = api_request(args.api, "POST", "/api/keywords", json={"term": args.term})
        result = keyword_check_impl(args) if args.check else None
    else:
        db.init_db()
        try:
            keyword = db.create_keyword(args.term)
        except Exception as exc:
            fail(f"布防失败：{exc}", args.json)
        result = asyncio.run(run_keyword_check("skill")) if args.check else None
    alerts = []
    if args.check:
        alert_args = argparse.Namespace(api=args.api, limit=5, unread=False)
        alerts = alerts_impl(alert_args)
    output(
        {"ok": True, "keyword": keyword, "run": result, "alerts": alerts},
        args.json,
        [f"已布防：{args.term}"],
    )


def cmd_run(args):
    collect_result = collect_impl(args)
    keyword_result = keyword_check_impl(
        argparse.Namespace(api=args.api, wait=args.wait, timeout=args.timeout)
    )
    output(
        {"ok": True, "collect": collect_result, "keywords": keyword_result},
        args.json,
        ["完整巡检已完成。"],
    )


def add_topic_filter_args(parser, default_hours=168, default_limit=20):
    parser.add_argument("--scope", action="append", help="范围，可重复")
    parser.add_argument("--hours", type=int, default=default_hours)
    parser.add_argument("--limit", type=int, default=default_limit)
    parser.add_argument("--verdict", action="append", choices=["verified", "likely", "unverified", "suspicious"])
    parser.add_argument("--tier", action="append", choices=["official", "release", "research", "media", "community", "heat"])
    parser.add_argument("--kind", action="append", choices=["news", "release", "research", "discussion", "video", "heat"])
    parser.add_argument("--min-credibility", type=float)
    parser.add_argument("--max-credibility", type=float)
    parser.add_argument("--min-relevance", type=float)
    parser.add_argument("--max-relevance", type=float)
    parser.add_argument("--min-sources", type=int)
    parser.add_argument("--single-source", action="store_true", default=False)
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--has-keyword", action="store_true", default=False)
    parser.add_argument("--match-level", action="append", choices=["exact", "strong", "partial", "weak"])
    parser.add_argument("--q")
    parser.add_argument("--time-field", choices=["published", "fetched"], default="published")
    parser.add_argument("--sort", choices=["composite", "newest", "fetched", "credibility", "relevance", "sources"], default="composite")
    parser.add_argument("--include-keyword-only", action="store_true", default=False)


def build_parser():
    parser = argparse.ArgumentParser(
        description="SignalRadar Agent Skill CLI：监控 AI 热点、关键词与可信度。"
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--api", default=os.getenv("RADAR_API", ""), help="SignalRadar 服务地址；为空则直接读写本地数据库")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看服务、模型与数据状态").set_defaults(func=cmd_status)

    scopes = sub.add_parser("scopes", help="管理监控范围")
    scopes.add_argument("--action", choices=["list", "add", "remove"], default="list")
    scopes.add_argument("--id")
    scopes.add_argument("--name")
    scopes.add_argument("--query")
    scopes.add_argument("--description")
    scopes.set_defaults(func=cmd_scopes)

    keywords = sub.add_parser("keywords", help="管理关键词哨兵")
    keywords.add_argument("--action", choices=["list", "add", "remove", "check"], default="list")
    keywords.add_argument("--id")
    keywords.add_argument("--term")
    keywords.add_argument("--wait", action="store_true", default=False)
    keywords.add_argument("--timeout", type=int, default=300)
    keywords.set_defaults(func=cmd_keywords)

    topics = sub.add_parser("topics", help="读取已发现的热点")
    add_topic_filter_args(topics, default_hours=168, default_limit=20)
    topics.set_defaults(func=cmd_topics)

    alerts = sub.add_parser("alerts", help="读取关键词电报")
    alerts.add_argument("--limit", type=int, default=20)
    alerts.add_argument("--unread", action="store_true", default=False)
    alerts.set_defaults(func=cmd_alerts)

    collect = sub.add_parser("collect", help="立即采集一轮热点")
    collect.add_argument("--scope", action="append", help="只采集指定范围，可重复")
    collect.add_argument("--no-feeds", action="store_true", help="不抓取官方 RSS")
    collect.add_argument("--wait", action="store_true", default=True, help="API 模式下等待完成")
    collect.add_argument("--no-wait", dest="wait", action="store_false")
    collect.add_argument("--timeout", type=int, default=300)
    collect.set_defaults(func=cmd_collect)

    scan = sub.add_parser("scan", help="采集并返回最新热点（推荐入口）")
    add_topic_filter_args(scan, default_hours=72, default_limit=10)
    scan.add_argument("--no-collect", action="store_true", help="只读数据库，不触发采集")
    scan.add_argument("--no-feeds", action="store_true")
    scan.add_argument("--wait", action="store_true", default=True)
    scan.add_argument("--timeout", type=int, default=300)
    scan.set_defaults(func=cmd_scan)

    verify = sub.add_parser("verify", help="让 AI 核查一条内容的真实性")
    verify.add_argument("--title", required=True)
    verify.add_argument("--summary", default="")
    verify.add_argument("--source", default="")
    verify.add_argument("--url", default="")
    verify.add_argument("--keyword", default="")
    verify.set_defaults(func=cmd_verify)

    watch = sub.add_parser("watch", help="布防一个关键词并可立即检查")
    watch.add_argument("--term", required=True)
    watch.add_argument("--check", action="store_true", help="布防后立即巡检")
    watch.add_argument("--wait", action="store_true", default=True)
    watch.add_argument("--no-wait", dest="wait", action="store_false")
    watch.add_argument("--timeout", type=int, default=300)
    watch.set_defaults(func=cmd_watch)

    run = sub.add_parser("run", help="完整巡检：采集热点 + 关键词")
    run.add_argument("--scope", action="append")
    run.add_argument("--no-feeds", action="store_true")
    run.add_argument("--wait", action="store_true", default=True)
    run.add_argument("--timeout", type=int, default=300)
    run.set_defaults(func=cmd_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
        if result is not None and getattr(args, "func", None) in {cmd_collect, cmd_run}:
            pass
    except SystemExit:
        raise
    except Exception as exc:
        fail(str(exc), getattr(args, "json", False))


if __name__ == "__main__":
    main()
