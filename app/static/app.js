const $ = (id) => document.getElementById(id);

const VERDICT_LABELS = {
  verified: "已核实",
  likely: "可信",
  unverified: "待核实",
  suspicious: "存疑",
};

const CLAIM_LABELS = {
  official: "官方发布",
  third_party: "媒体报道",
  rumor: "传闻",
  opinion: "观点",
  marketing: "营销",
  unknown: "类型不明",
};

const LEVEL_LABELS = {
  critical: "已核实",
  warning: "高可信",
  info: "待核实",
};

const KIND_LABELS = {
  news: "新闻",
  research: "论文",
  release: "项目更新",
  discussion: "社区讨论",
  video: "视频",
  heat: "热榜信号",
};

const TIER_LABELS = {
  official: "官方",
  release: "项目发布",
  media: "媒体",
  community: "社区",
  heat: "热榜",
  research: "研究",
};

const state = {
  scopes: [],
  keywords: [],
  topics: [],
  alerts: [],
  settings: {},
  stats: {},
  health: null,
  activeScope: null,
  verdict: "",
  limit: 30,
  hasMore: false,
  loading: false,
  requestSeq: 0,
  stream: null,
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function safeUrl(value) {
  if (!value) return "#";
  try {
    const url = new URL(value, window.location.href);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return escapeHtml(url.href);
    }
  } catch (error) {
    return "#";
  }
  return "#";
}

function formatRelative(iso) {
  if (!iso) return "时间未知";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

function formatToday() {
  const now = new Date();
  const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
  return `${now.getFullYear()} 年 ${now.getMonth() + 1} 月 ${now.getDate()} 日 · 星期${weekdays[now.getDay()]}`;
}

function debounce(fn, wait = 400) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (error) {
    data = { detail: text };
  }
  if (!response.ok) {
    throw new Error(data.detail || `请求失败（${response.status}）`);
  }
  return data;
}

function toast(message, timeout = 5000) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), timeout);
}

function credDots(credibility) {
  const active = Math.max(0, Math.min(5, Math.round(Number(credibility || 0) / 20)));
  return Array.from({ length: 5 }, (_, index) => `<i class="${index < active ? "on" : ""}"></i>`).join("");
}

function renderStatus() {
  const keywordCount = state.keywords.filter((item) => item.enabled).length;
  const scopeCount = state.scopes.filter((item) => item.enabled).length;
  const online = state.health ? state.health.ok : true;
  $("statusLed").classList.toggle("off", !online);
  $("statusText").textContent = online
    ? `监听中 · ${scopeCount} 个范围 · ${keywordCount} 个关键词`
    : "本地模型离线 · 采集降级";

  if (state.health) {
    const ollama = state.health.ollama || {};
    $("ollamaStatus").textContent = ollama.ok
      ? `Ollama 在线 · ${ollama.model}${ollama.available === false ? "（模型未找到）" : ""}`
      : `Ollama 离线：${ollama.error || "无法连接"}`;
  }

  const latest = (state.stats && state.stats.latest_run) || (state.runs && state.runs[0]);
  if (latest) {
    const kind = latest.kind === "keyword" ? "关键词巡检" : "热点采集";
    if (latest.status === "running") {
      $("runStatus").textContent = `${kind}进行中 · ${formatRelative(latest.started_at)}`;
    } else {
      const extra = latest.kind === "keyword" ? `${latest.alerts} 条电报` : `新增 ${latest.inserted} 条`;
      $("runStatus").textContent = `上次${kind} · ${formatRelative(latest.started_at)} · ${extra}`;
    }
  }
}

function renderScopes() {
  const container = $("scopeTabs");
  const tabs = [{ name: null, label: "全部" }].concat(
    state.scopes.map((scope) => ({ name: scope.name, label: scope.name }))
  );
  if (state.activeScope && !state.scopes.some((scope) => scope.name === state.activeScope)) {
    state.activeScope = null;
  }
  container.innerHTML = tabs
    .map(
      (tab) =>
        `<button type="button" class="scope-tab${state.activeScope === tab.name ? " active" : ""}" data-scope="${escapeHtml(tab.name || "")}">${escapeHtml(tab.label)}</button>`
    )
    .join("");
}

