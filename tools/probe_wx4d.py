"""Test the pure-UIA send path on WeChat 4.x (no OCR needed)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TARGET = "文件传输助手"


def weixin_pids() -> list[int]:
    import psutil

    u = ctypes.windll.user32
    hits: list[int] = []
    EP = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        if not u.IsWindowVisible(h):
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 256)
        if cls.value.startswith("Qt5"):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value:
                hits.append(int(pid.value))
        return True

    u.EnumWindows(EP(cb), 0)
    out = []
    for pid in sorted(set(hits)):
        try:
            if (psutil.Process(pid).name() or "").lower() == "weixin.exe":
                out.append(pid)
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    pids = weixin_pids()
    from wechatauto import WeChatDB

    WeChatDB._find_weixin_pids = lambda self: list(pids)  # type: ignore[assignment]
    print(f"Weixin pids = {pids}")

    from wechatauto.uia_driver import WeChatUIA

    uia = WeChatUIA()
    print("\n=== 1. 确保窗口 + 物化控件树 ===")
    print(f"  ensure_window -> {uia.ensure_window(timeout=25)}")

    print("\n=== 2. 打开会话（走搜索框）===")
    t0 = time.time()
    opened = uia.open_chat(TARGET)
    print(f"  open_chat({TARGET!r}) -> {opened}  ({time.time()-t0:.1f}s)")
    print(f"  current_chat -> {uia.current_chat()!r}")

    print("\n=== 3. 发送文本（回车，不用 OCR）===")
    text = f"落小鱼 UI自动化自检 {time.strftime('%H:%M:%S')}"
    t0 = time.time()
    sent = uia.send_text(text)
    print(f"  send_text -> {sent}  ({time.time()-t0:.1f}s)")
    print(f"  内容: {text}")

    print("\n=== 4. 还原被最小化的窗口 ===")
    try:
        from wechatauto.guia import WeChatGUI

        gui = WeChatGUI()
        if hasattr(gui, "restore_blockers"):
            gui.restore_blockers()
            print("  已还原遮挡窗口")
        if hasattr(gui, "release_foreground"):
            gui.release_foreground()
            print("  已取消置顶")
    except Exception:  # noqa: BLE001
        print("  还原失败（不影响功能）：")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
