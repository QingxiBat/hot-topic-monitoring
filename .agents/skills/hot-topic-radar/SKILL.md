---
name: hot-topic-radar
description: Monitor AI hotspots and user-specified keywords with the local SignalRadar tool (Ollama qwen3:8b). Use when the user asks to track AI news, watch a keyword or company/model, find the latest AI programming/model releases, or fact-check a hot claim before sharing it.
---

# Hot Topic Radar

SignalRadar is a local-first AI news radar. It collects public news and official AI blog feeds, classifies items with local `qwen3:8b`, deduplicates stories across sources, scores credibility, and stores alerts for watched keywords.

Use this skill to answer questions such as:

- "最近 AI 编程领域有什么热点？"
- "帮我盯着 Claude Code / DeepSeek / GPT 的新消息。"
- "这条 AI 新闻是真的吗？"
- "整理今天的大模型发布动态。"
- "把最新的 AI Agent 热点发给我。"

## Location

The CLI lives in this skill at `scripts/radar.py`. It reads the SignalRadar project at the repository root and shares the same SQLite database as the web app.

If the project is not at the default relative path, set:

```bash
RADAR_PROJECT_ROOT=/path/to/hot-topic-monitoring
```

## Prerequisites

- Ollama is running and has `qwen3:8b` (`ollama list`).
- Python dependencies are installed from the project `requirements.txt`.
- Run the CLI with the project virtual environment if one exists:
  - Windows: `.venv\Scripts\python.exe .agents\skills\hot-topic-radar\scripts\radar.py ...`
  - macOS/Linux: `.venv/bin/python .agents/skills/hot-topic-radar/scripts/radar.py ...`

## Preferred Mode

If the SignalRadar web service is running at `http://127.0.0.1:8765`, add:

```bash
--api http://127.0.0.1:8765
```

This avoids competing with the web scheduler and uses the app's endpoints. Without `--api`, the CLI reads and writes the local SQLite database directly.

Always add `--json` when the output will be parsed by an agent.

## Commands

### 1. Check status

```bash
python scripts/radar.py --json status
```

Returns Ollama health, counts, scopes, keywords, and the latest run.

### 2. Read the latest hotspots

```bash
python scripts/radar.py --json topics --scope "AI 编程" --hours 72 --limit 10
```

Useful flags:

- `--scope "大模型"` — filter by a configured scope.
- `--hours 168` — look back up to 7 days.
- `--verdict verified|likely|unverified|suspicious` — filter by credibility.
- `--include-keyword-only` — include articles found only by keyword watches.

### 3. Collect fresh hotspots

```bash
python scripts/radar.py --json collect --scope "AI 编程" --wait
```

- `--scope` can be repeated in direct mode.
- `--no-feeds` skips official RSS for a faster scan.
- A full collection can take 1–3 minutes because local `qwen3:8b` classifies each item.
- If the web service is running, prefer `--api ... collect ...` so the app handles concurrency.

### 4. Scan and return results in one step (recommended)

```bash
python scripts/radar.py --json scan --scope "AI 编程" --limit 10 --hours 72
```

Use `--no-collect` to only read existing results without contacting sources.

### 5. Watch a keyword

```bash
python scripts/radar.py --json watch --term "Claude Code" --check
```

This adds the keyword and immediately runs a keyword check. Without `--check`, it only registers the keyword for the web scheduler.

Then read alert telegrams:

```bash
python scripts/radar.py --json alerts --limit 10
```

### 6. Fact-check a claim

```bash
python scripts/radar.py --json verify \
  --title "OpenAI 发布 GPT-6" \
  --source "some-blog.xyz" \
  --url "https://some-blog.xyz/post" \
  --summary "文章称 OpenAI 已在内部发布 GPT-6。" \
  --keyword "GPT-6"
```

Returns `verdict`, `credibility`, `fake_risk`, `reason`, and `what_to_check`. Treat this as an editorial aid, not ground truth.

### 7. Manage scopes and keywords

```bash
python scripts/radar.py --json scopes --action list
python scripts/radar.py --json scopes --action add --name "AI 芯片" --query "AI chip NVIDIA 芯片"
python scripts/radar.py --json scopes --action remove --name "AI 芯片"

python scripts/radar.py --json keywords --action list
python scripts/radar.py --json keywords --action add --term "Qwen"
python scripts/radar.py --json keywords --action remove --term "Qwen"
python scripts/radar.py --json keywords --action check --wait
```

## How to Present Results

- Always include the source name, publish time, and original URL.
- State the credibility label from the tool: `已核实` (verified), `可信` (likely), `待核实` (unverified), `存疑` (suspicious).
- If `also_sources` contains multiple domains, mention how many independent sources corroborate the story.
- Do not repeat a suspicious claim as fact. Say "存疑/待核实" and explain the AI's reason.
- Prefer 3–7 items, ordered by importance, not by raw feed order.
- Do not claim the tool covers "all news"; it covers the configured public sources and scopes.
- Mention that AI summaries may contain errors and the original article is authoritative.

## Quality and Safety Rules

- Never publish a webhook or secret from settings.
- Do not add a keyword that is a private individual's name or sensitive personal data without the user's explicit request.
- Do not run repeated full collections in a loop; they consume time and local model resources.
- `suspicious` items are intentionally not pushed to Webhook; they remain viewable in the app and CLI.
- If Ollama is offline, report the failure and offer to read existing cached topics instead of inventing news.
- The keyword watcher limits alerts per keyword per run to reduce noise; do not treat a missing alert as proof that nothing happened.

## Suggested Agent Workflow

1. Run `status`.
2. If the user asks for the latest hotspots: `topics` or `scan --no-collect` first, and only trigger `collect` when data is stale or the user explicitly asks for fresh collection.
3. Filter and summarize with links and credibility labels.
4. If the user asks about a specific keyword: `watch --check`, then `alerts`.
5. If a claim looks viral but unverified: `verify`, and recommend checking the primary source.

## Output Contract

All commands accept `--json` and return an object with `ok`. On failure:

```json
{"ok": false, "error": "reason"}
```

Common success shapes:

```json
{"ok": true, "count": 3, "items": [...]}
{"ok": true, "keyword": {...}, "run": {...}, "alerts": [...]}
{"ok": true, "assessment": {"verdict": "likely", "credibility": 74, "fake_risk": 20, "reason": "..."}}
```

Exit code is non-zero on errors.