function renderTicker() {
  const titles = state.topics.slice(0, 6).map((topic) => topic.title).filter(Boolean);
  const track = $("tickerTrack");
  if (!titles.length) {
    track.textContent = "正在监听第一声信号…";
    return;
  }
  track.textContent = `${titles.join("　◆　")}　◆　`;
}

function topicMarkup(topic, index) {
  const verdict = VERDICT_LABELS[topic.verdict] ? topic.verdict : "unverified";
  const stamp = VERDICT_LABELS[verdict];
  const scopes = (topic.scope_names || []).join(" / ") || "关键词雷达";
  const summary = topic.ai_summary || topic.summary || "暂无摘要";
  const tags = (topic.tags || []).slice(0, 4);
  const sources = Array.isArray(topic.also_sources) ? topic.also_sources.filter(Boolean) : [];
  const sourceText = topic.source || topic.source_domain || "未知来源";
  const domain = topic.source_domain ? `<i>${escapeHtml(topic.source_domain)}</i>` : "";
  const corroboration = sources.length > 1
    ? `<span class="corro">${sources.length} 家独立来源</span>`
    : '<span class="corro">单一来源</span>';
  const time = formatRelative(topic.published_at || topic.fetched_at);
  const tagsMarkup = tags.length
    ? `<div class="topic-tags">${tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>`
    : "";
  const reason = topic.verdict_reason || "暂无判断依据";
  const claim = CLAIM_LABELS[topic.claim_type] || CLAIM_LABELS.unknown;
  const kind = KIND_LABELS[topic.content_kind] || KIND_LABELS.news;
  const tier = TIER_LABELS[topic.source_tier] || TIER_LABELS.media;
  return `
    <article class="topic" data-id="${topic.id}" style="--i:${index}">
      <span class="topic-stamp stamp-${verdict}">${stamp}</span>
      <div class="topic-kicker">
        <span class="topic-index">${String(index + 1).padStart(2, "0")}</span>
        <span class="dot-sep"></span>
        <span>${escapeHtml(scopes)}</span>
        <span class="dot-sep"></span>
        <time>${escapeHtml(time)}</time>
      </div>
      <h3><a href="${safeUrl(topic.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(topic.title)}</a></h3>
      <p class="topic-summary">${escapeHtml(summary)}</p>
      <div class="topic-foot">
        <span class="topic-source">${escapeHtml(sourceText)} ${domain}</span>
        <span>可信度 <b>${Math.round(Number(topic.credibility || 0))}</b> <span class="cred-dots">${credDots(topic.credibility)}</span></span>
        ${corroboration}
        <span class="topic-kind kind-${escapeHtml(topic.content_kind || "news")}">${escapeHtml(tier)} · ${escapeHtml(kind)}</span>
        <span>${escapeHtml(claim)}</span>
      </div>
      ${tagsMarkup}
      <details class="topic-more">
        <summary>判断依据</summary>
        <p>${escapeHtml(reason)}</p>
        <p>AI 摘要由本地 qwen3:8b 生成，请以原文为准。</p>
      </details>
    </article>
  `;
}

function renderTopics() {
  const list = $("topicList");
  const empty = $("wireEmpty");
  if (!state.topics.length) {
    list.innerHTML = "";
    empty.hidden = false;
  } else {
    empty.hidden = true;
    list.innerHTML = state.topics.map(topicMarkup).join("");
  }
  $("wireCount").textContent = state.topics.length
    ? `近七天 · ${state.topics.length} 条信号`
    : "";
  $("loadMoreBtn").hidden = !state.hasMore || state.topics.length === 0;
  renderTicker();
}

function renderRadarPips() {
  const container = $("radarPips");
  const radar = document.querySelector(".radar");
  const keywords = state.keywords.filter((item) => item.enabled).slice(0, 8);
  if (!keywords.length || !radar) {
    container.innerHTML = "";
    return;
  }
  const size = radar.clientWidth || 220;
  container.innerHTML = keywords
    .map((keyword, index) => {
      const angle = (360 / keywords.length) * index - 90;
      const radius = size * (0.19 + (index % 3) * 0.075);
      return `<span class="radar-pip" style="--angle:${angle}deg;--radius:${radius.toFixed(1)}px;--delay:${(index * 0.24).toFixed(2)}s"><p>${escapeHtml(keyword.term)}</p></span>`;
    })
    .join("");
}

