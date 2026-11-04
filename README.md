# SignalRadar · 热点雷达

本地优先的 AI 热点监测工具。自动抓取公开新闻与 AI 官方博客，用本地 Ollama `qwen3:8b` 做分类、去重、可信度判断，并在关键词命中时发送站内 / 浏览器 / Webhook 通知。

## 文档

- [需求文档](docs/01-需求文档.md)
- [技术方案（含 MCP 最新资料核验）](docs/02-技术方案.md)

## 功能

- **关键词哨兵**：输入关键词，定时巡检 Google News、Bing News、Hacker News；AI 判断是否真的相关，并识别标题党、同名蹭热度、旧闻翻炒等风险。
- **范围热点**：按「AI 编程 / 大模型 / AI Agent / 开源 AI」等范围定时采集，自动去重、跨源印证、排序展示。
- **分级告警**：已核实 / 高可信内容写入 Webhook；待核实内容只进站内电报，不打扰。
- **响应式 Web 页面**：暖色编辑部风格，Canvas-free 雷达动画、电报流、实时 SSE 推送。
- **Agent Skills**：附带命令行技能，可交给其他 AI 监控和发现热点。

## 快速开始

```powershell
cd hot-topic-monitoring
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3:8b
.\.venv\Scripts\python.exe main.py
```

打开 http://127.0.0.1:8765

首次启动会自动执行一轮关键词巡检与热点采集。也可以在页面点击「采集新信号」。

## 配置

页面右上角「设置手记」可配置：

- 采集间隔、关键词巡检间隔
- 关键词相关度门槛、Webhook 最低可信度
- 单关键词单轮最多电报数
- Ollama 地址与模型
- 飞书 / 企业微信 / 钉钉 / 通用 Webhook
- 监控范围与关键词

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RADAR_HOST` | `127.0.0.1` | 监听地址 |
| `RADAR_PORT` | `8765` | 监听端口 |
| `RADAR_DB_PATH` | `data/radar.db` | SQLite 路径 |
| `RADAR_DISABLE_SCHEDULER` | `0` | 设为 `1` 关闭后台定时任务 |
| `RADAR_API` | 空 | Agent Skill 默认调用的服务地址 |

## 数据源

无需任何 API Key：

- Google News RSS（`when:7d` / `when:3d`）
- Bing News RSS
- Hacker News Algolia API
- 精选 RSS：OpenAI、Google AI、DeepMind、Hugging Face、Simon Willison、Cursor Changelog、Ollama、GitHub Blog、The Decoder、TechCrunch AI、The Verge AI、量子位、Latent Space

## 可信度与反假冒

每条内容经过三层校验：

1. **来源分层**：官方域名 / 头部媒体 / 中文科技媒体 / 社区 / 未知来源。
2. **跨源印证**：标题相似度聚合，同一事件被多个独立域名报道时提升可信度。
3. **AI 审阅**：`qwen3:8b` 输出相关度、可信度、标题党标记、内容类型与假冒风险。

判定结果：

- `verified` 已核实：官方来源，或多源印证且分数足够
- `likely` 可信：来源与模型判断均达到阈值
- `unverified` 待核实：仅站内展示，不 Webhook 推送
- `suspicious` 存疑：识别为高风险内容，仅入库展示

AI 判断不是事实本身。页面与 CLI 都会提示「以原文为准」。

## Agent Skills

技能位于：

```
.agents/skills/hot-topic-radar/
├── SKILL.md
└── scripts/radar.py
```

示例：

```powershell
# 查看状态
.\.venv\Scripts\python.exe .agents\skills\hot-topic-radar\scripts\radar.py --json status

# 采集并返回 AI 编程热点
.\.venv\Scripts\python.exe .agents\skills\hot-topic-radar\scripts\radar.py --json scan --scope "AI 编程" --limit 10

# 布防关键词并立即巡检
.\.venv\Scripts\python.exe .agents\skills\hot-topic-radar\scripts\radar.py --json watch --term "Claude Code" --check

# 核查一条爆料
.\.venv\Scripts\python.exe .agents\skills\hot-topic-radar\scripts\radar.py --json verify --title "..." --source "..." --url "..."
```

如果网页版正在运行，建议加 `--api http://127.0.0.1:8765`，让服务统一处理并避免与定时任务竞争。

## 项目结构

```
app/
  main.py        FastAPI 路由、SSE
  pipeline.py    去重、可信度、采集与关键词流程
  sources.py     RSS / 新闻源解析
  llm.py         Ollama 结构化输出
  db.py          SQLite
  scheduler.py   APScheduler 定时任务
  notifier.py    SSE 广播与 Webhook
  static/        响应式前端
docs/            需求文档与技术方案
tests/           pytest 测试
.agents/skills/hot-topic-radar/
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖：URL 规范化、标题相似度、RSS 解析、去重合并、可信度规则、LLM 输出映射、API、告警与 Webhook 载荷、采集/关键词工作流。

## API

- `GET /api/dashboard` 聚合首屏数据
- `GET /api/topics` 热点列表
- `GET /api/keywords`、`POST /api/keywords`、`DELETE /api/keywords/{id}`
- `GET /api/alerts`、`POST /api/alerts/{id}/read`
- `GET /api/settings`、`PUT /api/settings`
- `POST /api/collect/run`、`POST /api/keywords/check`
- `GET /api/stream` SSE 实时推送
- `POST /api/webhook/test` Webhook 连通性测试

## 限制

- 只聚合公开 RSS / API，不保证覆盖全网。
- `qwen3:8b` 是本地小模型，摘要和判断可能出错，尤其是反讽、长文和复杂传闻。
- 关键词巡检默认 10 分钟一轮；真正的「第一时间」受数据源更新频率限制。
- 本项目默认只监听 `127.0.0.1`，请勿在未加认证的情况下暴露到公网。
