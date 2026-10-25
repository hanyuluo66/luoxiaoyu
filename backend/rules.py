"""Decision engine: should this incoming message get a reply, and how.

Kept free of I/O so it can be unit-tested without WeChat or the network.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import re
from dataclasses import dataclass, field

from .models import AppConfig, Rule


@dataclass(slots=True)
class IncomingMessage:
    """Normalised inbound message, produced by an adapter."""

    chat: str
    chat_type: str  # "friend" | "group"
    sender: str
    content: str
    msg_type: str = "text"     # text | image | sticker
    image_path: str = ""       # 本地已解码的图片（识图 / 收藏表情包用）
    raw_id: str = ""
    at_me: bool = False
    at_all: bool = False
    timestamp: float = 0.0


@dataclass(slots=True)
class Decision:
    reply: bool
    reason: str
    source: str = "default"
    rule_id: str = ""
    rule_name: str = ""
    template: str = ""
    prompt: str = ""
    cooldown: float = 0.0
    extras: dict[str, str] = field(default_factory=dict)


IGNORE = Decision(reply=False, reason="")


def _chat_matches(pattern: str, chat: str) -> bool:
    """Exact match, or a glob when the pattern has wildcards."""
    if not pattern:
        return False
    if any(ch in pattern for ch in "*?[]"):
        return fnmatch.fnmatch(chat, pattern)
    return pattern == chat


def chat_in(patterns: list[str], chat: str) -> bool:
    return any(_chat_matches(p, chat) for p in patterns)


def text_matches(kind: str, patterns: list[str], text: str) -> tuple[bool, str]:
    """Return (matched, matched_pattern)."""
    if not patterns:
        return False, ""
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if kind == "contains":
                if pattern in text:
                    return True, pattern
            elif kind == "exact":
                if text.strip() == pattern.strip():
                    return True, pattern
            elif kind == "startswith":
                if text.strip().startswith(pattern.strip()):
                    return True, pattern
            elif kind == "regex":
                if re.search(pattern, text):
                    return True, pattern
            elif kind == "always":
                return True, pattern
        except re.error:
            # A broken user regex must never crash the bot.
            continue
    return False, ""


def normalize_at(text: str) -> str:
    """Strip the invisible characters WeChat puts around @mentions."""
    return (text or "").replace("\u2005", " ").replace("\u200b", "").strip()


def mentions_me(content: str, my_names: list[str]) -> bool:
    body = normalize_at(content)
    return any(name and f"@{name}" in body for name in my_names)


def mentions_all(content: str) -> bool:
    body = normalize_at(content)
    return any(token in body for token in ("@所有人", "@All", "@all", "@全体成员"))


def in_quiet_hours(now: dt.datetime, start: str, end: str) -> bool:
    """True when `now` falls inside [start, end); handles overnight windows."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (ValueError, AttributeError):
        return False
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    now_min = now.hour * 60 + now.minute
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