function renderKeywords() {
  const list = $("keywordList");
  if (!state.keywords.length) {
    list.innerHTML = '<li class="mini-empty">还没有哨兵。输入一个关键词，让它替你盯着。</li>';
  } else {
    list.innerHTML = state.keywords
      .map(
        (keyword) => `
        <li class="keyword-item${keyword.enabled ? "" : " muted"}" data-id="${keyword.id}">
          <span class="kw-dot"></span>
          <button class="kw-term" type="button" data-action="toggle" title="点击${keyword.enabled ? "暂停" : "恢复"}监控">${escapeHtml(keyword.term)}</button>
          <span class="kw-time">${keyword.last_checked_at ? formatRelative(keyword.last_checked_at) : "未巡检"}</span>
          <button class="kw-del" type="button" data-action="delete" aria-label="删除关键词">×</button>
        </li>
      `
      )
      .join("");
  }
  renderRadarPips();
}

function renderAlerts() {
  const list = $("alertList");
  const empty = $("alertEmpty");
  if (!state.alerts.length) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  list.innerHTML = state.alerts
    .map((alert) => {
      const level = alert.level in LEVEL_LABELS ? alert.level : "info";
      const credibility = alert.credibility != null ? Math.round(Number(alert.credibility)) : null;
      const metaParts = [];
      if (alert.source) metaParts.push(escapeHtml(alert.source));
      if (credibility != null) metaParts.push(`可信度 ${credibility}`);
      metaParts.push(alert.pushed ? "已推送" : "仅站内");
      return `
        <li class="alert-item${alert.read ? "" : " unread"}" data-id="${alert.id}" data-url="${safeUrl(alert.article_url)}">
          <div class="alert-top">
            <span class="alert-level level-${level}">${LEVEL_LABELS[level]}</span>
            <span>${escapeHtml(alert.keyword)}</span>
            <span style="margin-left:auto">${escapeHtml(formatRelative(alert.created_at))}</span>
          </div>
          <p class="alert-title">${escapeHtml(alert.title)}</p>
          <div class="alert-meta">${metaParts.join(" · ")}</div>
        </li>
      `;
    })
    .join("");
}

function fillSettings() {
  const settings = state.settings || {};
  $("collectInterval").value = settings.collect_interval_minutes ?? 30;
  $("keywordInterval").value = settings.keyword_interval_minutes ?? 10;
  $("relevanceThreshold").value = settings.relevance_threshold ?? 55;
  $("relevanceOut").textContent = settings.relevance_threshold ?? 55;
  $("alertCredibility").value = settings.alert_min_credibility ?? 60;
  $("alertCredOut").textContent = settings.alert_min_credibility ?? 60;
  $("maxAlerts").value = settings.max_alerts_per_keyword_run ?? 3;
  $("autoCollect").checked = settings.auto_collect !== false;
  $("browserNotify").checked = settings.browser_notify !== false;
  $("ollamaUrl").value = settings.ollama_url || "http://localhost:11434";
  $("ollamaModel").value = settings.ollama_model || "qwen3:8b";
  $("webhookUrl").value = settings.webhook_url || "";
  $("webhookType").value = settings.webhook_type || "auto";
  renderScopeManage();
}

function renderScopeManage() {
  const container = $("scopeManage");
  if (!state.scopes.length) {
    container.innerHTML = '<div class="mini-empty">暂无范围，先添加一个吧。</div>';
    return;
  }
  container.innerHTML = state.scopes
    .map(
      (scope) => `
      <div class="scope-row">
        <span class="scope-name">${escapeHtml(scope.name)}</span>
        <span class="scope-query">${escapeHtml(scope.query)}</span>
        <button class="scope-del" type="button" data-id="${scope.id}" aria-label="删除范围">删除</button>
      </div>
    `
    )
    .join("");
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  state.scopes = data.scopes || [];
  state.keywords = data.keywords || [];
  state.topics = data.topics || [];
  state.alerts = data.alerts || [];
  state.settings = data.settings || {};
  state.stats = data.stats || {};
  state.runs = data.runs || [];
  state.hasMore = state.topics.length >= state.limit;
  if (state.activeScope && !state.scopes.some((scope) => scope.name === state.activeScope)) {
    state.activeScope = null;
  }
  renderScopes();
  renderTopics();
  renderKeywords();
  renderAlerts();
  renderStatus();
  if ($("settingsDialog").open) fillSettings();
}

