"""Read-only + send test for WeChat 4.x via wechatauto.

Patches the library's process lookup (the sandbox's Python sees a filtered
process list) and reports: DB keys, sessions, then a send to 文件传输助手.
"""
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
    print(f"Weixin pid = {pids}")
    if not pids:
        return 2

    from wechatauto import WeChatDB

    WeChatDB._find_weixin_pids = lambda self: list(pids)  # type: ignore[assignment]

    print("\n=== 数据库 ===")
    db = WeChatDB()
    try:
        keys = getattr(db, "_keys", None)
        if keys:
            print(f"  已加载密钥: {len(keys)} 个")
        sessions = db.get_sessions(limit=10)
        print(f"  会话数 = {len(sessions)}")
        for s in sessions[:10]:
            if isinstance(s, dict):
                name = s.get("remark") or s.get("nick_name") or s.get("username") or ""
                print(f"     {str(name)[:22]:24} unread={s.get('unread', 0)}")
            else:
                print(f"     {s}")
    except Exception:  # noqa: BLE001
        print("  读会话失败：")
        traceback.print_exc()

    print("\n=== 发送测试（目标：文件传输助手）===")
    try:
        from wechatauto import WeChat

        wx = WeChat()
        print("  WeChat 实例化成功")
        msg = f"落小鱼 4.x 自检 {time.strftime('%H:%M:%S')}"
        sender = None
        for name in ("SendMsg", "send_msg", "quick_send"):
            if hasattr(wx, name):
                sender = getattr(wx, name)
                print(f"  使用方法: {name}")
                break
        if sender is None:
            print("  可用方法:", [m for m in dir(wx) if not m.startswith("_")][:30])
            return 1
        try:
            result = sender(msg, TARGET)
        except TypeError:
            result = sender(TARGET, msg)
        print(f"  发送结果 = {result}")
    except Exception:  # noqa: BLE001
        print("  发送失败：")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
