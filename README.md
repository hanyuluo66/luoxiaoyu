![落小鱼](assets/banner.png)

# 落小鱼 · 微信 AI 自动回复机器人

> 住在你电脑里的微信分身：在**独立桌面**里替你回消息，你的鼠标、窗口、输入法都不受影响，微信版本随便升级。

一个 Windows 单文件程序：把你电脑上登录的微信接给大模型（默认 DeepSeek），
私聊 / 群聊自动回复，规则、人设、节奏全部在本地网页控制台里配置。

**不注入微信进程、不改微信文件、不需要 OCR、不需要降级微信版本。**

---

## 特性

| 能力 | 说明 |
|---|---|
| **桌面分身隔离** | 微信和机器人跑在独立的 Windows 子会话（RDP child session）里，有自己的输入队列。你在主桌面打字、玩游戏、拖窗口，都不受干扰 |
| **收发消息** | 收：解密读取微信本地数据库；发：UIA 控件写入 + 回车。全程零注入 |
| **群聊三种触发** | 仅 @我 / 命中关键词 / 全部消息 |
| **规则与人设** | 白名单黑名单、关键词固定话术、AI 人设（自称/口吻/口头禅/底线）、免打扰时段、频率限制、人工接管、会话冷却 |
| **识图** | 对方发来的图片转成标准 jpg 交给视觉模型（`deepseek-chat` 已支持读图），再结合图里的内容回复 |
| **表情包** | 自动收藏别人发的表情包；想发时模型写 `[[表情]]`，程序挑一张真发出去（GIF 走 CF_HDROP 保留动画） |
| **独立控制台** | 不占用浏览器标签页：Edge `--app` 模式开一个原生窗口，9 个页面配置全部内容 |
| **安全** | 只监听 `127.0.0.1`；Host/Origin 校验挡 DNS rebinding 与 CSRF；API Key 不回传前端；凭据走 Windows 凭据管理器 |

---

## 技术原理

### 1. 为什么需要「桌面分身」

微信界面是自绘的，UI 自动化必须操作真实窗口，而操作窗口 = 抢焦点、抢鼠标。
本项目把微信和机器人放进 **RDP 子会话**（Windows 自带的 Child Session），
它有自己的桌面和输入队列：

```
主会话（你在用）                     子会话（机器人在用）
├─ 你的浏览器 / 游戏                 ├─ 微信（登录态）
├─ 你的鼠标键盘                      ├─ 落小鱼（机器人实例）
└─ 控制台托盘                        └─ 独立输入队列，互不干扰
```

关键点：**子会话窗口必须保持隐藏**。Windows 的 RDP 客户端会把远端光标位置
映射到本机鼠标指针上，所以程序在做任何鼠标/键盘动作前都会先藏起分身窗口
（`hide_viewer()`）。

### 2. 怎么收发消息

- **收**：微信 4.x 的消息存在本地加密数据库里。程序用 `wechatauto-replica`
  从进程内存取密钥解密读取，不点界面、不轮询窗口。
- **发**：把目标会话切到前台 → UIA 往输入框写文字 → 回车。
  发图片/表情包是把文件塞进剪贴板再 `Ctrl+V`。
- **切会话**：微信 4.x 的会话列表是自绘的，UIA 读不到列表项，
  所以用「搜索框真实按键输入 + 逐行试点列表」定位目标。

### 3. 图片与表情包

微信 4.x 的图片在库里是 `wxgf`（HEVC）容器，需要 `ffmpeg` 转码才能给模型看。
项目用 `imageio-ffmpeg` 自带的静态 ffmpeg（打包进 exe），用户不用另外装。

---

## 目录结构

```
落小鱼/
├── main.py                     # 入口：编排、托盘、控制台窗口、子会话
├── build.py                    # PyInstaller 打包脚本
├── requirements.txt
├── backend/
│   ├── api.py                  # FastAPI 路由（控制台后端）
│   ├── runtime.py              # 运行时：适配器生命周期、配置、通知
│   ├── pipeline.py             # 流水线：决策 → AI → 发送 → 表情包
│   ├── ai.py                   # OpenAI 兼容客户端（聊天 + 识图）
│   ├── models.py               # 配置模型 + 默认人设
│   ├── store.py                # 配置持久化 + 消息/事件数据库
│   ├── rules.py                # 触发规则与决策引擎
│   ├── childsession.py         # 桌面分身：会话、进程投递、瘦身、登录体检
│   ├── startup_items.py        # 分身干净启动：临时挂起/恢复开机启动项
│   ├── media.py                # 收图转码、表情库、剪贴板（SetImage / CF_HDROP）
│   ├── keyinput.py             # SendInput 真实按键（中文输入）
│   ├── focus.py                # 强制前台（AttachThreadInput）
│   ├── console_window.py       # 独立控制台窗口（Edge app / WebView2）
│   ├── wincred.py              # Windows 凭据管理器读写
│   ├── logview.py / spawn.py / paths.py
│   └── adapters/               # 接入层：wechatauto(4.x) / wxauto(3.9) / hook / mock
├── web/                        # 控制台前端（纯静态，无外部依赖）
├── tools/
│   ├── ChildSessionKeeper.cs   # 桌面分身引擎（C#，编译成 落小鱼引擎.exe）
│   ├── selftest.py             # 自检
│   └── *.py / *.bat / *.ps1    # 诊断、导出日志、修复登录等
├── vendor/wxauto/              # 微信 3.9 旧适配器（可选）
└── docs/                       # 使用说明、版本说明、控制台规格、人设提示词
```

---

## 从源码运行

环境：**Windows 10/11**、微信 **4.1.12+**（已登录）、Python **3.11+**。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 编译桌面分身引擎（用系统自带的 csc）
C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe `
  /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll `
  /r:System.Core.dll /r:System.Management.dll `
  /target:winexe /platform:x64 /out:tools\落小鱼引擎.exe tools\ChildSessionKeeper.cs

.venv\Scripts\python.exe main.py      # 启动
```

## 打包

```powershell
.venv\Scripts\python.exe build.py             # 产物：release\落小鱼.exe（单文件）
.venv\Scripts\python.exe build.py --console   # 带控制台的调试版
```

---

## 使用要点（踩过的坑）

1. **第一次启动要建分身**，Windows 会要求在该会话里登录一次（输入 Windows 密码）。
   Microsoft 账户可能要在登录框里选「更多选项 → 使用其他账户」填
   `计算机名\用户名`。凭据可以存进凭据管理器，之后静默登录。
2. **微信要勾「自动登录」**，否则分身里重启微信会停在扫码界面。
3. **同一个微信号不能两台电脑同时登录**。分身里登录后，主桌面不要再登同一个号。
4. **别把程序放到低权限目录**：它需要读微信进程内存来解密数据库。
5. **系统代理**：代理程序没运行时模型请求会失败。默认直连，
   需要走代理时在「AI 接口」页勾选「使用系统代理」。
6. 分身窗口**平时保持隐藏**；可见时机器人在分身里动鼠标会连带你的真实指针。

---

## 免责声明

- 本项目仅供**个人学习与技术研究**，请勿用于商业用途、批量营销或任何违反法律法规的场景。
- 使用自动化工具操作微信**可能违反微信用户协议**，存在账号被限制的风险，**后果自负**。
- 作者不对因使用本项目造成的任何直接或间接损失负责。
- 请勿用于骚扰他人、群发广告、诈骗等行为。

---

## License

[MIT](LICENSE)