async function loadHealth() {
  try {
    state.health = await api("/api/health");
    state.stats = state.health.stats || state.stats;
    renderStatus();
  } catch (error) {
    state.health = { ok: false, ollama: { ok: false, error: error.message } };
    renderStatus();
  }
}

async function fetchTopics({ reset = false } = {}) {
  const seq = ++state.requestSeq;
  state.loading = true;
  const offset = reset ? 0 : state.topics.length;
  const params = new URLSearchParams({
    limit: String(state.limit),
    offset: String(offset),
    since_hours: "168",
  });
  if (state.activeScope) params.set("scope", state.activeScope);
  if (state.verdict) params.set("verdict", state.verdict);
  try {
    const data = await api(`/api/topics?${params.toString()}`);
    if (seq !== state.requestSeq) return;
    const items = data.items || [];
    state.topics = reset ? items : state.topics.concat(items);
    state.hasMore = items.length >= state.limit;
    renderTopics();
  } catch (error) {
    if (seq === state.requestSeq) toast(`读取热点失败：${error.message}`);
  } finally {
    if (seq === state.requestSeq) state.loading = false;
  }
}

async function saveSettings({ silent = false } = {}) {
  const payload = {
    collect_interval_minutes: Number($("collectInterval").value),
    keyword_interval_minutes: Number($("keywordInterval").value),
    relevance_threshold: Number($("relevanceThreshold").value),
    alert_min_credibility: Number($("alertCredibility").value),
    max_alerts_per_keyword_run: Number($("maxAlerts").value),
    auto_collect: $("autoCollect").checked,
    browser_notify: $("browserNotify").checked,
    ollama_url: $("ollamaUrl").value.trim(),
    ollama_model: $("ollamaModel").value.trim(),
    webhook_url: $("webhookUrl").value.trim(),
    webhook_type: $("webhookType").value,
  };
  const saved = await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
  state.settings = saved;
  if (!silent) {
    $("dialogStatus").textContent = "已保存，将在下一轮生效";
    toast("设置手记已保存");
  }
  return saved;
}

function openSettings() {
  fillSettings();
  $("dialogStatus").textContent = "";
  $("settingsDialog").showModal();
}

function updateNotifyButton() {
  const button = $("notifyBtn");
  if (!("Notification" in window)) {
    button.textContent = "不支持通知";
    button.disabled = true;
    return;
  }
  if (Notification.permission === "granted") {
    button.textContent = "桌面通知已开";
  } else if (Notification.permission === "denied") {
    button.textContent = "通知被拦截";
  } else {
    button.textContent = "桌面通知";
  }
}

async function requestNotifyPermission() {
  if (!("Notification" in window)) {
    toast("当前浏览器不支持桌面通知");
    return;
  }
  if (Notification.permission === "granted") {
    toast("桌面通知已开启");
    return;
  }
  if (Notification.permission === "denied") {
    toast("通知已被浏览器拦截，请在站点设置中恢复");
    return;
  }
  const permission = await Notification.requestPermission();
  updateNotifyButton();
  toast(permission === "granted" ? "桌面通知已开启" : "未获得通知权限");
}

function maybeNotify(alert, article) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (state.settings.browser_notify === false) return;
  if (alert.level === "info") return;
  const label = LEVEL_LABELS[alert.level] || "信号";
  const notification = new Notification(`【${label}】${alert.keyword}`, {
    body: article.title || alert.title,
    tag: `radar-alert-${alert.id}`,
  });
  notification.onclick = () => {
    if (article.url) window.open(article.url, "_blank", "noopener");
    notification.close();
  };
}

function handleAlertEvent(payload) {
  const alert = payload.alert;
  const article = payload.article || {};
  if (!alert) return;
  if (!state.alerts.some((item) => item.id === alert.id)) {
    state.alerts.unshift({
      ...alert,
      article_url: article.url,
      source: article.source,
      verdict: article.verdict,
      credibility: article.credibility,
    });
    state.alerts = state.alerts.slice(0, 30);
  }
  renderAlerts();
  renderStatus();
  toast(`${LEVEL_LABELS[alert.level] || "信号"}｜${alert.keyword}：${article.title || alert.title}`, 7000);
  maybeNotify(alert, article);
  setTimeout(() => fetchTopics({ reset: true }), 1200);
}

