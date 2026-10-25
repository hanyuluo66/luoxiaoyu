"""Process spawning helpers that never flash a console window.

The app is built with ``--noconsole`` (no console of its own). Any console
program it starts — ``whoami``, ``powershell``, ``tasklist`` … — would otherwise
get a fresh console window, which looks like a stray black box popping up.

Always spawn through these helpers instead of calling subprocess directly.
"""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
SW_HIDE = 0


def hidden_kwargs() -> dict:
    """Extra kwargs that keep a child process completely invisible."""
    if sys.platform != "win32":
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": info}


def run(cmd, **kwargs):
    """subprocess.run without a visible console window."""
    for key, value in hidden_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.run(cmd, **kwargs)


def popen(cmd, **kwargs):
    """subprocess.Popen without a visible console window."""
    for key, value in hidden_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.Popen(cmd, **kwargs)


def check_output(cmd, **kwargs):
    """subprocess.check_output without a visible console window."""
    for key, value in hidden_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.check_output(cmd, **kwargs)

def install_global_guard() -> None:
    """Wrap subprocess.Popen so *every* child process is windowless.

    Third-party code (e.g. the WeChat library's ffmpeg call) spawns processes on
    its own; patching Popen once covers all of it, including subprocess.run.
    """
    if sys.platform != "win32":
        return
    if getattr(subprocess.Popen, "_dfy_guarded", False):
        return
    base = subprocess.Popen

    # 必须保持是「类」：stdlib 的 asyncio.windows_utils 会 class Popen(subprocess.Popen)，
    # 换成普通函数会在 import 时炸掉（TypeError: function() argument 'code' must be code）。
    class GuardedPopen(base):  # type: ignore[misc, valid-type]
        _dfy_guarded = True

        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
            if kwargs.get("startupinfo") is None:
                info = subprocess.STARTUPINFO()
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = SW_HIDE
                kwargs["startupinfo"] = info
            super().__init__(*args, **kwargs)

    subprocess.Popen = GuardedPopen  # type: ignore[assignment,misc]