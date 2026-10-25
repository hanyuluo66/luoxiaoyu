# 落小鱼 · 项目备忘

> 落 = 主人，小鱼 = 我。这是我们第一个一起做出来的东西（这句是内部故事，不进产品文案）。

## 对外文案（软件要给别人用，不能带个人叙事）

- 标题：落小鱼
- 副标：住在你电脑里的微信分身
- 说明：在独立桌面里替你回消息 · 你的鼠标键盘不受影响

## 交付方式（2026-10-25 主人定的）

**以后不再打 zip 包**，改动直接写进 `E:\微信大肥鱼\release\` 文件夹。
朋友要的话，直接把整个 `release` 目录（去掉 `data*`）拷给他就行。

> `落小鱼-v1.0.0.zip` 是最后打的一个包，留着当存档，不再更新。

## 目录约定

```
E:\微信大肥鱼\
├─ release\                  ← 交付物：双击这里面的 exe
│  ├─ 落小鱼.exe             主程序（47.2 MB，含图标）
│  ├─ 落小鱼引擎.exe          桌面分身引擎（C#，13 KB）
│  ├─ data\                  控制器/本机模式数据（配置、SQLite、日志）
│  ├─ data-sessionN\         分身实例数据（每个会话一套）
│  ├─ wechatauto_logs\       微信自动化库日志
│  └─ childsession-keeper.log 分身引擎日志
├─ assets\                   图标与横幅原图（luoxiaoyu-source.png / icon.ico / banner.png）
├─ backend\                  Python 后端（adapters / api / runtime / pipeline / models）
├─ web\                      控制台（index.html / app.js / style.css / logo.png / favicon.png）
├─ tools\                    构建、诊断、探针、改名脚本
└─ docs\                     人设提示词、微信版本说明等
```

## 核心机制（记住这三点）

1. **桌面分身 = Windows RDP 子会话**，用 `IMsRdpExtendedSettings.ConnectToChildSession=true` 建立，
   不需要 3389 监听器（家庭版可用）。实现在 `tools/ChildSessionKeeper.cs`。
2. **机器人在子会话里跑**（`data-sessionN`），驱动同会话里的微信，所以**不抢主桌面的鼠标键盘**。
   把进程投进会话走任务计划 `RunEx(TASK_RUN_USE_SESSION_ID)`，不需要管理员。
3. **收消息读微信本地数据库，发消息走 UIA**（`wechatauto` 适配器）。
   不注入、不改客户端 → **任意微信 4.x 版本通用**。Hook 方案已废弃（版本死锁 + 会被自动升级打崩）。

## 容易踩的坑

- **「仅允许 Windows Hello 登录」必须关掉**（`DevicePasswordLessBuildVersion=2` 会导致子会话登录失败），
  且 Windows 账户必须有密码。
- 微信必须**在分身窗口里**登录；勾上「自动登录」，否则重启要重新扫码。
- BetterGI 的桌面分身会占着 RDP 子会话，两者不能同时用。
- **画面开着时**，RDP 会把分身后的光标映射到本机鼠标上；`bot.auto_hide_viewer=true` 已在发消息时自动隐藏。
- 分身实例的日志要单独看：`data-sessionN\logs\app.log`（早期版本会混写进 `data\logs`，已修）。

## 常用命令（用 `C:\Users\23798\dfy-venv\Scripts\python.exe`）

```powershell
# 自测（32 项）
python tools\selftest.py

# 构建（输出 release\落小鱼.exe）
$env:TEMP='C:\Users\23798\AppData\Local\Temp'; python build.py

# 诊断桌面分身（生成桌面报告）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\..\诊断桌面分身.ps1
```

## 控制台

- 地址：`http://127.0.0.1:8760`（跨会话可访问）
- 页面：概览（含欢迎页 hero）/ 私聊设置 / 群聊设置 / 监听规则 / 人设设置 / AI 接口 / 收发节奏 / 消息日志 / 系统设置
- 默认人设：`backend/models.py` 的 `DEFAULT_PERSONA`（鲸鱼姑娘「小鱼」，来自深海的蓝色大肥鱼）

