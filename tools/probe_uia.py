"""Probe WeChat UI tree using the `uiautomation` COM client.

Finds the Qt WeChat main window (class Qt51514QWindowIcon / title 微信) and dumps
its control tree with name, class, control type, automation id and rect.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys

import uiautomation as auto

user32 = ctypes.windll.user32


def find_wechat_handles() -> list[tuple[int, int, str, str]]:
    """Return (hwnd, pid, class, title) for visible WeChat top-level windows."""
    results: list[tuple[int, int, str, str]] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = title.value
        if "微信" in t or cls.value.startswith("Qt5"):
            results.append((int(hwnd), int(pid.value), cls.value, t))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--out", default=None)
    parser.add_argument("--root-list", action="store_true", help="dump UIA root children")
    parser.add_argument("--hwnd", type=int, default=None, help="explicit window handle")
    args = parser.parse_args()

    auto.SetGlobalSearchTimeout(2)
    print("=== native enumeration ===")
    handles = find_wechat_handles()
    for hwnd, pid, cls, title in handles:
        print(f"  hwnd={hwnd} pid={pid} class={cls!r} title={title!r}")
    if not handles:
        print("no native WeChat window", file=sys.stderr)
        return 2

    if args.root_list:
        print("=== uia root children ===")
        root = auto.GetRootControl()
        for child in root.GetChildren():
            try:
                print(
                    f"  name={child.Name!r} class={child.ClassName!r} "
                    f"type={child.ControlTypeName} pid={child.ProcessId}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  <failed: {exc}>")

    hwnd, pid, cls, title = handles[0]
    if args.hwnd:
        hwnd = args.hwnd
    else:
        for cand in handles:
            if cand[2].startswith("Qt5"):
                hwnd, pid, cls, title = cand
                break
    print(f"=== using hwnd={hwnd} class={cls!r} title={title!r}")
    lines: list[str] = []
    try:
        win = auto.ControlFromHandle(hwnd)
    except Exception as exc:  # noqa: BLE001
        print(f"ControlFromHandle FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(f"=== main window control: name={win.Name!r} class={win.ClassName!r} type={win.ControlTypeName}")

    def walk(ctrl, depth: int, out: list[str]) -> None:
        try:
            rect = ctrl.BoundingRectangle
            rect_s = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
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
            f"{indent}[{ctrl.ControlTypeName}] name={name!r} "
            f"class={ctrl.ClassName!r} id={aid!r} rect={rect_s}"
        )
        if depth >= args.depth:
            return
        try:
            kids = ctrl.GetChildren()
        except Exception as exc:  # noqa: BLE001
            out.append(f"{indent}  <GetChildren failed: {exc}>")
            return
        for kid in kids:
            walk(kid, depth + 1, out)

    walk(win, 0, lines)
    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"written {len(lines)} lines -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
