"""控制台独立窗口。

把原来用浏览器打开的 `http://127.0.0.1:8760` 换成原生窗口（Edge WebView2）。
没有任何浏览器标签页/地址栏，标题和图标都是落小鱼自己的。

窗口由**独立进程**承载（`落小鱼.exe --console-window <url>`），这样：
  * 关掉窗口不会影响后台机器人；
  * 控制台通常由分身里的实例在服务，窗口本身可以请主桌面去开（见 open_on_console）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def run_window(url: str, title: str = "落小鱼 · 控制台", width: int = 1180, height: int = 820) -> int:
    """Open a native webview window and block until it is closed."""
    try:
        import webview
    except Exception:  # noqa: BLE001
        log.warning("pywebview 不可用，退回浏览器", exc_info=True)
        return _open_in_browser(url)

    try:
        webview.create_window(
            title,
            url,
            width=width,
            height=height,
            min_size=(900, 620),
            confirm_close=False,
            text_select=True,
        )
        # gui="edgechromium" 用系统自带的 WebView2，不需要额外运行时
        webview.start(gui="edgechromium" if sys.platform == "win32" else None)
        return 0
    except Exception:  # noqa: BLE001
        log.warning("原生窗口启动失败，退回浏览器", exc_info=True)
        return _open_in_browser(url)


def _open_in_browser(url: str) -> int:
    try:
        import webbrowser

        webbrowser.open(url)
        return 0
    except Exception:  # noqa: BLE001
        return 1


def _browser_exe() -> str | None:
    """Edge/Chrome 的可执行文件（app 模式兜底时用）。"""
    candidates = [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return shutil.which("msedge") or shutil.which("chrome")


def open_app_window(url: str, title: str = "落小鱼 · 控制台") -> bool:
    """Start the console window as a detached process (does not block the caller)."""
    exe = Path(sys.executable).resolve()
    app_dir = exe.parent

    # Edge/Chrome 的 --app 模式最稳（无地址栏、无标签页，独立窗口）
    browser = _browser_exe()
    if browser:
        try:
            subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    "--window-size=1200,860",
                    "--user-data-dir=" + str(Path(os.environ.get("LOCALAPPDATA", "")) / "Luoxiaoyu" / "console-window"),
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                close_fds=True,
                creationflags=0x08000000,
            )
            log.info("控制台窗口已启动（%s app 模式）：%s", Path(browser).name, url)
            return True
        except Exception:  # noqa: BLE001
            log.debug("app 模式失败", exc_info=True)

    # 没有 Edge/Chrome 时，用自己（WebView2）承载
    if _is_frozen():
        try:
            subprocess.Popen(
                [str(exe), "--console-window", url],
                cwd=str(app_dir),
                close_fds=True,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            log.info("控制台窗口已启动：%s", url)
            return True
        except Exception:  # noqa: BLE001
            log.warning("启动控制台窗口失败，尝试浏览器 app 模式", exc_info=True)

    return _open_in_browser(url) == 0
