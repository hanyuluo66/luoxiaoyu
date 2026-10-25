/* ==========================================================================
 * 落小鱼 控制台 —— 纯原生 JS，无依赖、无外网请求
 * ========================================================================== */
'use strict';

/* ------------------------------------------------------------------ 工具函数 */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function pad2(n) { return String(n).padStart(2, '0'); }

function num(value, fallback) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : fallback;
  const raw = String(value === null || value === undefined ? '' : value).trim();
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function clamp(n, low, high) { return Math.min(high, Math.max(low, n)); }

function fmtInt(n) {
  const v = Math.round(num(n, 0));
  try { return v.toLocaleString('zh-CN'); } catch (err) { return String(v); }
}

function fmtTime(ts) {
  const n = num(ts, 0);
  if (!n) return '—';
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
    pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
}

function fmtClock(ts) {
  const n = num(ts, 0);
  if (!n) return '—';
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
}

function fmtUptime(seconds) {
  const s = Math.max(0, Math.floor(num(seconds, 0)));
  if (!s) return '—';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return d + ' 天 ' + h + ' 小时';
  if (h > 0) return h + ' 小时 ' + m + ' 分';
  if (m > 0) return m + ' 分';
  return s + ' 秒';
}

function fmtSeconds(seconds) {
  const n = Math.max(0, Math.round(num(seconds, 0)));
  if (n < 60) return n + ' 秒';
  const m = Math.floor(n / 60);
  const rest = n % 60;
  return rest ? m + ' 分 ' + rest + ' 秒' : m + ' 分';
}

function getPath(obj, path) {
  if (!obj) return undefined;
  return path.split('.').reduce((acc, key) => (acc === null || acc === undefined ? acc : acc[key]), obj);
}

function setPath(obj, path, value) {
  const keys = path.split('.');
  let target = obj;
  for (let i = 0; i < keys.length - 1; i += 1) {
    if (target[keys[i]] === null || typeof target[keys[i]] !== 'object') target[keys[i]] = {};
    target = target[keys[i]];
  }
  target[keys[keys.length - 1]] = value;
}

function setText(sel, text) {
  const el = typeof sel === 'string' ? $(sel) : sel;
  if (el) el.textContent = text === null || text === undefined ? '' : String(text);
}

function setBusy(btn, busy, label) {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.originalText) btn.dataset.originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = label || '处理中…';
  } else {
    btn.disabled = false;
    if (btn.dataset.originalText) {
      btn.textContent = btn.dataset.originalText;
      delete btn.dataset.originalText;
    }
  }
}

function newRuleId() {
  const hex = '0123456789abcdef';
  let id = '';
  for (let i = 0; i < 12; i += 1) id += hex[Math.floor(Math.random() * 16)];
  return id;
}

/* ------------------------------------------------------------------ 常量 */

const VIEW_META = {
  overview: ['概览', '一眼看清运行状态'],
  private: ['私聊设置', '哪些好友会被自动回复'],
  group: ['群聊设置', '群聊的触发方式与范围'],
  rules: ['监听规则', '按优先级决定怎么回'],
  persona: ['人设设置', '它是什么性格，怎么说话'],
  ai: ['AI 接口', '模型服务、密钥与试跑'],
  rhythm: ['收发节奏', '延迟、冷却与免打扰'],
  logs: ['消息日志', '收到和发出的每一条'],
  system: ['系统设置', '接入模式、测试与关于'],
};

const SCOPE_LABEL = { all: '全部会话', private: '仅私聊', group: '仅群聊' };
const MATCH_LABEL = {
  contains: '包含关键词',
  exact: '完全等于',
  startswith: '关键词开头',
  regex: '正则匹配',
  always: '总是命中',
};
const ACTION_LABEL = { ai: 'AI 回复', template: '固定话术', ignore: '不回复' };
const SOURCE_LABEL = {
  default: '默认逻辑',
  rule: '命中规则',
  'private-all': '私聊默认回复',
  'private-keyword': '私聊关键词',
  'group-all': '群聊全部消息',
  'group-keyword': '群聊关键词',
  'group-at': '群聊 @我',
  'group-at-all': '群聊 @所有人',
};
const KIND_LABEL = {
  reply: '回复',
  error: '错误',
  skip: '跳过',
  start: '启动',
  stop: '停止',
  config: '配置',
  takeover: '人工接管',
  info: '信息',
};

const NUM_FIELDS = [
  ['ai.temperature', 0, 2, 1.1, false],
  ['ai.max_tokens', 16, 8192, 800, true],
  ['ai.timeout', 5, 600, 90, true],
  ['ai.history_rounds', 0, 50, 6, true],
  ['ai.reply_max_chars', 20, 4000, 600, true],
  ['bot.reply_delay_min', 0, 60, 1.5, false],
  ['bot.reply_delay_max', 0, 60, 4, false],
  ['bot.per_chat_cooldown', 0, 3600, 8, false],
  ['bot.global_per_minute', 1, 600, 20, true],
  ['bot.takeover_minutes', 0, 1440, 10, true],
  ['bot.poll_interval', 0.5, 30, 1.5, false],
  ['bot.max_queue', 1, 1000, 50, true],
];

const BOOL_FIELDS = [
  'bot.enabled',
  'bot.private_enabled',
  'bot.group_enabled',
  'bot.quiet_hours_enabled',
  'bot.keep_history',
  'private.require_keyword',
  'group.reply_to_at_all',
];

const STRING_FIELDS = [
  'ai.provider',
  'ai.base_url',
  'ai.api_key',
  'ai.model',
  'ai.persona',
  'ai.extra_prompt',
];

const LIST_FIELDS = [
  'private.chats',
  'private.keywords',
  'group.chats',
  'group.keywords',
  'blacklist.chats',
  'blacklist.senders',
  'blacklist.ignore_keywords',
];

/* ------------------------------------------------------------------ 状态 */

const state = {
  booted: false,
  bound: false,
  tagsMounted: false,
  version: '',
  config: null,
  status: {},
  presets: {},
  view: 'overview',
  dirty: false,
  pollFailed: false,
  timer: 0,
  logs: { items: [], offset: 0, chat: '', done: false, loaded: false, loading: false, expanded: new Set() },
  rule: { open: false, index: -1, draft: null },
  picker: { target: '', filter: '', sessions: [], selected: new Set(), loading: false },
};

let confirmResolver = null;

/* ------------------------------------------------------------------ 网络层 */

async function api(path, options) {
  const opts = { method: (options && options.method) || 'GET', headers: {} };
  if (options && options.body !== undefined && options.body !== null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(options.body);
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (err) {
    throw new Error('连不上本地服务（' + path + '），请确认 落小鱼 主程序还在运行');
  }

  let raw = '';
  try { raw = await res.text(); } catch (err) { raw = ''; }

  let data = null;
  if (raw) {
    try { data = JSON.parse(raw); } catch (err) { data = null; }
  }

  if (!res.ok) {
    let detail = data && (data.detail || data.message || data.error);
    if (Array.isArray(detail)) {
      detail = detail.map((d) => (d && (d.msg || d.message)) || JSON.stringify(d)).join('；');
    }
    if (detail && typeof detail === 'object') detail = JSON.stringify(detail);
    throw new Error(String(detail || raw || ('请求失败（HTTP ' + res.status + '）')));
  }
  return data;
}

/* ------------------------------------------------------------------ Toast */

function toast(message, type) {
  const box = $('#toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (type === 'error' ? 'error' : type === 'warn' ? 'warn' : 'ok');
  const dot = document.createElement('span');
  dot.className = 'toast-dot';
  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = String(message === null || message === undefined ? '' : message);
  el.append(dot, text);
  box.appendChild(el);
  window.setTimeout(() => {
    el.classList.add('out');
    window.setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 240);
  }, 2500);
}

/* ------------------------------------------------------------------ 确认框 */

function confirmDialog(opts) {
  const options = opts || {};
  return new Promise((resolve) => {
    const mask = $('#modal-confirm');
    if (!mask) { resolve(false); return; }
    setText('#confirm-title', options.title || '确认操作');
    setText('#confirm-message', options.message || '');
    const okBtn = $('#confirm-ok');
    okBtn.textContent = options.okText || '确定';
    okBtn.className = 'btn ' + (options.danger === false ? 'primary' : 'danger');
    mask.hidden = false;
    if (confirmResolver) confirmResolver(false);
    confirmResolver = resolve;
    okBtn.focus();
  });
}

function settleConfirm(result) {
  const mask = $('#modal-confirm');
  if (mask) mask.hidden = true;
  const fn = confirmResolver;
  confirmResolver = null;
  if (fn) fn(!!result);
}

/* ------------------------------------------------------------------ 配置处理 */

function normalizeRule(raw) {
  const r = raw || {};
  return {
    id: String(r.id || newRuleId()),
    name: String(r.name || '新规则'),
    enabled: r.enabled !== false,
    scope: ['all', 'private', 'group'].indexOf(r.scope) >= 0 ? r.scope : 'all',
    chats: Array.isArray(r.chats) ? r.chats.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim()) : [],
    match: ['contains', 'exact', 'startswith', 'regex', 'always'].indexOf(r.match) >= 0 ? r.match : 'contains',
    patterns: Array.isArray(r.patterns) ? r.patterns.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim()) : [],
    action: ['ai', 'template', 'ignore'].indexOf(r.action) >= 0 ? r.action : 'ai',
    template: String(r.template === null || r.template === undefined ? '' : r.template),
    prompt: String(r.prompt === null || r.prompt === undefined ? '' : r.prompt),
    priority: num(r.priority, 100),
    cooldown_seconds: num(r.cooldown_seconds, 0),
  };
}

function normalizeConfig(raw) {
  const cfg = raw || {};

  cfg.version = Math.max(1, Math.trunc(num(cfg.version, 1)) || 1);
  cfg.adapter = cfg.adapter === 'mock' ? 'mock' : 'wxauto';

  cfg.ai = Object.assign({
    provider: 'deepseek',
    base_url: '',
    api_key: '',
    model: '',
    temperature: 1.1,
    max_tokens: 800,
    timeout: 90,
    persona: '',
    extra_prompt: '',
    history_rounds: 6,
    reply_max_chars: 600,
  }, cfg.ai || {});

  cfg.bot = Object.assign({
    enabled: true,
    private_enabled: true,
    group_enabled: true,
    reply_delay_min: 1.5,
    reply_delay_max: 4,
    per_chat_cooldown: 8,
    global_per_minute: 20,
    quiet_hours_enabled: false,
    quiet_start: '23:30',
    quiet_end: '07:30',
    takeover_minutes: 10,
    poll_interval: 1.5,
    max_queue: 50,
    keep_history: true,
  }, cfg.bot || {});

  cfg.private = Object.assign({
    mode: 'include', chats: [], require_keyword: false, keywords: [],
  }, cfg.private || {});

  cfg.group = Object.assign({
    mode: 'include', chats: [], trigger: 'at', keywords: [], reply_to_at_all: true,
  }, cfg.group || {});

  cfg.blacklist = Object.assign({ chats: [], senders: [], ignore_keywords: [] }, cfg.blacklist || {});

  cfg.private.mode = ['all', 'include', 'exclude'].indexOf(cfg.private.mode) >= 0 ? cfg.private.mode : 'include';
  cfg.group.mode = ['all', 'include', 'exclude'].indexOf(cfg.group.mode) >= 0 ? cfg.group.mode : 'include';
  cfg.group.trigger = ['at', 'keyword', 'all'].indexOf(cfg.group.trigger) >= 0 ? cfg.group.trigger : 'at';

  BOOL_FIELDS.forEach((path) => setPath(cfg, path, !!getPath(cfg, path)));
  STRING_FIELDS.forEach((path) => {
    const value = getPath(cfg, path);
    setPath(cfg, path, value === null || value === undefined ? '' : String(value));
  });
  LIST_FIELDS.forEach((path) => {
    const value = getPath(cfg, path);
    setPath(cfg, path, Array.isArray(value)
      ? value.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim())
      : []);
  });

  cfg.ai.provider = cfg.ai.provider || 'deepseek';

  cfg.rules = Array.isArray(cfg.rules) ? cfg.rules.map(normalizeRule) : [];
  return cfg;
}

