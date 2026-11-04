from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("RADAR_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("RADAR_DB_PATH", DATA_DIR / "radar.db"))
STATIC_DIR = BASE_DIR / "app" / "static"
DISABLE_SCHEDULER = os.getenv("RADAR_DISABLE_SCHEDULER", "0") == "1"
HOST = os.getenv("RADAR_HOST", "127.0.0.1")
PORT = int(os.getenv("RADAR_PORT", "8765"))
USER_AGENT = os.getenv(
    "RADAR_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 SignalRadar/1.0",
)

DEFAULT_SETTINGS = {
    "collect_interval_minutes": 30,
    "keyword_interval_minutes": 10,
    "relevance_threshold": 55,
    "alert_min_credibility": 60,
    "max_alerts_per_keyword_run": 3,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen3:8b",
    "webhook_url": "",
    "webhook_type": "auto",
    "auto_collect": True,
    "browser_notify": True,
    "max_google_per_scope": 12,
    "max_bing_per_scope": 6,
    "max_hn_per_scope": 8,
    "max_feed_items": 6,
    "freshness_hours": 168,
    "keyword_freshness_hours": 48,
}

DEFAULT_SCOPES = [
    {
        "name": "AI 编程",
        "query": "AI编程 AI coding Cursor Copilot Claude Code 代码生成 开发者",
        "description": "AI 辅助编程、编码工具与开发者生态的动态",
    },
    {
        "name": "大模型",
        "query": "大模型 LLM GPT Claude Gemini DeepSeek Qwen 发布 更新",
        "description": "主流大模型发布、升级与能力变化",
    },
    {
        "name": "AI Agent",
        "query": "AI Agent 智能体 MCP OpenAI Anthropic 自动化",
        "description": "智能体、工具调用与自动化工作流",
    },
    {
        "name": "开源 AI",
        "query": "开源大模型 HuggingFace Llama Mistral 开源 本地部署",
        "description": "开源模型、框架与本地部署生态",
    },
]

DEFAULT_FEEDS = [
    ("OpenAI News", "https://openai.com/news/rss.xml"),
    ("Google AI Blog", "https://blog.google/technology/ai/rss/"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
    ("Cursor Changelog", "https://cursor.com/changelog/rss.xml"),
    ("Ollama Blog", "https://ollama.com/blog/rss.xml"),
    ("Ollama Releases", "https://github.com/ollama/ollama/releases.atom"),
    ("GitHub Blog", "https://github.blog/feed/"),
    ("The Decoder", "https://the-decoder.com/feed/"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("量子位", "https://www.qbitai.com/feed"),
    ("Latent Space", "https://www.latent.space/feed"),
]
