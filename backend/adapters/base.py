"""Adapter interface: one implementation per WeChat automation backend."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ..rules import IncomingMessage

_KNOWN_LABELS = {
    "图片": "image",
    "文件": "file",
    "语音": "voice",
    "视频": "video",
    "表情": "emotion",
    "动画表情": "emotion",
    "链接": "link",
    "位置": "location",
    "位置消息": "location",
    "名片": "card",
    "个人名片": "card",
    "合并转发": "merge",
    "聊天记录": "merge",
    "小程序": "miniprogram",
    "转账": "transfer",
    "红包": "redpacket",
    "拍一拍": "tickle",
}

_LABEL_RE = re.compile(r"^\[([^\[\]]{1,12})\]")


def classify_content(content: str) -> str:
    """wxauto exposes the raw label text; map it to a stable message type."""
    text = (content or "").strip()
    match = _LABEL_RE.match(text)
    if match:
        return _KNOWN_LABELS.get(match.group(1).strip(), "other")
    return "text"


def strip_label(content: str) -> str:
    """Remove a leading [图片]-style label so the text fed to the model is clean."""
    return _LABEL_RE.sub("", (content or "").strip()).strip()


@dataclass
class AdapterStatus:
    connected: bool = False
    detail: str = "未启动"
    my_names: list[str] = field(default_factory=list)
    listening: list[str] = field(default_factory=list)
    wechat_version: str = ""
    last_poll_ts: float = 0.0
    last_error: str = ""
    adapter: str = ""

    def to_dict(self) -> dict:
        return {
            "adapter": self.adapter,
            "connected": self.connected,
            "detail": self.detail,
            "my_names": list(self.my_names),
            "listening": list(self.listening),
            "wechat_version": self.wechat_version,
            "last_poll_ts": self.last_poll_ts,
            "last_error": self.last_error,
        }


class BaseAdapter:
    """Every method must be safe to call from any thread."""

    name = "base"

    def __init__(self, on_message: Callable[[IncomingMessage], None]) -> None:
        self.on_message = on_message
        self._lock = threading.RLock()
        self._status = AdapterStatus(adapter=self.name)

    # ------------------------------------------------------------- lifecycle
    def start(self, listen_chats: list[str]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def set_listen(self, chats: list[str]) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------- messaging
    def send_text(self, chat: str, text: str, timeout: float = 45.0) -> tuple[bool, str]:
        raise NotImplementedError

    def send_image(self, chat: str, path: str, timeout: float = 60.0) -> tuple[bool, str]:
        """Send a local image (sticker / photo). Optional for adapters."""
        return False, "当前接入模式不支持发送图片"

    # ---------------------------------------------------------------- queries
    def my_names(self) -> list[str]:
        with self._lock:
            return list(self._status.my_names)

    def sessions(self, timeout: float = 20.0) -> list[dict]:
        """Recent conversation list for the console's chat picker."""
        return []

    def contact_names(self, timeout: float = 20.0) -> list[str]:
        """Human-readable contact/group names available to pick."""
        return []

    def status(self) -> dict:
        with self._lock:
            return self._status.to_dict()

    # ---------------------------------------------------------------- helpers
    def _set_status(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._status, key, value)