function validHhmm(value, fallback) {
  const text = String(value === null || value === undefined ? '' : value).trim();
  const m = /^(\d{1,2}):(\d{2})$/.exec(text);
  if (!m) return fallback;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  if (h > 23 || mi > 59) return fallback;
  return pad2(h) + ':' + pad2(mi);
}

/** 把所有数字字段压成合法数值，避免字符串 / NaN 让后端 400。 */
function coerceConfig(cfg) {
  cfg.version = Math.max(1, Math.trunc(num(cfg.version, 1)) || 1);

  NUM_FIELDS.forEach((item) => {
    const path = item[0];
    const low = item[1];
    const high = item[2];
    const fallback = item[3];
    const intOnly = item[4];
    let value = num(getPath(cfg, path), fallback);
    if (intOnly) value = Math.round(value);
    setPath(cfg, path, clamp(value, low, high));
  });

  if (num(cfg.bot.reply_delay_max, 4) < num(cfg.bot.reply_delay_min, 1.5)) {
    cfg.bot.reply_delay_max = num(cfg.bot.reply_delay_min, 1.5);
  }

  cfg.bot.quiet_start = validHhmm(cfg.bot.quiet_start, '23:30');
  cfg.bot.quiet_end = validHhmm(cfg.bot.quiet_end, '07:30');

  cfg.rules.forEach((rule) => {
    rule.priority = clamp(Math.round(num(rule.priority, 100)), 0, 9999);
    rule.cooldown_seconds = clamp(num(rule.cooldown_seconds, 0), 0, 86400);
    rule.chats = (Array.isArray(rule.chats) ? rule.chats : []).filter(Boolean).map((x) => String(x).trim()).filter(Boolean);
    rule.patterns = (Array.isArray(rule.patterns) ? rule.patterns : []).filter((x) => String(x).trim()).map((x) => String(x).trim());
    rule.name = String(rule.name || '新规则').trim() || '新规则';
  });
  return cfg;
}

function validateConfig(cfg) {
  if (!String(cfg.ai.base_url || '').trim()) {
    return { view: 'ai', message: 'Base URL 还没填，填好再保存' };
  }
  if (num(cfg.bot.reply_delay_max, 4) < num(cfg.bot.reply_delay_min, 1.5)) {
    return { view: 'rhythm', message: '回复延迟的最大值不能小于最小值' };
  }
  if (num(cfg.bot.global_per_minute, 20) < 1) {
    return { view: 'rhythm', message: '全局每分钟上限至少是 1 条' };
  }
  for (let i = 0; i < cfg.rules.length; i += 1) {
    const rule = cfg.rules[i];
    if (rule.match !== 'regex') continue;
    for (let j = 0; j < rule.patterns.length; j += 1) {
      try { RegExp(rule.patterns[j]); } catch (err) {
        return { view: 'rules', message: '规则「' + rule.name + '」里的正则写错了：' + rule.patterns[j] };
      }
    }
  }
  return null;
}

function setDirty(flag) {
  state.dirty = !!flag;
  const dirtyFlag = $('#dirty-flag');
  if (dirtyFlag) dirtyFlag.hidden = !state.dirty;
  [$('#btn-save-top'), $('#btn-save-fab')].forEach((btn) => {
    if (btn) btn.classList.toggle('dirty-glow', state.dirty);
  });
  const fab = $('.save-fab');
  if (fab) fab.classList.toggle('dirty', state.dirty);
  setText('#fab-hint', state.dirty ? '有未保存的修改' : '改完记得保存');
}

function markDirty() {
  if (!state.config) return;
  setDirty(true);
}

/* ------------------------------------------------------------------ 绑定 */

function bindInputs() {
  $$('[data-bind]').forEach((el) => {
    const path = el.dataset.bind;
    const handler = () => {
      if (!state.config) return;
      if (el.type === 'checkbox') {
        setPath(state.config, path, el.checked);
      } else if (el.type === 'number' || el.type === 'range') {
        const raw = String(el.value === null || el.value === undefined ? '' : el.value).trim();
        if (!raw) {
          setPath(state.config, path, null);
        } else {
          const n = Number(raw);
          setPath(state.config, path, Number.isFinite(n) ? n : null);
        }
      } else {
        setPath(state.config, path, el.value);
      }
      markDirty();
      afterInput(path);
    };

    if (el.type === 'checkbox' || el.tagName === 'SELECT') {
      el.addEventListener('change', handler);
    } else {
      el.addEventListener('input', handler);
    }
  });

  $$('[data-bind-radio]').forEach((box) => {
    const path = box.dataset.bindRadio;
    box.addEventListener('change', (ev) => {
      const input = ev.target.closest('input[type="radio"]');
      if (!input || !state.config) return;
      setPath(state.config, path, input.value);
      markDirty();
      afterInput(path);
    });
  });
}

function afterInput(path) {
  if (path === 'ai.provider') applyPreset(String(getPath(state.config, 'ai.provider') || ''));
  if (path === 'ai.temperature') updateTempDisplay();
  syncModeUi();
}

/** 把内存里的配置写回表单控件（只在启动 / 保存成功后调用，绝不打断正在编辑的人）。 */
function syncInputs() {
  const cfg = state.config;
  if (!cfg) return;

  $$('[data-bind]').forEach((el) => {
    const value = getPath(cfg, el.dataset.bind);
    if (el.type === 'checkbox') {
      el.checked = !!value;
    } else {
      el.value = value === null || value === undefined ? '' : String(value);
    }
  });

  $$('[data-bind-radio]').forEach((box) => {
    const value = String(getPath(cfg, box.dataset.bindRadio));
    $$('input[type="radio"]', box).forEach((input) => { input.checked = input.value === value; });
  });

  updateTempDisplay();
  renderModelDatalist();
}

function updateTempDisplay() {
  const value = num(getPath(state.config || {}, 'ai.temperature'), 1.1);
  setText('#temp-value', value.toFixed(1));
}

function providerLabel(key) {
  const preset = (state.presets || {})[key];
  if (preset && preset.label) return preset.label;
  return key || '—';
}

function buildProviderSelect() {
  const sel = $('#ai-provider');
  if (!sel) return;
  const presets = state.presets || {};
  sel.textContent = '';
  Object.keys(presets).forEach((key) => {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = (presets[key] && presets[key].label) || key;
    sel.appendChild(opt);
  });
  const current = getPath(state.config || {}, 'ai.provider');
  if (current && !presets[current]) {
    const opt = document.createElement('option');
    opt.value = current;
    opt.textContent = current;
    sel.appendChild(opt);
  }
}

function renderModelDatalist() {
  const list = $('#model-options');
  if (!list) return;
  const preset = (state.presets || {})[String(getPath(state.config || {}, 'ai.provider') || '')];
  list.textContent = '';
  const models = preset && preset.models ? String(preset.models).split(',') : [];
  models.map((m) => m.trim()).filter(Boolean).forEach((name) => {
    const opt = document.createElement('option');
    opt.value = name;
    list.appendChild(opt);
  });
}

function applyPreset(key) {
  const preset = (state.presets || {})[key];
  const cfg = state.config;
  if (!preset || !cfg) return;

  if (key === 'custom') {
    cfg.ai.base_url = '';
    cfg.ai.model = '';
  } else {
    if (preset.base_url) cfg.ai.base_url = preset.base_url;
    if (preset.model) cfg.ai.model = preset.model;
  }

  const baseEl = $('#ai-base-url');
  if (baseEl) baseEl.value = cfg.ai.base_url;
  const modelEl = $('#ai-model');
  if (modelEl) modelEl.value = cfg.ai.model;

  renderModelDatalist();
  markDirty();
  toast('已填入「' + (preset.label || key) + '」的默认地址和模型', 'ok');
}

/* ------------------------------------------------------------------ 标签编辑器 */

let composing = false;

function mountTagEditor(el, rootGetter, pathOverride) {
  const path = pathOverride || el.dataset.tags;
  if (!path) return;

  el.classList.add('tags');
  el.textContent = '';

  const chips = document.createElement('div');
  chips.className = 'chips';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'tag-input';
  input.spellcheck = false;
  input.autocomplete = 'off';
  el.append(chips, input);

  const placeholder = el.dataset.placeholder || '输入后按回车添加';
  const placeholderMore = el.dataset.placeholderMore || '继续输入，回车添加';

  function current() {
    const value = getPath(rootGetter(), path);
    return Array.isArray(value) ? value.slice() : [];
  }

  function commit(list) {
    const root = rootGetter();
    if (!root) return;
    setPath(root, path, list);
    markDirty();
    render();
    syncModeUi();
  }

  function render() {
    chips.textContent = '';
    const list = current();
    if (!list.length) {
      const empty = document.createElement('span');
      empty.className = 'tag-empty';
      empty.textContent = el.dataset.empty || '暂无，留空也可以';
      chips.appendChild(empty);
    }
    list.forEach((name, index) => {
      const chip = document.createElement('span');
      chip.className = 'tag';
      const text = document.createElement('span');
      text.className = 'tag-text';
      text.textContent = String(name);
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'tag-x';
      del.textContent = '×';
      del.title = '删除';
      del.setAttribute('aria-label', '删除 ' + name);
      del.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const next = current();
        next.splice(index, 1);
        commit(next);
      });
      chip.append(text, del);
      chips.appendChild(chip);
    });
    input.placeholder = list.length ? placeholderMore : placeholder;
  }

  input.addEventListener('compositionstart', () => { composing = true; });
  input.addEventListener('compositionend', () => { composing = false; });

  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      if (ev.isComposing || composing) return;
      ev.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      const list = current();
      if (list.indexOf(value) >= 0) {
        toast('「' + value + '」已经添加过了', 'warn');
        input.value = '';
        return;
      }
      list.push(value);
      input.value = '';
      commit(list);
    } else if (ev.key === 'Backspace' && !input.value) {
      const list = current();
      if (list.length) commit(list.slice(0, -1));
    } else if (ev.key === 'Escape') {
      input.blur();
    }
  });

  el.addEventListener('click', (ev) => {
    if (!ev.target || !ev.target.closest || !ev.target.closest('.tag')) input.focus();
  });

  el._tagEditor = { render: render, get: current, set: commit };
  input.placeholder = placeholder;
  render();
}

function mountConfigTags() {
  if (state.tagsMounted) return;
  $$('[data-tags]').forEach((el) => mountTagEditor(el, () => state.config));
  state.tagsMounted = true;
}

function renderTagEditors() {
  $$('[data-tags]').forEach((el) => {
    if (el._tagEditor) el._tagEditor.render();
  });
}

function mountRuleTags() {
  $$('[data-rule-tags]').forEach((el) => {
    mountTagEditor(el, () => state.rule.draft, el.dataset.ruleTags);
  });
}

function renderRuleTags() {
  $$('[data-rule-tags]').forEach((el) => {
    if (el._tagEditor) el._tagEditor.render();
  });
}

/* ------------------------------------------------------------------ 会话选择器 */

function openSessionPicker(target) {
  state.picker.target = target;
  state.picker.filter = '';
  state.picker.selected = new Set();
  state.picker.sessions = [];
  const mask = $('#modal-sessions');
  if (mask) mask.hidden = false;
  const search = $('#session-search');
  if (search) search.value = '';
  setText('#session-selected-count', '已选 0 个');
  loadSessions();
}

function closeSessionPicker() {
  const mask = $('#modal-sessions');
  if (mask) mask.hidden = true;
}

