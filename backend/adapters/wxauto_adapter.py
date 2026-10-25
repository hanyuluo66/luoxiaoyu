"""WeChat 3.9.x adapter built on the vendored wxauto library.

Why a dedicated thread: wxauto drives `uiautomation`/COM, and every UI call must
happen on the same thread that initialised COM. So all WeChat work is funnelled
through one worker thread:

    callers (HTTP handlers / reply worker)
        -> queue command  ->  worker thread executes  ->  result box
    worker thread also polls `GetListenMessage()` on an interval.

Nothing outside this class may touch a wxauto object.
"""
from __future__ import annotations

import itertools
import logging
import queue
import re
import threading
import time
from typing import Any

from ..rules import IncomingMessage
from .base import BaseAdapter, classify_content

log = logging.getLogger(__name__)

_MEMBER_COUNT_RE = re.compile(r"^\(\d+\)$")
_GROUP_TITLE_RE = re.compile(r"\(\d+\)\s*$")


class WxautoAdapter(BaseAdapter):
    name = "wxauto"

    def __init__(
        self,
        on_message,
        *,
        on_self_message=None,
        poll_interval: float = 1.5,
        debug: bool = False,
    ) -> None:
        super().__init__(on_message)
        self.on_self_message = on_self_message
        self.poll_interval = poll_interval
        self.debug = debug

        self._cmds: queue.Queue[tuple[int, str, dict[str, Any], dict[str, Any]]] = queue.Queue()
        self._seq = itertools.count(1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._desired: list[str] = []
        self._desired_lock = threading.RLock()
        self._listen: dict[str, Any] = {}
        self._chat_type_hint: dict[str, str] = {}
        self._listen_failed: dict[str, float] = {}
        self._reconnect_delay = 5.0
        self._last_poll = 0.0
        self._wxauto = None
        self._uia = None
        self._ChatWnd = None

    # ================================================================== public
    def start(self, listen_chats: list[str]) -> None:
        self.set_listen(listen_chats)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wxauto-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=8)
        self._thread = None
        self._listen.clear()
        self._set_status(connected=False, detail="已停止")

    def set_listen(self, chats: list[str]) -> None:
        with self._desired_lock:
            self._desired = [c for c in dict.fromkeys(chats) if c]
        self._set_status(listening=list(self._desired))

    def send_text(self, chat: str, text: str, timeout: float = 45.0) -> tuple[bool, str]:
        result = self._call("send", {"chat": chat, "text": text}, timeout=timeout)
        if result is None:
            return False, "微信线程无响应（发送超时）"
        return bool(result.get("ok")), str(result.get("detail", ""))

    def sessions(self, timeout: float = 25.0) -> list[dict]:
        result = self._call("sessions", {}, timeout=timeout)
        return list(result or [])

    def contact_names(self, timeout: float = 25.0) -> list[str]:
        result = self._call("contacts", {}, timeout=timeout)
        return list(result or [])

    def chat_type_of(self, chat: str) -> str:
        with self._lock:
            return self._chat_type_hint.get(chat, "")

    # ============================================================ worker thread
    def _run(self) -> None:
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except Exception:  # noqa: BLE001 - already initialised is fine
            pass

        try:
            import wxauto  # imported here so COM/UIA is created on this thread
            from wxauto import uiautomation as wxuia
            from wxauto.elements import ChatWnd
        except Exception as exc:  # noqa: BLE001
            self._set_status(
                connected=False,
                detail=f"加载 wxauto 失败：{exc}",
                last_error=str(exc),
            )
            log.exception("导入 wxauto 失败")
            return

        self._wxauto = wxauto
        self._uia = wxuia
        self._ChatWnd = ChatWnd
        wx = None

        while not self._stop.is_set():
            if wx is None:
                wx = self._connect()
                if wx is None:
                    self._stop.wait(self._reconnect_delay)
                    continue

            # 1) run every pending command
            self._drain_commands(wx)

            # 2) poll for new messages
            if time.time() - self._last_poll >= self.poll_interval:
                self._poll(wx)

        self._on_close()

    def _connect(self):
        try:
            wx = self._wxauto.WeChat(debug=self.debug)
        except Exception as exc:  # noqa: BLE001
            hint = (
                "连不上微信。请确认：①微信已登录并保持窗口打开；"
                "②微信是 3.9.11.17 版本（4.x 无法自动化）。"
                f"原始错误：{type(exc).__name__}"
            )
            self._set_status(connected=False, detail=hint, last_error=str(exc)[:300])
            return None

        try:
            nickname = getattr(wx, "nickname", "") or ""
        except Exception:  # noqa: BLE001
            nickname = ""
        version = str(getattr(self._wxauto.WeChat, "VERSION", ""))
        self._set_status(
            connected=True,
            detail=f"已连接微信：{nickname or '未知昵称'}",
            my_names=[n for n in [nickname] if n],
            wechat_version=version,
            last_error="",
        )
        log.info("微信连接成功：%s (wxauto %s)", nickname, version)
        return wx

    def _drain_commands(self, wx) -> None:
        processed = 0
        while processed < 8:
            try:
                seq, kind, args, box = self._cmds.get_nowait()
            except queue.Empty:
                return
            processed += 1
            try:
                if kind == "send":
                    box["result"] = self._do_send(wx, args["chat"], args["text"])
                elif kind == "reload_listen":
                    self._sync_listen(wx)
                    box["result"] = {"ok": True}
                elif kind == "sessions":
                    box["result"] = self._do_sessions(wx)
                elif kind == "contacts":
                    box["result"] = self._do_contacts(wx)
                else:
                    box["result"] = None
            except Exception as exc:  # noqa: BLE001
                log.exception("处理微信命令失败: %s", kind)
                box["result"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            finally:
                box["event"].set()

    # ------------------------------------------------------------------ polling
    def _poll(self, wx) -> None:
        self._last_poll = time.time()
        self._sync_listen(wx)
        try:
            bundle = wx.GetListenMessage()
        except Exception as exc:  # noqa: BLE001
            log.warning("拉取新消息失败：%s", exc)
            self._set_status(last_error=f"拉取消息失败：{exc}")
            return

        if not bundle:
            return

        for chat_obj, messages in bundle.items():
            who = getattr(chat_obj, "who", "") or ""
            if not who:
                continue
            # Cache the live window object so sends reuse it.
            self._listen[who] = chat_obj
            with self._lock:
                chat_type = self._chat_type_hint.get(who) or ""
            if not chat_type:
                chat_type = self._detect_chat_type(chat_obj, who)
                with self._lock:
                    self._chat_type_hint[who] = chat_type
            for msg in messages or []:
                self._dispatch(msg, who, chat_type)

    def _dispatch(self, msg, who: str, chat_type: str) -> None:
        kind = getattr(msg, "type", "")
        content = str(getattr(msg, "content", "") or "")
        if kind == "self":
            log.debug("[自己发言] %s: %s", who, content)
            if self.on_self_message:
                try:
                    self.on_self_message(who)
                except Exception:  # noqa: BLE001
                    log.exception("on_self_message 回调异常")
            return
        if kind in ("sys", "time", "recall"):
            log.debug("[%s] %s: %s", kind, who, content)
            return
        if kind != "friend":
            return

        msg_type = classify_content(content)
        if msg_type != "text":
            # Images/files/voice carry no text for the model; skip politely.
            log.debug("跳过非文本消息：%s %s", who, content[:24])
            return

        sender = str(getattr(msg, "sender", "") or "")
        remark = str(getattr(msg, "sender_remark", "") or "")
        my_names = self.my_names()
        at_me = chat_type == "group" and any(n and f"@{n}" in content for n in my_names)
        at_all = chat_type == "group" and ("@所有人" in content or "@All" in content)

        incoming = IncomingMessage(
            chat=who,
            chat_type=chat_type,
            sender=remark or sender,
            content=content,
            msg_type=msg_type,
            raw_id=str(getattr(msg, "id", "") or ""),
            at_me=at_me,
            at_all=at_all,
            timestamp=time.time(),
        )
        try:
            self.on_message(incoming)
        except Exception:  # noqa: BLE001
            log.exception("on_message 回调异常")

    # -------------------------------------------------------------- listen set
    def _sync_listen(self, wx) -> None:
        with self._desired_lock:
            desired = list(self._desired)
        if desired == list(self._listen.keys()):
            return

        for who in list(self._listen.keys()):
            if who not in desired:
                try:
                    wx.RemoveListenChat(who)
                except Exception:  # noqa: BLE001
                    log.debug("移除监听失败：%s", who)
                self._listen.pop(who, None)

        for who in desired:
            if who in self._listen:
                continue
            failed_at = self._listen_failed.get(who, 0.0)
            if failed_at and time.time() - failed_at < 60:
                continue  # don't hammer a chat that keeps failing
            try:
                wx.AddListenChat(who)
                self._listen[who] = True
                self._listen_failed.pop(who, None)
                log.info("开始监听：%s", who)
            except Exception as exc:  # noqa: BLE001
                self._listen_failed[who] = time.time()
                log.warning("添加监听失败：%s (%s)", who, exc)
                self._set_status(last_error=f"监听「{who}」失败：{exc}")

    # ------------------------------------------------------------------ actions
    def _do_send(self, wx, chat: str, text: str) -> dict[str, Any]:
        chat = (chat or "").strip()
        if not chat:
            return {"ok": False, "detail": "没有指定发送对象"}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "detail": "发送内容为空"}

        if chat in self._listen:
            cached = self._listen.get(chat)
            try:
                win = cached if cached not in (None, True) else self._ChatWnd(chat)
                win.SendMsg(text)
                return {"ok": True, "detail": "已发送（独立窗口）"}
            except Exception as exc:  # noqa: BLE001
                log.warning("独立窗口发送失败，回退主窗口：%s", exc)

        try:
            wx.SendMsg(text, chat)
            return {"ok": True, "detail": "已发送（主窗口）"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"发送失败：{type(exc).__name__}: {exc}"}

    def _do_sessions(self, wx) -> list[dict]:
        out: list[dict] = []
        try:
            for item in wx.GetSession():
                name = getattr(item, "name", "") or ""
                if not name:
                    continue
                out.append(
                    {
                        "name": name,
                        "last": getattr(item, "content", "") or "",
                        "time": getattr(item, "time", "") or "",
                        "unread": bool(getattr(item, "isnew", False)),
                        "type": self.chat_type_of(name) or "unknown",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("获取会话列表失败：%s", exc)
        return out

    def _do_contacts(self, wx) -> list[str]:
        names: list[str] = []
        try:
            names.extend(item["name"] for item in self._do_sessions(wx))
        except Exception:  # noqa: BLE001
            pass
        return names

    # ------------------------------------------------------------- chat typing
    def _detect_chat_type(self, chat_obj, who: str) -> str:
        """A group's chat header reads "群名 (23)"; a friend's is just the name."""
        detected = ""
        uia = getattr(self, "_uia", None)
        if uia is not None:
            try:
                uia.SetGlobalSearchTimeout(0.8)  # never stall polling for 10s
            except Exception:  # noqa: BLE001
                uia = None
        try:
            header = chat_obj.UiaAPI.TextControl(searchDepth=15)
            title = (header.Name or "").strip()
            if title:
                detected = "group" if _GROUP_TITLE_RE.search(title) else "friend"
        except Exception:  # noqa: BLE001
            detected = ""
        finally:
            if uia is not None:
                try:
                    uia.SetGlobalSearchTimeout(10)
                except Exception:  # noqa: BLE001
                    pass

        if detected:
            log.info("识别会话「%s」为%s", who, "群聊" if detected == "group" else "私聊")
            return detected

        try:
            if chat_obj.UiaAPI.ButtonControl(Name="聊天信息").Exists(0):
                log.info("识别会话「%s」为群聊（按按钮判断）", who)
                return "group"
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            previous = self._chat_type_hint.get(who)
        if not previous:
            log.info("识别会话「%s」为私聊（默认）", who)
        return previous or "friend"

    # ------------------------------------------------------------ command glue
    def _call(self, kind: str, args: dict[str, Any], timeout: float = 45.0):
        if not self._thread or not self._thread.is_alive():
            return None
        seq = next(self._seq)
        box: dict[str, Any] = {"event": threading.Event(), "result": None}
        self._cmds.put((seq, kind, args, box))
        if not box["event"].wait(timeout):
            log.warning("微信命令超时：%s", kind)
            return None
        return box["result"]

    def _on_close(self) -> None:
        try:
            comtypes = __import__("comtypes")
            comtypes.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass
