const $ = (id) => document.getElementById(id);

const VERDICT_LABELS = {
  verified: "已核实",
  likely: "可信",
  unverified: "待核实",
  suspicious: "存疑",
};

const MATCH_LEVEL_LABELS = {
  exact: "精确匹配",
  strong: "强匹配",
  partial: "部分匹配",
  weak: "弱相关",
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

const VERDICT_ORDER = ["verified", "likely", "unverified", "suspicious"];
const TIER_OPTIONS = ["official", "release", "research", "media", "community", "heat"];
const KIND_OPTIONS = ["news", "release", "research", "discussion", "video", "heat"];
const MATCH_LEVEL_ORDER = ["exact", "strong", "partial", "weak"];

const PRESETS = {
  all: { label: "全部", timeHours: 168, timeField: "published", sort: "composite" },
  today: {
    label: "今日选题",
    timeHours: 24,
    timeField: "published",
    verdicts: ["verified", "likely"],
    minRelevance: 60,
    sort: "composite",
  },
  official: {
    label: "官方发布",
    timeHours: 168,
    timeField: "published",
    tiers: ["official", "release"],
    sort: "newest",
  },
  sources: {
    label: "多源热点",
    timeHours: 168,
    timeField: "published",
    minSources: 2,
    sort: "sources",
  },
  rumor: {
    label: "爆料核查",
    timeHours: 168,
    timeField: "published",
    verdicts: ["suspicious", "unverified"],
    sort: "newest",
  },
};

const QUICK_CHIPS = [
  { key: "verified", label: "已核实" },
  { key: "sources", label: "多源" },
  { key: "today", label: "今日新增" },
  { key: "keyword", label: "关键词命中" },
];

const FILTER_STORAGE_KEY = "signalradar.filters.v1";
const FILTER_PARAM_KEYS = [
  "scope", "verdict", "tier", "kind", "match_level", "since_hours", "time_field",
  "min_credibility", "max_credibility", "min_relevance", "max_relevance", "min_sources",
  "single_source", "keyword", "has_keyword", "include_keyword_only", "q", "sort", "preset",
];

function defaultFilters() {
  return {
    activeScopes: [],
    verdicts: [],
    tiers: [],
    kinds: [],
    matchLevels: [],
    timeHours: 168,
    timeField: "published",
    minCredibility: null,
    maxCredibility: null,
    minRelevance: null,
    maxRelevance: null,
    minSources: null,
    singleSource: false,
    keywordFilter: "",
    hasKeyword: false,
    includeKeywordOnly: false,
    searchQuery: "",
    sort: "composite",
    preset: "all",
  };
}

const state = {
  scopes: [],
  keywords: [],
  topics: [],
  alerts: [],
  settings: {},
  stats: {},
  health: null,
  limit: 30,
  hasMore: false,
  total: 0,
  loading: false,
  requestSeq: 0,
  stream: null,
  filterOpen: false,
  ...defaultFilters(),
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

function numberOrNull(value, min = 0, max = 100) {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const num = Number(text);
  if (!Number.isFinite(num)) return null;
  return Math.max(min, Math.min(max, num));
}

function filtersFromState() {
  return {
    activeScopes: [...state.activeScopes],
    verdicts: [...state.verdicts],
    tiers: [...state.tiers],
    kinds: [...state.kinds],
    matchLevels: [...state.matchLevels],
    timeHours: state.timeHours,
    timeField: state.timeField,
    minCredibility: state.minCredibility,
    maxCredibility: state.maxCredibility,
    minRelevance: state.minRelevance,
    maxRelevance: state.maxRelevance,
    minSources: state.minSources,
    singleSource: state.singleSource,
    keywordFilter: state.keywordFilter,
    hasKeyword: state.hasKeyword,
    includeKeywordOnly: state.includeKeywordOnly,
    searchQuery: state.searchQuery,
    sort: state.sort,
    preset: state.preset,
  };
}

function sanitizeFilters(raw) {
  const filters = defaultFilters();
  if (!raw || typeof raw !== "object") return filters;
  const list = (value, allowed) =>
    Array.isArray(value) ? value.map(String).filter((item) => !allowed || allowed.includes(item)) : [];
  filters.activeScopes = list(raw.activeScopes);
  filters.verdicts = list(raw.verdicts, VERDICT_ORDER);
  filters.tiers = list(raw.tiers, TIER_OPTIONS);
  filters.kinds = list(raw.kinds, KIND_OPTIONS);
  filters.matchLevels = list(raw.matchLevels, MATCH_LEVEL_ORDER);
  filters.timeHours = [24, 72, 168, 720].includes(Number(raw.timeHours)) ? Number(raw.timeHours) : 168;
  filters.timeField = ["published", "fetched"].includes(raw.timeField) ? raw.timeField : "published";
  filters.minCredibility = numberOrNull(raw.minCredibility);
  filters.maxCredibility = numberOrNull(raw.maxCredibility);
  filters.minRelevance = numberOrNull(raw.minRelevance);
  filters.maxRelevance = numberOrNull(raw.maxRelevance);
  filters.minSources = [2, 3].includes(Number(raw.minSources)) ? Number(raw.minSources) : null;
  filters.singleSource = Boolean(raw.singleSource);
  filters.keywordFilter = String(raw.keywordFilter || "").trim();
  filters.hasKeyword = Boolean(raw.hasKeyword);
  filters.includeKeywordOnly = Boolean(raw.includeKeywordOnly);
  filters.searchQuery = String(raw.searchQuery || "").trim();
  filters.sort = ["composite", "newest", "fetched", "credibility", "relevance", "sources"].includes(raw.sort)
    ? raw.sort
    : "composite";
  filters.preset = Object.prototype.hasOwnProperty.call(PRESETS, raw.preset) ? raw.preset : "all";
  return filters;
}

function filtersFromParams(params) {
  const filters = defaultFilters();
  if (params.has("scope")) filters.activeScopes = params.getAll("scope");
  if (params.has("verdict")) filters.verdicts = params.getAll("verdict");
  if (params.has("tier")) filters.tiers = params.getAll("tier");
  if (params.has("kind")) filters.kinds = params.getAll("kind");
  if (params.has("match_level")) filters.matchLevels = params.getAll("match_level");
  if (params.has("since_hours")) filters.timeHours = Number(params.get("since_hours"));
  if (params.has("time_field")) filters.timeField = params.get("time_field");
  filters.minCredibility = numberOrNull(params.get("min_credibility"));
  filters.maxCredibility = numberOrNull(params.get("max_credibility"));
  filters.minRelevance = numberOrNull(params.get("min_relevance"));
  filters.maxRelevance = numberOrNull(params.get("max_relevance"));
  if (params.get("single_source") === "true") filters.singleSource = true;
  else if (params.has("min_sources")) filters.minSources = Number(params.get("min_sources"));
  if (params.has("keyword")) filters.keywordFilter = params.get("keyword");
  filters.hasKeyword = params.get("has_keyword") === "true";
  filters.includeKeywordOnly = params.get("include_keyword_only") === "true";
  filters.searchQuery = params.get("q") || "";
  filters.sort = params.get("sort") || "composite";
  const presetKey = params.get("preset") || "";
  filters.preset = presetKey;
  const explicitConditions = FILTER_PARAM_KEYS.filter((key) => key !== "preset");
  if (presetKey && PRESETS[presetKey] && !explicitConditions.some((key) => params.has(key))) {
    const { label, ...presetFilters } = PRESETS[presetKey];
    Object.assign(filters, presetFilters);
  }
  return sanitizeFilters(filters);
}

function restoreFilters() {
  const params = new URLSearchParams(window.location.search);
  let filters = null;
  if (FILTER_PARAM_KEYS.some((key) => params.has(key))) {
    filters = filtersFromParams(params);
  } else {
    try {
      const stored = window.localStorage.getItem(FILTER_STORAGE_KEY);
      if (stored) filters = sanitizeFilters(JSON.parse(stored));
    } catch (error) {
      filters = null;
    }
  }
  Object.assign(state, defaultFilters(), filters || defaultFilters());
}

function persistFilters() {
  const filters = filtersFromState();
  try {
    window.localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(filters));
  } catch (error) {
    console.warn("filter persist failed", error);
  }
  const params = new URLSearchParams();
  filters.activeScopes.forEach((item) => params.append("scope", item));
  filters.verdicts.forEach((item) => params.append("verdict", item));
  filters.tiers.forEach((item) => params.append("tier", item));
  filters.kinds.forEach((item) => params.append("kind", item));
  filters.matchLevels.forEach((item) => params.append("match_level", item));
  if (filters.timeHours !== 168) params.set("since_hours", String(filters.timeHours));
  if (filters.timeField !== "published") params.set("time_field", filters.timeField);
  if (filters.minCredibility != null) params.set("min_credibility", String(filters.minCredibility));
  if (filters.maxCredibility != null) params.set("max_credibility", String(filters.maxCredibility));
  if (filters.minRelevance != null) params.set("min_relevance", String(filters.minRelevance));
  if (filters.maxRelevance != null) params.set("max_relevance", String(filters.maxRelevance));
  if (filters.singleSource) params.set("single_source", "true");
  else if (filters.minSources) params.set("min_sources", String(filters.minSources));
  if (filters.keywordFilter) params.set("keyword", filters.keywordFilter);
  if (filters.hasKeyword) params.set("has_keyword", "true");
  if (filters.includeKeywordOnly) params.set("include_keyword_only", "true");
  if (filters.searchQuery) params.set("q", filters.searchQuery);
  if (filters.sort !== "composite") params.set("sort", filters.sort);
  if (filters.preset && filters.preset !== "all") params.set("preset", filters.preset);
  const query = params.toString();
  const path = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState(null, "", path);
}

function activeFilterCount() {
  let count = 0;
  count += state.activeScopes.length;
  count += state.verdicts.length;
  count += state.tiers.length;
  count += state.kinds.length;
  count += state.matchLevels.length;
  if (state.timeHours !== 168 || state.timeField !== "published") count += 1;
  if (state.minCredibility != null || state.maxCredibility != null) count += 1;
  if (state.minRelevance != null || state.maxRelevance != null) count += 1;
  if (state.minSources != null || state.singleSource) count += 1;
  if (state.keywordFilter) count += 1;
  if (state.hasKeyword) count += 1;
  if (state.includeKeywordOnly) count += 1;
  if (state.searchQuery) count += 1;
  return count;
}

function filterOptionMarkup(name, values, labels, stateKey) {
  return values
    .map(
      (value) => `
        <label class="check-chip">
          <input type="checkbox" name="${name}" value="${escapeHtml(value)}"${state[stateKey].includes(value) ? " checked" : ""}>
          <span>${escapeHtml(labels[value] || value)}</span>
        </label>
      `
    )
    .join("");
}

function renderFilterOptions() {
  $("verdictOptions").innerHTML = filterOptionMarkup("verdict", VERDICT_ORDER, VERDICT_LABELS, "verdicts");
  $("tierOptions").innerHTML = filterOptionMarkup("tier", TIER_OPTIONS, TIER_LABELS, "tiers");
  $("kindOptions").innerHTML = filterOptionMarkup("kind", KIND_OPTIONS, KIND_LABELS, "kinds");
  $("matchLevelOptions").innerHTML = filterOptionMarkup(
    "match_level",
    MATCH_LEVEL_ORDER,
    MATCH_LEVEL_LABELS,
    "matchLevels"
  );
}

function renderKeywordOptions() {
  const select = $("keywordFilter");
  if (!select) return;
  const options = state.keywords
    .map((keyword) => `<option value="${escapeHtml(keyword.term)}">${escapeHtml(keyword.term)}</option>`)
    .join("");
  select.innerHTML = `<option value="">全部关键词</option>${options}`;
  if (state.keywordFilter && !state.keywords.some((keyword) => keyword.term === state.keywordFilter)) {
    state.keywordFilter = "";
  }
  select.value = state.keywordFilter;
}

function renderPresets() {
  $("presetTabs").innerHTML = Object.entries(PRESETS)
    .map(
      ([key, preset]) =>
        `<button type="button" class="preset-tab${state.preset === key ? " active" : ""}" data-preset="${key}">${escapeHtml(preset.label)}</button>`
    )
    .join("");
}

function quickChipActive(key) {
  if (key === "verified") return state.verdicts.includes("verified");
  if (key === "sources") return state.minSources === 2;
  if (key === "today") return state.timeHours === 24 && state.timeField === "fetched";
  if (key === "keyword") return state.hasKeyword;
  return false;
}

function renderQuickChips() {
  $("quickChips").innerHTML = QUICK_CHIPS
    .map(
      (chip) =>
        `<button type="button" class="quick-chip${quickChipActive(chip.key) ? " active" : ""}" data-quick="${chip.key}">${escapeHtml(chip.label)}</button>`
    )
    .join("");
}

function activeFilterEntries() {
  const entries = [];
  state.activeScopes.forEach((item) => entries.push({ filter: "scope", value: item, label: item }));
  state.verdicts.forEach((item) =>
    entries.push({ filter: "verdict", value: item, label: VERDICT_LABELS[item] || item })
  );
  state.tiers.forEach((item) =>
    entries.push({ filter: "tier", value: item, label: `来源·${TIER_LABELS[item] || item}` })
  );
  state.kinds.forEach((item) =>
    entries.push({ filter: "kind", value: item, label: KIND_LABELS[item] || item })
  );
  state.matchLevels.forEach((item) =>
    entries.push({ filter: "matchLevel", value: item, label: MATCH_LEVEL_LABELS[item] || item })
  );
  if (state.timeHours !== 168 || state.timeField !== "published") {
    const hours = state.timeHours === 24 ? "24 小时" : state.timeHours === 72 ? "3 天" : state.timeHours === 720 ? "30 天" : "7 天";
    entries.push({
      filter: "time",
      label: `${state.timeField === "fetched" ? "入库" : "发布"} ${hours}`,
    });
  }
  if (state.minCredibility != null) entries.push({ filter: "minCredibility", label: `可信度 ≥ ${state.minCredibility}` });
  if (state.maxCredibility != null) entries.push({ filter: "maxCredibility", label: `可信度 ≤ ${state.maxCredibility}` });
  if (state.minRelevance != null) entries.push({ filter: "minRelevance", label: `相关度 ≥ ${state.minRelevance}` });
  if (state.maxRelevance != null) entries.push({ filter: "maxRelevance", label: `相关度 ≤ ${state.maxRelevance}` });
  if (state.singleSource) entries.push({ filter: "singleSource", label: "仅单源" });
  else if (state.minSources) entries.push({ filter: "minSources", label: `至少 ${state.minSources} 家来源` });
  if (state.keywordFilter) entries.push({ filter: "keyword", label: `关键词·${state.keywordFilter}` });
  if (state.hasKeyword) entries.push({ filter: "hasKeyword", label: "仅关键词命中" });
  if (state.includeKeywordOnly) entries.push({ filter: "includeKeywordOnly", label: "含关键词入库" });
  if (state.searchQuery) entries.push({ filter: "search", label: `搜索·${state.searchQuery}` });
  return entries;
}

function renderActiveFilters() {
  const entries = activeFilterEntries();
  const container = $("activeFilters");
  if (!entries.length) {
    container.innerHTML = '<span class="filter-empty">未启用筛选 · 近七天 · 综合优先</span>';
    return;
  }
  container.innerHTML =
    entries
      .map(
        (entry) =>
          `<button type="button" class="filter-pill" data-filter="${entry.filter}" data-value="${escapeHtml(entry.value || "")}">${escapeHtml(entry.label)} <i>×</i></button>`
      )
      .join("") + '<button type="button" class="filter-clear" id="clearFilterPills">清空</button>';
}

function updateFilterBadge() {
  const count = activeFilterCount();
  const badge = $("filterBadge");
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

function syncFilterControls() {
  $("timeHours").value = String(state.timeHours);
  $("timeField").value = state.timeField;
  $("minSources").value = state.singleSource ? "single" : state.minSources ? String(state.minSources) : "";
  renderKeywordOptions();
  $("searchInput").value = state.searchQuery;
  $("minCredibility").value = state.minCredibility ?? "";
  $("maxCredibility").value = state.maxCredibility ?? "";
  $("minRelevance").value = state.minRelevance ?? "";
  $("maxRelevance").value = state.maxRelevance ?? "";
  $("hasKeyword").checked = state.hasKeyword;
  $("includeKeywordOnly").checked = state.includeKeywordOnly;
  $("sortSelect").value = state.sort;
  renderFilterOptions();
  renderPresets();
  renderQuickChips();
  renderActiveFilters();
  updateFilterBadge();
  $("filterPanel").hidden = !state.filterOpen;
  $("filterToggle").setAttribute("aria-expanded", state.filterOpen ? "true" : "false");
}

function readFilterControls() {
  state.timeHours = Number($("timeHours").value) || 168;
  state.timeField = $("timeField").value;
  const sourceValue = $("minSources").value;
  state.singleSource = sourceValue === "single";
  state.minSources = !state.singleSource && sourceValue ? Number(sourceValue) : null;
  state.keywordFilter = $("keywordFilter").value;
  state.searchQuery = $("searchInput").value.trim();
  state.minCredibility = numberOrNull($("minCredibility").value);
  state.maxCredibility = numberOrNull($("maxCredibility").value);
  state.minRelevance = numberOrNull($("minRelevance").value);
  state.maxRelevance = numberOrNull($("maxRelevance").value);
  state.hasKeyword = $("hasKeyword").checked;
  state.includeKeywordOnly = $("includeKeywordOnly").checked;
  state.verdicts = Array.from(document.querySelectorAll('#verdictOptions input:checked')).map((el) => el.value);
  state.tiers = Array.from(document.querySelectorAll('#tierOptions input:checked')).map((el) => el.value);
  state.kinds = Array.from(document.querySelectorAll('#kindOptions input:checked')).map((el) => el.value);
  state.matchLevels = Array.from(document.querySelectorAll('#matchLevelOptions input:checked')).map((el) => el.value);
  state.preset = "";
}

function applyPreset(key) {
  const preset = PRESETS[key];
  if (!preset) return;
  const { label, ...filters } = preset;
  Object.assign(state, defaultFilters(), filters);
  state.preset = key;
  syncFilterControls();
  fetchTopics({ reset: true });
}

function toggleQuickChip(key) {
  if (key === "verified") {
    state.verdicts = state.verdicts.includes("verified")
      ? state.verdicts.filter((item) => item !== "verified")
      : [...state.verdicts, "verified"];
  } else if (key === "sources") {
    state.minSources = state.minSources === 2 ? null : 2;
    state.singleSource = false;
  } else if (key === "today") {
    if (state.timeHours === 24 && state.timeField === "fetched") {
      state.timeHours = 168;
      state.timeField = "published";
    } else {
      state.timeHours = 24;
      state.timeField = "fetched";
    }
  } else if (key === "keyword") {
    state.hasKeyword = !state.hasKeyword;
  }
  state.preset = "";
  syncFilterControls();
  persistFilters();
  fetchTopics({ reset: true });
}

function clearFilter(key, value) {
  if (key === "scope") state.activeScopes = state.activeScopes.filter((item) => item !== value);
  else if (key === "verdict") state.verdicts = state.verdicts.filter((item) => item !== value);
  else if (key === "tier") state.tiers = state.tiers.filter((item) => item !== value);
  else if (key === "kind") state.kinds = state.kinds.filter((item) => item !== value);
  else if (key === "matchLevel") state.matchLevels = state.matchLevels.filter((item) => item !== value);
  else if (key === "time") {
    state.timeHours = 168;
    state.timeField = "published";
  } else if (key === "minCredibility") state.minCredibility = null;
  else if (key === "maxCredibility") state.maxCredibility = null;
  else if (key === "minRelevance") state.minRelevance = null;
  else if (key === "maxRelevance") state.maxRelevance = null;
  else if (key === "minSources") state.minSources = null;
  else if (key === "singleSource") state.singleSource = false;
  else if (key === "keyword") state.keywordFilter = "";
  else if (key === "hasKeyword") state.hasKeyword = false;
  else if (key === "includeKeywordOnly") state.includeKeywordOnly = false;
  else if (key === "search") state.searchQuery = "";
  state.preset = "";
  syncFilterControls();
  persistFilters();
  fetchTopics({ reset: true });
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
  const available = new Set(state.scopes.map((scope) => scope.name));
  state.activeScopes = state.activeScopes.filter((name) => available.has(name));
  const tabs = [{ name: null, label: "全部" }].concat(
    state.scopes.map((scope) => ({ name: scope.name, label: scope.name }))
  );
  const allActive = state.activeScopes.length === 0;
  container.innerHTML = tabs
    .map((tab) => {
      const active = tab.name === null ? allActive : state.activeScopes.includes(tab.name);
      return `<button type="button" class="scope-tab${active ? " active" : ""}" data-scope="${escapeHtml(tab.name || "")}" aria-pressed="${active}">${escapeHtml(tab.label)}</button>`;
    })
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
  const bestMatch = topic.best_match;
  const matchLevel = bestMatch ? bestMatch.match_level : "";
  const matchLabel = MATCH_LEVEL_LABELS[matchLevel] || "";
  const matchedAnchors = bestMatch && Array.isArray(bestMatch.matched_anchors) ? bestMatch.matched_anchors : [];
  const matchBadge = matchLevel
    ? `<span class="match-badge match-${escapeHtml(matchLevel)}">${escapeHtml(matchLabel)}</span>`
    : "";
  const matchMarkup = bestMatch
    ? `
        <p class="match-line">
          <span>关键词 ${escapeHtml(bestMatch.keyword)}</span>
          ${matchedAnchors.length ? `<span>命中 ${matchedAnchors.map((item) => escapeHtml(item)).join(" / ")}</span>` : ""}
        </p>
        ${bestMatch.evidence ? `<p class="match-evidence">证据：${escapeHtml(bestMatch.evidence)}</p>` : ""}
        ${bestMatch.match_reason ? `<p class="match-reason">${escapeHtml(bestMatch.match_reason)}</p>` : ""}
      `
    : "";
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
        ${matchBadge}
      </div>
      ${tagsMarkup}
      <details class="topic-more">
        <summary>判断依据</summary>
        ${matchMarkup}
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
    empty.innerHTML =
      activeFilterCount() > 0
        ? '<p class="empty-big">没有符合筛选的信号</p><p class="empty-small">试着放宽时间窗，或清空部分筛选条件。</p>'
        : '<p class="empty-big">等待第一声信号</p><p class="empty-small">编辑部已就位。点击「采集新信号」，或稍等自动巡检。</p>';
  } else {
    empty.hidden = true;
    list.innerHTML = state.topics.map(topicMarkup).join("");
  }
  const total = state.total || state.topics.length;
  $("wireCount").textContent = state.topics.length
    ? activeFilterCount() > 0
      ? `筛选后 ${total} 条 · 已显示 ${state.topics.length} 条`
      : `近七天 · ${total} 条信号`
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
  renderKeywordOptions();
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
      const matchLevel = alert.match_level || "";
      const matchLabel = MATCH_LEVEL_LABELS[matchLevel] || "";
      const anchors = Array.isArray(alert.matched_anchors) ? alert.matched_anchors : [];
      const matchBadge = matchLevel
        ? `<span class="match-badge match-${escapeHtml(matchLevel)}">${escapeHtml(matchLabel)}</span>`
        : "";
      return `
        <li class="alert-item${alert.read ? "" : " unread"}" data-id="${alert.id}" data-url="${safeUrl(alert.article_url)}">
          <div class="alert-top">
            <span class="alert-level level-${level}">${LEVEL_LABELS[level]}</span>
            <span>${escapeHtml(alert.keyword)}</span>
            ${matchBadge}
            <span style="margin-left:auto">${escapeHtml(formatRelative(alert.created_at))}</span>
          </div>
          <p class="alert-title">${escapeHtml(alert.title)}</p>
          ${anchors.length ? `<p class="alert-match">命中 ${anchors.map((item) => escapeHtml(item)).join(" / ")}</p>` : ""}
          ${alert.match_evidence ? `<p class="alert-evidence">证据：${escapeHtml(alert.match_evidence)}</p>` : ""}
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
  state.total = state.topics.length;
  state.hasMore = state.topics.length >= state.limit;
  renderScopes();
  renderTopics();
  renderKeywords();
  renderAlerts();
  syncFilterControls();
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
    since_hours: String(state.timeHours),
    time_field: state.timeField,
    sort: state.sort,
  });
  state.activeScopes.forEach((item) => params.append("scope", item));
  state.verdicts.forEach((item) => params.append("verdict", item));
  state.tiers.forEach((item) => params.append("tier", item));
  state.kinds.forEach((item) => params.append("kind", item));
  state.matchLevels.forEach((item) => params.append("match_level", item));
  if (state.minCredibility != null) params.set("min_credibility", String(state.minCredibility));
  if (state.maxCredibility != null) params.set("max_credibility", String(state.maxCredibility));
  if (state.minRelevance != null) params.set("min_relevance", String(state.minRelevance));
  if (state.maxRelevance != null) params.set("max_relevance", String(state.maxRelevance));
  if (state.singleSource) params.set("single_source", "true");
  else if (state.minSources) params.set("min_sources", String(state.minSources));
  if (state.keywordFilter) params.set("keyword", state.keywordFilter);
  if (state.hasKeyword) params.set("has_keyword", "true");
  if (state.includeKeywordOnly) params.set("include_keyword_only", "true");
  if (state.searchQuery) params.set("q", state.searchQuery);
  if (reset) persistFilters();
  try {
    const data = await api(`/api/topics?${params.toString()}`);
    if (seq !== state.requestSeq) return;
    const items = data.items || [];
    state.topics = reset ? items : state.topics.concat(items);
    state.total = Number(data.total || 0);
    state.hasMore = offset + items.length < state.total;
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
    const keywordBefore = state.keywordFilter;
    state.keywords = keywords;
    state.alerts = alerts;
    renderKeywords();
    renderAlerts();
    syncFilterControls();
    renderStatus();
    if (keywordBefore && !state.keywordFilter) {
      fetchTopics({ reset: true });
    }
  } catch (error) {
    console.warn(error);
  }
}

function bindEvents() {
  $("scopeTabs").addEventListener("click", (event) => {
    const button = event.target.closest(".scope-tab");
    if (!button) return;
    const name = button.dataset.scope || "";
    if (!name) {
      state.activeScopes = [];
    } else if (state.activeScopes.includes(name)) {
      state.activeScopes = state.activeScopes.filter((item) => item !== name);
    } else {
      state.activeScopes = [...state.activeScopes, name];
    }
    state.preset = "";
    renderScopes();
    syncFilterControls();
    fetchTopics({ reset: true });
  });

  $("sortSelect").addEventListener("change", (event) => {
    state.sort = event.target.value;
    state.preset = "";
    syncFilterControls();
    fetchTopics({ reset: true });
  });

  $("filterToggle").addEventListener("click", () => {
    state.filterOpen = !state.filterOpen;
    syncFilterControls();
  });

  $("presetTabs").addEventListener("click", (event) => {
    const button = event.target.closest(".preset-tab");
    if (!button) return;
    applyPreset(button.dataset.preset);
  });

  $("quickChips").addEventListener("click", (event) => {
    const button = event.target.closest(".quick-chip");
    if (!button) return;
    toggleQuickChip(button.dataset.quick);
  });

  $("applyFilters").addEventListener("click", () => {
    readFilterControls();
    if (
      state.minCredibility != null &&
      state.maxCredibility != null &&
      state.minCredibility > state.maxCredibility
    ) {
      toast("可信度最低值不能高于最高值");
      return;
    }
    if (
      state.minRelevance != null &&
      state.maxRelevance != null &&
      state.minRelevance > state.maxRelevance
    ) {
      toast("相关度最低值不能高于最高值");
      return;
    }
    state.filterOpen = false;
    syncFilterControls();
    fetchTopics({ reset: true });
  });

  $("resetFilters").addEventListener("click", () => {
    Object.assign(state, defaultFilters());
    state.filterOpen = false;
    syncFilterControls();
    fetchTopics({ reset: true });
  });

  $("activeFilters").addEventListener("click", (event) => {
    if (event.target.closest("#clearFilterPills")) {
      Object.assign(state, defaultFilters());
      state.filterOpen = false;
      syncFilterControls();
      fetchTopics({ reset: true });
      return;
    }
    const pill = event.target.closest(".filter-pill");
    if (!pill) return;
    clearFilter(pill.dataset.filter, pill.dataset.value);
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
      await fetchTopics({ reset: true });
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
      await fetchTopics({ reset: true });
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
  restoreFilters();
  bindEvents();
  try {
    await Promise.all([loadDashboard(), loadHealth()]);
    await fetchTopics({ reset: true });
  } catch (error) {
    toast(`初始化失败：${error.message}`, 8000);
  }
  connectStream();
  setInterval(loadHealth, 90000);
}

document.addEventListener("DOMContentLoaded", init);