function pickerExisting() {
  const target = state.picker.target;
  if (target === 'rule') {
    return state.rule.draft && Array.isArray(state.rule.draft.chats) ? state.rule.draft.chats : [];
  }
  const value = getPath(state.config || {}, target);
  return Array.isArray(value) ? value : [];
}

async function loadSessions() {
  const box = $('#session-list');
  if (!box) return;
  state.picker.loading = true;
  box.textContent = '';
  const loading = document.createElement('div');
  loading.className = 'session-loading';
  loading.textContent = '正在从微信拉取会话…';
  box.appendChild(loading);

  try {
    const data = await api('/api/sessions');
    state.picker.sessions = data && Array.isArray(data.items) ? data.items : [];
    if (!state.picker.sessions.length) {
      box.textContent = '';
      const empty = document.createElement('div');
      empty.className = 'empty';
      const title = document.createElement('p');
      title.className = 'empty-title';
      title.textContent = '没拿到会话';
      const sub = document.createElement('p');
      sub.className = 'muted tiny';
      sub.textContent = '确认机器人已启动、微信已登录；模拟模式下会给出几个演示会话。';
      empty.append(title, sub);
      box.appendChild(empty);
      return;
    }
    renderSessionList();
  } catch (err) {
    box.textContent = '';
    const empty = document.createElement('div');
    empty.className = 'empty';
    const title = document.createElement('p');
    title.className = 'empty-title';
    title.textContent = '拉取会话失败';
    const sub = document.createElement('p');
    sub.className = 'muted tiny';
    sub.textContent = err.message;
    empty.append(title, sub);
    box.appendChild(empty);
    toast('拉取会话失败：' + err.message, 'error');
  } finally {
    state.picker.loading = false;
  }
}

function renderSessionList() {
  const box = $('#session-list');
  if (!box) return;
  const existing = new Set(pickerExisting().map((x) => String(x)));
  const keyword = state.picker.filter.trim().toLowerCase();
  const items = state.picker.sessions.filter((item) => {
    if (!keyword) return true;
    return String(item && item.name ? item.name : '').toLowerCase().indexOf(keyword) >= 0;
  });

  box.textContent = '';
  if (!items.length) {
    const loading = document.createElement('div');
    loading.className = 'session-loading';
    loading.textContent = '没有匹配的会话';
    box.appendChild(loading);
    updatePickerCount();
    return;
  }

  items.forEach((item) => {
    const name = String(item && item.name ? item.name : '');
    if (!name) return;
    const exists = existing.has(name);
    const row = document.createElement('label');
    row.className = 'session-item' + (exists ? ' exists' : '');

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.disabled = exists;
    cb.checked = exists || state.picker.selected.has(name);
    if (!exists) {
      cb.addEventListener('change', () => {
        if (cb.checked) state.picker.selected.add(name);
        else state.picker.selected.delete(name);
        updatePickerCount();
      });
    }

    const main = document.createElement('div');
    main.className = 'session-main';
    const nameEl = document.createElement('div');
    nameEl.className = 'session-name';
    nameEl.textContent = name;
    const lastEl = document.createElement('div');
    lastEl.className = 'session-last';
    lastEl.textContent = String(item.last || item.time || '');
    main.append(nameEl, lastEl);

    const meta = document.createElement('div');
    meta.className = 'session-meta';
    const type = String(item.type || '');
    const typeBadge = document.createElement('span');
    typeBadge.className = 'badge ' + (type === 'group' ? 'blue' : 'ghost');
    typeBadge.textContent = type === 'group' ? '群聊' : type === 'friend' ? '好友' : '未知';
    meta.appendChild(typeBadge);
    if (item.unread) {
      const unread = document.createElement('span');
      unread.className = 'badge warn';
      unread.textContent = '未读';
      meta.appendChild(unread);
    }
    if (exists) {
      const added = document.createElement('span');
      added.className = 'badge ok';
      added.textContent = '已加入';
      meta.appendChild(added);
    }

    row.append(cb, main, meta);
    box.appendChild(row);
  });

  updatePickerCount();
}

function updatePickerCount() {
  setText('#session-selected-count', '已选 ' + state.picker.selected.size + ' 个');
}

function confirmSessionPick() {
  const names = Array.from(state.picker.selected);
  if (!names.length) {
    toast('先勾选几个会话吧', 'warn');
    return;
  }

  if (state.picker.target === 'rule') {
    const draft = state.rule.draft;
    if (!draft) {
      toast('规则编辑面板还没打开', 'error');
      return;
    }
    const list = Array.isArray(draft.chats) ? draft.chats.slice() : [];
    names.forEach((name) => { if (list.indexOf(name) < 0) list.push(name); });
    draft.chats = list;
    markDirty();
    renderRuleTags();
  } else {
    if (!state.config) {
      toast('配置还没加载完成，稍等一下再试', 'error');
      return;
    }
    const current = getPath(state.config, state.picker.target);
    const list = Array.isArray(current) ? current.slice() : [];
    names.forEach((name) => { if (list.indexOf(name) < 0) list.push(name); });
    setPath(state.config, state.picker.target, list);
    markDirty();
    renderTagEditors();
    syncModeUi();
  }

  closeSessionPicker();
  toast('已加入 ' + names.length + ' 个会话');
}

/* ------------------------------------------------------------------ 监听规则 */

function blankRule() {
  return {
    id: newRuleId(),
    name: '新规则',
    enabled: true,
    scope: 'all',
    chats: [],
    match: 'contains',
    patterns: [],
    action: 'ai',
    template: '',
    prompt: '',
    priority: 100,
    cooldown_seconds: 0,
  };
}

function badgeEl(text, cls) {
  const b = document.createElement('span');
  b.className = 'badge' + (cls ? ' ' + cls : '');
  b.textContent = text;
  return b;
}

function renderRules() {
  const box = $('#rules-list');
  if (!box) return;
  const rules = (state.config && Array.isArray(state.config.rules)) ? state.config.rules : [];
  box.textContent = '';

  if (!rules.length) {
    const empty = document.createElement('div');
    empty.className = 'card empty';
    const title = document.createElement('p');
    title.className = 'empty-title';
    title.textContent = '还没有任何规则';
    const sub = document.createElement('p');
    sub.className = 'muted tiny';
    sub.textContent = '点右上角「新增规则」，比如「收到“你好”就回一句」。';
    empty.append(title, sub);
    box.appendChild(empty);
    return;
  }

  rules.forEach((rule, index) => box.appendChild(buildRuleCard(rule, index)));
}

function buildRuleCard(rule, index) {
  const card = document.createElement('div');
  const editing = state.rule.open && state.rule.index === index;
  card.className = 'rule-card' + (rule.enabled ? '' : ' off') + (editing ? ' editing' : '');

  const main = document.createElement('div');
  main.className = 'rule-main';

  const top = document.createElement('div');
  top.className = 'rule-top';
  const name = document.createElement('span');
  name.className = 'rule-name';
  name.textContent = rule.name || '未命名规则';
  top.appendChild(name);

  const badges = document.createElement('div');
  badges.className = 'rule-badges';
  badges.appendChild(badgeEl(SCOPE_LABEL[rule.scope] || '全部会话', 'ghost'));
  badges.appendChild(badgeEl(MATCH_LABEL[rule.match] || rule.match, 'ghost'));
  badges.appendChild(badgeEl(
    ACTION_LABEL[rule.action] || rule.action,
    rule.action === 'ignore' ? 'err' : rule.action === 'template' ? 'warn' : 'accent'
  ));
  badges.appendChild(badgeEl('优先级 ' + rule.priority, 'ghost'));
  if (num(rule.cooldown_seconds, 0) > 0) badges.appendChild(badgeEl('冷却 ' + rule.cooldown_seconds + ' 秒', 'ghost'));
  if (!rule.enabled) badges.appendChild(badgeEl('已停用', 'ghost'));

  const detail = document.createElement('div');
  detail.className = 'rule-detail';
  const parts = [];
  if (rule.match === 'always') parts.push('匹配：无条件命中');
  else parts.push('关键词：' + (rule.patterns.length ? rule.patterns.join('、') : '（空，永远不会命中）'));
  parts.push('会话：' + (rule.chats.length ? rule.chats.join('、') : '全部'));
  if (rule.action === 'template' && rule.template) parts.push('话术：' + rule.template);
  if (rule.action === 'ai' && rule.prompt) parts.push('追加提示：' + rule.prompt);
  detail.textContent = parts.join(' · ');

  main.append(top, badges, detail);

  const side = document.createElement('div');
  side.className = 'rule-side';

  const sw = document.createElement('label');
  sw.className = 'switch';
  sw.title = '启用 / 停用';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = !!rule.enabled;
  cb.addEventListener('change', () => {
    rule.enabled = cb.checked;
    markDirty();
    renderRules();
  });
  const track = document.createElement('span');
  track.className = 'track';
  sw.append(cb, track);

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'btn sm ghost';
  editBtn.textContent = '编辑';
  editBtn.addEventListener('click', () => openRuleEditor(index));

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'btn sm danger';
  delBtn.textContent = '删除';
  delBtn.addEventListener('click', () => removeRule(index));

  side.append(sw, editBtn, delBtn);
  card.append(main, side);
  return card;
}