## 待办 / 已知问题

- [ ] 朋友机器上报过「桌面分身没能建立」，已加诊断脚本与四条排查建议，等他反馈
- [ ] `落小鱼引擎.exe` 卡在 RDP=2 时会自动弹窗（等待 Windows 登录），体验可再优化
- [ ] 群聊 @ 检测目前靠文本匹配，可考虑用数据库字段

## 识图与表情包（2026-10-25 下午加的）

- **识图**：`ai.vision_model` 填一个支持图片的模型名，走同一套 base_url / api_key。
  收图 → `MediaDownloader.download_media()` 下载 → ffmpeg 把 wxgf(HEVC) 转 jpg →
  `AIClient.describe_image()` 拿到描述 → 拼进 prompt（"对方发的图我看过：…"）。
  留空则只存图不识别；`bot.image_reply` 控制"看不到图也回一句"。
- **表情包**：收到 `动画表情`/图片 → 存进 `data\media\stickers`（内容哈希去重）→
  对方发表情包时随机挑一张，用「剪贴板塞图 + Ctrl+V + Enter」发回去。
- **ffmpeg**：`pip install imageio-ffmpeg`，自带静态 ffmpeg（83 MB），PyInstaller 会打进 exe
  （体积从 47 MB 涨到 77 MB）。没有它：图片只能存 .wxgf 原始容器。
- **发送实现**：优先用库的 `WeChatGUI.send_image(path, who)`；失败回退到自己的
  剪贴板路线（`media.copy_to_clipboard` + UIA Ctrl+V）。
- **控制台**：AI 接口页加「识图模型」；系统设置加三个开关 + 表情库网格（缩略图）。
- **踩坑**：全局 `subprocess.Popen` 守卫必须用**子类**实现（`class GuardedPopen(Popen)`），
  换成普通函数会让 `asyncio.windows_utils` 的 `class Popen(subprocess.Popen)` 在 import 时崩。
- **已知环境问题**：反复创建/注销子会话后，RDP ActiveX 会永久卡在 `RDP=2`，
  `Connect()` 抛 `TargetInvocationException`，`ensure_keeper` 已加"杀死引擎重启（2 次）"，
  但仍建不起来时需要**重启电脑**清掉会话状态。

## 分身干净启动（2026-10-25 下午）

**为什么不能换账户**：本机是 Windows 11 **家庭版**，没有 RDP 服务端（3389 不监听）。
桌面分身靠 `IMsRdpExtendedSettings.ConnectToChildSession`，它**只连当前用户的子会话**，
换账户就建不出会话（除非上 RDPWrap 破解，不做）。

**实际做法**：临时挂起启动项（backend/startup_items.py）
- 建分身之前：`HKCU\...\Run` 的值搬到 `HKCU\Software\LuoXiaoyu\SuspendedRun`，
  「启动」文件夹里的文件挪到 `data/startup-suspended/`
- 分身登录完成（或失败）后立刻恢复；`main.py` 启动时若发现残留会自动还原（崩溃自愈）
- 主桌面已经登录过，不会重读 Run → **完全不受影响**
- 实测：挂起 6 项 → Run 归零 → 恢复 6 项且内容一致；建分身失败时也会还原
- 兜底：`bot.child_session_slim` 仍会在分身里清掉漏网程序（实测 40 → 31）

**凭据问题**（`你的凭据不工作`）：
- 根因：`DevicePasswordLessBuildVersion = 2`（只允许 MSA 的 Windows Hello 登录）
  → 子会话无法用密码登录。已改为 0（需重启/注销生效）
- 控制台「系统设置 → 桌面分身登录」可保存 `TERMSRV/localhost` 凭据（cmdkey），
  以后分身静默登录不弹窗
