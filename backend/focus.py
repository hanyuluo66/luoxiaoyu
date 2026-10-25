"""窗口焦点工具。

Windows 会阻止后台进程 `SetForegroundWindow`（用户正在别的窗口打字时尤其如此），
所以发按键之前必须先**强制**把目标窗口变成前台窗口，否则按键会跑到别的窗口
（实测：Ctrl+F 直接打开了 Windows 开始菜单的搜索）。

做法是 AttachThreadInput：临时把当前线程输入队列挂到前台窗口所在线程上，
改完焦点再脱离。这是 WeeMessenger / 各种 UI 自动化项目通用的绕过方式。
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)


def force_foreground(hwnd: int) -> bool:
    """把 hwnd 抢到前台。返回是否成功。"""
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        tid_me = kernel32.GetCurrentThreadId()
        tid_target = user32.GetWindowThreadProcessId(hwnd, None)
        if tid_fg:
            user32.AttachThreadInput(tid_me, tid_fg, True)
        if tid_target:
            user32.AttachThreadInput(tid_me, tid_target, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        time.sleep(0.25)
        if tid_target:
            user32.AttachThreadInput(tid_me, tid_target, False)
        if tid_fg:
            user32.AttachThreadInput(tid_me, tid_fg, False)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:  # noqa: BLE001
        log.debug("force_foreground 失败", exc_info=True)
        return False
