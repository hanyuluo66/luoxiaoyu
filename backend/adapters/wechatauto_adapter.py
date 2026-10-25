"""WeChat 4.x adapter built on wechatauto-replica.

Design notes
------------
* Reading goes through the **local SQLCipher database** (`WeChatDB` + `Listener`):
  full history, no UI scraping, no OCR jitter.
* Sending goes through the **UIA driver** (`WeChat`): it hot-activates the Qt
  accessibility gate byte inside Weixin.dll so the `mmui::*` control tree
  materialises, then pastes text and presses Enter. No OCR needed.
* Both need process access to Weixin.exe, so everything runs on one worker
  thread created after we resolve the pid from the WeChat window handle
  (the pid list from plain enumeration can be filtered in sandboxed hosts).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import itertools
import logging
import os
import queue
import threading
from pathlib import Path
import time
from typing import Any

from ..rules import IncomingMessage
from .base import BaseAdapter

log = logging.getLogger(__name__)

CHATROOM_SUFFIX = "@chatroom"

# wechatauto maps local_type numbers to Chinese names (db.MSG_TYPE_NAMES):
#   1=文本 3=图片 34=语音 43=视频 47=动画表情 48=位置 49=文件/链接/卡片 …
# Only 文本 carries text worth replying to; everything else is skipped.
TEXT_TYPES = {"文本", "text", "Text", "txt"}


# ---------------------------------------------------------------------------
# process lookup (window handshake -> pid) + library patch
# ---------------------------------------------------------------------------
def _weixin_pids() -> list[int]:
    """Resolve Weixin.exe pids through visible Qt windows."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        psutil = None  # type: ignore[assignment]

    user32 = ctypes.windll.user32
    hits: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lparam):  # noqa: ANN001
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Qt5"):
                return True
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                hits.append(int(pid.value))
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(enum_proc(cb), 0)
    pids: list[int] = []
    for pid in sorted(set(hits)):
        if psutil is None:
            pids.append(pid)
            continue
        try:
            if (psutil.Process(pid).name() or "").lower() == "weixin.exe":
                pids.append(pid)
        except Exception:  # noqa: BLE001
            continue

    if not pids and psutil is not None:
        # WeChat minimised to the tray has no visible window, so fall back to
        # the process list (only pids that belong to our own session inherit the
        # same winsta, which is what we want inside a child session).
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                if (proc.info.get("name") or "").lower() != "weixin.exe":
                    continue
                try:
                    if proc.connections():
                        pass
                except Exception:  # noqa: BLE001
                    pass
                pids.append(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001
            log.debug("按进程名枚举微信失败", exc_info=True)
    return pids


def _patch_library(pids_provider) -> None:
    """Make the library resolve Weixin pids without `tasklist`."""
    try:
        from wechatauto import WeChatDB

        WeChatDB._find_weixin_pids = lambda self: list(pids_provider())  # type: ignore[assignment]
        log.debug("已替换 wechatauto 的进程查找逻辑")
    except Exception:  # noqa: BLE001
        log.exception("替换进程查找失败")


class WechatautoAdapter(BaseAdapter):
    name = "wechatauto"

    def __init__(
        self,
        on_message,
        *,
        on_self_message=None,
        poll_interval: float = 1.0,
    ) -> None:
        super().__init__(on_message)
        self.on_self_message = on_self_message
        self.poll_interval = max(0.5, float(poll_interval))
        # 静默模式：发送时不移动鼠标、不最小化任何窗口，只在打字期间
        # 短暂把微信提到前台（发完还原）。DAFEIYU_SILENT=0 可退回点击式发送。
        self.silent = os.environ.get("DAFEIYU_SILENT", "1") != "0"

        self._cmds: queue.Queue[tuple[int, str, dict[str, Any], dict[str, Any]]] = queue.Queue()
        self._seq = itertools.count(1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._db = None
        self._media = None
        self._wx = None
        self._uia = None
        self._listener = None
        self._names: dict[str, str] = {}
        self._self_username = ""
        self._started_at = 0.0
        self._listening = False
        self._last_poll = 0.0

    # ================================================================== public
    def start(self, listen_chats: list[str]) -> None:
        self.set_listen(listen_chats)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wx4-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=15)
        self._thread = None
        self._listener = None
        self._set_status(connected=False, detail="已停止")

    def set_listen(self, chats: list[str]) -> None:
        # DB-based reading sees every chat; the filter is applied in the pipeline.
        self._set_status(listening=list(chats))

# ---------------------------------------------------------------- 图片 / 表情包
    def _handle_image_row(
        self,
        row: dict[str, Any],
        chat_username: str,
        chat: str,
        sender_username: str,
        msg_type: str,
    ) -> None:
        """收图：下载 → 转标准图片 → 存表情库（表情包）→ 交给流水线。"""
        if sender_username and sender_username == self._self_username:
            return
        if self._media is None:
            log.debug("收到图片但没有下载器，跳过：%s", chat)
            return
        # 监听器给的 local_id 是「消息行」的 id，图片要用图片表自己的 local_id
        # 才能下载（实测用监听器的 id 会查到文字消息，下载必然失败）。
        candidates: list[int] = []
        try:
            image_rows = self._db.get_image_rows(chat_username, limit=10)
        except Exception:  # noqa: BLE001
            image_rows = []
        row_seq = str(row.get("sort_seq") or "")
        if msg_type == "动画表情" and image_rows:
            emoji_rows = [r for r in image_rows if "表情" in str(r.get("content") or "")]
            if emoji_rows:
                image_rows = emoji_rows
        if image_rows:
            # 优先同 sort_seq 的那条，其次按时间倒序前几条
            matched = [r for r in image_rows if str(r.get("sort_seq") or "") == row_seq]
            ordered = matched + [r for r in image_rows if r not in matched]
            for r in ordered[:4]:
                try:
                    candidates.append(int(r["local_id"]))
                except Exception:  # noqa: BLE001
                    continue
        fallback = row.get("local_id")
        if fallback is not None:
            try:
                candidates.append(int(fallback))
            except Exception:  # noqa: BLE001
                pass
        if not candidates:
            log.debug("图片消息没有可用 local_id：%s", chat)
            return

        from .. import media as media_mod

        path = None
        for local_id in candidates:
            try:
                path = self._media.download_media(
                    chat_username, local_id, save_dir=str(media_mod.inbox_dir())
                )
            except Exception:  # noqa: BLE001
                log.warning("下载图片失败：%s local_id=%s", chat, local_id, exc_info=True)
                path = None
            if path:
                log.debug("图片下载成功：%s local_id=%s → %s", chat, local_id, Path(path).name)
                break
        if not path:
            log.info("图片下载无结果（可能还没缓存）：%s", chat)
            return

        media_mod.cleanup_inbox()
        suffix = Path(path).suffix.lower()
        readable = suffix in media_mod.IMAGE_SUFFIXES
        log.info(
            "收到%s：%s → %s（%s）",
            "表情包" if msg_type == "动画表情" else "图片",
            chat,
            Path(path).name,
            "可读图片" if readable else "原始容器，需要 ffmpeg 转码",
        )

        sticker_path = ""
        is_sticker = msg_type == "动画表情" or suffix in (".gif", ".webp")
        if readable and is_sticker:
            saved = media_mod.save_sticker(path)
            if saved:
                sticker_path = str(saved)
                log.debug("表情包已收藏：%s", sticker_path)

        is_group = chat_username.endswith(CHATROOM_SUFFIX)
        sender = self._display(sender_username, sender_username) if sender_username else chat
        content = "[表情]" if msg_type == "动画表情" else "[图片]"
        try:
            self.on_message(
                IncomingMessage(
                    chat=chat,
                    chat_type="group" if is_group else "friend",
                    sender=sender,
                    content=content,
                    msg_type="sticker" if msg_type == "动画表情" else "image",
                    image_path=str(path) if readable else "",
                    raw_id=str(row.get("sort_seq") or local_id),
                    at_me=False,
                    at_all=False,
                    timestamp=float(row.get("create_time") or time.time()),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("回调图片消息失败")


    # ------------------------------------------------------- 登录确认页处理
    def _dismiss_enter_confirm(self) -> bool:
        """分身里的微信可能停在「进入微信」确认页，点一下才进主界面。

        不处理的话 UIA 读不到聊天控件，所有发送都会超时（拿不到主窗口控件）。
        """
        uia = self._uia
        if uia is None:
            return False
        try:
            win = getattr(uia, "_win", None) or uia._find_main()  # noqa: SLF001
            if win is None:
                return False
            for label in ("进入微信", "进入", "登 录", "登录"):
                try:
                    btn = win.Button(Name=label, searchDepth=12)
                    if btn.Exists(0.4):
                        btn.Click(simulateMove=False, waitTime=0.3)
                        log.info("微信停在登录确认页，已点击「%s」", label)
                        return True
                except Exception:  # noqa: BLE001
                    continue
            for label in ("进入微信", "进入"):
                try:
                    txt = win.TextControl(Name=label, searchDepth=12)
                    if txt.Exists(0.3):
                        txt.Click(simulateMove=False, waitTime=0.3)
                        log.info("已点击文字「%s」进入微信", label)
                        return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            log.debug("检查登录确认页失败", exc_info=True)
        return False

    def send_image(self, chat: str, path: str, timeout: float = 90.0) -> tuple[bool, str]:
        result = self._call("send_image", {"chat": chat, "path": path}, timeout=timeout)
        if result is None:
            return False, "微信线程无响应（发图片超时）"
        return bool(result.get("ok")), str(result.get("detail", ""))
    def send_text(self, chat: str, text: str, timeout: float = 90.0) -> tuple[bool, str]:
        result = self._call("send", {"chat": chat, "text": text}, timeout=timeout)
        if result is None:
            return False, "微信线程无响应（发送超时）"
        return bool(result.get("ok")), str(result.get("detail", ""))

    def sessions(self, timeout: float = 30.0) -> list[dict]:
        result = self._call("sessions", {}, timeout=timeout)
        return list(result or [])

    def contact_names(self, timeout: float = 30.0) -> list[str]:
        return [s["name"] for s in self.sessions(timeout) if s.get("name")]

    # ============================================================ worker thread
    def _run(self) -> None:
        self._set_status(connected=False, detail="正在启动微信 4.x 引擎…")
        try:
            import comtypes

            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except Exception:  # noqa: BLE001
            pass

        try:
            from wechatauto.param import WxParam

            WxParam.ENABLE_OCR = False   # 本环境装不了 winsdk，OCR 一律不启用
            log.info("已关闭 OCR（走纯 UIA 路径）")
        except Exception:  # noqa: BLE001
            pass

        _patch_library(_weixin_pids)

        try:

            from wechatauto.guia import WeChatGUI

        

            WeChatGUI._minimize_blockers = lambda self, *a, **k: None   # 不最小化用户的窗口

        except Exception:  # noqa: BLE001

            pass

        try:
            pids = _weixin_pids()
            if not pids:
                self._set_status(
                    connected=False,
                    detail="没找到微信进程。请先打开并登录微信（4.x）。",
                    last_error="weixin.exe not found",
                )
                return
            log.info("微信进程: %s", pids)

            from wechatauto import WeChat, WeChatDB

            t0 = time.time()
            self._db = WeChatDB()
            try:
                from wechatauto import MediaDownloader

                self._media = MediaDownloader(self._db)
                log.info("图片下载器就绪（可收图 / 收藏表情包）")
            except Exception:  # noqa: BLE001
                self._media = None
                log.warning("图片下载器不可用（收图功能关闭）", exc_info=True)
            info = {}
            try:
                info = self._db.get_self_info() or {}
            except Exception:  # noqa: BLE001
                log.warning("读取自己信息失败", exc_info=True)
            self._self_username = str(info.get("username") or "")
            nick = str(info.get("nick_name") or "").strip()
            self._names = {}
            try:
                self._refresh_names()
            except Exception:  # noqa: BLE001
                log.debug("nickname_map 失败", exc_info=True)
            log.info(
                "数据库已就绪：%s（%s），昵称表 %d 条，用时 %.1fs",
                self._self_username,
                nick,
                len(self._names),
                time.time() - t0,
            )

            self._set_status(
                connected=True,
                detail=f"已连接微信 4.x：{nick or self._self_username}",
                my_names=[n for n in [nick] if n],
                wechat_version="4.x",
                last_error="",
            )

            self._started_at = time.time()
            self._start_listener()

            # UIA driver is needed for sending; build it after the DB so a
            # failure to activate the gate does not block message reading.
            try:
                t0 = time.time()
                from wechatauto.uia_driver import WeChatUIA

                self._uia = WeChatUIA()
                log.info("UIA 驱动就绪，用时 %.1fs", time.time() - t0)
            except Exception as exc:  # noqa: BLE001
                log.exception("UIA 驱动初始化失败（只能读消息，不能发送）")
                self._set_status(last_error=f"UIA 初始化失败：{exc}")

            # The GUI wrapper is only a fallback for sending; building it also
            # calibrates the layout. Non-fatal if it fails.
            try:
                self._wx = WeChat()
                log.info("WeChat 封装就绪")
            except Exception as exc:  # noqa: BLE001
                log.warning("WeChat 封装初始化失败（不影响 UIA 发送）：%s", exc)

            while not self._stop.is_set():
                try:
                    seq, kind, args, box = self._cmds.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    if kind == "send":
                        box["result"] = self._do_send(args["chat"], args["text"])
                    elif kind == "send_image":
                        box["result"] = self._do_send_image(args["chat"], args["path"])
                    elif kind == "sessions":
                        box["result"] = self._do_sessions()
                    else:
                        box["result"] = None
                except Exception as exc:  # noqa: BLE001
                    log.exception("处理命令失败: %s", kind)
                    box["result"] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
                finally:
                    box["event"].set()
        except Exception as exc:  # noqa: BLE001
            log.exception("微信 4.x 引擎启动失败")
            self._set_status(
                connected=False,
                detail=f"启动失败：{exc}",
                last_error=str(exc)[:300],
            )
        finally:
            self._shutdown_listener()

    # ------------------------------------------------------------------ reading
    def _start_listener(self) -> None:
        from wechatauto.db import Listener

        self._listener = Listener(self._db, interval=self.poll_interval)
        self._listener.add_all(self._on_db_message, discover=True)
        self._listener.start()
        self._listening = True
        log.info("数据库监听已启动（间隔 %.1fs，自动发现新会话）", self.poll_interval)

    def _shutdown_listener(self) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:  # noqa: BLE001
            log.debug("停止监听失败", exc_info=True)
        self._listener = None

    def _display(self, username: str, fallback: str = "") -> str:
        if not username:
            return fallback
        name = self._names.get(username)
        if name:
            return name
        if username.endswith(CHATROOM_SUFFIX):
            # Group names live in the message index / contact tables, not in
            # the plain nickname map.
            try:
                if self._db is not None:
                    resolved = self._db.group_id_to_name(username)
                    if resolved:
                        self._names[username] = str(resolved)
                        return str(resolved)
            except Exception:  # noqa: BLE001
                log.debug("群名解析失败：%s", username, exc_info=True)
            return fallback or username
        return fallback or username

    def _refresh_names(self) -> None:
        """Merge nickname map + per-chat display names (covers groups)."""
        names: dict[str, str] = {}
        try:
            if self._db is not None:
                names.update({str(k): str(v) for k, v in (self._db.nickname_map() or {}).items() if v})
        except Exception:  # noqa: BLE001
            log.debug("nickname_map 失败", exc_info=True)
        try:
            if self._db is not None:
                for item in self._db.list_message_chats() or []:
                    username = str(item.get("username") or "")
                    name = str(item.get("name") or "")
                    if username and name:
                        names[username] = name
        except Exception:  # noqa: BLE001
            log.debug("list_message_chats 失败", exc_info=True)
        if names:
            self._names = names
            log.info("会话名称表已更新：%d 条", len(names))

    def _on_db_message(self, payload: Any, _listener: Any = None) -> None:
        try:
            msgs = payload if isinstance(payload, list) else [payload]
            for msg in msgs:
                if isinstance(msg, dict):
                    self._handle_row(msg)
        except Exception:  # noqa: BLE001
            log.exception("处理数据库消息失败")

    def _handle_row(self, row: dict[str, Any]) -> None:
        created = float(row.get("create_time") or 0)
        if created and self._started_at and created < self._started_at - 120:
            return  # 启动前的历史消息不回复

        chat_username = str(
            row.get("chat_username") or row.get("username") or row.get("chat") or ""
        )
        chat_fallback = str(row.get("chat_name") or row.get("name") or "")
        chat = self._display(chat_username, chat_fallback)

        sender_username = str(row.get("sender_username") or "")
        msg_type = str(row.get("type") or "text")
        content = str(row.get("content") or "")

        if sender_username and sender_username == self._self_username:
            # Our own message (bot or human) — used for human-takeover detection.
            if self.on_self_message:
                try:
                    self.on_self_message(chat, content)
                except Exception:  # noqa: BLE001
                    log.exception("on_self_message 回调异常")
            return

        image_types = {"图片", "动画表情", "image", "sticker", "emoji"}
        if msg_type in image_types:
            self._handle_image_row(row, chat_username, chat, sender_username, msg_type)
            return
        if msg_type not in TEXT_TYPES:
            log.debug("跳过非文本消息：%s type=%r", chat, msg_type)
            return
        if not content.strip():
            return

        is_group = chat_username.endswith(CHATROOM_SUFFIX)
        sender = self._display(sender_username, sender_username) if sender_username else chat
        my_names = self.my_names()
        at_me = is_group and any(n and f"@{n}" in content for n in my_names)
        at_all = is_group and ("@所有人" in content or "@All" in content)

        incoming = IncomingMessage(
            chat=chat,
            chat_type="group" if is_group else "friend",
            sender=sender,
            content=content,
            msg_type="text",
            raw_id=f"{row.get('shard','')}-{row.get('local_id','')}",
            at_me=at_me,
            at_all=at_all,
            timestamp=created or time.time(),
        )
        try:
            self.on_message(incoming)
        except Exception:  # noqa: BLE001
            log.exception("on_message 回调异常")

    # ------------------------------------------------------------------ actions
    @staticmethod
    def _find_main_window() -> int:
        """Largest visible WeChat Qt window (independent of library internals)."""
        user32 = ctypes.windll.user32
        best = 0
        best_area = 0
        enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def cb(hwnd, _lparam):  # noqa: ANN001
            nonlocal best, best_area
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                if not cls.value.startswith("Qt5"):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                title = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(hwnd, title, n + 1)
                if "微信" not in title.value and "Weixin" not in title.value:
                    return True
                rect = wt.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                if area > best_area:
                    best, best_area = int(hwnd), area
            except Exception:  # noqa: BLE001
                pass
            return True

        user32.EnumWindows(enum_proc(cb), 0)
        return best

    def _ensure_window_on_screen(self) -> str:
        """Make sure the WeChat main window is fully on the primary screen.

        UIA clicks are derived from the window rectangle; a window dragged (or
        left) partly off-screen makes every click miss, and `open_chat` then
        fails with "侧栏未确认打开".
        """
        try:
            user32 = ctypes.windll.user32
            hwnd = self._find_main_window()
            if not hwnd:
                return "no-window"
            rect = wt.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return "rect-failed"
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width <= 200 or height <= 200:
                return f"too-small {width}x{height}"
            inside = (
                rect.left >= 0
                and rect.top >= 0
                and rect.right <= screen_w
                and rect.bottom <= screen_h
            )
            if inside:
                return f"ok ({rect.left},{rect.top}) {width}x{height}"
            new_w = min(width, max(1000, screen_w - 160))
            new_h = min(height, max(700, screen_h - 160))
            x = max(20, (screen_w - new_w) // 2)
            y = max(20, (screen_h - new_h) // 2)
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.MoveWindow(hwnd, int(x), int(y), int(new_w), int(new_h), True)
            log.info(
                "微信窗口原本越界 (%s,%s,%sx%s)，已移回 (%s,%s) %sx%s",
                rect.left, rect.top, width, height, int(x), int(y), int(new_w), int(new_h),
            )
            return f"moved to ({int(x)},{int(y)}) {int(new_w)}x{int(new_h)}"
        except Exception as exc:  # noqa: BLE001
            log.warning("检查微信窗口位置失败：%s", exc)
            return f"error {exc}"

    @staticmethod
    def _raise_window(hwnd: int) -> bool:
        """Bring a window to the foreground WITHOUT touching the mouse."""
        if not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            if user32.GetForegroundWindow() == hwnd:
                return True
            cur_thread = kernel32.GetCurrentThreadId()
            fg = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = False
            if fg_thread and fg_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(fg_thread, cur_thread, False)
            return user32.GetForegroundWindow() == hwnd
        except Exception:  # noqa: BLE001
            return False

    def _reset_chat_state(self, uia) -> None:
        """Clear a stuck search box and close any popup.

        When a search leaves text behind and the chat never opens, WeChat's main
        pane stays blank and every later send times out. Esc + empty the search
        box brings it back without touching the mouse.
        """
        try:
            win = getattr(uia, "_win", None)  # noqa: SLF001
            if win is not None:
                win.SendKeys("{ESC}")
                time.sleep(0.2)
                win.SendKeys("{ESC}")
                time.sleep(0.2)
            box = uia._search_box(win, expand=True) if win is not None else None  # noqa: SLF001
            if box is not None:
                uia._set_text(box, "")  # noqa: SLF001
                time.sleep(0.3)
                win.SendKeys("{ESC}")
                time.sleep(0.3)
            log.info("已复位微信搜索框/弹窗状态")
        except Exception:  # noqa: BLE001
            log.debug("复位微信状态失败", exc_info=True)



    def _hide_viewer(self) -> None:
        """动鼠标/按键之前先藏分身窗口，否则 RDP 会把光标映射到用户真实鼠标上。"""
        try:
            from .. import childsession as _cs

            if _cs.is_child_session():
                _cs.hide_viewer()
        except Exception:  # noqa: BLE001
            pass

    def _open_chat_by_click(self, chat: str) -> bool:
        """用「搜索过滤 + 逐行点击」切换会话。

        微信 4.x 的会话列表是自绘的，UIA 里根本没有列表项（实测只有导航按钮），
        所以只能按坐标点。先输关键字过滤，目标会排在前面，然后从列表顶部往下
        逐行试点，直到 current_chat() 变成目标为止。
        """
        uia = self._uia
        self._hide_viewer()
        if uia is None:
            return False
        win = getattr(uia, "_win", None)
        if win is None:
            return False
        try:
            rect = win.BoundingRectangle
            left, top = rect.left, rect.top
            width, height = rect.right - rect.left, rect.bottom - rect.top
        except Exception:  # noqa: BLE001
            return False

        box = None
        try:
            box = uia._search_box(win, expand=True)  # noqa: SLF001
            if box is not None:
                from .. import keyinput

                try:
                    box.SetFocus()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
                keyinput.select_all()
                keyinput.type_text(chat)
                time.sleep(1.3)
                keyinput.press_enter()
                time.sleep(1.2)
        except Exception:  # noqa: BLE001
            box = None

        user32 = ctypes.windll.user32
        x = int(left + width * 0.18)
        for frac in (0.30, 0.32, 0.34, 0.36, 0.38, 0.40, 0.42):
            y = int(top + height * frac)
            try:
                user32.SetCursorPos(x, y)
                time.sleep(0.1)
                user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                time.sleep(0.06)
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                time.sleep(1.1)
                if uia.current_chat() == chat:
                    log.info("点击列表切换会话成功：%s（y=%.2f）", chat, frac)
                    return True
            except Exception:  # noqa: BLE001
                continue
        # 点不出来就把搜索框清掉，别让它挡住界面
        try:
            if box is not None:
                uia._set_text(box, "")  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        return False

    def _silent_send(self, chat: str, text: str) -> tuple[bool, str]:
        """Send without moving the mouse and without minimizing other windows.

        Mouse-free path: UIA ValuePattern writes the search box / chat input,
        Enter is the only keystroke. We do have to raise WeChat briefly because
        key injection targets the foreground window, but the cursor is never
        touched and the previously focused window is restored right after.
        """
        uia = self._uia
        self._hide_viewer()
        if uia is None:
            return False, "UIA 未就绪"
        user32 = ctypes.windll.user32
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        saved_cursor = (int(pt.x), int(pt.y))
        prev_fg = int(user32.GetForegroundWindow() or 0)
        hwnd = self._find_main_window()
        if not hwnd:
            return False, "找不到微信窗口"
        try:
            self._raise_window(hwnd)
            try:
                uia._win = uia._find_main() or uia.ControlFromHandle(hwnd)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                return False, "拿不到主窗口控件"
            if getattr(uia, "_win", None) is None:
                return False, "控件树不可用"

            if uia.current_chat() != chat:
                self._reset_chat_state(uia)
                box = uia._search_box(uia._win, expand=True)  # noqa: SLF001
                if box is None:
                    return False, "找不到搜索框"
                # 微信只认真实键盘事件：ValuePattern 写进去不会触发搜索
                from .. import keyinput

                try:
                    box.SetFocus()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.25)
                keyinput.select_all()
                keyinput.type_text(chat)
                time.sleep(0.9)
                keyinput.press_enter()
                time.sleep(1.2)
                opened = uia.current_chat()
                if opened != chat:
                    # UIA 里没有会话列表，搜索回车经常不生效 → 按坐标点第一行
                    if self._open_chat_by_click(chat):
                        opened = uia.current_chat()
                    if opened != chat:
                        self._reset_chat_state(uia)
                        return False, f"会话没打开（当前={opened!r}，已尝试点击列表）"

            edit = uia._chat_input()  # noqa: SLF001
            if edit is None:
                return False, "找不到聊天输入框"
            if not uia._set_text(edit, text):  # noqa: SLF001
                return False, "输入框写值失败"
            time.sleep(0.25)
            if not uia._read_edit_value(edit):  # noqa: SLF001
                return False, "内容没进输入框"
            edit.SendKeys("{ENTER}")
            time.sleep(0.6)
            return True, "已发送（静默模式：未移动鼠标）"
        except Exception as exc:  # noqa: BLE001
            return False, f"静默发送异常：{type(exc).__name__}: {exc}"
        finally:
            try:
                user32.SetCursorPos(saved_cursor[0], saved_cursor[1])
            except Exception:  # noqa: BLE001
                pass
            if prev_fg and prev_fg != hwnd:
                self._raise_window(prev_fg)

    def _do_send_image(self, chat: str, path: str) -> dict[str, Any]:
        """把本地图片发给某个会话：剪贴板塞图 → Ctrl+V → Enter。

        走的是「纯键盘 + 剪贴板」路线：不点鼠标、不依赖 UIA ValuePattern，
        微信会自动把它当图片消息插入。
        """
        self._hide_viewer()
        from .. import media as media_mod
        from pathlib import Path as _Path

        path = str(path or "").strip()
        if not path:
            return {"ok": False, "detail": "没有指定图片路径"}
        if not _Path(path).exists():
            return {"ok": False, "detail": f"图片不存在：{path}"}
        if not media_mod.is_image_file(path):
            return {"ok": False, "detail": "只支持 jpg / png / gif / webp 图片"}

        # 首选库自带的发送器
        gui = getattr(self._wx, "_gui", None) if self._wx is not None else None
        if gui is not None:
            try:
                resp = gui.send_image(path, who=chat, verify=True)
                if bool(resp):
                    log.info("已发送图片（库路径）：%s → %s", _Path(path).name, chat)
                    return {"ok": True, "detail": "已发送（图片，库路径）"}
                log.warning("库发送图片失败（%s），改用剪贴板路线", str(resp)[:120])
            except Exception:  # noqa: BLE001
                log.warning("库发送图片异常，改用剪贴板路线", exc_info=True)

        uia = self._uia
        hwnd = self._find_main_window()
        if uia is None or not hwnd:
            return {"ok": False, "detail": "找不到微信窗口"}

        user32 = ctypes.windll.user32
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        saved_cursor = (int(pt.x), int(pt.y))
        prev_fg = int(user32.GetForegroundWindow() or 0)
        try:
            self._raise_window(hwnd)
            try:
                uia._win = uia._find_main() or uia.ControlFromHandle(hwnd)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                return {"ok": False, "detail": "拿不到主窗口控件"}
            if getattr(uia, "_win", None) is None:
                return {"ok": False, "detail": "控件树不可用"}

            if uia.current_chat() != chat:
                if not self._open_chat_by_click(chat):
                    return {"ok": False, "detail": f"会话没打开（当前={uia.current_chat()!r}）"}

            if not media_mod.copy_image_for_paste(path):
                return {"ok": False, "detail": "复制图片到剪贴板失败"}

            edit = uia._chat_input()  # noqa: SLF001
            if edit is None:
                return {"ok": False, "detail": "找不到聊天输入框"}
            edit.SendKeys("{CTRL}V")
            time.sleep(1.2)
            edit.SendKeys("{ENTER}")
            time.sleep(0.8)
            log.info("已发送图片：%s → %s", _Path(path).name, chat)
            return {"ok": True, "detail": "已发送（图片，剪贴板路线）"}
        except Exception as exc:  # noqa: BLE001
            log.exception("发送图片异常")
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                user32.SetCursorPos(saved_cursor[0], saved_cursor[1])
            except Exception:  # noqa: BLE001
                pass
            if prev_fg and prev_fg != hwnd:
                self._raise_window(prev_fg)


    def _do_send(self, chat: str, text: str) -> dict[str, Any]:
        chat = (chat or "").strip()
        text = (text or "").strip()
        if not chat:
            return {"ok": False, "detail": "没有指定发送对象"}
        if not text:
            return {"ok": False, "detail": "发送内容为空"}

        # 1) 静默模式（默认）：全程不移动鼠标、不最小化任何窗口，
        #    只在打字期间短暂把微信提到前台，发完立刻还原你原来的窗口。
        if self.silent:
            ok, detail = self._silent_send(chat, text)
            if ok:
                return {"ok": True, "detail": detail}
            log.warning("静默发送未成功（%s），回退到点击式发送", detail)

        # 2) 回退：点击式 UIA 路径（会移动鼠标、抬升窗口）
        uia = self._uia
        if uia is not None:
            gui = getattr(self._wx, "_gui", None)
            for attempt in range(2):
                try:
                    if gui is not None:
                        try:
                            gui.ensure_visible()
                        except Exception:  # noqa: BLE001
                            log.debug("抬升微信窗口失败", exc_info=True)
                    log.info("发送前窗口位置：%s", self._ensure_window_on_screen())
                    if not uia.ensure_window(timeout=20):
                        log.warning("UIA 窗口不可用（第 %d 次）", attempt + 1)
                        time.sleep(1.0)
                        continue
                    try:
                        current = uia.current_chat()
                    except Exception:  # noqa: BLE001
                        current = None
                    log.info("发送诊断：当前会话=%r 目标=%r", current, chat)
                    if current == chat or uia.open_chat(chat):
                        sent = uia.send_text(text)
                        log.info("发送诊断：send_text=%s", sent)
                        if sent:
                            return {"ok": True, "detail": "已发送（UIA 路径）"}
                    else:
                        log.warning("发送诊断：open_chat 失败")
                    log.warning("UIA 发送未确认成功（第 %d 次）：%s", attempt + 1, chat)
                except Exception as exc:  # noqa: BLE001
                    log.warning("UIA 发送异常（第 %d 次）：%s", attempt + 1, exc)
                time.sleep(1.0)
            try:
                if gui is not None:
                    gui.release_foreground()
            except Exception:  # noqa: BLE001
                pass
            log.warning("UIA 两轮都没成功，改用库的 SendMsg 兜底：%s", chat)

        # 2) 兜底：库封装的 SendMsg（内部可能走 OCR，缺 winsdk 时会失败）
        if self._wx is None:
            return {"ok": False, "detail": "微信 UIA 驱动未就绪（无法发送）"}
        try:
            resp = self._wx.SendMsg(text, chat)
            ok = bool(getattr(resp, "is_success", False))
            message = getattr(resp, "message", "") or str(resp)
            return {"ok": ok, "detail": message[:200]}
        except Exception as exc:  # noqa: BLE001
            log.exception("发送失败：%s", chat)
            return {"ok": False, "detail": f"发送异常：{type(exc).__name__}: {exc}"}

    def _do_sessions(self) -> list[dict]:
        if self._db is None:
            return []
        out: list[dict] = []
        try:
            if not self._names:
                self._names = self._db.nickname_map() or {}
            for item in self._db.get_sessions(limit=200):
                username = str(item.get("username") or "")
                if not username:
                    continue
                display = self._display(username, str(item.get("remark") or item.get("nick_name") or ""))
                out.append(
                    {
                        "name": display,
                        "username": username,
                        "type": "group" if username.endswith(CHATROOM_SUFFIX) else "friend",
                        "unread": int(item.get("unread") or 0),
                        "last": "",
                        "time": "",
                    }
                )
        except Exception:  # noqa: BLE001
            log.warning("读取会话列表失败", exc_info=True)
        return out

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





