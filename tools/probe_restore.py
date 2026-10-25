"""Inspect all top-level windows of the WeChat process and dump the tree after restore."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import uiautomation as auto

user32 = ctypes.windll.user32
user32.ShowWindow.restype = wt.BOOL
user32.IsWindowVisible.restype = wt.BOOL

SW_RESTORE = 9
SW_SHOW = 5


def windows_of_pid(pid: int) -> list[dict]:
    found: list[dict] = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):  # noqa: ANN001
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if int(p.value) != pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        n = user32.GetWindowTextLengthW(hwnd)
        t = ctypes.create_unicode_buffer(max(n + 1, 2))
        user32.GetWindowTextW(hwnd, t, n + 1)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        parent = user32.GetParent(hwnd)
        found.append(
            {
                "hwnd": int(hwnd),
                "class": cls.value,
                "title": t.value,
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "rect": (r.left, r.top, r.right, r.bottom),
                "parent": int(parent) if parent else 0,
            }
        )
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return found


def dump(ctrl, depth: int, max_depth: int, out: list[str]) -> None:
    try:
        r = ctrl.BoundingRectangle
        rect_s = f"({r.left},{r.top},{r.right},{r.bottom})"
    except Exception:  # noqa: BLE001
        rect_s = "(?)"
    indent = "  " * depth
    name = (ctrl.Name or "").replace("\n", "\\n")
    if len(name) > 70:
        name = name[:70] + "…"
    try:
        aid = ctrl.AutomationId or ""
    except Exception:  # noqa: BLE001
        aid = "?"
    out.append(
        f"{indent}[{ctrl.ControlTypeName}] name={name!r} class={ctrl.ClassName!r} id={aid!r} rect={rect_s}"
    )
    if depth >= max_depth:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception as exc:  # noqa: BLE001
        out.append(f"{indent}  <GetChildren failed: {exc}>")
        return
    for kid in kids:
        dump(kid, depth + 1, max_depth, out)


def main() -> int:
    auto.SetGlobalSearchTimeout(3)
    pid = 26748
    wins = windows_of_pid(pid)
    print(f"=== top-level windows of pid {pid}: {len(wins)}")
    for w in wins:
        print(
            f"  hwnd={w['hwnd']} class={w['class']!r} title={w['title']!r} "
            f"visible={w['visible']} rect={w['rect']} parent={w['parent']}"
        )

    target = None
    for w in wins:
        if w["class"].startswith("Qt5") and w["visible"]:
            target = w
            break
    if target is None:
        print("no visible Qt window for WeChat", flush=True)
        return 2

    hwnd = target["hwnd"]
    print(f"=== restoring hwnd={hwnd}")
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.SetForegroundWindow(hwnd)
    time.sleep(2)

    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"after restore rect=({r.left},{r.top},{r.right},{r.bottom})")

    win = auto.ControlFromHandle(hwnd)
    print(f"control name={win.Name!r} class={win.ClassName!r} type={win.ControlTypeName}")
    lines: list[str] = []
    dump(win, 0, 8, lines)
    text = "\n".join(lines)
    with open(".tmp/wx_uia_restored.txt", "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"written {len(lines)} lines -> .tmp/wx_uia_restored.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
