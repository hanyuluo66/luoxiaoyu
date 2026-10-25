"""Runtime orchestrator: owns the adapter, the pipeline and the shared state."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from .adapters import build_adapter
from .adapters.base import BaseAdapter
from .childsession import keeper_command
from .models import AppConfig
from .paths import data_dir
from .pipeline import ReplyPipeline
from .rules import IncomingMessage, chat_in
from .store import ConfigStore, MessageStore

log = logging.getLogger(__name__)


class BotRuntime:
    """Single owner of the running bot. Thread-safe public surface."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.config_store = ConfigStore()
        self.store = MessageStore()
        self.pipeline = ReplyPipeline(self)

        self.adapter: BaseAdapter | None = None
        self._adapter_kind = ""
        self._running = False
        self.started_at = 0.0
        # 桌面分身状态（安全模式下 ready=False 并带原因，控制台据此显示横幅）
        self.child_desktop_state: dict[str, Any] = {"ready": True, "reason": ""}
        # 启动进度（0-100）与当前步骤，控制台用来显示进度条
        self.startup_progress: dict[str, Any] = {"percent": 100, "step": "", "detail": ""}
        self._notifications: deque[dict[str, Any]] = deque(maxlen=300)
        self._discovered: list[str] = []
        self._refresher: threading.Thread | None = None
        self._refresh_stop = threading.Event()

    # ------------------------------------------------------------------ config
    @property
    def config(self) -> AppConfig:
        return self.config_store.config

    def notify(
        self, message: str, *, kind: str = "info", level: str = "info"
    ) -> None:
        entry = {
            "ts": time.time(),
            "kind": kind,
            "level": level,
            "message": message,
        }
        with self._lock:
            self._notifications.appendleft(entry)
        if level == "debug":
            log.debug(message)
        elif level in ("error", "warn"):
            self.store.add_event(kind, message, level)
            log.warning(message)
        else:
            self.store.add_event(kind, message, level)
            log.info(message)

    # ------------------------------------------------------------------- start
    def _effective_adapter(self) -> str:
        """Self-heal an impossible engine choice.

        `wxauto` only works with WeChat 3.9.x. If the machine has WeChat 4.x
        installed (process `Weixin.exe`, not `WeChat.exe`) that choice can never
        work, and a stale console tab used to be able to force it back — which
        looked like "连不上微信 / 3.9.11.17 版本" out of nowhere.
        """
        kind = self.config.adapter
        if kind != "wxauto":
            return kind
        try:
            import psutil

            names = {
                (p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])
            }
            if "weixin.exe" in names and "wechat.exe" not in names:
                self.notify(
                    "检测到微信是 4.x 版本（Weixin.exe），旧的 3.9 引擎不可用，已自动切换到 4.x 引擎",
                    kind="config",
                    level="warn",
                )
                data = self.config_store.snapshot()
                data["adapter"] = "wechatauto"
                self.config_store.save(data)
                return "wechatauto"
        except Exception:  # noqa: BLE001
            log.debug("自动判断微信版本失败", exc_info=True)
        return kind

    def start(self) -> None:
        """Start the bot — but never on the user's own desktop by accident.

        本机模式会操作主桌面上的微信（抢焦点、动鼠标）。只要开着「桌面分身」又没
        允许回退，就拒绝在这里启动，返回清晰的失败原因，让用户去修分身。
        """
        cfg = self.config
        try:
            from . import childsession as _cs

            if (
                cfg.bot.child_desktop
                and not cfg.bot.allow_local_fallback
                and not _cs.is_child_session()
                and cfg.bot.enabled
            ):
                message = (
                    "桌面分身不可用，本机启动会抢你的鼠标和焦点，已拒绝。\n"
                    "请先修好分身登录（系统设置 → 桌面分身登录 → 保存凭据），再点托盘「重建桌面分身」。"
                )
                log.warning("拒绝在本机启动机器人：桌面分身未就绪")
                self.notify(message, level="error", kind="error")
                raise RuntimeError(message)
        except ImportError:  # noqa: BLE001
            pass
        except RuntimeError:
            raise
        with self._lock:
            if self._running:
                return
            kind = self._effective_adapter()
            cfg = self.config
            self._teardown_adapter()
            self.adapter = build_adapter(
                kind,
                self._on_incoming,
                on_self_message=self._on_self_message,
                poll_interval=cfg.bot.poll_interval,
            )
            self._adapter_kind = kind
            listen = self._compute_listen()
            self.pipeline.start()
            self.adapter.start(listen)
            self._running = True
            self.started_at = time.time()
            self._start_refresher()
            # "全部私聊 / 排除名单" modes need the session list, otherwise the
            # console shows "监听 0 个会话" for a minute and looks broken.
            needs_discovery = self._needs_discovery()
            adapter = self.adapter
        if needs_discovery:
            try:
                self.refresh_sessions()
                listen = self._compute_listen()
                if adapter:
                    adapter.set_listen(listen)
            except Exception:  # noqa: BLE001
                log.debug("启动时刷新会话列表失败", exc_info=True)
        self.notify(
            f"落小鱼已启动（{'模拟模式' if kind == 'mock' else '微信模式'}），监听 {len(listen)} 个会话",
            kind="start",
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._refresh_stop.set()
            adapter = self.adapter
        self.pipeline.stop()
        if adapter:
            adapter.stop()
        self.notify("落小鱼已停止", kind="stop")

    def restart(self) -> None:
        self.stop()
        self.start()

    def apply_config(self) -> None:
        """Push the current config into the running bot (hot reload)."""
        cfg = self.config
        with self._lock:
            running = self._running
            kind_changed = running and cfg.adapter != self._adapter_kind
            adapter = self.adapter
        if not running:
            return
        if kind_changed:
            self.restart()
            self.notify("切换接入模式，已重启", kind="config")
            return
        listen = self._compute_listen()
        if adapter:
            adapter.set_listen(listen)
            try:
                adapter.poll_interval = cfg.bot.poll_interval  # type: ignore[attr-defined]
            except AttributeError:
                pass
        self.notify(f"配置已生效，正在监听 {len(listen)} 个会话", kind="config")

    def save_config(self, payload: dict[str, Any]) -> AppConfig:
        cfg = self.config_store.save(payload)
        self.notify("配置已保存", kind="config")
        self.apply_config()
        return cfg

    # --------------------------------------------------------------- callbacks
    def _on_incoming(self, msg: IncomingMessage) -> None:
        cfg = self.config
        # Config lists win over UI guessing, so a mis-detected chat still behaves.
        if chat_in(cfg.group.chats, msg.chat):
            msg.chat_type = "group"
        elif chat_in(cfg.private.chats, msg.chat):
            msg.chat_type = "friend"
        self.pipeline.submit(msg)

    def _on_self_message(self, chat: str, content: str = "") -> None:
        self.pipeline.note_self_message(chat, content)

    # ------------------------------------------------------------------ status
    def my_names(self) -> list[str]:
        adapter = self.adapter
        if adapter:
            names = adapter.my_names()
            if names:
                return names
        return ["落小鱼"]

    def _hide_viewer_if_needed(self) -> None:
        """Hide the desktop-clone window while the bot is working.

        The RDP client mirrors the remote cursor onto the local pointer, so a
        visible clone window makes the mouse visibly jump during automation.
        """
        try:
            if not getattr(self.config.bot, "auto_hide_viewer", True):
                return
            keeper_command("hide")
        except Exception:  # noqa: BLE001
            pass

    def send_image(self, chat: str, path: str) -> tuple[bool, str]:
        adapter = self.adapter
        if not adapter:
            return False, "机器人还没启动"
        self._hide_viewer_if_needed()
        try:
            return adapter.send_image(chat, path)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    def send_text(self, chat: str, text: str) -> dict[str, Any]:
        adapter = self.adapter
        if not adapter:
            return {"ok": False, "detail": "机器人还没启动"}
        self._hide_viewer_if_needed()
        ok, detail = adapter.send_text(chat, text)
        if ok:
            try:
                self.pipeline.remember_sent(chat, text)   # 避免被当成"人工发言"触发接管
            except Exception:  # noqa: BLE001
                pass
        self.store.add_message(
            chat=chat,
            chat_type="friend",
            sender="小鱼",
            direction="out",
            content=text,
            status="ok" if ok else "failed",
            note=f"手动发送：{detail}",
        )
        return {"ok": ok, "detail": detail}

    def refresh_sessions(self) -> list[dict]:
        adapter = self.adapter
        if not adapter:
            return []
        sessions = adapter.sessions()
        if sessions:
            with self._lock:
                self._discovered = [s["name"] for s in sessions if s.get("name")]
        return sessions

    def set_startup_progress(self, percent: float, step: str = "", detail: str = "") -> None:
        self.startup_progress = {
            "percent": max(0.0, min(100.0, float(percent))),
            "step": step,
            "detail": detail,
        }

    def set_child_desktop_state(self, ready: bool, reason: str = "") -> None:
        """让控制台知道桌面分身能不能用（安全模式时必须写原因）。"""
        self.child_desktop_state = {"ready": bool(ready), "reason": reason or ""}
        if not ready and reason:
            self.notify(reason, level="error", kind="error")

    def status(self) -> dict[str, Any]:
        cfg = self.config
        with self._lock:
            adapter = self.adapter
            running = self._running
            notifications = list(self._notifications)[:40]
            listen = self._compute_listen()
        return {
            "running": running,
            "uptime": int(time.time() - self.started_at) if running and self.started_at else 0,
            "adapter": adapter.status() if adapter else {"connected": False, "detail": "未启动"},
            "pipeline": self.pipeline.state(),
            "listen": listen,
            "stats": self.store.stats(),
            "ai_configured": bool(cfg.ai.api_key.strip()),
            "adapter_kind": cfg.adapter,
            "data_dir": str(data_dir()),
            "notifications": notifications,
            "child_desktop": dict(self.child_desktop_state),
            "startup": dict(self.startup_progress),
        }

    # ------------------------------------------------------------------ listen
    def _compute_listen(self) -> list[str]:
        cfg = self.config
        discovered = list(self._discovered)
        chats: list[str] = []

        if cfg.bot.private_enabled:
            if cfg.private.mode == "include":
                chats.extend(cfg.private.chats)
            else:
                pool = [c for c in discovered if c not in cfg.group.chats]
                if cfg.private.mode == "exclude":
                    pool = [c for c in pool if not chat_in(cfg.private.chats, c)]
                chats.extend(pool)

        if cfg.bot.group_enabled:
            if cfg.group.mode == "include":
                chats.extend(cfg.group.chats)
            else:
                pool = [c for c in discovered if c in cfg.group.chats]
                if cfg.group.mode == "exclude":
                    pool = [c for c in pool if not chat_in(cfg.group.chats, c)]
                chats.extend(pool)

        cleaned = []
        for chat in dict.fromkeys(chats):
            chat = (chat or "").strip()
            if not chat:
                continue
            if chat_in(cfg.blacklist.chats, chat):
                continue
            cleaned.append(chat)
        return cleaned

    def _needs_discovery(self) -> bool:
        cfg = self.config
        return (
            (cfg.bot.private_enabled and cfg.private.mode != "include")
            or (cfg.bot.group_enabled and cfg.group.mode != "include")
        )

    def _start_refresher(self) -> None:
        self._refresh_stop.clear()
        if self._refresher and self._refresher.is_alive():
            return
        self._refresher = threading.Thread(
            target=self._refresh_loop, name="session-refresher", daemon=True
        )
        self._refresher.start()

    def _refresh_loop(self) -> None:
        while not self._refresh_stop.wait(90):
            if not self._running or not self._needs_discovery():
                continue
            try:
                self.refresh_sessions()
                self.apply_config()
            except Exception:  # noqa: BLE001
                log.debug("刷新会话列表失败", exc_info=True)

    def _teardown_adapter(self) -> None:
        adapter = self.adapter
        if adapter:
            try:
                adapter.stop()
            except Exception:  # noqa: BLE001
                log.debug("停止适配器失败", exc_info=True)
        self.adapter = None

    # ----------------------------------------------------------------- release
    def shutdown(self) -> None:
        try:
            self.stop()
        finally:
            self.store.close()