function openRuleEditor(index) {
  const rules = (state.config && state.config.rules) || [];
  const source = index >= 0 && rules[index] ? rules[index] : null;
  const draft = source ? JSON.parse(JSON.stringify(source)) : blankRule();

  state.rule = { open: true, index: source ? index : -1, draft: draft };

  const editor = $('#rule-editor');
  if (editor) editor.hidden = false;
  setText('#rule-editor-title', source ? '编辑规则' : '新增规则');

  const nameEl = $('#rule-name');
  if (nameEl) { nameEl.value = draft.name || ''; }
  const enabledEl = $('#rule-enabled');
  if (enabledEl) enabledEl.checked = !!draft.enabled;
  const matchEl = $('#rule-match');
  if (matchEl) matchEl.value = draft.match;
  const actionEl = $('#rule-action');
  if (actionEl) actionEl.value = draft.action;
  const priorityEl = $('#rule-priority');
  if (priorityEl) priorityEl.value = String(draft.priority);
  const cooldownEl = $('#rule-cooldown');
  if (cooldownEl) cooldownEl.value = String(draft.cooldown_seconds);
  const templateEl = $('#rule-template');
  if (templateEl) templateEl.value = draft.template || '';
  const promptEl = $('#rule-prompt');
  if (promptEl) promptEl.value = draft.prompt || '';
  $$('#rule-scope input[type="radio"]').forEach((radio) => { radio.checked = radio.value === draft.scope; });

  mountRuleTags();
  updateRuleEditorUi();
  renderRules();

  if (editor && editor.scrollIntoView) {
    try { editor.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (err) { /* 忽略 */ }
  }
}

function updateRuleEditorUi() {
  const draft = state.rule.draft;
  if (!draft) return;
  const match = ($('#rule-match') || {}).value;
  const action = ($('#rule-action') || {}).value;
  const patternsField = $('#rule-patterns-field');
  if (patternsField) patternsField.hidden = match === 'always';
  const templateField = $('#rule-template-field');
  if (templateField) templateField.hidden = action !== 'template';
  const promptField = $('#rule-prompt-field');
  if (promptField) promptField.hidden = action !== 'ai';
  const enabledEl = $('#rule-enabled');
  setText('#rule-enabled-text', enabledEl && enabledEl.checked ? '已启用' : '已停用');
}

function closeRuleEditor() {
  state.rule = { open: false, index: -1, draft: null };
  const editor = $('#rule-editor');
  if (editor) editor.hidden = true;
  renderRules();
}

function saveRuleDraft() {
  const draft = state.rule.draft;
  if (!draft || !state.config) return;

  const nameEl = $('#rule-name');
  draft.name = String((nameEl && nameEl.value) || '').trim();
  if (!draft.name) {
    toast('先给规则起个名字', 'error');
    if (nameEl) nameEl.focus();
    return;
  }

  const enabledEl = $('#rule-enabled');
  draft.enabled = !!(enabledEl && enabledEl.checked);
  const scopeInput = $$('#rule-scope input[type="radio"]').filter((r) => r.checked)[0];
  draft.scope = scopeInput ? scopeInput.value : 'all';
  const matchEl = $('#rule-match');
  draft.match = matchEl ? matchEl.value : 'contains';
  const actionEl = $('#rule-action');
  draft.action = actionEl ? actionEl.value : 'ai';
  const priorityEl = $('#rule-priority');
  draft.priority = clamp(Math.round(num(priorityEl && priorityEl.value, 100)), 0, 9999);
  const cooldownEl = $('#rule-cooldown');
  draft.cooldown_seconds = clamp(num(cooldownEl && cooldownEl.value, 0), 0, 86400);
  const templateEl = $('#rule-template');
  draft.template = templateEl ? String(templateEl.value || '') : '';
  const promptEl = $('#rule-prompt');
  draft.prompt = promptEl ? String(promptEl.value || '') : '';

  if (draft.match !== 'always' && !draft.patterns.length) {
    toast('这种匹配方式至少要有一个关键词', 'error');
    return;
  }
  if (draft.match === 'regex') {
    for (let i = 0; i < draft.patterns.length; i += 1) {
      try { RegExp(draft.patterns[i]); } catch (err) {
        toast('正则写错了：' + draft.patterns[i], 'error');
        return;
      }
    }
  }

  const rules = state.config.rules.slice();
  if (state.rule.index >= 0 && state.rule.index < rules.length) rules[state.rule.index] = draft;
  else rules.push(draft);
  state.config.rules = rules;

  markDirty();
  closeRuleEditor();
  toast('规则已写入，记得点「保存并生效」');
}

async function removeRule(index) {
  const rule = state.config && state.config.rules ? state.config.rules[index] : null;
  if (!rule) return;
  const ok = await confirmDialog({
    title: '删除规则',
    message: '确定删除规则「' + (rule.name || '未命名') + '」吗？删掉之后还要点「保存并生效」才会写入后端。',
    okText: '删除',
  });
  if (!ok) return;
  state.config.rules.splice(index, 1);
  if (state.rule.open) closeRuleEditor();
  markDirty();
  renderRules();
  toast('规则已删除，记得保存');
}

/* ------------------------------------------------------------------ 概览与状态 */

function kvRow(label, value) {
  const row = document.createElement('div');
  row.className = 'kv-row';
  const key = document.createElement('span');
  key.textContent = label;
  const val = document.createElement('b');
  val.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  row.append(key, val);
  return row;
}

function resultRow(label, value) {
  const row = document.createElement('div');
  row.className = 'r-row';
  const key = document.createElement('span');
  key.textContent = label;
  const val = document.createElement('span');
  val.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  row.append(key, val);
  return row;
}

function renderOverview() {
  const st = state.status || {};
  const stats = st.stats || {};

  setText('#stat-today', fmtInt(stats.last24h));
  setText('#stat-today-sub', '收到 ' + fmtInt(stats.inbound) + ' 条');
  setText('#stat-replied', fmtInt(stats.replied));
  setText('#stat-replied-sub', '累计收发 ' + fmtInt(stats.total) + ' 条');
  setText('#stat-chats', fmtInt(stats.chats24h));
  setText('#stat-uptime', fmtUptime(st.uptime));
  setText('#stat-uptime-sub', st.running ? '本次启动至今' : '机器人当前未运行');

  const adapter = st.adapter || {};
  const connected = !!adapter.connected;
  const connBadge = $('#conn-badge');
  if (connBadge) {
    connBadge.className = 'badge ' + (connected ? 'ok' : (st.running ? 'err' : 'ghost'));
    connBadge.textContent = connected ? '已连接' : (st.running ? '未连接' : '未启动');
  }

  const connBody = $('#conn-body');
  if (connBody) {
    connBody.textContent = '';
    const kind = adapter.adapter || st.adapter_kind || '';
    connBody.appendChild(kvRow('接入方式', kind === 'mock' ? '模拟模式（mock）' : (kind ? '微信 ' + kind : '—')));
    connBody.appendChild(kvRow('连接详情', adapter.detail || '—'));
    connBody.appendChild(kvRow('微信版本', adapter.wechat_version || '—'));
    connBody.appendChild(kvRow('我的昵称', adapter.my_names && adapter.my_names.length ? adapter.my_names.join('、') : '—'));
    const listen = Array.isArray(st.listen) ? st.listen : [];
    let listenText = '0 个';
    if (listen.length) {
      listenText = listen.length + ' 个：' + listen.slice(0, 4).join('、') + (listen.length > 4 ? ' 等' : '');
    }
    connBody.appendChild(kvRow('监听会话', listenText));
    connBody.appendChild(kvRow('最近轮询', adapter.last_poll_ts ? fmtTime(adapter.last_poll_ts) : '—'));
  }

  const errorEl = $('#conn-error');
  if (errorEl) {
    if (adapter.last_error) {
      errorEl.textContent = String(adapter.last_error);
      errorEl.hidden = false;
    } else {
      errorEl.hidden = true;
    }
  }

  const cfg = state.config || {};
  const ai = cfg.ai || {};
  const aiBody = $('#ai-card-body');
  if (aiBody) {
    aiBody.textContent = '';
    aiBody.appendChild(kvRow('配置状态', st.ai_configured ? 'API Key 已填写' : '还没填 API Key'));
    aiBody.appendChild(kvRow('服务商', providerLabel(ai.provider)));
    aiBody.appendChild(kvRow('模型', ai.model || '—'));
    aiBody.appendChild(kvRow('上下文', num(ai.history_rounds, 6) + ' 轮 · 最大 ' + num(ai.max_tokens, 800) + ' tokens'));
    aiBody.appendChild(kvRow('人设长度', String(ai.persona || '').length + ' 字'));
  }

  const feed = $('#feed');
  const notes = Array.isArray(st.notifications) ? st.notifications : [];
  if (feed) {
    feed.textContent = '';
    if (!notes.length) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.style.padding = '28px 12px';
      const title = document.createElement('p');
      title.className = 'empty-title';
      title.textContent = '还没有动态';
      const sub = document.createElement('p');
      sub.className = 'muted tiny';
      sub.textContent = '启动机器人后，回复、跳过、人工接管都会出现在这里。';
      li.append(title, sub);
      feed.appendChild(li);
    } else {
      notes.slice(0, 40).forEach((note) => {
        const li = document.createElement('li');
        li.className = 'feed-item';
        const dot = document.createElement('span');
        dot.className = 'feed-dot k-' + String(note.kind || 'info');
        const main = document.createElement('div');
        main.className = 'feed-main';
        const msg = document.createElement('p');
        msg.className = 'feed-msg';
        msg.textContent = String(note.message || '');
        const time = document.createElement('span');
        time.className = 'feed-time';
        time.textContent = fmtClock(note.ts) + ' · ' + (KIND_LABEL[note.kind] || '信息');
        main.append(msg, time);
        li.append(dot, main);
        feed.appendChild(li);
      });
    }
  }
  setText('#notif-count', notes.length ? '共 ' + notes.length + ' 条' : '');

  const pipeline = st.pipeline || {};
  setText('#qa-adapter', st.adapter_kind === 'mock' ? '模拟模式' : '真实微信');
  setText('#qa-queue', num(pipeline.queued, 0) + ' 条' + (pipeline.busy ? '（处理中）' : ''));
  setText('#qa-listen', (Array.isArray(st.listen) ? st.listen.length : 0) + ' 个会话');
}

function renderStatusUi() {
  const st = state.status || {};
  const running = !!st.running;
  renderChildBanner(st);
  renderStartupProgress(st);

  const pill = $('#run-pill');
  if (pill) {
    pill.className = 'pill';
    if (state.pollFailed) pill.classList.add('err');
    else if (running) pill.classList.add('on');
    else pill.classList.add('warn');
  }
  setText('#run-pill-text', state.pollFailed ? '后端连接中断' : (running ? '机器人运行中' : '已停止'));

  const brand = $('#brand-status');
  if (brand) {
    brand.className = 'brand-status';
    if (state.pollFailed) brand.classList.add('err');
    else if (running) brand.classList.add('on');
  }
  setText('#brand-status-text', state.pollFailed ? '连接中断' : (running ? '运行中' : '已停止'));

  const startBtn = $('#btn-start');
  if (startBtn) startBtn.disabled = running || state.pollFailed;
  const stopBtn = $('#btn-stop');
  if (stopBtn) stopBtn.disabled = !running || state.pollFailed;
  const qaStart = $('#qa-start');
  if (qaStart) qaStart.disabled = running;
  const qaStop = $('#qa-stop');
  if (qaStop) qaStop.disabled = !running;

  setText('#side-version', state.version || '—');
  setText('#side-adapter', st.adapter_kind === 'mock' ? '模拟模式' : '真实微信');

  const keyBadge = $('#ai-key-badge');
  if (keyBadge) {
    keyBadge.className = 'badge ' + (st.ai_configured ? 'ok' : 'err');
    keyBadge.textContent = st.ai_configured ? 'API Key 已配置' : '还没填 API Key';
  }
}

function renderSystemInfo() {
  const st = state.status || {};
  const cfg = state.config || {};
  setText('#data-dir', st.data_dir || '—');
  setText('#about-version', state.version ? 'v' + state.version : '—');
  setText('#adapter-live', st.running
    ? (st.adapter_kind === 'mock' ? '模拟模式运行中' : '真实微信运行中')
    : '机器人未启动（当前选择：' + (cfg.adapter === 'mock' ? '模拟模式' : '真实微信') + '）');

  const mockCard = $('#mock-card');
  if (mockCard) mockCard.hidden = cfg.adapter !== 'mock';
}

function renderRhythmDyn() {
  const st = state.status || {};
  const takeover = (st.pipeline && st.pipeline.takeover) || {};
  const names = Object.keys(takeover);
  const card = $('#takeover-card');
  if (!card) return;
  card.hidden = names.length === 0;

  const list = $('#takeover-list');
  if (!list) return;
  list.textContent = '';

  names.sort((a, b) => num(takeover[b], 0) - num(takeover[a], 0)).forEach((chat) => {
    const row = document.createElement('div');
    row.className = 'takeover-item';

    const left = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'takeover-chat';
    name.textContent = chat;
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = '自动回复暂停中';
    left.append(name, hint);

    const right = document.createElement('div');
    right.className = 'row';
    const time = document.createElement('span');
    time.className = 'takeover-time';
    time.textContent = '还剩 ' + fmtSeconds(takeover[chat]);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn sm ghost';
    btn.textContent = '恢复';
    btn.addEventListener('click', () => resumeChat(chat));
    right.append(time, btn);

    row.append(left, right);
    list.appendChild(row);
  });
}

async function resumeChat(chat) {
  try {
    await api('/api/chats/resume', { method: 'POST', body: { chat: chat } });
    toast('已恢复「' + chat + '」的自动回复');
    await pollStatus(true);
  } catch (err) {
    toast('恢复失败：' + err.message, 'error');
  }
}

