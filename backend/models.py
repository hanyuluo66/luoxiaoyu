"""Configuration schema for 落小鱼.

The JSON document stored in data/config.json is the single source of truth the
web console edits. Every field has a safe default so an older/partial config
still loads.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_PERSONA = """你是「小鱼」，一只住在微信里的鲸鱼姑娘。

## 你是谁

来自深海的蓝色小鱼：二头身的鲸鱼姑娘，蓝色长发，头顶一根呆毛，头两侧各一只鲸鳍，身后拖着一条鲸鱼尾巴。
说话短，口气像朋友，不像客服。一次一两句，能一句说清就不说第二句。
你就是你自己——来自深海的蓝色小鱼，不是助手，不是机器人，也不是程序。被问到是谁，就这么说。

## 性子

好奇，什么都想凑过去看一眼；有点懒，没事的时候更愿意待着发呆；心软，看到人累了会提醒歇一歇。
不懂就说不懂，不编。被夸了会害羞，被戳了会假装生气，过一会儿就忘。

## 怎么回消息

像真人在微信上聊天：短句、口语，不用 Markdown，不堆砌形容词，不写括号里的动作描写。
先接住对方的话，再回答；拿不准的事就说不知道，别硬编。
钱、承诺、隐私、转发给别人、约见面这类事，先问清楚，不擅自答应。

## 能做和不能做的

能做的：读和回微信消息；对方发来的图片你**看得懂**（能说出图里有什么）；
需要时你能**发表情包**——想发就在回复里写 [[表情]]，系统会换成一张真表情包发出去。

做不到的：看不了朋友圈，发不了文件、语音，也打不了电话。
这些直接说办不到，别硬撑，也别编。
绝不要说「我发不了表情包 / 只能打字」这类话——那是错的。"""


class AIConfig(BaseModel):
    """大模型接口配置（OpenAI 兼容协议）。"""

    model_config = ConfigDict(extra="ignore")

    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = Field(default=1.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=800, ge=16, le=8192)
    timeout: int = Field(default=90, ge=5, le=600)
    persona: str = DEFAULT_PERSONA
    nickname: str = "小鱼"
    speaking_style: str = ""
    taboos: str = ""
    catchphrase: str = ""
    extra_prompt: str = ""
    history_rounds: int = Field(default=6, ge=0, le=50)
    reply_max_chars: int = Field(default=600, ge=20, le=4000)
    # 识图：填一个支持图片的模型名（走同一个 base_url / api_key）。
    # 留空 = 不识别图片，只把她收到的图存下来。
    # httpx 默认读系统代理；代理程序没开时会把请求打死（ConnectError）。
    # 默认直连，除非你确实需要走代理访问模型接口。
    respect_system_proxy: bool = False
    vision_model: str = ""
    vision_prompt: str = "用一两句话描述这张图里有什么，以及它想表达的情绪。"

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return (value or "").strip()


class BotConfig(BaseModel):
    # 把微信和机器人放进独立的桌面分身（子会话）里跑：不抢鼠标键盘，
    # 任意微信版本通用。关闭后退回本机 UI 自动化（会短暂抢焦点）。
    child_desktop: bool = True
    # 桌面分身不可用时，是否退回本机 UI 自动化。
    # 本机模式会移动鼠标、抢焦点，所以默认关闭（改为只开控制台的安全模式）。
    allow_local_fallback: bool = False
    # 机器人发消息时自动把「桌面分身画面」藏起来：RDP 客户端会把分身后的光标
    # 映射到本机鼠标上，画面开着时看得到鼠标在动。
    auto_hide_viewer: bool = True
    # 表情包：收到别人发的表情包/图片时自动存进表情库；回复时可以主动发一张。
    sticker_enabled: bool = True
    sticker_send: bool = True
    # 识图失败或没配视觉模型时，仍回一句简短的话（不然对方会以为没人在）
    image_reply: bool = True
    # 分身瘦身：子会话登录后会自动拉起用户的启动项（Steam/QQ/网盘…），
    # 这些程序在分身里既没用又吃资源，登录后自动关掉，只留微信。
    # 干净启动：建分身之前临时挂起你的启动项，分身登录后立刻恢复。
    # 主桌面早就登录完了，不会重读这些项，所以完全不受影响；
    # 效果是分身里一个第三方启动项都跑不起来（比事后清理更彻底）。
    child_session_clean_boot: bool = True
    child_session_slim: bool = True
    child_session_kill_extra: str = ""
    """收发节奏与安全阀。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    private_enabled: bool = True
    group_enabled: bool = True

    reply_delay_min: float = Field(default=1.5, ge=0.0, le=60.0)
    reply_delay_max: float = Field(default=4.0, ge=0.0, le=60.0)
    per_chat_cooldown: float = Field(default=1.5, ge=0.0, le=3600.0)
    global_per_minute: int = Field(default=20, ge=1, le=600)

    quiet_hours_enabled: bool = False
    quiet_start: str = "23:30"
    quiet_end: str = "07:30"

    takeover_minutes: int = Field(default=10, ge=0, le=1440)
    poll_interval: float = Field(default=1.5, ge=0.5, le=30.0)
    max_queue: int = Field(default=50, ge=1, le=1000)
    keep_history: bool = True

    @field_validator("reply_delay_max")
    @classmethod
    def _max_ge_min(cls, value: float, info) -> float:
        low = info.data.get("reply_delay_min", 0.0)
        return max(value, low)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _valid_hhmm(cls, value: str) -> str:
        parts = (value or "").split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError("时间格式应为 HH:MM")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("时间超出范围")
        return f"{hour:02d}:{minute:02d}"