class DecisionEngine:
    """Applies blacklist -> scope -> rules -> default trigger, in that order."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ public
    def resolve(self, msg: IncomingMessage, my_names: list[str]) -> Decision:
        cfg = self.config
        body = normalize_at(msg.content)

        if not cfg.bot.enabled:
            return Decision(reply=False, reason="总开关已关闭")

        # ---- hard stops ---------------------------------------------------
        if chat_in(cfg.blacklist.chats, msg.chat):
            return Decision(reply=False, reason=f"会话「{msg.chat}」在永久忽略名单里")
        if msg.sender and chat_in(cfg.blacklist.senders, msg.sender):
            return Decision(reply=False, reason=f"发送人「{msg.sender}」在忽略名单里")
        hit, pattern = text_matches("contains", cfg.blacklist.ignore_keywords, body)
        if hit:
            return Decision(reply=False, reason=f"消息命中忽略词「{pattern}」")

        # ---- scope --------------------------------------------------------
        if msg.chat_type == "group":
            if not cfg.bot.group_enabled:
                return Decision(reply=False, reason="群聊自动回复总开关已关闭")
            scope_ok = self._scope_allows(cfg.group.mode, cfg.group.chats, msg.chat)
            if not scope_ok:
                return Decision(reply=False, reason=f"群「{msg.chat}」不在监听范围内")
        else:
            if not cfg.bot.private_enabled:
                return Decision(reply=False, reason="私聊自动回复总开关已关闭")
            scope_ok = self._scope_allows(cfg.private.mode, cfg.private.chats, msg.chat)
            if not scope_ok:
                return Decision(reply=False, reason=f"「{msg.chat}」不在私聊监听范围内")

        # ---- rules (highest priority wins) --------------------------------
        for rule in self._sorted_rules():
            if not self._rule_applies(rule, msg):
                continue
            matched, pattern = text_matches(rule.match, rule.patterns, body)
            if rule.match == "always" or matched:
                if rule.action == "ignore":
                    return Decision(
                        reply=False,
                        reason=f"命中规则「{rule.name}」= 忽略",
                        rule_id=rule.id,
                        rule_name=rule.name,
                    )
                return Decision(
                    reply=True,
                    reason=f"命中规则「{rule.name}」"
                    + (f"（关键词：{pattern}）" if pattern and rule.match != "always" else ""),
                    source="rule",
                    rule_id=rule.id,
                    rule_name=rule.name,
                    template=rule.template,
                    prompt=rule.prompt,
                    cooldown=rule.cooldown_seconds,
                )

        # ---- fallback to the default trigger ------------------------------
        if msg.chat_type == "group":
            return self._group_default(msg, my_names, body)
        return self._private_default(msg, body)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _scope_allows(mode: str, chats: list[str], chat: str) -> bool:
        if mode == "all":
            return True
        listed = chat_in(chats, chat)
        return listed if mode == "include" else not listed

    def _sorted_rules(self) -> list[Rule]:
        rules = [r for r in self.config.rules if r.enabled]
        rules.sort(key=lambda r: (r.priority, r.name))
        return rules

    @staticmethod
    def _rule_applies(rule: Rule, msg: IncomingMessage) -> bool:
        if rule.scope == "private" and msg.chat_type != "friend":
            return False
        if rule.scope == "group" and msg.chat_type != "group":
            return False
        if rule.chats and not chat_in(rule.chats, msg.chat):
            return False
        return True

    def _private_default(self, msg: IncomingMessage, body: str) -> Decision:
        cfg = self.config
        if cfg.private.require_keyword:
            hit, pattern = text_matches("contains", cfg.private.keywords, body)
            if not hit:
                return Decision(reply=False, reason="私聊要求关键词触发，本条未命中")
            return Decision(reply=True, reason=f"私聊关键词命中「{pattern}」", source="private-keyword")
        return Decision(reply=True, reason="私聊默认全部回复", source="private-all")

    def _group_default(self, msg: IncomingMessage, my_names: list[str], body: str) -> Decision:
        cfg = self.config
        trigger = cfg.group.trigger
        if trigger == "all":
            return Decision(reply=True, reason="群聊设置为回复所有消息", source="group-all")

        if trigger == "keyword":
            hit, pattern = text_matches("contains", cfg.group.keywords, body)
            at_me = mentions_me(body, my_names)
            if hit or at_me:
                why = f"关键词「{pattern}」" if hit else "@我"
                return Decision(reply=True, reason=f"群聊关键词触发（{why}）", source="group-keyword")
            return Decision(reply=False, reason="群聊要求关键词/@触发，本条未命中")

        # trigger == "at"
        if mentions_me(body, my_names):
            return Decision(reply=True, reason="群聊里@了我", source="group-at")
        if cfg.group.reply_to_at_all and mentions_all(body):
            return Decision(reply=True, reason="群聊里@所有人", source="group-at-all")
        return Decision(reply=False, reason="群聊只在@我时回复，本条未@我")