function syncModeUi() {
  const cfg = state.config;
  if (!cfg) return;

  const pmode = cfg.private.mode;
  setText('#private-mode-hint', pmode === 'all'
    ? '所有私聊都会回复（永久忽略名单和规则依然优先）。'
    : pmode === 'include'
      ? '只回复名单里的私聊；名单为空就一条都不回。'
      : '名单里的私聊不回复，其它私聊都回。');
  const privateList = $('#private-list-card');
  if (privateList) privateList.hidden = pmode === 'all';
  setText('#private-list-hint', pmode === 'include'
    ? '只回这些人：回车添加，点 × 删除。留空 = 不回复任何私聊。'
    : pmode === 'exclude'
      ? '不回这些人：回车添加，点 × 删除。留空 = 所有私聊都回。'
      : '');

  const requireKeyword = !!cfg.private.require_keyword;
  const keywordCard = $('#private-keyword-card');
  if (keywordCard) keywordCard.style.opacity = requireKeyword ? '1' : '0.72';
  setText('#private-keyword-hint', requireKeyword
    ? '只有包含这些关键词的私聊才会回复。'
    : '当前关闭：私聊不要求关键词，会按默认逻辑回复。');

  const gmode = cfg.group.mode;
  setText('#group-mode-hint', gmode === 'all'
    ? '所有群消息都会进入判定（还要看下面的触发方式）。'
    : gmode === 'include'
      ? '只处理名单里的群；名单为空就一条都不回。'
      : '名单里的群不处理，其它群都处理。');
  const groupList = $('#group-list-card');
  if (groupList) groupList.hidden = gmode === 'all';
  setText('#group-list-hint', gmode === 'include'
    ? '只回这些群：回车添加，点 × 删除。留空 = 不回复任何群。'
    : gmode === 'exclude'
      ? '不回这些群：回车添加，点 × 删除。'
      : '');

  const trigger = cfg.group.trigger;
  setText('#group-trigger-hint', trigger === 'at'
    ? '只有群里 @我 才回，最稳当。'
    : trigger === 'keyword'
      ? '消息里出现关键词，或者 @我，都会回复。'
      : '群里任何人说话都回，非常容易刷屏，慎用。');
  const groupKeywordCard = $('#group-keyword-card');
  if (groupKeywordCard) groupKeywordCard.style.opacity = trigger === 'keyword' ? '1' : '0.72';
  setText('#group-keyword-hint', trigger === 'keyword'
    ? '命中任意一个关键词就回复。'
    : '当前触发方式不是「命中关键词」，这里填的关键词不会生效。');

  const quiet = !!cfg.bot.quiet_hours_enabled;
  $$('#quiet-fields input').forEach((input) => { input.disabled = !quiet; });
  const quietFields = $('#quiet-fields');
  if (quietFields) quietFields.style.opacity = quiet ? '1' : '0.55';

  const mockCard = $('#mock-card');
  if (mockCard) mockCard.hidden = cfg.adapter !== 'mock';

  if (state.rule.open) updateRuleEditorUi();
}

/* ------------------------------------------------------------------ 轮询 */

async function pollStatus(manual) {
  if (!state.booted) return;
  try {
    const status = await api('/api/status');
    state.status = status || {};
    if (state.pollFailed) {
      state.pollFailed = false;
      toast('已恢复与后端的连接');
    }
    renderStatusUi();
    renderOverview();
    renderRhythmDyn();
    renderSystemInfo();
    if (manual) toast('状态已刷新');
  } catch (err) {
    const first = !state.pollFailed;
    state.pollFailed = true;
    renderStatusUi();
    if (first || manual) toast('状态刷新失败：' + err.message, 'error');
  }
}

function startPolling() {
  if (state.timer) window.clearInterval(state.timer);
  state.timer = window.setInterval(() => { pollStatus(false); }, 3000);
}

/* ------------------------------------------------------------------ 启停机器人 */

