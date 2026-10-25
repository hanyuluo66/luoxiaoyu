"""Probe WeChat 4.x automation with the process-lookup workaround.

The DSH-hosted Python sees a filtered process list, so the library's
`tasklist`-based lookup finds nothing. We resolve the pid from the WeChat window
handle instead and patch it in, then test: DB key extraction and the Qt
accessibility gate.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def weixin_pids_from_windows() -> list[int]:
    """All pids owning a visible Qt window whose process is Weixin.exe."""
    u = ctypes.windll.user32
    hits: list[int] = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if cls.value.startswith("Qt5"):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                hits.append(int(pid.value))
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return sorted(set(hits))


def exe_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name() or ""
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    print("=== 1. 从窗口反查微信 pid ===")
    pids = [p for p in weixin_pids_from_windows() if exe_name(p).lower() == "weixin.exe"]
    print(f"  Weixin.exe pid = {pids}")
    if not pids:
        print("  ✗ 没找到，确认微信已登录且窗口没关")
        return 2

    print("\n=== 2. 打补丁：让 WeChatDB 用窗口反查的 pid ===")
    from wechatauto import WeChatDB

    orig = WeChatDB._find_weixin_pids
    WeChatDB._find_weixin_pids = lambda self: list(pids)  # type: ignore[assignment]
    print("  已替换 _find_weixin_pids")

    print("\n=== 3. 数据库：定位 + 提取密钥 + 读会话 ===")
    try:
        db = WeChatDB()
        print("  WeChatDB 实例化成功")
        try:
            sessions = db.list_sessions(limit=8)
            print(f"  会话数 = {len(sessions)}")
            for s in sessions[:8]:
                if isinstance(s, dict):
                    print(f"     {s.get('username','?')[:28]:30} {str(s.get('remark') or s.get('nick_name') or '')[:16]}")
                else:
                    print(f"     {s}")
        except Exception:  # noqa: BLE001
            print("  list_sessions 失败：")
            traceback.print_exc()
    except Exception:  # noqa: BLE001
        print("  ✗ 数据库失败：")
        traceback.print_exc()

    print("\n=== 4. UIA：窗口筛选（含 Weixin.dll 模块校验）===")
    try:
        from wechatauto.uia_driver import WeChatUIA

        uia = WeChatUIA()
        diag: dict = {}
        hwnds = uia._wechat_hwnds(diag)  # noqa: SLF001
        print(f"  diag = {diag}")
        print(f"  可用窗口 = {hwnds}")
        if hwnds:
            print("\n=== 5. 热激活闸门字节 ===")
            ok = uia._hot_activate_accessibility(hwnds[0])  # noqa: SLF001
            print(f"  热激活结果 = {ok}")
            rep = uia.describe_layout()
            print(f"  布局 = {rep.get('layout')}  主窗类名 = {rep.get('main_class')!r}")
            for name, desc in (rep.get("anchors") or {}).items():
                print(f"     {name}: {desc}")
        else:
            print("  ✗ 没有可用窗口，热激活跳过")
    except Exception:  # noqa: BLE001
        print("  ✗ UIA 失败：")
        traceback.print_exc()

    WeChatDB._find_weixin_pids = orig  # type: ignore[assignment]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
