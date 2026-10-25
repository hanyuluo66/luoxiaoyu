"""Probe WeChat 4.x automation: login state, Qt accessibility gate, DB access.

Run:  .venv\\Scripts\\python.exe tools\\probe_wx4.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def windows() -> list[tuple[int, str, str, int]]:
    user32 = ctypes.windll.user32
    out: list[tuple[int, str, str, int]] = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            n = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, title, n + 1)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((int(hwnd), cls.value, title.value, int(pid.value)))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    step("1. 微信窗口")
    wins = [w for w in windows() if "Weixin" in w[1] or "微信" in w[2] or w[1].startswith("Qt5")]
    for hwnd, cls, title, pid in wins[:12]:
        print(f"  hwnd={hwnd} pid={pid} class={cls!r} title={title!r}")
    if not wins:
        print("  ✗ 没看到微信窗口")
        return 2

    step("2. 找到主进程 pid（加载 Weixin.dll 的）")
    import psutil  # noqa: F401  只为确认可用性

    weixin_pids: list[int] = []
    try:
        from wechatauto import WeChat as Wx4  # noqa: F401

        print("  wechatauto 导入成功")
    except Exception as exc:  # noqa: BLE001
        print(f"  wechatauto 导入失败: {exc}")
        traceback.print_exc()
        return 3

    # 用窗口 pid 反查（不依赖 psutil 的进程枚举）
    for hwnd, cls, title, pid in wins:
        try:
            import win32gui
            import win32process
            from win32con import PROCESS_QUERY_INFORMATION, PROCESS_VM_READ

            pids = set()
            def cb(h, _):
                pids.add(win32process.GetWindowThreadProcessId(h)[1])
                return True
            win32gui.EnumWindows(cb, None)
            weixin_pids = sorted(pids)
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  枚举失败: {exc}")
    print(f"  窗口所属 pid 集合大小 = {len(weixin_pids)}")

    step("3. UIA 热激活（闸门字节）")
    try:
        from wechatauto.uia_driver import WeChatUIA

        uia = WeChatUIA()
        ok = uia.ensure_window(timeout=20)
        print(f"  ensure_window -> {ok}")
        rep = uia.describe_layout()
        print(f"  布局: {rep.get('layout')}")
        print(f"  主窗类名: {rep.get('main_class')!r}  标题: {rep.get('title')!r}")
        for name, desc in (rep.get("anchors") or {}).items():
            print(f"    {name}: {desc}")
    except Exception:  # noqa: BLE001
        print("  ✗ UIA 道路失败：")
        traceback.print_exc()

    step("4. 数据库定位与解密")
    try:
        from wechatauto import WeChatDB, auto_detect_db_dir, list_accounts

        try:
            root = auto_detect_db_dir()
            print(f"  自动检测到数据目录: {root}")
        except Exception as exc:  # noqa: BLE001
            print(f"  自动检测失败: {exc}")
        try:
            accounts = list_accounts()
            print(f"  账号列表: {accounts}")
        except Exception:  # noqa: BLE001
            traceback.print_exc()

        db = WeChatDB()
        print("  WeChatDB 实例化成功")
        try:
            sessions = db.list_sessions(limit=10)
            print(f"  会话数 {len(sessions)}：")
            for s in sessions[:10]:
                print(f"     {s}")
        except Exception:  # noqa: BLE001
            print("  list_sessions 失败：")
            traceback.print_exc()
    except Exception:  # noqa: BLE001
        print("  ✗ 数据库道路失败：")
        traceback.print_exc()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