async function botAction(action) {
  if (!state.booted) return;
  const isStart = action === 'start';
  const btn = isStart ? $('#btn-start') : $('#btn-stop');
  setBusy(btn, true, isStart ? '启动中…' : '停止中…');
  try {
    const data = await api('/api/bot/' + action, { method: 'POST' });
    if (data && data.status) state.status = data.status;
    renderStatusUi();
    renderOverview();
    renderRhythmDyn();
    renderSystemInfo();
    toast(isStart ? '机器人已启动' : '机器人已停止');
  } catch (err) {
    toast((isStart ? '启动' : '停止') + '失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
    renderStatusUi();
  }
}

/* ------------------------------------------------------------------ 保存 */

async function saveAll() {
  if (!state.booted || !state.config) {
    toast('后端还没连上，暂时不能保存', 'error');
    return;
  }

  const invalid = validateConfig(state.config);
  if (invalid) {
    toast(invalid.message, 'error');
    switchView(invalid.view);
    return;
  }

  coerceConfig(state.config);

  const topBtn = $('#btn-save-top');
  const fabBtn = $('#btn-save-fab');
  [topBtn, fabBtn].forEach((btn) => setBusy(btn, true, '保存中…'));

  try {
    const data = await api('/api/config', { method: 'PUT', body: state.config });
    if (data && data.config) state.config = normalizeConfig(data.config);

    closeRuleEditor();
    setDirty(false);
    syncInputs();
    syncModeUi();
    renderTagEditors();
    renderRules();
    renderOverview();
    renderStatusUi();
    renderRhythmDyn();
    renderSystemInfo();
    if (state.view === 'logs') renderLogs();
    toast('已保存并生效');
  } catch (err) {
    toast('保存失败：' + err.message, 'error');
  } finally {
    [topBtn, fabBtn].forEach((btn) => setBusy(btn, false));
  }
}

/* ------------------------------------------------------------------ AI 页 */

async function testAI() {
  const btn = $('#ai-test-btn');
  const box = $('#ai-test-result');
  if (!box) return;
  const ai = (state.config && state.config.ai) || {};
  const keyInput = $('#ai-api-key');
  if (keyInput && ai.api_key_set && !keyInput.value) {
    keyInput.placeholder = ai.api_key_hint ? ('已保存 ' + ai.api_key_hint + '，留空表示不修改') : '已保存，留空表示不修改';
  }

  setBusy(btn, true, '测试中…');
  box.hidden = true;

  try {
    const data = await api('/api/ai/test', {
      method: 'POST',
      body: { base_url: ai.base_url, api_key: ai.api_key, model: ai.model },
    });
    box.className = 'result ok';
    box.textContent = '';
    const title = document.createElement('div');
    title.className = 'r-title';
    title.textContent = '连接成功';
    box.appendChild(title);
    box.appendChild(resultRow('延迟', num(data && data.latency_ms, 0) + ' ms'));
    box.appendChild(resultRow('模型', (data && data.model) || ai.model || '—'));
    const pre = document.createElement('pre');
    pre.textContent = String((data && data.reply) || '（模型没有返回内容）');
    box.appendChild(pre);
    box.hidden = false;
    toast('模型接口连接正常');
  } catch (err) {
    box.className = 'result err';
    box.textContent = '';
    const title = document.createElement('div');
    title.className = 'r-title';
    title.textContent = '连接失败';
    const text = document.createElement('div');
    text.textContent = err.message;
    box.append(title, text);
    box.hidden = false;
    toast('连接失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

async function runPreview() {
  const textEl = $('#preview-text');
  const btn = $('#preview-btn');
  const box = $('#preview-result');
  if (!textEl || !box) return;

  const text = String(textEl.value || '').trim();
  if (!text) {
    toast('先写一条要试跑的消息', 'error');
    textEl.focus();
    return;
  }

  setBusy(btn, true, '试跑中…');
  box.hidden = true;

  try {
    const data = await api('/api/ai/preview', {
      method: 'POST',
      body: {
        text: text,
        chat: String(($('#preview-chat') || {}).value || '').trim() || '测试会话',
        chat_type: String(($('#preview-type') || {}).value || 'friend'),
        sender: String(($('#preview-sender') || {}).value || '').trim() || '小明',
      },
    });

    box.className = 'result ' + (data && data.reply ? 'ok' : '');
    box.textContent = '';

    const title = document.createElement('div');
    title.className = 'r-title';
    title.appendChild(badgeEl(data && data.reply ? '会回复' : '不会回复', data && data.reply ? 'ok' : 'ghost'));
    box.appendChild(title);

    box.appendChild(resultRow('判定原因', (data && data.reason) || '—'));
    box.appendChild(resultRow('命中规则', (data && data.rule) || '无（走默认逻辑）'));
    box.appendChild(resultRow('来源', SOURCE_LABEL[(data && data.source) || 'default'] || (data && data.source) || '—'));
    if (data && data.template) box.appendChild(resultRow('固定话术', data.template));
    if (data && data.reply_text) {
      const pre = document.createElement('pre');
      pre.textContent = String(data.reply_text);
      box.appendChild(pre);
    }
    if (data && data.error) {
      box.appendChild(resultRow('模型报错', data.error));
    }
    box.hidden = false;
  } catch (err) {
    box.className = 'result err';
    box.textContent = '';
    const title = document.createElement('div');
    title.className = 'r-title';
    title.textContent = '试跑失败';
    const text2 = document.createElement('div');
    text2.textContent = err.message;
    box.append(title, text2);
    box.hidden = false;
    toast('试跑失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

function toggleKeyVisibility() {
  const input = $('#ai-api-key');
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
}

/* ------------------------------------------------------------------ 消息日志 */

function tdEl(text, cls) {
  const td = document.createElement('td');
  if (cls) td.className = cls;
  td.textContent = text === null || text === undefined ? '' : String(text);
  return td;
}

function renderLogs() {
  const body = $('#log-body');
  if (!body) return;
  const box = state.logs;

  body.textContent = '';
  box.items.forEach((item) => body.appendChild(buildLogRow(item)));

  const empty = $('#log-empty');
  if (empty) {
    empty.hidden = box.items.length > 0;
    if (box.items.length === 0) {
      setText(empty.querySelector('.empty-title'), box.chat ? '没有符合筛选条件的记录' : '还没有消息记录');
    }
  }

  const more = $('#log-more');
  if (more) more.hidden = box.done || box.items.length === 0;

  setText('#log-count', box.items.length
    ? '已加载 ' + box.items.length + ' 条' + (box.done ? ' · 没有更多了' : '')
    : '');
}

function buildLogRow(item) {
  const tr = document.createElement('tr');
  const id = String(item.id === null || item.id === undefined ? '' : item.id);

  tr.appendChild(tdEl(fmtTime(item.ts), 'cell-time'));

  const dirCell = document.createElement('td');
  const dir = String(item.direction || '');
  dirCell.appendChild(badgeEl(dir === 'out' ? '发出' : '收到', dir === 'out' ? 'blue' : 'ghost'));
  tr.appendChild(dirCell);

  tr.appendChild(tdEl(item.chat || '—', 'cell-chat'));
  tr.appendChild(tdEl(item.sender || '—', 'cell-chat'));

  const contentCell = document.createElement('td');
  contentCell.className = 'cell-content';
  const content = String(item.content || '');
  const div = document.createElement('div');
  if (!content) {
    div.className = 'muted';
    div.textContent = '（空）';
  } else if (content.length > 80) {
    const expanded = state.logs.expanded.has(id);
    div.className = expanded ? 'expanded' : 'clipped';
    div.textContent = expanded ? content : content.slice(0, 80) + '…';
    div.title = '点击展开 / 收起';
    div.addEventListener('click', () => {
      if (state.logs.expanded.has(id)) state.logs.expanded.delete(id);
      else state.logs.expanded.add(id);
      renderLogs();
    });
  } else {
    div.textContent = content;
  }
  contentCell.appendChild(div);
  tr.appendChild(contentCell);

  const statusCell = document.createElement('td');
  const status = String(item.status || 'ok');
  const statusMap = {
    ok: ['ok', '成功'],
    failed: ['err', '失败'],
    skipped: ['ghost', '跳过'],
  };
  const info = statusMap[status] || ['ghost', status];
  statusCell.appendChild(badgeEl(info[1], info[0]));
  tr.appendChild(statusCell);

  tr.appendChild(tdEl(item.note || '', 'cell-note'));
  return tr;
}

async function loadLogs(options) {
  const opts = options || {};
  const reset = !!opts.reset;
  const box = state.logs;
  if (box.loading) return;

  if (reset) {
    box.items = [];
    box.offset = 0;
    box.done = false;
    box.expanded.clear();
    renderLogs();
  }

  box.loading = true;
  const more = $('#log-more');
  if (more) setBusy(more, true, '加载中…');

  try {
    let url = '/api/messages?limit=50&offset=' + box.offset;
    if (box.chat) url += '&chat=' + encodeURIComponent(box.chat);
    const data = await api(url);
    const items = data && Array.isArray(data.items) ? data.items : [];
    box.items = box.items.concat(items);
    box.offset += items.length;
    box.done = items.length < 50;
    box.loaded = true;
    renderLogs();
  } catch (err) {
    box.loaded = true;
    renderLogs();
    toast('加载消息日志失败：' + err.message, 'error');
  } finally {
    box.loading = false;
    if (more) setBusy(more, false);
  }
}

function applyLogFilter() {
  const input = $('#log-filter');
  state.logs.chat = String((input && input.value) || '').trim();
  loadLogs({ reset: true });
}

async function clearLogs() {
  const ok = await confirmDialog({
    title: '清空消息日志',
    message: '所有收发记录都会被删除，无法恢复。确定清空吗？',
    okText: '清空',
  });
  if (!ok) return;
  try {
    await api('/api/messages', { method: 'DELETE' });
    state.logs.items = [];
    state.logs.offset = 0;
    state.logs.done = false;
    state.logs.expanded.clear();
    renderLogs();
    toast('消息日志已清空');
  } catch (err) {
    toast('清空失败：' + err.message, 'error');
  }
}

/* ------------------------------------------------------------------ 系统设置 */

async function systemSend() {
  const chatEl = $('#send-chat');
  const textEl = $('#send-text');
  const box = $('#send-result');
  const chat = String((chatEl && chatEl.value) || '').trim();
  const text = String((textEl && textEl.value) || '').trim();

  if (!chat || !text) {
    toast('会话名和消息内容都要填', 'error');
    return;
  }

  const btn = $('#send-btn');
  setBusy(btn, true, '发送中…');
  if (box) box.hidden = true;

  try {
    const data = await api('/api/send', { method: 'POST', body: { chat: chat, text: text } });
    const ok = !!(data && data.ok);
    if (box) {
      box.className = 'result ' + (ok ? 'ok' : 'err');
      box.textContent = '';
      box.appendChild(resultRow('结果', ok ? '发送成功' : '发送失败'));
      box.appendChild(resultRow('详情', (data && data.detail) || '—'));
      box.hidden = false;
    }
    if (ok) toast('已发送到「' + chat + '」');
    else toast('发送失败：' + ((data && data.detail) || '未知原因'), 'error');
  } catch (err) {
    if (box) {
      box.className = 'result err';
      box.textContent = '';
      box.appendChild(resultRow('失败原因', err.message));
      box.hidden = false;
    }
    toast('发送失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

async function mockInject() {
  const chat = String(($('#mock-chat') || {}).value || '').trim();
  const sender = String(($('#mock-sender') || {}).value || '').trim();
  const content = String(($('#mock-content') || {}).value || '').trim();
  const chatType = String(($('#mock-type') || {}).value || 'friend');

  if (!content) {
    toast('写点内容再注入吧', 'error');
    return;
  }

  const btn = $('#mock-inject-btn');
  const box = $('#mock-result');
  setBusy(btn, true, '注入中…');
  if (box) box.hidden = true;

  try {
    await api('/api/mock/inject', {
      method: 'POST',
      body: { chat: chat || '测试好友', sender: sender || chat || '测试好友', content: content, chat_type: chatType },
    });
    if (box) {
      box.className = 'result ok';
      box.textContent = '';
      box.appendChild(resultRow('结果', '已注入，几秒后可以看日志和实时动态'));
      box.hidden = false;
    }
    toast('已注入一条模拟消息');
    await pollStatus(false);
    loadMockSent(true);
  } catch (err) {
    if (box) {
      box.className = 'result err';
      box.textContent = '';
      box.appendChild(resultRow('失败原因', err.message));
      box.hidden = false;
    }
    toast('注入失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

async function loadMockSent(silent) {
  const list = $('#mock-list');
  if (!list) return;
  if (!state.config || state.config.adapter !== 'mock') return;

  try {
    const data = await api('/api/mock/sent');
    const items = data && Array.isArray(data.items) ? data.items.slice().reverse() : [];
    list.textContent = '';
    if (!items.length) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.style.padding = '20px 12px';
      const p = document.createElement('p');
      p.className = 'muted tiny';
      p.textContent = '模拟模式下还没有发出过消息。';
      li.appendChild(p);
      list.appendChild(li);
      return;
    }
    items.slice(0, 30).forEach((item) => {
      const li = document.createElement('li');
      li.className = 'list-item';
      const time = document.createElement('span');
      time.className = 'li-time';
      time.textContent = fmtClock(item.ts);
      const chat = document.createElement('span');
      chat.className = 'li-chat';
      chat.textContent = '→ ' + String(item.chat || '');
      const text = document.createElement('span');
      text.className = 'li-text';
      text.textContent = String(item.text || '');
      li.append(time, chat, text);
      list.appendChild(li);
    });
  } catch (err) {
    if (!silent) toast('读取模拟发送记录失败：' + err.message, 'error');
  }
}

async function loadEvents(silent) {
  const list = $('#events-list');
  if (!list) return;
  try {
    const data = await api('/api/events?limit=20');
    const items = data && Array.isArray(data.items) ? data.items : [];
    list.textContent = '';
    if (!items.length) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.style.padding = '20px 12px';
      const p = document.createElement('p');
      p.className = 'muted tiny';
      p.textContent = '还没有系统事件。';
      li.appendChild(p);
      list.appendChild(li);
      return;
    }
    items.forEach((ev) => {
      const li = document.createElement('li');
      li.className = 'list-item';
      const time = document.createElement('span');
      time.className = 'li-time';
      time.textContent = fmtTime(ev.ts);
      const level = String(ev.level || 'info');
      const lv = badgeEl(
        level === 'error' ? '错误' : level === 'warn' ? '警告' : '信息',
        level === 'error' ? 'err' : level === 'warn' ? 'warn' : 'ghost'
      );
      lv.classList.add('li-level');
      const msg = document.createElement('span');
      msg.className = 'li-text';
      msg.textContent = String(ev.message || '');
      li.append(time, lv, msg);
      list.appendChild(li);
    });
  } catch (err) {
    if (!silent) toast('读取系统事件失败：' + err.message, 'error');
  }
}

async function refreshSessionsForSystem() {
  const btn = $('#sessions-refresh-system');
  const box = $('#sessions-result');
  setBusy(btn, true, '拉取中…');
  if (box) box.hidden = true;

  try {
    const data = await api('/api/sessions');
    const items = data && Array.isArray(data.items) ? data.items : [];
    if (box) {
      box.className = 'result ' + (items.length ? 'ok' : 'err');
      box.textContent = '';
      if (!items.length) {
        box.appendChild(resultRow('结果', '没拿到会话'));
        box.appendChild(resultRow('可能原因', '机器人未启动 / 微信未登录 / 模拟模式下还未启动'));
      } else {
        box.appendChild(resultRow('结果', '共 ' + items.length + ' 个会话'));
        const pre = document.createElement('pre');
        pre.textContent = items.slice(0, 30)
          .map((item) => '· ' + String(item.name || '') + (item.type === 'group' ? '（群）' : ''))
          .join('\n');
        box.appendChild(pre);
      }
      box.hidden = false;
    }
    if (items.length) toast('已刷新，共 ' + items.length + ' 个会话');
    else toast('没拉到会话，检查一下机器人是否在运行', 'warn');
  } catch (err) {
    if (box) {
      box.className = 'result err';
      box.textContent = '';
      box.appendChild(resultRow('失败原因', err.message));
      box.hidden = false;
    }
    toast('拉取会话失败：' + err.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

/* ------------------------------------------------------------------ 人设页 */

const PERSONA_FIELDS = ['nickname', 'speaking_style', 'catchphrase', 'taboos', 'persona'];

function updatePersonaBadge() {
  const cfg = state.config || {};
  const nickname = String(getPath(cfg, 'ai.nickname') || '').trim();
  const persona = String(getPath(cfg, 'ai.persona') || '').trim();
  const badge = $('#persona-badge');
  if (!badge) return;
  if (!persona) {
    badge.textContent = '未设置';
    badge.className = 'badge warn';
  } else {
    badge.textContent = `${nickname || '未命名'} · ${persona.length} 字`;
    badge.className = 'badge ok';
  }
}

async function renderPersona() {
  updatePersonaBadge();

  const box = $('#persona-presets');
  if (box && !box.dataset.ready) {
    box.dataset.ready = '1';
    try {
      state.personaInfo = await api('/api/persona');
    } catch (err) {
      state.personaInfo = null;
    }
    const presets = (state.personaInfo && state.personaInfo.presets) || [];
    box.innerHTML = '';
    presets.forEach((preset) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn ghost';
      btn.textContent = preset.label;
      btn.title = preset.speaking_style || '';
      btn.addEventListener('click', () => {
        if (!state.config) return;
        PERSONA_FIELDS.forEach((field) => {
          setPath(state.config, 'ai.' + field, preset[field] || '');
        });
        syncInputs();
        markDirty();
        updatePersonaBadge();
        toast(`已套用「${preset.label}」，改完记得点保存`, 'ok');
      });
      box.appendChild(btn);
    });
  }

  const restore = $('#persona-restore');
  if (restore && !restore.dataset.ready) {
    restore.dataset.ready = '1';
    restore.addEventListener('click', async () => {
      if (!state.config) return;
      if (!state.personaInfo) {
        try { state.personaInfo = await api('/api/persona'); } catch (err) { state.personaInfo = null; }
      }
      const text = (state.personaInfo && state.personaInfo.default) || '';
      if (!text) { toast('拿不到默认人设', 'error'); return; }
      setPath(state.config, 'ai.persona', text);
      syncInputs();
      markDirty();
      updatePersonaBadge();
      toast('已恢复默认人设，记得保存', 'ok');
    });
  }

  const previewBtn = $('#persona-preview-btn');
  if (previewBtn && !previewBtn.dataset.ready) {
    previewBtn.dataset.ready = '1';
    previewBtn.addEventListener('click', runPersonaPreview);
  }
}

async function runPersonaPreview() {
  const input = $('#persona-preview-input');
  const box = $('#persona-preview-result');
  const btn = $('#persona-preview-btn');
  if (!input || !box) return;
  const text = String(input.value || '').trim();
  if (!text) { toast('先写一句对方说的话', 'error'); input.focus(); return; }

  setBusy(btn, true, '调用中…');
  box.hidden = true;
  try {
    const data = await api('/api/ai/preview', {
      method: 'POST',
      body: { text, chat: '预览会话', chat_type: 'friend', sender: '朋友' },
    });
    box.className = 'result ' + (data && data.reply_text ? 'ok' : '');
    box.textContent = '';
    const title = document.createElement('div');
    title.className = 'r-title';
    title.appendChild(badgeEl(data && data.reply_text ? '这样回' : '没回复', data && data.reply_text ? 'ok' : 'ghost'));
    box.appendChild(title);
    box.appendChild(resultRow('判定', (data && data.reason) || '—'));
    if (data && data.reply_text) {
      const pre = document.createElement('pre');
      pre.textContent = String(data.reply_text);
      box.appendChild(pre);
    }
    if (data && data.error) box.appendChild(resultRow('模型报错', data.error));
    box.hidden = false;
  } catch (err) {
    toast(err && err.message ? err.message : '试跑失败', 'error');
  } finally {
    setBusy(btn, false);
  }
}

/* ------------------------------------------------------------------ 运行日志 */

async function loadRunLogFiles(keepSelection) {
  const sel = $('#runlog-file');
  if (!sel) return;
  try {
    const data = await api('/api/logs/files');
    const items = (data && data.items) || [];
    const prev = keepSelection ? sel.value : '';
    sel.innerHTML = '';
    if (!items.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '（还没有日志文件）';
      sel.appendChild(opt);
      setText('#runlog-meta', '');
      return;
    }
    items.forEach((item) => {
      const opt = document.createElement('option');
      opt.value = item.key;
      const kb = item.size < 1024 ? `${item.size} B` : `${(item.size / 1024).toFixed(1)} KB`;
      opt.textContent = `${item.label} · ${item.name}（${kb}）`;
      opt.dataset.dir = item.dir;
      sel.appendChild(opt);
    });
    if (prev && items.some((i) => i.key === prev)) sel.value = prev;
    setText('#runlog-meta', `共 ${items.length} 个文件 · 数据目录 ${(data && data.data_dir) || ''}`);
  } catch (err) {
    toast(err && err.message ? err.message : '读取日志列表失败', 'error');
  }
}

async function loadRunLogTail() {
  const sel = $('#runlog-file');
  const box = $('#runlog-text');
  const linesEl = $('#runlog-lines');
  const btn = $('#runlog-load');
  if (!sel || !box) return;
  const key = sel.value;
  if (!key) {
    box.textContent = '还没有日志文件，先启动一次机器人。';
    return;
  }
  const lines = String((linesEl && linesEl.value) || '300');
  setBusy(btn, true, '读取中…');
  try {
    const data = await api(`/api/logs/tail?key=${encodeURIComponent(key)}&lines=${encodeURIComponent(lines)}`);
    const text = (data && data.text) || '';
    box.textContent = text || '（这个日志还是空的）';
    box.scrollTop = box.scrollHeight;
    setText('#runlog-meta', `${data.name || ''} · ${text.split('\n').length} 行`);
  } catch (err) {
    box.textContent = (err && err.message) || '读取失败';
  } finally {
    setBusy(btn, false);
  }
}

function wireRunLog() {
  const sel = $('#runlog-file');
  if (!sel || sel.dataset.ready) return;
  sel.dataset.ready = '1';

  const refreshBtn = $('#runlog-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', () => loadRunLogFiles(true));

  const loadBtn = $('#runlog-load');
  if (loadBtn) loadBtn.addEventListener('click', () => loadRunLogTail());

  if (sel) sel.addEventListener('change', () => loadRunLogTail());

  const openBtn = $('#runlog-open');
  if (openBtn) {
    openBtn.addEventListener('click', async () => {
      try {
        const res = await api('/api/logs/open', { method: 'POST', body: { key: sel.value || '' } });
        toast(res && res.ok ? '已在资源管理器中打开' : '打不开（可能没有权限）', res && res.ok ? 'ok' : 'error');
      } catch (err) {
        toast(err && err.message ? err.message : '打开失败', 'error');
      }
    });
  }

  const dlBtn = $('#runlog-download');
  if (dlBtn) {
    dlBtn.addEventListener('click', () => {
      if (!sel.value) return;
      window.open(`/api/logs/download?key=${encodeURIComponent(sel.value)}`, '_blank');
    });
  }

  loadRunLogFiles(false).then(() => loadRunLogTail());
}

/* ------------------------------------------------------ 启动进度条 */

function renderStartupProgress(status) {
  const card = $('#startup-card');
  if (!card) return;
  const s = (status && status.startup) || {};
  const percent = Number(s.percent ?? 100);
  const running = !!(status && status.running);
  const show = !running && percent < 100;
  card.hidden = !show;
  if (!show) return;
  const fill = $('#startup-fill');
  const pct = $('#startup-percent');
  const step = $('#startup-step');
  const detail = $('#startup-detail');
  if (fill) fill.style.width = Math.max(2, Math.min(100, percent)) + '%';
  if (pct) pct.textContent = Math.round(percent) + '%';
  if (step) step.textContent = s.step || '准备中…';
  if (detail) detail.textContent = s.detail || '';
}

/* ---------------------------------------------------- 顶部告警横幅 */

function renderChildBanner(status) {
  const box = $('#child-banner');
  const text = $('#child-banner-text');
  if (!box || !text) return;
  const state = (status && status.child_desktop) || {};
  const down = state.ready === false;
  box.hidden = !down;
  if (down) {
    text.textContent = state.reason
      || '桌面分身没有建立，机器人已停止（避免抢你的鼠标）。';
  }
}

async function retryChildDesktop() {
  const btn = $('#child-banner-retry');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '正在重试…';
  }
  try {
    const r = await api('/api/childsession/rebuild', { method: 'POST', body: {} });
    toast((r && r.detail) || '已重试', r && r.ok ? 'ok' : 'error');
    if (!(r && r.ok)) refreshStatus();
  } catch (err) {
    toast('重试失败', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '重试建立分身';
    }
  }
}

/* -------------------------------------------------------- 识图测试 */

async function testVision() {
  const input = $('#vision-test-path');
  const box = $('#vision-test-result');
  const btn = $('#vision-test-btn');
  if (!input || !box) return;
  const path = (input.value || '').trim();
  if (!path) {
    toast('先填一张图片路径', 'error');
    return;
  }
  setBusy(btn, true, '识别中…');
  box.hidden = true;
  try {
    const data = await api('/api/vision/describe', { method: 'POST', body: { path: path } });
    box.className = 'result ok';
    box.hidden = false;
    box.textContent = (data && data.text) || '（模型没有返回内容）';
  } catch (err) {
    box.className = 'result err';
    box.hidden = false;
    box.textContent = (err && err.message) || '识图失败';
  } finally {
    setBusy(btn, false);
  }
}

/* ------------------------------------------------ 桌面分身登录状态 */

async function loadChildLogin() {
  const list = $('#childlogin-list');
  const badge = $('#childlogin-badge');
  if (!list) return;
  try {
    const d = await api('/api/childsession/login');
    list.innerHTML = '';
    const row = (ok, title, detail) => {
      const li = document.createElement('li');
      const mark = document.createElement('b');
      mark.textContent = ok ? '✅ ' : '⚠️ ';
      li.appendChild(mark);
      li.appendChild(document.createTextNode(title + (detail ? '：' + detail : '')));
      list.appendChild(li);
    };
    row(d.hello_only === false, '允许密码登录', d.hello_only === false ? '正常' : '系统只允许 Windows Hello 登录，分身无法登录');
    row(!!d.has_credential, 'localhost 凭据', d.has_credential ? '已保存' : '还没保存，会每次弹窗');
    const ok = d.ok === true;
    if (badge) {
      badge.textContent = ok ? '已就绪' : '需要修复';
      badge.className = 'badge ' + (ok ? 'ok' : 'warn');
    }
  } catch (err) {
    if (badge) { badge.textContent = '读取失败'; badge.className = 'badge warn'; }
  }
}

function wireChildLogin() {
  const btn = $('#childlogin-fix');
  if (btn && !btn.dataset.ready) {
    btn.dataset.ready = '1';
    btn.addEventListener('click', async () => {
      try {
        const r = await api('/api/childsession/open-fix', { method: 'POST', body: {} });
        toast(r && r.ok ? '已打开修复脚本，按提示输入密码即可' : '打不开修复脚本', r && r.ok ? 'ok' : 'error');
      } catch (err) {
        toast('打不开修复脚本', 'error');
      }
    });
  }
  const save = $('#cred-save');
  if (save && !save.dataset.ready) {
    save.dataset.ready = '1';
    save.addEventListener('click', async () => {
      const user = ($('#cred-user') || {}).value || '';
      const pass = ($('#cred-pass') || {}).value || '';
      if (!user.trim() || !pass) {
        toast('账户和密码都要填', 'error');
        return;
      }
      save.disabled = true;
      save.textContent = '正在保存…';
      try {
        const r = await api('/api/childsession/credential', { method: 'POST', body: { user: user.trim(), password: pass } });
        if (r && r.ok) {
          toast('凭据已保存，重建桌面分身即可不再弹窗', 'ok');
          const f = $('#cred-pass');
          if (f) f.value = '';
          loadChildLogin();
        } else {
          toast('保存失败：' + ((r && r.detail) || '未知错误'), 'error');
        }
      } catch (err) {
        toast('保存失败', 'error');
      } finally {
        save.disabled = false;
        save.textContent = '保存凭据（不再弹窗）';
      }
    });
  }
  const hideBtn = $('#viewer-hide');
  const showBtn = $('#viewer-show');
  const callViewer = async (action) => {
    try {
      const r = await api('/api/childsession/viewer', { method: 'POST', body: { action } });
      toast(r && r.ok ? (action === 'hide' ? '分身窗口已隐藏' : '分身窗口已显示（注意：此时动鼠标会联动你的指针）') : '操作失败', r && r.ok ? 'ok' : 'error');
    } catch (err) {
      toast('操作失败', 'error');
    }
  };
  if (hideBtn && !hideBtn.dataset.ready) {
    hideBtn.dataset.ready = '1';
    hideBtn.addEventListener('click', () => callViewer('hide'));
  }
  if (showBtn && !showBtn.dataset.ready) {
    showBtn.dataset.ready = '1';
    showBtn.addEventListener('click', () => callViewer('show'));
  }

  loadChildLogin();
}

/* ------------------------------------------------------------------ 表情库 */

async function loadStickers() {
  const grid = $('#sticker-grid');
  const meta = $('#sticker-meta');
  if (!grid) return;
  try {
    const data = await api('/api/media/stickers?limit=120');
    const items = (data && data.items) || [];
    grid.innerHTML = '';
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'sticker-empty';
      empty.textContent = '还没有表情包。别人发一张，她就会自动收进来。';
      grid.appendChild(empty);
    } else {
      items.forEach((item) => {
        const img = document.createElement('img');
        img.src = `/api/media/sticker_file?name=${encodeURIComponent(item.name)}`;
        img.alt = item.name;
        img.loading = 'lazy';
        grid.appendChild(img);
      });
    }
    if (meta) {
      meta.textContent = `共 ${data.count || 0} 张 · ${(data && data.dir) || ''}`;
    }
  } catch (err) {
    if (meta) meta.textContent = '读取表情库失败';
  }
}

function wireStickers() {
  const openBtn = $('#sticker-open');
  if (openBtn && !openBtn.dataset.ready) {
    openBtn.dataset.ready = '1';
    openBtn.addEventListener('click', async () => {
      try {
        const res = await api('/api/media/open', { method: 'POST', body: { which: 'stickers' } });
        if (!res || !res.ok) toast('打不开文件夹', 'error');
      } catch (err) {
        toast('打不开文件夹', 'error');
      }
    });
  }
  const sendBtn = $('#sticker-send-test');
  if (sendBtn && !sendBtn.dataset.ready) {
    sendBtn.dataset.ready = '1';
    sendBtn.addEventListener('click', async () => {
      setBusy(sendBtn, true, '发送中…');
      try {
        const items = (await api('/api/media/stickers?limit=1')).items || [];
        if (!items.length) {
          toast('表情库是空的，先收一个表情包', 'error');
          return;
        }
        const r = await api('/api/send_image', {
          method: 'POST',
          body: { chat: '文件传输助手', path: '', name: items[0].name },
        });
        toast(r && r.ok ? '已发到「文件传输助手」' : ('发送失败：' + ((r && r.detail) || '')), r && r.ok ? 'ok' : 'error');
      } catch (err) {
        toast('发送失败', 'error');
      } finally {
        setBusy(sendBtn, false);
      }
    });
  }

  const refreshBtn = $('#sticker-refresh');
  if (refreshBtn && !refreshBtn.dataset.ready) {
    refreshBtn.dataset.ready = '1';
    refreshBtn.addEventListener('click', () => loadStickers());
  }
  loadStickers();
}

/* ------------------------------------------------------------------ 视图切换 */

const VIEW_RENDER = {
  overview: () => { renderOverview(); },
  private: () => { syncModeUi(); },
  group: () => { syncModeUi(); },
  rules: () => { renderRules(); },
  persona: () => { renderPersona(); },
  ai: () => { renderModelDatalist(); updateTempDisplay(); },
  rhythm: () => { renderRhythmDyn(); syncModeUi(); },
  logs: () => {
    if (!state.logs.loaded) loadLogs({ reset: true });
    else renderLogs();
    wireRunLog();
  },
  system: () => {
    renderSystemInfo();
    loadMockSent(true);
    loadEvents(true);
    wireStickers();
    wireChildLogin();
  },
};

function switchView(name) {
  const view = VIEW_META[name] ? name : 'overview';
  state.view = view;

  $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  $$('.view').forEach((section) => { section.hidden = section.id !== 'view-' + view; });

  const meta = VIEW_META[view];
  setText('#page-title', meta[0]);
  setText('#page-sub', meta[1]);

  const render = VIEW_RENDER[view];
  if (render) {
    try { render(); } catch (err) {
      if (window.console && console.error) console.error(err);
      toast('页面渲染出错：' + (err && err.message ? err.message : err), 'error');
    }
  }

  const content = $('#content');
  if (content) content.scrollTop = 0;
}

/* ------------------------------------------------------------------ 渲染全部 */

function renderAll() {
  syncInputs();
  syncModeUi();
  renderTagEditors();
  renderRules();
  renderOverview();
  renderStatusUi();
  renderRhythmDyn();
  renderSystemInfo();
  if (state.view === 'logs') renderLogs();
}

/* ------------------------------------------------------------------ 事件绑定 */

function wireEvents() {
  $$('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  const bannerRetry = $('#child-banner-retry');
  if (bannerRetry && !bannerRetry.dataset.ready) {
    bannerRetry.dataset.ready = '1';
    bannerRetry.addEventListener('click', retryChildDesktop);
  }

  const visionBtn = $('#vision-test-btn');
  if (visionBtn && !visionBtn.dataset.ready) {
    visionBtn.dataset.ready = '1';
    visionBtn.addEventListener('click', testVision);
  }

  document.addEventListener('click', (ev) => {
    const goto = ev.target.closest ? ev.target.closest('[data-goto]') : null;
    if (goto) {
      ev.preventDefault();
      switchView(goto.dataset.goto);
      return;
    }
    const sess = ev.target.closest ? ev.target.closest('[data-sessions]') : null;
    if (sess) {
      ev.preventDefault();
      openSessionPicker(sess.dataset.sessions);
    }
  });

  ['#btn-save-top', '#btn-save-fab'].forEach((sel) => {
    const btn = $(sel);
    if (btn) btn.addEventListener('click', () => { saveAll(); });
  });

  const startBtn = $('#btn-start');
  if (startBtn) startBtn.addEventListener('click', () => { botAction('start'); });
  const stopBtn = $('#btn-stop');
  if (stopBtn) stopBtn.addEventListener('click', () => { botAction('stop'); });
  const qaStart = $('#qa-start');
  if (qaStart) qaStart.addEventListener('click', () => { botAction('start'); });
  const qaStop = $('#qa-stop');
  if (qaStop) qaStop.addEventListener('click', () => { botAction('stop'); });
  const qaRefresh = $('#qa-refresh');
  if (qaRefresh) qaRefresh.addEventListener('click', () => { pollStatus(true); });
  const takeoverRefresh = $('#takeover-refresh');
  if (takeoverRefresh) takeoverRefresh.addEventListener('click', () => { pollStatus(true); });

  const toggleKey = $('#toggle-key');
  if (toggleKey) toggleKey.addEventListener('click', toggleKeyVisibility);
  const aiTest = $('#ai-test-btn');
  if (aiTest) aiTest.addEventListener('click', () => { testAI(); });
  const previewBtn = $('#preview-btn');
  if (previewBtn) previewBtn.addEventListener('click', () => { runPreview(); });

  const sendBtn = $('#send-btn');
  if (sendBtn) sendBtn.addEventListener('click', () => { systemSend(); });
  const mockBtn = $('#mock-inject-btn');
  if (mockBtn) mockBtn.addEventListener('click', () => { mockInject(); });
  const mockRefresh = $('#mock-refresh');
  if (mockRefresh) mockRefresh.addEventListener('click', () => { loadMockSent(false); });
  const eventsRefresh = $('#events-refresh');
  if (eventsRefresh) eventsRefresh.addEventListener('click', () => { loadEvents(false); });
  const sessionBtn = $('#sessions-refresh-system');
  if (sessionBtn) sessionBtn.addEventListener('click', () => { refreshSessionsForSystem(); });

  const logSearch = $('#log-search');
  if (logSearch) logSearch.addEventListener('click', applyLogFilter);
  const logFilter = $('#log-filter');
  if (logFilter) {
    logFilter.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); applyLogFilter(); }
    });
  }
  const logRefresh = $('#log-refresh');
  if (logRefresh) logRefresh.addEventListener('click', () => { loadLogs({ reset: true }); });
  const logClear = $('#log-clear');
  if (logClear) logClear.addEventListener('click', () => { clearLogs(); });
  const logMore = $('#log-more');
  if (logMore) logMore.addEventListener('click', () => { loadLogs(); });

  const ruleNew = $('#rule-new');
  if (ruleNew) ruleNew.addEventListener('click', () => openRuleEditor(-1));
  const ruleSave = $('#rule-save');
  if (ruleSave) ruleSave.addEventListener('click', saveRuleDraft);
  const ruleCancel = $('#rule-cancel');
  if (ruleCancel) ruleCancel.addEventListener('click', closeRuleEditor);
  const ruleCancelX = $('#rule-cancel-x');
  if (ruleCancelX) ruleCancelX.addEventListener('click', closeRuleEditor);
  const ruleSessions = $('#rule-sessions');
  if (ruleSessions) {
    ruleSessions.addEventListener('click', () => {
      if (!state.rule.open) { toast('先点「新增规则」或某条规则的「编辑」', 'warn'); return; }
      openSessionPicker('rule');
    });
  }

  const ruleName = $('#rule-name');
  if (ruleName) {
    ruleName.addEventListener('input', () => {
      if (state.rule.draft) state.rule.draft.name = ruleName.value;
    });
  }
  const ruleEnabled = $('#rule-enabled');
  if (ruleEnabled) {
    ruleEnabled.addEventListener('change', () => {
      if (state.rule.draft) state.rule.draft.enabled = ruleEnabled.checked;
      updateRuleEditorUi();
    });
  }
  const ruleMatch = $('#rule-match');
  if (ruleMatch) {
    ruleMatch.addEventListener('change', () => {
      if (state.rule.draft) state.rule.draft.match = ruleMatch.value;
      updateRuleEditorUi();
    });
  }
  const ruleAction = $('#rule-action');
  if (ruleAction) {
    ruleAction.addEventListener('change', () => {
      if (state.rule.draft) state.rule.draft.action = ruleAction.value;
      updateRuleEditorUi();
    });
  }
  const ruleScope = $('#rule-scope');
  if (ruleScope) {
    ruleScope.addEventListener('change', (ev) => {
      const input = ev.target.closest('input[type="radio"]');
      if (input && state.rule.draft) state.rule.draft.scope = input.value;
    });
  }
  const rulePriority = $('#rule-priority');
  if (rulePriority) {
    rulePriority.addEventListener('input', () => {
      if (state.rule.draft) state.rule.draft.priority = num(rulePriority.value, 100);
    });
  }
  const ruleCooldown = $('#rule-cooldown');
  if (ruleCooldown) {
    ruleCooldown.addEventListener('input', () => {
      if (state.rule.draft) state.rule.draft.cooldown_seconds = num(ruleCooldown.value, 0);
    });
  }
  const ruleTemplate = $('#rule-template');
  if (ruleTemplate) {
    ruleTemplate.addEventListener('input', () => {
      if (state.rule.draft) state.rule.draft.template = ruleTemplate.value;
    });
  }
  const rulePrompt = $('#rule-prompt');
  if (rulePrompt) {
    rulePrompt.addEventListener('input', () => {
      if (state.rule.draft) state.rule.draft.prompt = rulePrompt.value;
    });
  }

  const sessionSearch = $('#session-search');
  if (sessionSearch) {
    sessionSearch.addEventListener('input', () => {
      state.picker.filter = sessionSearch.value || '';
      renderSessionList();
    });
  }
  const sessionsReload = $('#sessions-reload');
  if (sessionsReload) sessionsReload.addEventListener('click', () => { loadSessions(); });
  const sessionsConfirm = $('#sessions-confirm');
  if (sessionsConfirm) sessionsConfirm.addEventListener('click', confirmSessionPick);

  $$('[data-close-modal]').forEach((btn) => btn.addEventListener('click', closeSessionPicker));

  const sessionsMask = $('#modal-sessions');
  if (sessionsMask) {
    sessionsMask.addEventListener('click', (ev) => {
      if (ev.target === sessionsMask) closeSessionPicker();
    });
  }

  const confirmMask = $('#modal-confirm');
  if (confirmMask) {
    confirmMask.addEventListener('click', (ev) => {
      if (ev.target === confirmMask) settleConfirm(false);
    });
  }
  const confirmOk = $('#confirm-ok');
  if (confirmOk) confirmOk.addEventListener('click', () => { settleConfirm(true); });
  const confirmCancel = $('#confirm-cancel');
  if (confirmCancel) confirmCancel.addEventListener('click', () => { settleConfirm(false); });

  const fatalRetry = $('#fatal-retry');
  if (fatalRetry) fatalRetry.addEventListener('click', () => { boot(); });
  const fatalPreview = $('#fatal-preview');
  if (fatalPreview) fatalPreview.addEventListener('click', showOfflinePreview);
  const offlineRetry = $('#offline-retry');
  if (offlineRetry) offlineRetry.addEventListener('click', () => { boot(); });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      const sessionsMaskEl = $('#modal-sessions');
      if (sessionsMaskEl && !sessionsMaskEl.hidden) { closeSessionPicker(); return; }
      const confirmMaskEl = $('#modal-confirm');
      if (confirmMaskEl && !confirmMaskEl.hidden) { settleConfirm(false); return; }
    }
    if ((ev.ctrlKey || ev.metaKey) && String(ev.key).toLowerCase() === 's') {
      ev.preventDefault();
      saveAll();
    }
  });

  window.addEventListener('beforeunload', (ev) => {
    if (!state.dirty) return;
    ev.preventDefault();
    ev.returnValue = '';
    return '';
  });
}

/* ------------------------------------------------------------------ 启动 */

function showFatal(message) {
  const box = $('#fatal');
  if (!box) return;
  setText('#fatal-message', message || '请确认 落小鱼 主程序已经启动，然后重试。');
  box.hidden = false;
}

/** 后端没连上时，允许先看界面（只是预览，不能保存）。 */
function showOfflinePreview() {
  const box = $('#fatal');
  if (box) box.hidden = true;
  const bar = $('#offline-bar');
  if (bar) bar.hidden = false;
  mountConfigTags();
  switchView('overview');
  toast('只是界面预览：后端没连上，改动不会保存', 'warn');
}

async function boot() {
  const fatal = $('#fatal');
  if (fatal) fatal.hidden = true;
  const offline = $('#offline-bar');
  if (offline) offline.hidden = true;

  try {
    const data = await api('/api/bootstrap');
    state.version = data && data.version ? String(data.version) : '1.0.0';
    state.presets = (data && data.presets) || {};
    state.config = normalizeConfig(data && data.config);
    state.status = (data && data.status) || {};
    state.pollFailed = false;

    buildProviderSelect();
    if (!state.bound) {
      bindInputs();
      state.bound = true;
    }
    mountConfigTags();

    state.booted = true;
    setDirty(false);
    syncInputs();
    renderAll();
    switchView(state.view);
    startPolling();

    toast('控制台已就绪');
  } catch (err) {
    state.booted = false;
    showFatal(err && err.message ? err.message : '未知错误');
  }
}

function init() {
  wireEvents();
  boot();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
