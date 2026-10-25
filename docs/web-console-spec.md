# 落小鱼 网页控制台 前端开发说明书

## 交付物（只允许写这三个文件）

- `web/index.html`
- `web/style.css`
- `web/app.js`

**禁止**修改工作区里除上面三个文件以外的任何文件（尤其不要动 `backend/`、`main.py`、`tools/`）。

## 硬性技术约束

1. 纯静态：原生 HTML + CSS + ES2020 JavaScript。**不允许任何构建步骤、npm、打包器**。
2. **绝对不允许引用外网资源**（无 CDN 字体/图标/框架）。程序在离线环境运行，任何 `https://` 的 `<script>`/`<link>` 都会导致控制台白屏。图标一律用内联 SVG 或 Unicode 字符。
3. 单个页面（SPA），用 JS 切换视图，不要多文件路由。
4. 所有请求走同源相对路径 `/api/...`（后端已经在 127.0.0.1 上托管这些静态文件）。
5. 界面语言：**简体中文**。语气自然，别用翻译腔。
6. 不能有控制台报错；所有 `fetch` 都要 try/catch 并有失败提示。
7. 桌面优先（最小宽度 1100px 舒适），窄屏能看即可，不做复杂响应式。

## 设计风格要求

- 深色系现代后台。建议背景 `#0f1420`，卡片 `#171e2e`，描边 `#253047`，主色暖黄 `#ffc457`（小鱼主题色），成功 `#3ddc97`，警告 `#ffb020`，错误 `#ff5d73`。
- 左侧固定侧边栏（约 220px）+ 右侧内容区。侧边栏顶部是产品名「落小鱼」和一个状态圆点。
- 顶部栏：当前页面标题 + 右上角「机器人运行中/已停止」胶囊 + 启动/停止按钮 + 保存按钮。
- 卡片化布局，圆角 12px，轻阴影。表单控件要好看（输入框/下拉/开关/单选/标签列表）。
- 表单**不要**每改一下就自动保存：统一用右下角固定「保存并生效」按钮 + 顶部也有一个。
- 轻量 toast 提示（右下角，成功/失败，2.5s 自动消失）。

## 后端接口契约（已实现，不要改）

