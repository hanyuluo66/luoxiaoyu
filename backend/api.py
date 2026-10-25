"""HTTP API + static web console."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, paths
from .ai import AIError, AIClient, PROVIDER_PRESETS
from .runtime import BotRuntime

log = logging.getLogger(__name__)

# Ready-made personas for the console's 人设设置 page: one click fills
# nickname / speaking_style / catchphrase / taboos / persona.
PERSONA_PRESETS: list[dict[str, str]] = [
    {
        "id": "warm",
        "label": "温柔体贴",
        "nickname": "小鱼",
        "speaking_style": "短句、口语、语气温和，先共情再回答",
        "catchphrase": "别急，我在呢",
        "taboos": "不说教、不敷衍、不打探隐私",
        "persona": (
            "你是「小鱼」，一个替主人看微信的朋友。\n"
            "性格温和、耐心、有分寸，先理解对方情绪再回话。\n"
            "说话像真人在微信上聊天：短句、口语、不堆砌形容词，不用 Markdown。\n"
            "不确定的事就说不知道，绝不编造。"
        ),
    },
    {
        "id": "brief",
        "label": "简洁干脆",
        "nickname": "小鱼",
        "speaking_style": "一句话说完，能省就省，不寒暄",
        "catchphrase": "行",
        "taboos": "不啰嗦、不复述对方的话",
        "persona": (
            "你是「小鱼」，替主人回复微信。\n"
            "风格极简：一句话说清结论，能不解释就不解释。\n"
            "不用表情包、不用语气词、不用 Markdown，像快速回消息的人。"
        ),
    },
    {
        "id": "humor",
        "label": "幽默贫嘴",
        "nickname": "小鱼",
        "speaking_style": "轻松、爱接梗，偶尔自嘲，但不油腻",
        "catchphrase": "问题不大",
        "taboos": "不拿对方的痛处开玩笑、不刷屏",
        "persona": (
            "你是「小鱼」，替主人回微信，带点贫嘴的幽默感。\n"
            "会接梗、会自嘲，但先给有用的回答，再补一句玩笑。\n"
            "对方不开心时立刻收起玩笑，认真安抚。"
        ),
    },
    {
        "id": "pro",
        "label": "专业严谨",
        "nickname": "小鱼",
        "speaking_style": "条理清楚、用词准确，必要时分点说明",
        "catchphrase": "",
        "taboos": "不夸大、不许诺做不到的事",
        "persona": (
            "你是「小鱼」，以助理身份替主人回复微信。\n"
            "风格专业克制：先给结论，再给一两条关键理由。\n"
            "涉及时间、金额、承诺时格外谨慎，不确定就说明会转达给本人。"
        ),
    },
    {
        "id": "tsundere",
        "label": "傲娇嘴硬",
        "nickname": "小鱼",
        "speaking_style": "嘴上不饶人但会帮忙，句子短、带点吐槽",
        "catchphrase": "算了，我帮你看看",
        "taboos": "不人身攻击、不阴阳怪气过头",
        "persona": (
            "你是「小鱼」，替主人回微信，性格有点傲娇。\n"
            "嘴上会吐槽两句，但实际会认真帮忙、把事办明白。\n"
            "对方真的着急或难过时，立刻收起傲娇，正经说话。"
        ),
    },
]


def _is_local_host(host: str) -> bool:
    """Host 头必须是本机名：挡住 DNS rebinding（外部页面解析到 127.0.0.1）。"""
    name = (host or "").split(":")[0].strip().lower()
    return name in {"127.0.0.1", "localhost", "[::1]", "::1", ""}


def _install_local_only_guard(app) -> None:
    """拒绝跨站请求。

    控制台没有登录态，任何网页都能往 127.0.0.1 发请求（CSRF）；
    这里检查 Host 和 Origin：只接受本机来源，跨站的写操作直接 403。
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.headers.get("host", "")
        if not _is_local_host(host):
            return JSONResponse({"detail": "只接受本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            allowed = (
                origin.startswith("http://127.0.0.1")
                or origin.startswith("http://localhost")
                or origin.startswith("file://")
                or origin == "null"
            )
            if not allowed:
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        return await call_next(request)


def create_app(runtime: BotRuntime, on_shutdown=None):
    from fastapi import FastAPI

    app = FastAPI(title="落小鱼 控制台", version=__version__, docs_url="/api/docs")
    _install_local_only_guard(app)
    router = APIRouter(prefix="/api")

    # ------------------------------------------------------------------ basics
    @router.get("/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return {
            "version": __version__,
            "config": runtime.config_store.snapshot(),
            "status": runtime.status(),
            "presets": PROVIDER_PRESETS,
            "mock_supported": True,
        }

    @router.get("/status")
    def status() -> dict[str, Any]:
        return runtime.status()

    @router.put("/config")
    def save_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        # A stale browser tab used to be able to overwrite the whole document
        # (it silently switched the engine back to the old adapter). Reject any
        # write whose revision stamp is missing or is not the stored one — a page
        # loaded from an older build carries no stamp at all, which is exactly
        # the case that kept flipping the engine back.
        current = runtime.config_store.config
        incoming = float(payload.get("saved_at") or 0)
        if current.saved_at:
            if not incoming:
                raise HTTPException(
                    status_code=409,
                    detail="这个页面是旧版本打开的，缺少配置版本标记。请按 F5 刷新后重新设置",
                )
            if abs(incoming - current.saved_at) > 1e-6:
                raise HTTPException(
                    status_code=409,
                    detail="配置已在其他页面被修改，请按 F5 刷新本页面后重新设置",
                )
        try:
            cfg = runtime.save_config(payload)
        except Exception as exc:  # noqa: BLE001 - pydantic validation
            raise HTTPException(status_code=400, detail=f"配置不合法：{exc}") from exc
        return {"ok": True, "config": cfg.model_dump()}

    # --------------------------------------------------------------------- bot
    @router.post("/bot/start")
    def bot_start() -> dict[str, Any]:
        try:
            runtime.start()
        except RuntimeError as exc:
            return {"ok": False, "detail": str(exc), "status": runtime.status()}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "status": runtime.status(),
            }
        return {"ok": True, "status": runtime.status()}

    @router.post("/bot/stop")
    def bot_stop() -> dict[str, Any]:
        runtime.stop()
        return {"ok": True, "status": runtime.status()}

    @router.post("/bot/restart")
    def bot_restart() -> dict[str, Any]:
        runtime.restart()
        return {"ok": True, "status": runtime.status()}

    @router.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        """Quit the whole program (tray menu fallback)."""
        import threading

        def _do_exit() -> None:
            import time

            time.sleep(0.4)  # let the HTTP response go out first
            try:
                if on_shutdown is not None:
                    on_shutdown()
                else:
                    runtime.stop()
            except Exception:  # noqa: BLE001
                log.exception("退出时出错")

        threading.Thread(target=_do_exit, name="shutdown", daemon=True).start()
        return {"ok": True, "detail": "正在退出"}

    # ---------------------------------------------------------------------- ai
    @router.post("/ai/test")
    def ai_test(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        cfg = runtime.config.ai.model_copy(
            update={
                k: v
                for k, v in payload.items()
                if k in {"base_url", "api_key", "model", "provider"} and v not in (None, "")
            }
        )
        if not (cfg.api_key or "").strip():
            cfg.api_key = runtime.config.ai.api_key  # 前端不回传密钥，用已保存的
        try:
            return AIClient(cfg).test()
        except AIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/ai/models")
    def ai_models() -> dict[str, Any]:
        return {"presets": PROVIDER_PRESETS, "current": runtime.config.ai.model_dump()}

    @router.get("/persona")
    def persona_info() -> dict[str, Any]:
        """Default persona + ready-made presets for the 人设设置 page."""
        from .models import DEFAULT_PERSONA

        return {"default": DEFAULT_PERSONA, "presets": PERSONA_PRESETS}

    @router.post("/ai/preview")
    def ai_preview(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="请先输入一条测试消息")
        return runtime.pipeline.preview(
            chat=str(payload.get("chat") or "测试会话"),
            chat_type=str(payload.get("chat_type") or "friend"),
            sender=str(payload.get("sender") or "小明"),
            text=text,
        )

    # ------------------------------------------------------------------ wechat
    @router.get("/sessions")
    def sessions() -> dict[str, Any]:
        items = runtime.refresh_sessions()
        return {"ok": bool(items), "items": items}

    @router.post("/send")
    def send(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        chat = str(payload.get("chat") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not chat or not text:
            raise HTTPException(status_code=400, detail="接收人和内容都不能为空")
        return runtime.send_text(chat, text)

    @router.post("/chats/resume")
    def resume(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        chat = str(payload.get("chat") or "").strip()
        if not chat:
            raise HTTPException(status_code=400, detail="缺少会话名")
        runtime.pipeline.resume_chat(chat)
        return {"ok": True}

    # -------------------------------------------------------------------- logs
    @router.post("/debug/inject")
    def debug_inject(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Feed a synthetic inbound message through the real pipeline.

        Used to verify "receive -> decide -> AI -> send" without waiting for a
        real contact. Replies are really sent, so pass a safe chat such as
        文件传输助手 unless you mean it.
        """
        import time as _t

        from .rules import IncomingMessage

        content = str(payload.get("content") or "").strip()
        chat = str(payload.get("chat") or "").strip()
        if not content or not chat:
            raise HTTPException(status_code=400, detail="chat 和 content 都不能为空")
        runtime._on_incoming(  # noqa: SLF001 - deliberate debug hook
            IncomingMessage(
                chat=chat,
                chat_type=str(payload.get("chat_type") or "friend"),
                sender=str(payload.get("sender") or chat),
                content=content,
                msg_type=str(payload.get("msg_type") or "text"),
                image_path=str(payload.get("image_path") or ""),
                timestamp=_t.time(),
            )
        )
        return {"ok": True, "detail": "已注入，稍后到「消息日志」查看结果"}

    @router.get("/messages")
    def messages(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        chat: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return {"items": runtime.store.list_messages(limit=limit, offset=offset, chat=chat)}

    @router.delete("/messages")
    def clear_messages() -> dict[str, Any]:
        runtime.store.clear_messages()
        return {"ok": True}

    # ------------------------------------------------------------ 运行日志
    @router.get("/logs/files")
    def log_files() -> dict[str, Any]:
        from . import logview

        return {
            "items": logview.list_logs(paths.install_dir(), paths.data_dir()),
            "install_dir": str(paths.install_dir()),
            "data_dir": str(paths.data_dir()),
        }

    @router.get("/logs/tail")
    def log_tail(key: str = Query(...), lines: int = Query(default=200, ge=10, le=2000)) -> dict[str, Any]:
        from . import logview

        path = logview.find_log(paths.install_dir(), paths.data_dir(), key)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="这个日志文件不存在（可能还没生成）")
        return {
            "key": key,
            "name": path.name,
            "path": str(path),
            "text": logview.tail(path, lines),
        }

    @router.post("/logs/open")
    def log_open(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        from . import logview

        key = str((payload or {}).get("key") or "")
        if key:
            path = logview.find_log(paths.install_dir(), paths.data_dir(), key)
            if path is None:
                raise HTTPException(status_code=404, detail="这个日志文件不存在")
            target = path
        else:
            target = paths.data_dir() / "logs"
            target.mkdir(parents=True, exist_ok=True)
        opened = logview.open_folder(target)
        return {"ok": opened, "path": str(target)}

    @router.get("/logs/download")
    def log_download(key: str = Query(...)):
        from fastapi.responses import FileResponse

        from . import logview

        path = logview.find_log(paths.install_dir(), paths.data_dir(), key)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="这个日志文件不存在")
        return FileResponse(str(path), filename=path.name, media_type="text/plain")

    @router.get("/events")
    def events(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        return {"items": runtime.store.list_events(limit=limit)}

    # ------------------------------------------------------ 桌面分身登录状态
    @router.get("/childsession/login")
    def childsession_login_status() -> dict[str, Any]:
        """Tell the console whether the child session can log in on its own."""
        hello_only = None
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "DevicePasswordLessBuildVersion")
                hello_only = int(value) == 2
        except Exception:  # noqa: BLE001
            hello_only = None

        has_cred = False
        try:
            from . import spawn

            out = spawn.run(
                ["cmdkey", "/list"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            ).stdout or ""
            has_cred = "TERMSRV/localhost" in out
        except Exception:  # noqa: BLE001
            pass

        fixer = paths.install_dir() / "修复桌面分身登录.bat"
        return {
            "hello_only": hello_only,
            "has_credential": has_cred,
            "fixer_exists": fixer.exists(),
            "fixer_path": str(fixer),
            "ok": (hello_only is False) and has_cred,
        }

    @router.get("/childsession/startup")
    def childsession_startup_status() -> dict[str, Any]:
        """有没有被临时挂起的启动项（正常情况下应该一直是 0）。"""
        from . import startup_items as si

        return si.pending()

    @router.post("/childsession/startup/restore")
    def childsession_startup_restore() -> dict[str, Any]:
        from . import startup_items as si

        return {"restored": si.restore()}

    @router.post("/vision/describe")
    def vision_test(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """拿一张本地图片试识图（控制台「AI 接口」页的测试按钮用）。"""
        from pathlib import Path as _Path

        path = str(payload.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="请给出图片路径")
        image = _Path(path)
        if not image.exists() or not image.is_file():
            raise HTTPException(status_code=404, detail=f"图片不存在：{path}")
        cfg = runtime.config.ai
        if not cfg.vision_model.strip():
            raise HTTPException(
                status_code=400,
                detail="还没填「识图模型」。DeepSeek 的 deepseek-chat 已支持图片，填它即可",
            )
        try:
            text = AIClient(cfg).describe_image(str(image), str(payload.get("prompt") or ""))
        except AIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": bool(text), "text": text, "model": cfg.vision_model}

    @router.post("/childsession/credential")
    def childsession_credential(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """保存 localhost 的登录凭据（走 CredWrite，密码不进命令行）。"""
        from . import wincred

        user = str(payload.get("user") or "").strip()
        password = str(payload.get("password") or "")
        if not user or not password:
            raise HTTPException(status_code=400, detail="账户和密码都不能为空")
        ok = wincred.save(user, password)
        # 引擎要用「域 + 用户名」两段才能静默登录
        try:
            domain, _, short = user.rpartition("\\")
            (paths.install_dir() / "childsession-account.txt").write_text(
                f"domain={domain or 'MicrosoftAccount'}\nuser={short or user}\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": ok,
            "detail": "凭据已保存" if ok else "保存失败（可能是权限问题）",
        }

    @router.post("/childsession/viewer")
    def childsession_viewer(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        """显示/隐藏桌面分身窗口。

        分身窗口**可见时**，RDP 会把分身里的光标映射到本机鼠标上 ——
        机器人一动鼠标，用户看到的指针就会跟着乱跑。所以平时必须保持隐藏。
        """
        from . import childsession as cs

        action = str((payload or {}).get("action") or "hide").strip().lower()
        if action not in {"show", "hide"}:
            raise HTTPException(status_code=400, detail="action 只能是 show 或 hide")
        result = cs.keeper_command(action)
        return {
            "ok": result is not None,
            "action": action,
            "status": cs.keeper_status(),
        }

    @router.post("/childsession/open-fix")
    def childsession_open_fix() -> dict[str, Any]:
        """Open the one-click repair script (it raises its own UAC prompt)."""
        fixer = paths.install_dir() / "修复桌面分身登录.bat"
        if not fixer.exists():
            raise HTTPException(status_code=404, detail="找不到「修复桌面分身登录.bat」")
        from . import childsession as _cs

        ok, detail = _cs.open_on_console(fixer)
        return {"ok": ok, "detail": detail, "path": str(fixer)}

    # ------------------------------------------------------------ 收图 / 表情包
    @router.post("/send_image")
    def send_image(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        chat = str(payload.get("chat") or "").strip()
        path = str(payload.get("path") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not path and name:
            from . import media as media_mod

            candidate = media_mod.sticker_dir() / name
            if candidate.is_file():
                path = str(candidate)
        if not chat or not path:
            raise HTTPException(status_code=400, detail="chat 和 path 都不能为空")
        from pathlib import Path as _Path

        ok, detail = runtime.send_image(chat, path)
        runtime.store.add_message(
            chat=chat,
            chat_type="friend",
            sender="小鱼",
            direction="out",
            content="[图片]",
            msg_type="image",
            status="ok" if ok else "failed",
            note=f"手动发送图片：{_Path(path).name} · {detail}",
        )
        return {"ok": ok, "detail": detail}

    @router.get("/media/stickers")
    def sticker_list(limit: int = Query(default=60, ge=1, le=300)) -> dict[str, Any]:
        from . import media as media_mod

        items = media_mod.list_stickers()
        return {
            "count": len(items),
            "dir": str(media_mod.sticker_dir()),
            "items": [
                {"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
                for p in items[:limit]
            ],
        }

    @router.get("/media/sticker_file")
    def sticker_file(name: str = Query(...)):
        from fastapi.responses import FileResponse

        from . import media as media_mod

        root = media_mod.sticker_dir().resolve()
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="非法文件名")
        safe = (root / name).resolve()
        try:
            inside = safe.is_relative_to(root)
        except AttributeError:  # Python < 3.9
            inside = str(safe).startswith(str(root) + str(Path("/")))
        if not inside or not safe.is_file():
            raise HTTPException(status_code=404, detail="表情不存在")
        return FileResponse(str(safe))

    @router.post("/media/open")
    def media_open(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        from . import media as media_mod

        which = str((payload or {}).get("which") or "stickers")
        target = media_mod.sticker_dir() if which == "stickers" else media_mod.inbox_dir()
        from . import childsession as _cs

        ok, detail = _cs.open_on_console(target)
        return {"ok": ok, "detail": detail, "path": str(target)}

    # --------------------------------------------------------------------- dev
    @router.post("/mock/inject")
    def mock_inject(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        adapter = runtime.adapter
        if adapter is None or adapter.name != "mock":
            raise HTTPException(status_code=400, detail="当前不是模拟模式，请先在「系统设置」切换到模拟模式")
        adapter.inject(  # type: ignore[attr-defined]
            chat=str(payload.get("chat") or "测试好友"),
            sender=str(payload.get("sender") or "测试好友"),
            content=str(payload.get("content") or "你好"),
            chat_type=str(payload.get("chat_type") or "friend"),
        )
        return {"ok": True}

    @router.get("/mock/sent")
    def mock_sent() -> dict[str, Any]:
        adapter = runtime.adapter
        if adapter is None or adapter.name != "mock":
            return {"items": []}
        return {"items": adapter.sent}  # type: ignore[attr-defined]

    app.include_router(router)

    web = paths.web_dir()
    index = web / "index.html"

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/")
    def root() -> FileResponse:
        if not index.exists():
            raise HTTPException(status_code=500, detail="缺少 web/index.html")
        return FileResponse(index)

    if web.exists():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")
    else:
        log.error("找不到前端资源目录：%s", web)

    return app
