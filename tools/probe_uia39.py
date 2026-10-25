"""Probe the UIA tree of the WeChat 3.9 login window.

If the controls are readable here, the 3.9 UI-automation approach is viable
(WeChat 4.x exposed nothing at all).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys


def find(cls: str) -> int:
    return ctypes.windll.user32.FindWindowW(cls, None)


def main() -> int:
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(3)
    for cls in ("WeChatMainWndForPC", "WeChatLoginWndForPC"):
        hwnd = find(cls)
        print(f"\n=== {cls}: hwnd={hwnd}")
        if not hwnd:
            print("   窗口不存在")
            continue
        win = auto.ControlFromHandle(hwnd)
        print(f"   root: name={win.Name!r} class={win.ClassName!r} type={win.ControlTypeName}")

        def walk(ctrl, depth: int, max_depth: int, out: list[str]) -> None:
            if depth > max_depth:
                return
            try:
                kids = ctrl.GetChildren()
            except Exception as exc:  # noqa: BLE001
                out.append("  " * depth + f"<children failed: {exc}>")
                return
            for kid in kids:
                name = (kid.Name or "").replace("\n", "\\n")
                if len(name) > 50:
                    name = name[:50] + "…"
                try:
                    rect = kid.BoundingRectangle
                    rs = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
                except Exception:  # noqa: BLE001
                    rs = "(?)"
                out.append(
                    "  " * depth
                    + f"[{kid.ControlTypeName}] {name!r} class={kid.ClassName!r} rect={rs}"
                )
                walk(kid, depth + 1, max_depth, out)

        lines: list[str] = []
        walk(win, 1, 4, lines)
        print(f"   控件总数（深度4内）: {len(lines)}")
        for line in lines[:40]:
            print("   " + line)
        if len(lines) > 40:
            print(f"   … 还有 {len(lines) - 40} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