- 一键脚本：`release\修复桌面分身登录.bat`（管理员）


## 表情包 / 识图链路（2026-10-25 晚）

**识图**：`deepseek-chat` 本身支持图片（实测能准确描述游戏截图）。
`ai.vision_model` 填 `deepseek-chat` 即可；控制台「AI 接口」页有「识图测试」按钮，
后端 `POST /api/vision/describe {path}`。

**收图下载的正确姿势**：监听器给的 `local_id` 是「消息行」id，拿它调
`download_media` 会查到文字消息（`local_type=1`）→ 永远失败。
必须用 `db.get_image_rows(chat)` 里的 `local_id`，并按 `sort_seq` 对齐。

**表情包**：
- 收到 `动画表情` 或 `.gif/.webp` → 存进 `data-*/media/stickers`（内容哈希去重）
- 对方发表情包 → 自动回一张
- 模型主动要：提示词里声明能力，回复中写 `[[表情]]` → 流水线剥离标记并补发一张
  （只回 `[[表情]]` 时只发表情，不发空文本）
- 发送：GIF 走 CF_HDROP（保动画），静态图走剪贴板 SetImage

**分身里的微信必须点一次「进入微信」**：
登录后微信停在确认页（头像 + 绿色按钮），UIA 读不到聊天控件 → 所有发送超时。
已加 `_dismiss_enter_confirm()`：启动时和每次发送前检查，自动点掉。
窗口被裁到桌面外也会导致聊天区空白，必要时用 SetWindowPos 摆正。

**登录提示**：`ConnectToChildSession` 对 Microsoft 账户会弹登录框且拒绝密码；
把账户拆成「域 + 用户名」并让 RDP 走 SSO 后仍不稳定。当前可行路径是
凭据管理器存对密码 + 用户手动输一次，或用「更多选项 → HANLING110129\23798」。


## 表情包 / 识图 最终修复（2026-10-25 夜）

**模型一直说「发不了表情包」的真实原因**（三层叠加）：
1. `persona` 里写着「你只会读和回微信文字消息：发不了图片、文件、语音」→ 人设权重压过系统提示；
2. 「遇到超出的请求，直接说办不到」→ 模型把发表情包当成超范围请求；
3. 历史消息里模型自己说过「发不了表情包」→ 它在照抄自己。

修法：
- 默认人设改成「能发文字/看图/发表情包」，并给出 `[[表情]]` 写法；
- `_ensure_persona_capability()` 幂等迁移所有已存配置（补能力块 + 替换那句模糊的"直接说办不到"）；
- `build_messages` 过滤历史里带「发不了表情包/只能打字」的助手消息；
- 流水线兜底：用户要表情包而模型拒绝 → 丢掉拒绝文字，直接发表情包。

**识图**：`deepseek-chat` 支持图片；`ai.vision_model` 填它即可。
`POST /api/vision/describe {path}` 可单独测；`POST /api/debug/inject {content,msg_type,image_path}`
可以把一条消息（含图片）直接灌进流水线验证全链路。

**切换会话**：微信 4.x 会话列表是自绘的，UIA 读不到（只有导航按钮）。
可行做法：`_open_chat_by_click()` —— 搜索框写关键字（真实按键）+ 逐行试点列表直到
`current_chat()` 等于目标。搜索框用 ValuePattern 写值微信不认，必须 SendInput。

**冷却**：`per_chat_cooldown` 默认 8 秒会造成"发几条就不回"，已降到 1.5 秒。

**实测（注入验证）**：
- 文字回复（全国聊天群/隐德莱希/施阳）→ `ok 已发送（UIA 路径）`
- 要表情包 → `ok 表情包：96c9efd752e15844.jpg（库路径）`
- 发图片 → 模型读出「是在逗AI玩呢，发招呼就收一大段回复」→ 证明识图链路通
