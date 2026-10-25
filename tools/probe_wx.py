"""Probe the WeChat 4.x (Qt) UI Automation tree.

Usage: python tools/probe_wx.py [--pid N] [--depth D] [--out FILE]
Prints control type / name / automation id / class / rect for each node.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from pywinauto import Desktop


def walk(ctrl, depth: int, max_depth: int, out: list[str]) -> None:
    try:
        rect = ctrl.rectangle()
        rect_s = f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
    except Exception:  # noqa: BLE001
        rect_s = "(?)"
    indent = "  " * depth
    try:
        ctype = ctrl.element_info.control_type
    except Exception:  # noqa: BLE001
        ctype = "?"
    name = (ctrl.element_info.name or "").replace("\n", "\\n")
    if len(name) > 60:
        name = name[:60] + "…"
    auto_id = ctrl.element_info.automation_id or ""
    class_name = ctrl.element_info.class_name or ""
    out.append(
        f"{indent}[{ctype}] name={name!r} id={auto_id!r} class={class_name!r} rect={rect_s}"
    )
    if depth >= max_depth:
        return
    try:
        children = ctrl.children()
    except Exception as exc:  # noqa: BLE001
        out.append(f"{indent}  <children failed: {exc}>")
        return
    for child in children:
        walk(child, depth + 1, max_depth, out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--depth", type=int, default=6)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    pid = args.pid
    windows = []
    for win in Desktop(backend="uia").windows():
        try:
            if win.window_text() == "微信" or "微信" in (win.window_text() or ""):
                windows.append(win)
        except Exception:  # noqa: BLE001
            continue
    if pid:
        windows = [w for w in windows if w.process_id() == pid] or windows

    print(f"matched windows: {len(windows)}")
    for win in windows:
        try:
            print(
                f"  pid={win.process_id()} title={win.window_text()!r} "
                f"class={win.class_name()!r} rect={win.rectangle()}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  <info failed: {exc}>")

    if not windows:
        print("no WeChat window found", file=sys.stderr)
        return 2

    lines: list[str] = []
    for win in windows:
        lines.append("=" * 100)
        try:
            lines.append(f"WINDOW pid={win.process_id()} title={win.window_text()!r}")
        except Exception:  # noqa: BLE001
            lines.append("WINDOW <info failed>")
        try:
            walk(win, 0, args.depth, lines)
        except Exception:  # noqa: BLE001
            lines.append(traceback.format_exc())

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"written {len(lines)} lines to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
