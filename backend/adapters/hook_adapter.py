"""WeChat 4.x adapter that talks to the injected hook DLL (version.dll).

The DLL (vendor/wechathook) hooks Weixin.dll inside the WeChat process and
exposes a local HTTP API on 127.0.0.1:30001:

    POST /SendTextMsg   {"wxidorgid": "<wxid|chatroom id>", "msg": "..."}
    POST /GetContacts   {"offset":0,"limit":2000}
    POST /GetSelfProfile {}
    GET  /QueryDB/status

It also **pushes** every received message to the URL configured in
`<WeChat dir>\\wx.ini` (`[wx] recv_url`). This adapter runs that webhook itself
on 127.0.0.1:18080 and turns pushes into IncomingMessage events.

No UI automation at all: no mouse, no keyboard, no focus stealing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from ..rules import IncomingMessage
from .base import BaseAdapter

log = logging.getLogger(__name__)

CHATROOM_SUFFIX = "@chatroom"
HOOK_API = "http://127.0.0.1:30001"
WEBHOOK_PORT = 18080  # must match wx.ini [wx] recv_url and the launcher CallBackURL
TEXT_MSGTYPES = {1}  # 1 = 文本; everything else is skipped


class _WebhookHandler(BaseHTTPRequestHandler):
    adapter: "HookAdapter" = None  # type: ignore[assignment]

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            if isinstance(payload, dict):
                self.adapter._on_webhook(payload)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            log.exception("处理消息推送失败")
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:  # noqa: BLE001
                pass

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok","service":"ai-dafeiyu-hook"}')

    def log_message(self, *_args) -> None:  # silence per-request logs
        return


class HookAdapter(BaseAdapter):
    name = "hook"

    def __init__(
        self,
        on_message,
        *,
        on_self_message=None,
        poll_interval: float = 1.0,
        api: str = HOOK_API,
        webhook_port: int = WEBHOOK_PORT,
    ) -> None:
        super().__init__(on_message)
        self.on_self_message = on_self_message
        self.poll_interval = poll_interval
        self.api = api.rstrip("/")
        self.webhook_port = webhook_port

        self._http = httpx.Client(base_url=self.api, timeout=30.0)
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._names: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self._self_wxid = ""
        self._started_at = 0.0
        self._refresher: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ================================================================== public
    def start(self, listen_chats: list[str]) -> None:
        self.set_listen(listen_chats)
        self._stop.clear()
        self._start_webhook()
        self._set_status(connected=False, detail="正在连接 Hook 接口…")
        threading.Thread(target=self._boot, name="hook-boot", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        server = self._server
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._server = None
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass
        self._set_status(connected=False, detail="已停止")

    def set_listen(self, chats: list[str]) -> None:
        # The hook pushes every message; filtering happens in the pipeline.
        self._set_status(listening=list(chats))

    def send_text(self, chat: str, text: str, timeout: float = 30.0) -> tuple[bool, str]:
        chat = (chat or "").strip()
        text = (text or "").strip()
        if not chat or not text:
            return False, "接收人或内容为空"
        target = self._resolve_id(chat)
        if not target:
            return False, f"找不到会话「{chat}」对应的 wxid（可能还没同步到联系人）"
        try:
            resp = self._http.post("/SendTextMsg", json={"wxidorgid": target, "msg": text})
            data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001
            return False, f"调用发送接口失败：{type(exc).__name__}: {exc}"
        if data.get("ret") == 0 and data.get("queued"):
            return True, "已发送（Hook 直连，后台无干扰）"
        return False, f"发送被拒绝：{data}"

    def sessions(self, timeout: float = 30.0) -> list[dict]:
        self._refresh_contacts()
        out: list[dict] = []
        with self._lock:
            for wxid, name in self._names.items():
                out.append(
                    {
                        "name": name,
                        "username": wxid,
                        "type": "group" if wxid.endswith(CHATROOM_SUFFIX) else "friend",
                        "unread": 0,
                        "last": "",
                        "time": "",
                    }
                )
        out.sort(key=lambda s: s["name"])
        return out

    def contact_names(self, timeout: float = 30.0) -> list[str]:
        return [s["name"] for s in self.sessions(timeout)]

    # =============================================================== internals
    def _start_webhook(self) -> None:
        if self._server is not None:
            return
        handler = type("_H", (_WebhookHandler,), {"adapter": self})
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.webhook_port), handler)
        except Exception as exc:  # noqa: BLE001
            log.error("消息接收端口 %s 绑定失败：%s", self.webhook_port, exc)
            self._set_status(last_error=f"webhook 端口占用：{exc}")
            return
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, name="hook-webhook", daemon=True
        )
        self._server_thread.start()
        log.info("消息接收服务已启动：http://127.0.0.1:%s/callback", self.webhook_port)

    def _boot(self) -> None:
        """Verify the hook API, read self profile and contact names."""
        deadline = time.time() + 30
        last = ""
        while time.time() < deadline and not self._stop.is_set():
            try:
                status = self._http.get("/QueryDB/status").json()
                login = int(status.get("IsLogin") or 0)
                profile = {}
                try:
                    profile = self._http.post("/GetSelfProfile", json={}).json()
                except Exception:  # noqa: BLE001
                    pass
                self._self_wxid = str(
                    profile.get("wxid") or profile.get("self_wxid") or status.get("SelfWxid") or ""
                )
                nick = str(profile.get("nick_name") or profile.get("nickname") or "")
                self._refresh_contacts()
                self._started_at = time.time()
                self._set_status(
                    connected=True,
                    detail=(
                        f"已连接微信 Hook（{'已登录' if login else '未登录'}）"
                        + (f"：{nick}" if nick else "")
                    ),
                    my_names=[n for n in [nick] if n],
                    wechat_version="4.1.10.27 + hook",
                    last_error="",
                )
                self._start_name_refresher()
                return
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(2)
        self._set_status(
            connected=False,
            detail="连不上 Hook 接口（127.0.0.1:30001）。请确认已注入 version.dll 且微信在运行。",
            last_error=last,
        )

    def _start_name_refresher(self) -> None:
        if self._refresher and self._refresher.is_alive():
            return

        def loop() -> None:
            while not self._stop.wait(300):
                try:
                    self._refresh_contacts()
                except Exception:  # noqa: BLE001
                    log.debug("刷新联系人失败", exc_info=True)

        self._refresher = threading.Thread(target=loop, name="hook-names", daemon=True)
        self._refresher.start()

    def _refresh_contacts(self) -> None:
        try:
            data = self._http.post("/GetContacts", json={"offset": 0, "limit": 4096}).json()
        except Exception:  # noqa: BLE001
            return
        contacts = data.get("contacts") or []
        names: dict[str, str] = {}
        for item in contacts:
            if not isinstance(item, dict):
                continue
            wxid = str(item.get("username") or item.get("UserName") or "")
            if not wxid:
                continue
            display = str(
                item.get("remark")
                or item.get("nick_name")
                or item.get("NickName")
                or item.get("alias")
                or ""
            ).strip()
            if display:
                names[wxid] = display
        if names:
            with self._lock:
                self._names = names
                self._ids = {}
                for wxid, name in names.items():
                    self._ids.setdefault(name, wxid)
            log.info("联系人名称表已更新：%d 条", len(names))

    def _resolve_id(self, chat: str) -> str:
        """Display name (or raw wxid) -> wxid usable by /SendTextMsg."""
        chat = chat.strip()
        if chat.endswith(CHATROOM_SUFFIX) or chat.startswith("wxid_"):
            return chat
        with self._lock:
            hit = self._ids.get(chat)
        if hit:
            return hit
        self._refresh_contacts()
        with self._lock:
            return self._ids.get(chat, "")

    def _display(self, wxid: str) -> str:
        if not wxid:
            return ""
        with self._lock:
            return self._names.get(wxid, wxid)

    def _on_webhook(self, payload: dict[str, Any]) -> None:
        selfwxid = str(payload.get("selfwxid") or self._self_wxid or "")
        for item in payload.get("msglist") or []:
            if not isinstance(item, dict):
                continue
            try:
                self._handle_item(item, selfwxid)
            except Exception:  # noqa: BLE001
                log.exception("解析推送消息失败")

    def _handle_item(self, item: dict[str, Any], selfwxid: str) -> None:
        msg_type = int(item.get("msgtype") or 0)
        content = str(item.get("msg") or "")
        fromid = str(item.get("fromid") or "")
        room = str(item.get("room") or "")
        sender_id = str(item.get("sender") or fromid)

        if fromid and selfwxid and fromid == selfwxid:
            if self.on_self_message:
                try:
                    self.on_self_message(self._display(room) or self._display(fromid), content)
                except Exception:  # noqa: BLE001
                    log.exception("on_self_message 回调异常")
            return
        if msg_type not in TEXT_MSGTYPES:
            log.debug("跳过非文本消息：type=%s", msg_type)
            return
        if not content.strip():
            return

        is_group = bool(room)
        chat = self._display(room) if is_group else self._display(fromid)
        sender = self._display(sender_id) if sender_id else chat
        my_names = self.my_names()
        at_me = is_group and any(n and f"@{n}" in content for n in my_names)
        at_all = is_group and ("@所有人" in content or "@All" in content)

        self.on_message(
            IncomingMessage(
                chat=chat,
                chat_type="group" if is_group else "friend",
                sender=sender,
                content=content,
                msg_type="text",
                raw_id=f"{item.get('msgsvrid','')}-{item.get('time','')}",
                at_me=at_me,
                at_all=at_all,
                timestamp=time.time(),
            )
        )
