"""落小鱼 — entry point.

Starts the local backend, opens the web console, then lives in the system tray.
Single instance: a second launch just re-opens the console of the running one.
"""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from backend import __version__, paths
from backend.api import create_app
from backend.runtime import BotRuntime

APP_TITLE = "落小鱼"
MUTEX_NAME = "LUO_XIAOYU_BOT_SINGLETON"
PORT_RANGE = range(8760, 8790)

log = logging.getLogger("dafeiyu")


# ------------------------------------------------------------------- session
def _current_session() -> int:
    sid = ctypes.c_ulong(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(
        ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(sid)
    )
    return int(sid.value)


def _console_session() -> int:
    return int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())


def _is_child_session() -> bool:
    """True when we run inside an RDP child session (an isolated desktop).

    The child session has its own input queue, so UI automation there never
    steals the mouse or keyboard of the person using the console session.
    """
    try:
        return _current_session() != _console_session()
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------- logging
def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler: logging.Handler | None = None
    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.logs_dir() / "app.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        pass

    # The wechatauto library reconfigures the ROOT logger on import (its own
    # file handler, its own level), which used to swallow every later log line
    # of ours. Pin our own handlers on our namespaces so diagnostics survive.
    if handler is not None:
        for name in ("backend", "dafeiyu"):
            own = logging.getLogger(name)
            own.setLevel(logging.INFO)
            own.addHandler(handler)
            own.propagate = False

    if sys.stdout is not None and (sys.stdout.isatty() or os.environ.get("DAFEIYU_VERBOSE")):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
        for name in ("backend", "dafeiyu"):
            logging.getLogger(name).addHandler(console)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("wxauto").setLevel(logging.WARNING)