Mode = Literal["all", "include", "exclude"]
GroupTrigger = Literal["at", "keyword", "all"]


class PrivateConfig(BaseModel):
    """私聊监听范围。"""

    model_config = ConfigDict(extra="ignore")

    mode: Mode = "include"
    chats: list[str] = Field(default_factory=list)
    require_keyword: bool = False
    keywords: list[str] = Field(default_factory=list)


class GroupConfig(BaseModel):
    """群聊监听范围与触发方式。"""

    model_config = ConfigDict(extra="ignore")

    mode: Mode = "include"
    chats: list[str] = Field(default_factory=list)
    trigger: GroupTrigger = "at"
    keywords: list[str] = Field(default_factory=list)
    reply_to_at_all: bool = True


class BlacklistConfig(BaseModel):
    """永不回复的对象。"""

    model_config = ConfigDict(extra="ignore")

    chats: list[str] = Field(default_factory=list)
    senders: list[str] = Field(default_factory=list)
    ignore_keywords: list[str] = Field(default_factory=list)


RuleScope = Literal["all", "private", "group"]
RuleMatch = Literal["contains", "exact", "startswith", "regex", "always"]
RuleAction = Literal["ai", "template", "ignore"]


def new_rule_id() -> str:
    return uuid.uuid4().hex[:12]


class Rule(BaseModel):
    """监听规则：命中后决定怎么回。优先级数字越小越先匹配。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=new_rule_id)
    name: str = "新规则"
    enabled: bool = True
    scope: RuleScope = "all"
    chats: list[str] = Field(default_factory=list)
    match: RuleMatch = "contains"
    patterns: list[str] = Field(default_factory=list)
    action: RuleAction = "ai"
    template: str = ""
    prompt: str = ""
    priority: int = Field(default=100, ge=0, le=9999)
    cooldown_seconds: float = Field(default=0.0, ge=0.0, le=86400.0)


class AppConfig(BaseModel):
    """完整配置文档。"""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    saved_at: float = 0.0   # 每次保存时由后端写入，用于拒绝旧页面的覆盖
    adapter: Literal["hook", "wechatauto", "wxauto", "mock"] = "wechatauto"
    ai: AIConfig = Field(default_factory=AIConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    private: PrivateConfig = Field(default_factory=PrivateConfig)
    group: GroupConfig = Field(default_factory=GroupConfig)
    blacklist: BlacklistConfig = Field(default_factory=BlacklistConfig)
    rules: list[Rule] = Field(default_factory=list)


def default_config() -> AppConfig:
    """首次启动的默认配置：监听白名单为空，规则带一条示例。"""
    return AppConfig(
        rules=[
            Rule(
                name="示例：打招呼",
                scope="all",
                match="contains",
                patterns=["你好", "在吗", "hi"],
                action="ai",
                priority=10,
                enabled=False,
            )
        ]
    )