function connectStream() {
  if (!window.EventSource) return;
  const stream = new EventSource("/api/stream");
  state.stream = stream;
  stream.addEventListener("alert", (event) => {
    try {
      handleAlertEvent(JSON.parse(event.data));
    } catch (error) {
      console.warn("alert parse failed", error);
    }
  });
  stream.addEventListener("topics", (event) => {
    try {
      const data = JSON.parse(event.data);
      toast(data.inserted > 0 ? `采集完成，新增 ${data.inserted} 条信号` : "采集完成，暂无新信号");
      fetchTopics({ reset: true });
      loadHealth();
    } catch (error) {
      console.warn("topics parse failed", error);
    }
  });
  stream.addEventListener("keywords", (event) => {
    try {
      const data = JSON.parse(event.data);
      toast(data.alerts > 0 ? `巡检完成，发出 ${data.alerts} 条电报` : "巡检完成，没有新的相关信号");
      refreshKeywordsAndAlerts();
      fetchTopics({ reset: true });
    } catch (error) {
      console.warn("keywords parse failed", error);
    }
  });
  stream.addEventListener("error", (event) => {
    if (event.data) {
      try {
        toast(JSON.parse(event.data).message || "后台任务出错");
      } catch (error) {
        toast("后台任务出错");
      }
    }
  });
  stream.onerror = () => {
    $("statusLed").classList.add("off");
  };
  stream.onopen = () => {
    $("statusLed").classList.remove("off");
  };
}

async function refreshKeywordsAndAlerts() {
  try {
    const [keywords, alerts] = await Promise.all([api("/api/keywords"), api("/api/alerts?limit=30")]);
    state.keywords = keywords;
    state.alerts = alerts;
    renderKeywords();
    renderAlerts();
    renderStatus();
  } catch (error) {
    console.warn(error);
  }
}

