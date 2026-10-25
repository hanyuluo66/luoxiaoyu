"""Experiment: send a WeChat message WITHOUT touching the mouse.

Uses UIA ValuePattern to fill the search box / chat input and sends Enter as a
keystroke, instead of the library's click-based path. Records the cursor
position before and after to prove the mouse did not move.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET = "文件传输助手"


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def foreground_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def main() -> int:
    from backend.adapters.wechatauto_adapter import _patch_library, _weixin_pids

    pids = _weixin_pids()
    _patch_library(lambda: pids)

    from wechatauto.param import WxParam

    WxParam.ENABLE_OCR = False

    from wechatauto.uia_driver import WeChatUIA

    uia = WeChatUIA()
    auto_ok = uia.ensure_window(timeout=25)
    print(f"ensure_window = {auto_ok}, 当前会话 = {uia.current_chat()!r}")

    before = cursor_pos()
    print(f"发送前：鼠标 = {before}  前台窗口 = {foreground_title()!r}")

    win = uia._win  # noqa: SLF001
    text = f"静默发送测试 {time.strftime('%H:%M:%S')}"

    # 1) 切换会话：搜索框写值（ValuePattern，不点鼠标）+ 回车
    if uia.current_chat() != TARGET:
        box = uia._search_box(win, expand=True)  # noqa: SLF001
        if box is None:
            print("✗ 找不到搜索框")
            return 2
        ok = uia._set_text(box, TARGET)  # noqa: SLF001
        print(f"搜索框写值 = {ok}")
        time.sleep(0.9)
        box.SendKeys("{ENTER}")
        time.sleep(1.3)
        print(f"回车后当前会话 = {uia.current_chat()!r}")

    # 2) 聊天输入框写值 + 回车
    edit = uia._chat_input()  # noqa: SLF001
    if edit is None:
        print("✗ 找不到聊天输入框")
        return 3
    ok = uia._set_text(edit, text)  # noqa: SLF001
    print(f"输入框写值 = {ok}")
    time.sleep(0.3)
    readback = uia._read_edit_value(edit)  # noqa: SLF001
    print(f"回读内容 = {readback!r}")
    if not readback:
        print("✗ 内容没写进输入框")
        return 4
    edit.SendKeys("{ENTER}")
    time.sleep(0.8)

    after = cursor_pos()
    print(f"发送后：鼠标 = {after}  前台窗口 = {foreground_title()!r}")
    print(f"鼠标是否移动 = {before != after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
