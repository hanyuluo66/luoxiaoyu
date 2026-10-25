"""Mock adapter: drive the whole pipeline without WeChat.

Used for development, for the console's "试用模式", and for the automated
self-test. It fabricates inbound messages on a timer and records outbound ones.
"""
from __future__ import annotations

import itertools
import random
import threading
import time

from ..rules import IncomingMessage
from .base import BaseAdapter

DEMO_FRIENDS = ["张三", "小美", "老王"]
DEMO_GROUPS = ["小鱼测试群", "摸鱼交流群"]

DEMO_LINES = [
    "你好呀",
    "在吗，晚上吃啥",
    "帮我看看这句话怎么回",
    "今天好累啊",
    "你这机器人挺智能的",
]


class MockAdapter(BaseAdapter):
    name = "mock"

    def __init__(self, on_message, *, auto_emit: bool = False, interval: float = 6.0) -> None:
        super().__init__(on_message)
        self.auto_emit = auto_emit
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._counter = itertools.count(1)
        self.sent: list[dict] = []
        self._listen: list[str] = []

    # ------------------------------------------------------------- lifecycle
    def start(self, listen_chats: list[str]) -> None:
        self._stop.clear()
        self.set_listen(listen_chats)
        self._set_status(
            connected=True,
            detail="模拟模式运行中（不连微信，用于试用和自测）",
            my_names=["落小鱼"],
            wechat_version="mock",
            last_error="",
        )
        if self.auto_emit and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name="mock-emitter", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
        self._set_status(connected=False, detail="已停止")

    def set_listen(self, chats: list[str]) -> None:
        with self._lock:
            self._listen = list(chats)
        self._set_status(listening=list(chats))

    # ------------------------------------------------------------- messaging
    def send_text(self, chat: str, text: str, timeout: float = 45.0) -> tuple[bool, str]:
        with self._lock:
            self.sent.append({"ts": time.time(), "chat": chat, "text": text})
            if len(self.sent) > 200:
                self.sent = self.sent[-200:]
        return True, "模拟发送成功"

    # ---------------------------------------------------------------- queries
    def sessions(self, timeout: float = 20.0) -> list[dict]:
        return [
            {"name": name, "type": "friend", "last": "示例会话"}
            for name in DEMO_FRIENDS + DEMO_GROUPS
        ]

    def contact_names(self, timeout: float = 20.0) -> list[str]:
        return DEMO_FRIENDS + DEMO_GROUPS

    # ---------------------------------------------------------------- helpers
    def inject(self, chat: str, sender: str, content: str, chat_type: str = "friend") -> None:
        """Push a fake inbound message through the normal callback."""
        self.on_message(
            IncomingMessage(
                chat=chat,
                chat_type=chat_type,
                sender=sender,
                content=content,
                msg_type="text",
                raw_id=f"mock-{next(self._counter)}",
                at_me=chat_type == "group" and "@落小鱼" in content,
                timestamp=time.time(),
            )
        )

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            group = random.random() < 0.4
            if group:
                chat = random.choice(DEMO_GROUPS)
                sender = random.choice(DEMO_FRIENDS)
                content = f"@落小鱼 {random.choice(DEMO_LINES)}"
            else:
                chat = random.choice(DEMO_FRIENDS)
                sender = chat
                content = random.choice(DEMO_LINES)
            self.inject(chat, sender, content, "group" if group else "friend")