所有响应 JSON。错误响应为 `{ "detail": "错误信息" }`，HTTP 4xx/5xx。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bootstrap` | 一次拿全：`{version, config, status, presets}` |
| GET | `/api/status` | 运行状态（每 3 秒轮询） |
| PUT | `/api/config` | 提交**完整配置对象**，返回 `{ok:true, config}` |
| POST | `/api/bot/start` \| `/api/bot/stop` \| `/api/bot/restart` | 启停，返回 `{ok, status}` |
| POST | `/api/ai/test` | body 可含 `{base_url, api_key, model}`（覆盖当前值测试），成功返回 `{ok, latency_ms, model, reply}` |
| POST | `/api/ai/preview` | body `{text, chat, chat_type, sender}`，返回 `{reply:bool, reason, source, rule, reply_text, error?}`，**不会真的发消息** |
| GET | `/api/sessions` | 从微信拉当前会话列表，返回 `{ok, items:[{name,last,time,unread,type}]}` |
| POST | `/api/send` | body `{chat, text}`，返回 `{ok, detail}` |
| POST | `/api/chats/resume` | body `{chat}`，解除该会话的人工接管 |
| GET | `/api/messages?limit=50&offset=0&chat=` | 消息日志 `{items:[...]}`，item 字段：`id,ts,chat,chat_type,sender,direction('in'/'out'),msg_type,content,rule,status,note` |
| DELETE | `/api/messages` | 清空日志 |
| GET | `/api/events?limit=100` | 系统事件 `{items:[{ts,level,kind,message}]}` |
| POST | `/api/mock/inject` | body `{chat, sender, content, chat_type}`，仅模拟模式下可用，注入一条假消息用于测试 |
| GET | `/api/mock/sent` | 模拟模式下已"发送"的消息 `{items:[{ts,chat,text}]}` |

### GET /api/status 返回结构

```jsonc
{
  "running": true,
  "uptime": 123,
  "adapter": {                       // 微信连接状态
    "adapter": "wxauto",             // 或 "mock"
    "connected": true,
    "detail": "已连接微信：小鱼",
    "my_names": ["小鱼"],
    "listening": ["张三", "测试群"],
    "wechat_version": "3.9.11.17",
    "last_poll_ts": 1730000000.0,
    "last_error": ""
  },
  "pipeline": { "queued": 0, "busy": false, "takeover": {"张三": 420} },  // 人工接管剩余秒数
  "listen": ["张三", "测试群"],       // 实际监听中的会话
  "stats": { "total": 10, "inbound": 6, "replied": 4, "last24h": 10, "chats24h": 2 },
  "ai_configured": true,
  "adapter_kind": "wxauto",
  "data_dir": "E:\\微信小鱼\\data",
  "notifications": [{"ts":1.0,"kind":"reply","level":"info","message":"已回复「张三」：你好呀"}]
}
```

### 配置对象结构（`config`，PUT 时必须整体回传）

```jsonc
{
  "version": 1,
  "adapter": "wxauto",              // "wxauto" 真实微信 / "mock" 模拟模式
  "ai": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 1.1,
    "max_tokens": 800,
    "timeout": 90,
    "persona": "你是「落小鱼」……",   // 人设，多行文本
    "extra_prompt": "",
    "history_rounds": 6,
    "reply_max_chars": 600
  },
  "bot": {
    "enabled": true,
    "private_enabled": true,
    "group_enabled": true,
    "reply_delay_min": 1.5,
    "reply_delay_max": 4.0,
    "per_chat_cooldown": 8.0,
    "global_per_minute": 20,
    "quiet_hours_enabled": false,
    "quiet_start": "23:30",
    "quiet_end": "07:30",
    "takeover_minutes": 10,
    "poll_interval": 1.5,
    "max_queue": 50,
    "keep_history": true
  },
  "private": {
    "mode": "include",              // "all" 全部 / "include" 只回名单内 / "exclude" 名单外都回
    "chats": ["张三"],
    "require_keyword": false,
    "keywords": []
  },
  "group": {
    "mode": "include",
    "chats": ["测试群"],
    "trigger": "at",                // "at" 仅@我 / "keyword" 关键词 / "all" 全部消息
    "keywords": [],
    "reply_to_at_all": true
  },
  "blacklist": { "chats": [], "senders": [], "ignore_keywords": [] },
  "rules": [                        // 监听规则
    {
      "id": "abc123456789",
      "name": "打招呼",
      "enabled": true,
      "scope": "all",               // "all" / "private" / "group"
      "chats": [],                  // 空=所有会话；支持 * 通配
      "match": "contains",          // "contains" / "exact" / "startswith" / "regex" / "always"
      "patterns": ["你好", "在吗"],
      "action": "ai",               // "ai" 走大模型 / "template" 固定话术 / "ignore" 不回复
      "template": "",               // 支持占位符 {msg} {sender} {chat} {time} {nickname}
      "prompt": "",                 // 命中后追加给模型的提示词
      "priority": 100,              // 数字小的先匹配
      "cooldown_seconds": 0.0
    }
  ]
}
```

`presets` 结构（`GET /api/bootstrap` 返回）：

```jsonc
{
  "deepseek": {"label":"DeepSeek","base_url":"https://api.deepseek.com","model":"deepseek-chat","models":"deepseek-chat,deepseek-reasoner"},
  "openai": {...}, "siliconflow": {...}, "moonshot": {...}, "dashscope": {...}, "custom": {...}
}
```

## 页面清单（侧边栏顺序）

### 1. 概览
- 4 个统计卡：今日消息（`stats.last24h`）、已回复（`stats.replied`）、活跃会话（`stats.chats24h`）、运行时长（`uptime` 格式化为「1小时23分」）。
- 「连接状态」卡片：微信连接是否正常（`adapter.connected` + `detail`）、微信版本、我的昵称、监听会话数量；异常时把 `last_error` 用红色显示。
- 「AI 接口」卡片：是否已配置 key（`ai_configured`）、当前模型、provider。
- 「实时动态」列表：`notifications`，按时间倒序，不同 `kind` 用不同颜色小圆点（reply 绿、error 红、skip 灰、start/config 黄、takeover 蓝）。
- 「快捷操作」：启动/停止、打开消息日志、发送测试消息（跳转系统设置页）。

### 2. 私聊设置
- 总开关 `bot.private_enabled`。
- 回复范围三选一单选：全部私聊 / 仅名单内 / 名单外全回（对应 `private.mode` = all/include/exclude），选中时下方名单区域显示或隐藏。
- 会话名单 `private.chats`：标签式编辑器（输入框回车添加，标签带 × 删除）。
- 「从微信拉取会话」按钮 → `GET /api/sessions`，弹出可多选的会话列表供加入名单。
- 「必须包含关键词才回复」开关 + 关键词标签编辑（`private.require_keyword` / `private.keywords`）。

### 3. 群聊设置
- 总开关 `bot.group_enabled`。
- 回复范围同私聊（`group.mode`）+ `group.chats` 名单编辑器（同样支持从微信拉取）。
- 触发方式下拉：仅@我（at）/ 命中关键词（keyword）/ 所有群消息（all），对应 `group.trigger`。
- 关键词标签编辑（`group.keywords`），trigger=keyword 时高亮。
- 「@所有人 也回复」开关（`group.reply_to_at_all`）。
- 一段灰色说明文字：群聊建议用「仅@我」，否则容易刷屏被踢。

### 4. 监听规则
- 说明文字：规则优先于上面的默认设置，按优先级从小到大匹配，命中即停。
- 规则列表卡片式，每张显示：名称、启用开关、范围徽标、匹配方式、关键词、动作、优先级数字；右侧有「编辑」「删除」。
- 「新增规则」按钮 → 弹出/展开编辑面板：名称、启用、范围(全部/私聊/群聊)、指定会话（标签编辑器，留空=所有）、匹配方式下拉、关键词标签编辑器（match=always 时隐藏）、动作下拉（AI 回复/固定话术/不回复）、话术文本框（动作=固定话术时显示，并提示可用占位符）、追加提示词（动作=AI 时显示）、优先级数字、冷却秒数。
- 删除需二次确认。

### 5. AI 接口
- Provider 下拉（来自 `presets`），选择后自动填充 `base_url` 与 `model`（custom 时留空让用户填）。
- Base URL、API Key（`type=password`，带眼睛按钮切换明文）、模型名（可手填，同时给 preset 的 models 做 datalist 建议）。
- 温度（滑块 0~2，显示数值）、最大回复长度 `max_tokens`、超时秒数、上下文轮数 `history_rounds`、回复字数上限 `reply_max_chars`。
- 「人设 Prompt」大文本框（`ai.persona`，至少 8 行高）。
- 「追加要求」文本框（`ai.extra_prompt`）。
- 「测试连接」按钮 → `POST /api/ai/test`（带上当前表单里**未保存**的 base_url/api_key/model），显示延迟与模型返回内容。失败用红色显示 detail。
- 「试跑几句」区域：输入一条消息 + 选择私聊/群聊 + 会话名 → `POST /api/ai/preview`，展示：是否回复、原因、命中规则、将要回复的文本。绝不能真的发消息。

### 6. 收发节奏
- `bot.enabled` 总开关。
- 回复随机延迟区间（两个数字输入：最小秒、最大秒）。
- 同一会话冷却秒数、全局每分钟上限。
- 免打扰时段：开关 + 开始/结束时间（`type=time`）。
- 人工接管：`takeover_minutes`（说明：你手动回消息后，该会话暂停多少分钟）。
- 轮询间隔 `poll_interval`、队列上限 `max_queue`。
- 永久忽略：会话名单、发送人名单、忽略关键词（三个标签编辑器）。
- 若 `status.pipeline.takeover` 非空，展示「正在人工接管」列表，每条带「恢复」按钮（调 `/api/chats/resume`）。

### 7. 消息日志
- 表格：时间、方向（收到/发出，带颜色）、会话、发送人、内容（过长截断，点击展开）、状态（ok/failed/skipped，色标）、备注。
- 顶部：会话筛选输入框、刷新、清空（二次确认）。
- 支持「加载更多」（offset 分页，每页 50）。
- 空状态友好提示。

### 8. 系统设置
- 接入模式单选：真实微信（wxauto）/ 模拟模式（mock）。说明：模拟模式不连微信，用于试用界面和调模型。
- 数据目录展示（`status.data_dir`）。
- 「发送测试消息」：会话名 + 内容 → `POST /api/send`，显示结果。
- 模拟模式下显示「注入测试消息」表单（会话名/发送人/内容/私聊或群聊）→ `POST /api/mock/inject`，并展示 `/api/mock/sent` 最近的"已发送"记录。
- 「刷新微信会话列表」按钮。
- 关于：版本号、技术说明（前端 wxauto 操作微信 3.9.11.17，仅本机 127.0.0.1 访问）。

## 交互与状态管理

- 启动时 `GET /api/bootstrap` → 保存 `state.config`、`state.status`、`state.presets`，渲染当前页。
- 表单改动直接改 `state.config`（内存），标记 `dirty`，顶部/底部保存按钮高亮。
- 保存：`PUT /api/config` 整体提交，成功后 `state.dirty=false`，toast「已保存并生效」。
- 每 3 秒 `GET /api/status` 刷新状态胶囊、概览统计、动态列表；**不要**用轮询结果覆盖用户正在编辑的表单。
- 数字输入要 `Number()` 转换并给合理默认，避免把字符串提交上去导致后端校验失败。
- 提交前做基本前端校验：`base_url` 非空、`reply_delay_max >= reply_delay_min`、`global_per_minute >= 1`；不通过则 toast 提示并中止。

## 验收标准

1. 三个文件齐全，`web/index.html` 直接打开或经后端访问都能渲染出完整界面，无白屏、无 JS 报错。
2. 八个页面都能切换，所有字段都能编辑并成功 `PUT /api/config`（用后端校验通过为准确认）。
3. 概览页轮询后统计与动态会更新。
4. AI 接口页的「测试连接」与「试跑几句」能正确展示成功/失败。
5. 监听规则的增删改查全部可用。
6. 无任何外网请求。
