from datetime import datetime, timedelta, timezone

from app.sources import (
    RawItem,
    _parse_entries,
    _scope_hn_query,
    canonicalize_url,
    clean_html,
    decode_bing_link,
    domain_of,
    matches_ai_hint,
    normalize_title,
    title_similarity,
)


def test_canonicalize_url_removes_tracking():
    url = "https://www.example.com/a/b/?utm_source=x&id=3&fbclid=y#frag"
    assert canonicalize_url(url) == "https://www.example.com/a/b?id=3"


def test_decode_bing_link():
    url = (
        "https://www.bing.com/news/apiclick.aspx?ref=FexRss&url="
        "https%3a%2f%2fwww.theverge.com%2fstory%3fid%3d1&c=123"
    )
    assert decode_bing_link(url) == "https://www.theverge.com/story?id=1"


def test_domain_of_strips_www():
    assert domain_of("https://www.openai.com/index") == "openai.com"


def test_clean_html():
    assert clean_html("<p>Hello &amp; <b>world</b></p>") == "Hello & world"


def test_normalize_and_similarity():
    assert normalize_title("OpenAI 发布 GPT-5！ - 机器之心") == normalize_title("OpenAI发布GPT5")
    assert title_similarity("OpenAI releases GPT-5", "OpenAI releases GPT-5 today") > 0.85
    assert title_similarity("OpenAI releases GPT-5", "Apple announces iPhone 18") < 0.5
    assert title_similarity("OpenAI releases GPT-5", "OpenAI releases GPT-5.1") < 0.86


def test_parse_entries_google_source_and_freshness():
    published = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>News</title>
      <item>
        <title>Claude Code update - Example</title>
        <link>https://news.google.com/rss/articles/abc?oc=5</link>
        <pubDate>{published}</pubDate>
        <source url="https://www.example.com">Example</source>
        <description>Something about coding</description>
      </item>
    </channel></rss>"""
    items = _parse_entries(xml.encode(), "Google News", limit=10, freshness_hours=48, origin="google")
    assert len(items) == 1
    assert items[0].source == "Example"
    assert items[0].source_domain == "example.com"
    assert items[0].origin == "google"


def test_parse_entries_filters_old():
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>News</title>
      <item><title>Old news</title><link>https://example.com/old</link><pubDate>{old}</pubDate></item>
    </channel></rss>"""
    assert _parse_entries(xml.encode(), "Test", freshness_hours=48) == []


def test_scope_hn_query_uses_first_ascii_token():
    assert _scope_hn_query("大模型 LLM GPT Claude 发布") == "LLM"
    assert _scope_hn_query("AI编程 Cursor Copilot") == "AI"
    assert _scope_hn_query("人工智能") == "人工智能"


def test_matches_ai_hint():
    assert matches_ai_hint("NVIDIA launches a new GPU")
    assert matches_ai_hint("DeepSeek 发布新模型")
    assert matches_ai_hint("AI 编程工具更新")
    assert not matches_ai_hint("Apple announces iPhone 18")
    assert not matches_ai_hint("email detail")


def test_default_scopes_cover_ai_breadth():
    from app.config import DEFAULT_SCOPES

    names = [scope["name"] for scope in DEFAULT_SCOPES]
    assert len(names) == 10
    assert "AI 芯片与算力" in names
    assert "多模态与生成式" in names
    assert "AI 安全与合规" in names
    assert "AI 研究前沿" in names


def test_raw_item_to_dict():
    item = RawItem(title="T", url="https://e.com/1", source="S", source_domain="e.com")
    data = item.to_dict()
    assert data["title"] == "T"
    assert data["source_domain"] == "e.com"
    assert data["source_tier"] == "media"
    assert data["content_kind"] == "news"