function bindEvents() {
  $("scopeTabs").addEventListener("click", (event) => {
    const button = event.target.closest(".scope-tab");
    if (!button) return;
    state.activeScope = button.dataset.scope || null;
    renderScopes();
    fetchTopics({ reset: true });
  });

  $("verdictFilter").addEventListener("change", (event) => {
    state.verdict = event.target.value;
    fetchTopics({ reset: true });
  });

  $("refreshBtn").addEventListener("click", async () => {
    const button = $("refreshBtn");
    button.disabled = true;
    button.textContent = "采集员出发…";
    try {
      await api("/api/collect/run", { method: "POST" });
      toast("采集员已出发，信号稍后见报");
    } catch (error) {
      toast(`触发采集失败：${error.message}`);
    }
    setTimeout(() => {
      button.disabled = false;
      button.textContent = "采集新信号";
    }, 4000);
  });

  $("keywordCheckBtn").addEventListener("click", async () => {
    const button = $("keywordCheckBtn");
    button.disabled = true;
    try {
      await api("/api/keywords/check", { method: "POST" });
      toast("哨兵开始巡检");
    } catch (error) {
      toast(`巡检失败：${error.message}`);
    }
    setTimeout(() => { button.disabled = false; }, 3000);
  });

  $("keywordForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("keywordInput");
    const term = input.value.trim();
    if (!term) return;
    try {
      const created = await api("/api/keywords", { method: "POST", body: JSON.stringify({ term }) });
      input.value = "";
      state.keywords.push(created);
      renderKeywords();
      toast("已布防，第一轮扫描马上开始");
      refreshKeywordsAndAlerts();
    } catch (error) {
      toast(error.message);
    }
  });

  $("keywordList").addEventListener("click", async (event) => {
    const row = event.target.closest(".keyword-item");
    if (!row) return;
    const id = row.dataset.id;
    const action = event.target.dataset.action;
    if (!action) return;
    if (action === "delete") {
      if (!window.confirm("撤下这名哨兵？")) return;
      try {
        await api(`/api/keywords/${id}`, { method: "DELETE" });
        await refreshKeywordsAndAlerts();
        toast("关键词已撤防");
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    if (action === "toggle") {
      const keyword = state.keywords.find((item) => String(item.id) === String(id));
      if (!keyword) return;
      try {
        await api(`/api/keywords/${id}?enabled=${keyword.enabled ? "false" : "true"}`, { method: "PATCH" });
        await refreshKeywordsAndAlerts();
      } catch (error) {
        toast(error.message);
      }
    }
  });

  $("alertList").addEventListener("click", async (event) => {
    const item = event.target.closest(".alert-item");
    if (!item) return;
    const id = item.dataset.id;
    try {
      await api(`/api/alerts/${id}/read`, { method: "POST" });
    } catch (error) {
      console.warn(error);
    }
    item.classList.remove("unread");
    const target = state.alerts.find((alert) => String(alert.id) === String(id));
    if (target) target.read = true;
    if (item.dataset.url && item.dataset.url !== "#") {
      window.open(item.dataset.url, "_blank", "noopener");
    }
  });

  $("readAllBtn").addEventListener("click", async () => {
    try {
      await api("/api/alerts/read-all", { method: "POST" });
      state.alerts = state.alerts.map((alert) => ({ ...alert, read: true }));
      renderAlerts();
      renderStatus();
    } catch (error) {
      toast(error.message);
    }
  });

  $("loadMoreBtn").addEventListener("click", () => fetchTopics({ reset: false }));

  $("settingsBtn").addEventListener("click", openSettings);
  $("addScopeBtn").addEventListener("click", () => {
    openSettings();
    setTimeout(() => $("scopeName").focus(), 80);
  });

  $("notifyBtn").addEventListener("click", requestNotifyPermission);

  $("relevanceThreshold").addEventListener("input", (event) => {
    $("relevanceOut").textContent = event.target.value;
  });
  $("alertCredibility").addEventListener("input", (event) => {
    $("alertCredOut").textContent = event.target.value;
  });

  $("saveSettingsBtn").addEventListener("click", async () => {
    try {
      await saveSettings();
      await loadHealth();
    } catch (error) {
      $("dialogStatus").textContent = error.message;
      toast(error.message);
    }
  });

  $("testWebhookBtn").addEventListener("click", async () => {
    const status = $("dialogStatus");
    try {
      await saveSettings({ silent: true });
      status.textContent = "正在测试…";
      const result = await api("/api/webhook/test", { method: "POST" });
      status.textContent = result.ok ? "Webhook 连通" : `Webhook 失败：${result.message}`;
      toast(result.ok ? "Webhook 测试成功" : `Webhook 测试失败：${result.message}`);
    } catch (error) {
      status.textContent = error.message;
      toast(error.message);
    }
  });

  $("healthBtn").addEventListener("click", async () => {
    const status = $("dialogStatus");
    status.textContent = "正在检查…";
    await loadHealth();
    const ollama = state.health?.ollama || {};
    status.textContent = ollama.ok
      ? `Ollama 在线，模型 ${ollama.model}${ollama.available === false ? "（未找到）" : ""}`
      : `Ollama 离线：${ollama.error || "未知错误"}`;
  });

  $("scopeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("scopeName").value.trim();
    const query = $("scopeQuery").value.trim();
    if (!name || !query) return;
    try {
      await api("/api/scopes", { method: "POST", body: JSON.stringify({ name, query }) });
      $("scopeName").value = "";
      $("scopeQuery").value = "";
      await loadDashboard();
      toast("新范围已加入下一期监听");
    } catch (error) {
      toast(error.message);
    }
  });

  $("scopeManage").addEventListener("click", async (event) => {
    const button = event.target.closest(".scope-del");
    if (!button) return;
    if (!window.confirm("删除这个监控范围？已采集的内容会保留。")) return;
    try {
      await api(`/api/scopes/${button.dataset.id}`, { method: "DELETE" });
      await loadDashboard();
      toast("范围已删除");
    } catch (error) {
      toast(error.message);
    }
  });

  $("settingsDialog").addEventListener("click", (event) => {
    if (event.target === $("settingsDialog")) $("settingsDialog").close();
  });

  window.addEventListener("resize", debounce(renderRadarPips, 200));
}

async function init() {
  $("todayLabel").textContent = formatToday();
  updateNotifyButton();
  bindEvents();
  try {
    await Promise.all([loadDashboard(), loadHealth()]);
  } catch (error) {
    toast(`初始化失败：${error.message}`, 8000);
  }
  connectStream();
  setInterval(loadHealth, 90000);
}

document.addEventListener("DOMContentLoaded", init);