# ---------------------------------------------------------------- single instance
def acquire_single_instance() -> bool:
    """True when we are the first instance."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    already = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    if already:
        kernel32.CloseHandle(handle)
        return False
    # Keep the handle alive for the process lifetime.
    globals()["_mutex_handle"] = handle
    return True


def runtime_file():
    return paths.data_dir() / "runtime.json"


def read_running_port() -> int | None:
    try:
        data = json.loads(runtime_file().read_text(encoding="utf-8"))
        port = int(data.get("port", 0))
        return port or None
    except (OSError, ValueError, TypeError):
        return None


def write_running_port(port: int) -> None:
    try:
        runtime_file().write_text(
            json.dumps({"port": port, "pid": os.getpid()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def pick_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8760-8789 端口全被占用，无法启动控制台")



class _RuntimeProgressProxy:
    """把进度写到 BotRuntime 单例上（安全模式下也能喂控制台页面）。"""

    @staticmethod
    def set_startup_progress(percent: float, step: str = "", detail: str = "") -> None:
        try:
            BotRuntime().set_startup_progress(percent, step, detail)
        except Exception:  # noqa: BLE001
            pass


_RUNTIME_PROGRESS = _RuntimeProgressProxy()

_SPLASH_STATE = None
_SPLASH_PROC = None  # 已不再使用独立启动窗口


def _splash_state_path():
    import tempfile

    return Path(tempfile.gettempdir()) / "luoxiaoyu-startup.json"


def _splash_update(percent: float, step: str = "", detail: str = "") -> None:
    """更新启动进度：写状态文件（启动窗口读）+ 喂给 runtime（控制台页面进度条）。"""
    try:
        if _SPLASH_STATE is not None:
            from backend import splash_win

            splash_win.write_state(_SPLASH_STATE, percent, step, detail)
    except Exception:  # noqa: BLE001
        pass
    try:
        _RUNTIME_PROGRESS.set_startup_progress(percent, step, detail)
    except Exception:  # noqa: BLE001
        pass


def _splash_done(ok: bool, message: str = "") -> None:
    try:
        if _SPLASH_STATE is None:
            return
        from backend import splash_win

        if ok:
            splash_win.write_state(_SPLASH_STATE, 100, "就绪", "控制台已打开", done=True)
        else:
            splash_win.write_state(
                _SPLASH_STATE, 100, "启动没有完成",
                message or "详见控制台日志", failed=True,
            )
    except Exception:  # noqa: BLE001
        pass


def open_console(port: int) -> None:
    """Open the console as its own window (native WebView2, no browser tab).

    控制台通常由分身里的实例在服务，所以在分身里调用时要把窗口请求转给主桌面，
    否则窗口会开在看不见的分身桌面上。
    """
    url = f"http://127.0.0.1:{port}/"
    try:
        from backend import console_window

        if _is_child_session():
            from backend import childsession as _cs

            exe = Path(sys.executable).resolve()
            ok, detail = _cs.open_on_console(
                exe, args=f"--console-window {url}", workdir=str(exe.parent)
            )
            if ok:
                log.info("已请主桌面打开控制台窗口：%s", url)
                return
            log.warning("请主桌面打开控制台窗口失败：%s", detail)
        if console_window.open_app_window(url):
            return
    except Exception:  # noqa: BLE001
        log.warning("打开控制台窗口失败", exc_info=True)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        log.warning("自动打开浏览器失败，请手动访问 %s", url)


# ------------------------------------------------------------------------- tray
def _patch_pystray_message_filter() -> None:
    """pystray hard-fails when ChangeWindowMessageFilterEx is denied.

    That call only lets the icon hear the shell's "taskbar recreated" broadcast;
    some locked-down sessions return WinError 5 for it and pystray then aborts
    tray creation entirely. Making it a no-op keeps the tray usable.
    """
    try:
        from pystray._util import win32 as pystray_win32

        pystray_win32.ChangeWindowMessageFilterEx = lambda *a, **k: True
        log.info("已放宽 pystray 的消息过滤器调用")
    except Exception:  # noqa: BLE001
        pass


def _tray_image(size: int = 64):
    """Tray icon: the 落小鱼 artwork when available, else a drawn fallback."""
    from PIL import Image

    for candidate in (
        paths.resource_dir() / "web" / "logo.png",
        paths.install_dir() / "web" / "logo.png",
        paths.install_dir() / "logo.png",
    ):
        try:
            if candidate.exists():
                return Image.open(candidate).convert("RGBA").resize((size, size))
        except Exception:  # noqa: BLE001
            continue

    from PIL import ImageDraw

    image = Image.new("RGB", (size, size), (18, 24, 38))
    draw = ImageDraw.Draw(image)
    scale = size / 64
    draw.ellipse(
        (int(6 * scale), int(14 * scale), int(58 * scale), int(50 * scale)),
        fill=(255, 196, 87),
    )
    draw.ellipse(
        (int(20 * scale), int(26 * scale), int(28 * scale), int(34 * scale)),
        fill=(40, 30, 10),
    )
    draw.ellipse(
        (int(38 * scale), int(26 * scale), int(46 * scale), int(34 * scale)),
        fill=(40, 30, 10),
    )
    return image


def build_tray(port: int, runtime: BotRuntime, server) -> object | None:
    """Tray icon with a small menu; returns None when pystray is unavailable."""
    try:
        import pystray

        from PIL import Image, ImageDraw  # noqa: F401  (只检查可用性)
    except Exception:  # noqa: BLE001
        log.info("pystray 不可用，跳过托盘图标")
        return None

    _patch_pystray_message_filter()

    image = _tray_image(64)

    def on_open(_icon=None, _item=None):
        open_console(port)

    def on_start(_icon=None, _item=None):
        try:
            runtime.start()
        except RuntimeError as exc:
            log.warning("托盘启动被拒绝：%s", exc)
        except Exception:  # noqa: BLE001
            log.exception("托盘启动失败")

    def on_stop(_icon=None, _item=None):
        runtime.stop()

    def on_quit(icon=None, _item=None):
        try:
            runtime.stop()
        finally:
            if icon:
                icon.stop()
            server.should_exit = True

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", on_open, default=True),
        pystray.MenuItem("启动机器人", on_start),
        pystray.MenuItem("停止机器人", on_stop),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("dafeiyu", image, f"{APP_TITLE} · 控制台 {port}", menu)
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    return icon


# ------------------------------------------------------------------------- main
def _probe_console(timeout: float = 0.0) -> int | None:
    """Port of a running console instance (the child-session bot), if any."""
    import urllib.request

    deadline = time.time() + timeout
    while True:
        for port in PORT_RANGE:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        return port
            except Exception:  # noqa: BLE001
                continue
        if time.time() >= deadline:
            return None
        time.sleep(2.0)


def _message_box(text: str, title: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def _dismiss_startup_dialogs(seconds: float = 90.0) -> None:
    """Close error dialogs raised by the user's own startup items in this session.

    A fresh child session runs the same Startup folder entries as the normal
    desktop. Apps that cannot start in an RDP session (e.g. Store apps such as
    TranslucentTB) pop a modal error dialog that blocks the desktop, so we look
    for those and press Enter — only inside this session.
    """
    user32 = ctypes.windll.user32
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    VK_RETURN = 0x0D
    deadline = time.time() + seconds
    handled: set[int] = set()
    while time.time() < deadline:
        time.sleep(1.5)
        found: list[tuple[int, str]] = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lparam, _out=found):
            buf = ctypes.create_unicode_buffer(256)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "#32770" and user32.IsWindowVisible(hwnd):
                _out.append((int(hwnd), buf.value))
            return True

        user32.EnumWindows(EnumProc(_cb), None)
        for hwnd, title in found:
            if hwnd in handled:
                continue
            if "WindowsApps" in title or "无法访问" in title or "错误" in title:
                try:
                    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
                    user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)
                    handled.add(hwnd)
                    log.info("已自动关闭子会话里的启动项弹窗：%s", title[:60])
                except Exception:  # noqa: BLE001
                    pass


def _ensure_wechat_in_session(session_id: int) -> tuple[bool, str]:
    """Make sure WeChat itself runs inside the child session."""
    from backend import childsession as cs

    if cs.pids_in_session(session_id, "Weixin"):
        return True, "微信已在桌面分身里运行"
    exe = Path(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
    if not exe.exists():
        return False, "找不到微信（C:\\Program Files\\Tencent\\Weixin\\Weixin.exe）"
    ok, detail = cs.launch_into_session(session_id, exe, exe.parent)
    return ok, detail


def _recreate_child_desktop() -> tuple[bool, str]:
    """Bring the child desktop (session + bot inside it) back up."""
    from backend import childsession as cs
    from backend import startup_items as si

    # 登录前置检查：没有凭据、或系统仍只允许 Hello 登录时，
    # 直接不去连 RDP——否则 Windows 会一直弹「你的凭据不工作」。
    try:
        if BotRuntime().config.bot.child_desktop:
            from backend import childsession as _cs

            _blocked = _cs.login_blocked()
            if _blocked:
                _cs.stop_keeper_for_login()
                _set_last_child_failure(_blocked)
                return False, _blocked
            if not _cs.credentials_stored():
                _cs.stop_keeper_for_login()
                _set_last_child_failure(
                    "还没有保存分身登录凭据，所以没有尝试登录（避免 Windows 弹「凭据不工作」）。\n"
                    "请到「系统设置 → 桌面分身登录」填入 Windows 密码并保存。"
                )
                return False, "缺少分身登录凭据"
            if _cs.hello_only_login() is True:
                _cs.stop_keeper_for_login()
                _set_last_child_failure(
                    "系统还开着「仅允许对此设备上的 Microsoft 帐户使用 Windows Hello 登录」，"
                    "分身无法用密码登录。\n"
                    "已尝试改成允许密码登录，但需要【注销并重新登录】或重启一次才生效。"
                )
                return False, "系统仍只允许 Windows Hello 登录（需要重启生效）"
    except Exception:  # noqa: BLE001
        log.debug("登录前置检查失败", exc_info=True)


    # 干净启动：先把用户的启动项挂起，这样分身登录时没有任何第三方程序可跑。
    # 主桌面已经登录完了，不会重读这些项 → 完全不受影响。
    clean_boot = False
    try:
        if BotRuntime().config.bot.child_session_clean_boot:
            si.restore()  # 先收拾上次可能残留的
            info = si.suspend()
            clean_boot = bool(info.get("run") or info.get("files"))
    except Exception:  # noqa: BLE001
        log.warning("挂起启动项失败（不影响建分身）", exc_info=True)

    if not cs.ensure_keeper(timeout=180):
        try:
            # 引擎留着不动：这种连接经常只是慢，稍后自己会连上
            pass
        except Exception:  # noqa: BLE001
            log.debug("记录登录失败计数失败", exc_info=True)
        _set_last_child_failure(_CHILD_FAIL_HINT)
        if clean_boot:
            try:
                si.restore()
            except Exception:  # noqa: BLE001
                log.warning("恢复启动项失败", exc_info=True)
        return False, (
            "桌面分身没能建立。常见原因：\n"
            "① 系统开着「仅允许 Windows Hello 登录」→ 设置-账户-登录选项里关掉，注销重登\n"
            "② Windows 账户没有密码 → 先设一个\n"
            "③ 其他程序占用的 RDP 子会话（如 BetterGI 桌面分身）→ 关掉它\n"
            "④ Child Sessions 没启用 → 用管理员运行一次「诊断桌面分身.bat」\n"
            "详细原因看同目录的 childsession-keeper.log 或诊断报告"
        )
    session_id = cs.child_session_id()
    if session_id is None:
        _set_last_child_failure("桌面分身会话不存在（可能刚被注销），请重新启动落小鱼。")
        return False, "桌面分身会话不存在"

    # 分身已经登录 → 立刻把启动项还给主桌面（下一次你登录时照常生效）
    if clean_boot:
        try:
            si.restore()
            log.info("分身已登录，启动项已恢复")
        except Exception:  # noqa: BLE001
            log.warning("恢复启动项失败，下次启动会自动重试", exc_info=True)

    wx_ok, wx_detail = _ensure_wechat_in_session(session_id)
    log.info("分身内微信：%s %s", wx_ok, wx_detail)
    _splash_update(68, "启动分身里的微信…", str(wx_detail)[:60])
    if not cs.pids_in_session(session_id, "落小鱼"):
        ok, detail = cs.launch_into_session(
            session_id, Path(sys.executable).resolve(), Path(sys.executable).parent
        )
        if not ok:
            return False, f"投递到会话 {session_id} 失败：{detail[:100]}"
    port = _probe_console(timeout=90)
    if port is None:
        return False, "分身里的控制台没有起来（机器人可能启动失败，看 data-session*/logs）"
    return True, f"桌面分身就绪：会话 {session_id}，控制台 {port}"


_CHILD_FAIL_HINT = (
    "桌面分身建不起来，机器人已停止（避免抢你的鼠标）。请依次检查：\n"
    "① 「系统设置 → 桌面分身登录」里保存登录凭据（最常见）；\n"
    "② 系统设置里关掉「仅允许 Windows Hello 登录」后注销重登；\n"
    "③ 关掉其它占用 RDP 子会话的程序（如 BetterGI 桌面分身）。"
)

_LAST_CHILD_FAILURE = ""


def _set_last_child_failure(reason: str) -> None:
    """记住上次建分身失败的原因，安全模式横幅要用。"""
    global _LAST_CHILD_FAILURE
    _LAST_CHILD_FAILURE = (reason or "").strip()


def pending_child_reason() -> str:
    """给控制台横幅用的一句话说明。"""
    if _LAST_CHILD_FAILURE:
        return _LAST_CHILD_FAILURE
    return (
        "桌面分身没有建立，机器人已停止（避免抢你的鼠标）。"
        "请到「系统设置 → 桌面分身登录」保存登录凭据，然后点托盘「重建桌面分身」。"
    )


def _run_safe_mode() -> int:
    """Serve the console but do NOT start the bot.

    Falling back to local UI automation is what makes the mouse jump, so when
    the child desktop cannot be used we stay idle instead and let the user
    retry from the tray.
    """
    port = pick_port()
    write_running_port(port)

    runtime = BotRuntime()
    holder: dict[str, object] = {}

    def request_exit() -> None:
        try:
            runtime.stop()
        finally:
            server = holder.get("server")
            if server is not None:
                server.should_exit = True  # type: ignore[attr-defined]

    app = create_app(runtime, on_shutdown=request_exit)
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
            log_config=None,
        )
    )
    holder["server"] = server

    runtime.notify(
        "桌面分身未就绪，机器人没有启动（避免抢你的鼠标）。"
        "可以在托盘菜单里点「重建桌面分身」重试。",
        kind="error",
        level="error",
    )
    # 让控制台显示醒目横幅（否则用户只看到机器人"没反应"）
    try:
        _left = pending_child_reason()
        runtime.set_child_desktop_state(False, _left)
    except Exception:  # noqa: BLE001
        log.debug("写入分身状态失败", exc_info=True)
    # 后台盯着引擎：一旦分身自己连上（这种连接常常只是慢），
    # 把状态翻成"就绪"，横幅会提示用户点一下重启让机器人接管。
    def _watch_child() -> None:
        import time as _t

        from backend import childsession as _cs

        while True:
            _t.sleep(15)
            try:
                status = _cs.keeper_status()
                if status.get("connected") == 1 and int(status.get("child", -1)) >= 0:
                    runtime.set_child_desktop_state(
                        False,
                        "桌面分身已经连上了（登录成功）。"
                        "点下方按钮重启落小鱼，机器人就会在分身里跑起来。",
                    )
                    return
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_watch_child, name="child-watch", daemon=True).start()

    log.warning("安全模式：只提供控制台，不启动机器人")

    def retry(_icon=None, _item=None) -> None:
        ok, message = _recreate_child_desktop()
        log.info("重建桌面分身：%s %s", ok, message)
        if ok:
            runtime.notify(message, kind="start", level="info")
            _message_box(
                f"{message}\n\n本机的临时控制台会关闭，请用分身里的控制台继续。",
                APP_TITLE,
                0x40,
            )
            request_exit()
        else:
            runtime.notify(message, kind="error", level="error")
            _message_box(message, APP_TITLE, 0x30)

    def build_safe_tray() -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return
        _patch_pystray_message_filter()
        image = _tray_image(64)
        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", lambda *_: open_console(port), default=True),
            pystray.MenuItem("重建桌面分身", retry),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "退出",
                lambda *_: (
                    runtime.notify("程序已退出", kind="stop"),
                    request_exit(),
                ),
            ),
        )
        icon = pystray.Icon("dafeiyu", image, f"{APP_TITLE} · 安全模式（未启动机器人）", menu)
        try:
            icon.run()
        except Exception:  # noqa: BLE001
            log.debug("安全模式托盘退出", exc_info=True)

    threading.Thread(target=build_safe_tray, name="tray-safe", daemon=True).start()
    threading.Timer(1.2, lambda: open_console(port)).start()

    log.info("%s v%s 控制台（安全模式）： http://127.0.0.1:%s", APP_TITLE, __version__, port)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.shutdown()
        try:
            runtime_file().unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def _start_slim_worker(session_id: int, interval_rounds: int = 4) -> None:
    """Keep the child session clean: close the user's startup apps there.

    A fresh session replays every startup entry (Steam, QQ, cloud drives, tray
    helpers). They are useless inside the clone, so we sweep a few times while
    they appear — never touching WeChat or system processes.
    """
    from backend import childsession as cs

    def run() -> None:
        import time as _t

        for _ in range(max(1, interval_rounds)):
            _t.sleep(12)
            try:
                killed = cs.slim_child_session(session_id, bot_cfg_child_extra())
                if killed:
                    log.info("分身瘦身第 %d 轮：%s", _ + 1, sorted(set(killed)))
            except Exception:  # noqa: BLE001
                log.debug("分身瘦身失败", exc_info=True)

    def bot_cfg_child_extra() -> str:
        try:
            return BotRuntime().config.bot.child_session_kill_extra
        except Exception:  # noqa: BLE001
            return ""

    threading.Thread(target=run, name="child-slim", daemon=True).start()
def _run_controller() -> int | None:
    """Session-1 role: own the child session, keep the bot running inside it.

    Returns an exit code, or None when the child desktop cannot be used and the
    caller should fall back to a normal local instance.
    """
    from backend import childsession as cs

    if not cs.child_sessions_enabled() and not cs.enable_child_sessions():
        log.warning("本机不支持桌面分身（Child Sessions 不可用），改用本机模式")
        return None

    log.info("步骤：准备桌面分身")
    _splash_update(18, "检查桌面分身登录…", "需要保存凭据 / 关闭 Hello 独占时这里会停下")
    had_session = cs.child_session_id() is not None

    # 干净启动：分身登录期间临时挂起用户启动项（主桌面已登录，不受影响）
    from backend import startup_items as si

    clean_boot = False
    try:
        if BotRuntime().config.bot.child_session_clean_boot and not had_session:
            si.restore()
            info = si.suspend()
            clean_boot = bool(info.get("run") or info.get("files"))
    except Exception:  # noqa: BLE001
        log.warning("挂起启动项失败（不影响建分身）", exc_info=True)

    def _restore_startup_once() -> None:
        nonlocal clean_boot
        if not clean_boot:
            return
        clean_boot = False
        try:
            si.restore()
            log.info("启动项已恢复")
        except Exception:  # noqa: BLE001
            log.warning("恢复启动项失败，下次启动会自动重试", exc_info=True)

    # 登录前置检查（同 _recreate_child_desktop）：没凭据或 Hello 未生效就别连，
    # 否则 Windows 会一直弹「你的凭据不工作」。
    try:
        if BotRuntime().config.bot.child_desktop:
            _blocked = cs.login_blocked()
            if _blocked:
                cs.stop_keeper_for_login()
                _set_last_child_failure(_blocked)
                log.warning('分身登录连续失败，暂停自动尝试')
                return None
            if not cs.credentials_stored():
                cs.stop_keeper_for_login()
                _set_last_child_failure(
                    "还没有保存分身登录凭据，所以没有尝试登录（避免弹「凭据不工作」）。"
                    "请到「系统设置 → 桌面分身登录」填入 Windows 密码并保存。"
                )
                log.warning("缺少分身登录凭据，跳过建分身")
                return None
            if cs.hello_only_login() is True:
                cs.stop_keeper_for_login()
                _set_last_child_failure(
                    "系统还开着「仅允许 Windows Hello 登录」，分身无法用密码登录。"
                    "已改成允许密码登录，但需要【注销并重新登录】或重启一次才生效。"
                )
                log.warning("系统仍只允许 Windows Hello 登录，跳过建分身")
                return None
    except Exception:  # noqa: BLE001
        log.debug("登录前置检查失败", exc_info=True)

    try:
        if not cs.ensure_keeper(timeout=180):
            log.warning("桌面分身未就绪，改用本机模式")
            return None

        session_id = cs.child_session_id()
        if session_id is None:
            log.warning("桌面分身会话不存在，改用本机模式")
            return None
        _restore_startup_once()  # 分身已经登录，立刻还原
        log.info("桌面分身已就绪：会话 %s", session_id)
        _splash_update(55, "桌面分身已连接", "会话 %s" % session_id)
    finally:
        _restore_startup_once()
    wx_ok, wx_detail = _ensure_wechat_in_session(session_id)
    log.info("分身内微信：%s %s", wx_ok, wx_detail)

    exe = Path(sys.executable).resolve()
    already = cs.pids_in_session(session_id, "落小鱼")
    if not already:
        log.info("步骤：把机器人投递进会话 %s", session_id)
        _splash_update(80, "把机器人放进分身…")
        ok, detail = cs.launch_into_session(session_id, exe, Path(sys.executable).parent)
        log.info("投递结果：%s %s", ok, detail)
        if not ok:
            log.error("投递失败，改用本机模式")
            return None
    else:
        log.info("会话 %s 里已有机器人实例 %s", session_id, already)

    _splash_update(90, "等待控制台就绪…", "机器人在分身里启动，通常 10~30 秒")
    port = _probe_console(timeout=120)
    if port is None:
        log.error("桌面分身里的控制台没有起来，改用本机模式")
        return None
    log.info("桌面分身里的控制台： http://127.0.0.1:%s", port)
    _splash_update(100, "就绪", "控制台 %s" % port)
    _splash_done(True)
    # 打开独立控制台窗口（不是浏览器标签页）
    try:
        open_console(port)
    except Exception:  # noqa: BLE001
        log.warning("打开控制台窗口失败", exc_info=True)

    if not had_session:
        # Fresh desktop: the user has to log into WeChat there once, so show it.
        log.info("新建的桌面分身，显示视图窗口供首次登录")
        cs.keeper_command("show")

        def auto_hide() -> None:
            # 画面里的远程光标会被 RDP 客户端映射到本机鼠标上，所以登录完就收起来，
            # 让机器人完全在后台工作。
            time.sleep(120)
            ok = cs.keeper_command("hide")
            log.info("已自动隐藏桌面分身画面（%s）", ok)

        threading.Thread(target=auto_hide, name="auto-hide-viewer", daemon=True).start()

    return _controller_tray(port, session_id)


def _controller_tray(port: int, session_id: int) -> int:
    """Small tray for the controller: console, viewer and quit."""
    from backend import childsession as cs

    stop = threading.Event()

    def open_console_click(_icon=None, _item=None):
        open_console(port)

    def show_viewer(_icon=None, _item=None):
        cs.keeper_command("show")

    def hide_viewer(_icon=None, _item=None):
        cs.keeper_command("hide")

    def on_quit(_icon=None, _item=None):
        MB_YESNO = 0x4
        MB_ICONQUESTION = 0x20
        answer = _message_box(
            "退出 落小鱼？\n\n"
            "「是」= 同时关闭桌面分身（里面的微信会退出，机器人停止）\n"
            "「否」= 只关闭这个托盘，桌面分身里的机器人继续在后台运行",
            APP_TITLE,
            MB_YESNO | MB_ICONQUESTION,
        )
        if answer == 6:  # IDYES
            log.info("退出并关闭桌面分身")
            cs.keeper_command("quit")
        else:
            log.info("仅退出托盘，桌面分身保持运行")
        stop.set()

    def rebuild(_icon=None, _item=None):
        def work() -> None:
            ok, message = _recreate_child_desktop()
            log.info("重建桌面分身：%s %s", ok, message)
            if ok:
                port_now = _probe_console(timeout=5)
                if port_now:
                    open_console(port_now)
            else:
                _message_box(message, APP_TITLE, 0x30)  # 警告图标

        threading.Thread(target=work, name="rebuild-child", daemon=True).start()

    def watchdog() -> None:
        """Keep the child desktop alive: recreate it if it disappears."""
        failures = 0
        while not stop.wait(20):
            try:
                if cs.child_session_id() is not None:
                    failures = 0
                    continue
                log.warning("桌面分身会话不见了，尝试重建（第 %d 次）", failures + 1)
                ok, message = _recreate_child_desktop()
                log.info("看门狗重建结果：%s %s", ok, message)
                failures = 0 if ok else failures + 1
                if failures >= 3:
                    log.error("连续重建失败，等待手动处理")
                    _message_box(
                        "桌面分身连续多次没能建立，机器人已停止（不会去抢你的鼠标）。\n\n"
                        "可以关掉 BetterGI 之类的程序后再从托盘点「重建桌面分身」。",
                        APP_TITLE,
                        0x30,
                    )
                    failures = 0
            except Exception:  # noqa: BLE001
                log.debug("看门狗异常", exc_info=True)

    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        log.info("pystray 不可用，控制器直接常驻")
        while not stop.is_set():
            time.sleep(1)
        return 0

    _patch_pystray_message_filter()
    image = _tray_image(64)

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", open_console_click, default=True),
        pystray.MenuItem("重建桌面分身", rebuild),
        pystray.MenuItem("显示桌面分身画面", show_viewer),
        pystray.MenuItem("隐藏桌面分身画面", hide_viewer),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    icon = pystray.Icon(
        "dafeiyu", image, f"{APP_TITLE} · 桌面分身（会话 {session_id}）· 控制台 {port}", menu
    )
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    log.info("托盘已就绪（桌面分身模式），看门狗已启动")

    while not stop.is_set():
        time.sleep(0.5)
    try:
        icon.stop()
    except Exception:  # noqa: BLE001
        pass
    return 0


def main() -> int:
    # 独立控制台窗口模式：`落小鱼.exe --console-window http://127.0.0.1:8760/`
    # 只开一个窗口，不启动机器人；窗口关掉进程就退出。
    if "--splash" in sys.argv:
        try:
            idx = sys.argv.index("--splash")
            state_file = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
        except Exception:  # noqa: BLE001
            state_file = ""
        from backend import splash_win

        return splash_win.run_splash_window(state_file)

    if "--console-window" in sys.argv:
        try:
            index = sys.argv.index("--console-window")
            url = sys.argv[index + 1] if len(sys.argv) > index + 1 else "http://127.0.0.1:8760/"
        except Exception:  # noqa: BLE001
            url = "http://127.0.0.1:8760/"
        from backend import console_window

        return console_window.run_window(url, f"{APP_TITLE} · 控制台")

    my_session = _current_session()
    console_session = _console_session()
    is_child = my_session != console_session

    # Decide the data directory *before* logging starts, otherwise the
    # child-session instance writes its log into the console instance's log file.
    if is_child:
        # The child-session instance must not share the console instance's data
        # directory: both would open the same SQLite file.
        child_data = paths.install_dir() / f"data-session{my_session}"
        os.environ["DAFEIYU_DATA_DIR"] = str(child_data)
        seed = paths.install_dir() / "data" / "config.json"
        target = child_data / "config.json"
        if seed.exists() and not target.exists():
            try:
                child_data.mkdir(parents=True, exist_ok=True)
                target.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass

    from backend.spawn import install_global_guard

    install_global_guard()  # 任何子进程都不弹控制台
    setup_logging()

    if is_child:
        log.info("运行在子会话 %s（控制台会话 %s），数据目录：%s",
                 my_session, console_session, paths.data_dir())
        # Dismiss the user's own startup-item error dialogs inside this session;
        # they would otherwise sit on top of the clone desktop.
        threading.Thread(
            target=_dismiss_startup_dialogs, name="startup-dialogs", daemon=True
        ).start()
        # The clone replays every startup entry; close them so only WeChat stays.
        try:
            if BotRuntime().config.bot.child_session_slim:
                _start_slim_worker(my_session)
        except Exception:  # noqa: BLE001
            log.debug("启动分身瘦身失败", exc_info=True)

    # 上次若是挂在「挂起启动项」阶段退出，这里自动还原，避免用户丢失开机启动
    try:
        from backend import startup_items as _si

        _left = _si.pending()
        if _left.get("run") or _left.get("files"):
            log.warning("检测到上次未恢复的启动项：%s，正在还原", _left)
            _si.restore()
    except Exception:  # noqa: BLE001
        log.debug("启动项自检失败", exc_info=True)

    if not acquire_single_instance():
        port = read_running_port() or _probe_console(timeout=5)
        print(f"{APP_TITLE} 已在运行，控制台端口：{port}")
        if port:
            open_console(port)
        return 0

    if not is_child:
        # Session 1: prefer running the bot inside an isolated child session so
        # UI automation never touches this desktop. Falls back to local mode.
        try:
            from backend import childsession as _cs

            use_child = getattr(BotRuntime().config.bot, "child_desktop", True)
        except Exception:  # noqa: BLE001
            use_child = False
        if use_child:
            log.info("步骤：进入桌面分身模式")
            # 启动进度：写进 runtime，控制台页面会用进度条显示；
            # 独立的启动小窗在冻结环境里不可靠，已去掉（控制台窗口一就绪就弹出）
            try:
                _splash_update(8, "读取配置…")
            except Exception:  # noqa: BLE001
                log.debug("启动进度更新失败", exc_info=True)
            try:
                code = _run_controller()
            except Exception:  # noqa: BLE001
                log.exception("桌面分身编排失败")
                code = None
            if code is not None:
                _splash_done(code == 0)
                return code
            try:
                allow_local = bool(BotRuntime().config.bot.allow_local_fallback)
            except Exception:  # noqa: BLE001
                allow_local = False
            if allow_local:
                log.warning("桌面分身不可用，按配置回退到本机模式（会抢焦点）")
            else:
                log.error("桌面分身不可用，进入安全模式：只开控制台，不启动机器人")
                _splash_done(False, "桌面分身没建起来，已进入安全模式（只开控制台）")
                return _run_safe_mode()

    port = pick_port()
    write_running_port(port)

    runtime = BotRuntime()
    holder: dict[str, object] = {}

    def request_exit() -> None:
        """Shared by the tray menu and the console's 退出程序 button."""
        try:
            runtime.stop()
        finally:
            server = holder.get("server")
            if server is not None:
                server.should_exit = True  # type: ignore[attr-defined]

    app = create_app(runtime, on_shutdown=request_exit)

    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    holder["server"] = server

    cfg = runtime.config
    if cfg.bot.enabled:
        log.info("步骤：启动机器人")
        try:
            runtime.start()
        except RuntimeError as exc:
            log.warning("机器人未启动：%s", exc)
            runtime.notify(str(exc), kind="error", level="error")
        except Exception:  # noqa: BLE001
            log.exception("启动机器人失败，控制台仍可使用")
            runtime.notify("启动机器人失败，请在控制台查看日志", kind="error", level="error")
    log.info("步骤：机器人启动流程结束")

    def _tray_later() -> None:
        # Tray creation touches the shell and can block on some sessions; it must
        # never delay the HTTP console, so it runs detached.
        if is_child:
            return
        time.sleep(1.0)
        try:
            log.info("步骤：创建托盘图标")
            build_tray(port, runtime, server)
            log.info("步骤：托盘就绪")
        except Exception:  # noqa: BLE001
            log.exception("托盘创建失败（不影响控制台）")

    threading.Thread(target=_tray_later, name="tray-boot", daemon=True).start()

    log.info("%s v%s 控制台： http://127.0.0.1:%s", APP_TITLE, __version__, port)
    if is_child:
        log.info("子会话实例：不弹浏览器、不建托盘（后台静默运行）")
    else:
        threading.Timer(1.2, lambda: open_console(port)).start()

    log.info("步骤：启动 HTTP 服务")
    try:
        server.run()
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，正在退出")
    finally:
        runtime.shutdown()
        try:
            runtime_file().unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
