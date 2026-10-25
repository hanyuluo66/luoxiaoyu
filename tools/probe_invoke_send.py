"""Try a fully background send: UIA Invoke for chat switching + 发送 button.

No mouse, no Enter keystroke, no foreground window required (if Invoke works).
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


def fg_title() -> str:
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(h, b, n + 1)
    return b.value


def cursor() -> tuple[int, int]:
    p = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return int(p.x), int(p.y)


def find_cell(win, target: str):
    """Session cell whose AutomationId is session_item_<target>."""
    stack = [(win, 0)]
    while stack:
        ctrl, depth = stack.pop()
        if depth > 22:
            continue
        try:
            kids = ctrl.GetChildren()
        except Exception:
            continue
        for ch in kids:
            try:
                aid = ch.AutomationId or ""
                cls = ch.ClassName or ""
                if cls == "mmui::ChatSessionCell" and aid.endswith(target):
                    return ch
                if cls == "mmui::ChatSessionCell" and (ch.Name or "").startswith(target):
                    return ch
            except Exception:
                pass
            stack.append((ch, depth + 1))
    return None


def find_button(win, exact: str, cls: str = "mmui::XOutlineButton"):
    stack = [(win, 0)]
    while stack:
        ctrl, depth = stack.pop()
        if depth > 22:
            continue
        try:
            kids = ctrl.GetChildren()
        except Exception:
            continue
        for ch in kids:
            try:
                if (ch.ClassName or "") == cls and (ch.Name or "").strip() == exact:
                    return ch
            except Exception:
                pass
            stack.append((ch, depth + 1))
    return None


def main() -> int:
    from backend.adapters.wechatauto_adapter import _patch_library, _weixin_pids

    pids = _weixin_pids()
    _patch_library(lambda: pids)
    from wechatauto.param import WxParam

    WxParam.ENABLE_OCR = False
    from wechatauto.uia_driver import WeChatUIA

    uia = WeChatUIA()
    print("材料化:", uia.ensure_materialized(timeout=10))
    win = uia._find_main()  # noqa: SLF001
    if win is None:
        print("✗ 没有主窗口")
        return 2

    before_fg, before_cur = fg_title(), cursor()
    print(f"开始前：前台={before_fg!r} 鼠标={before_cur}")

    # 1) 打开会话：Invoke 列表项（不点击）
    cell = find_cell(win, TARGET)
    print("找到会话项:", bool(cell), (cell.Name or "")[:24] if cell else "")
    if cell is None:
        return 3
    try:
        cell.GetInvokePattern().Invoke()
        print("  已 Invoke 会话项")
    except Exception as exc:  # noqa: BLE001
        print("  Invoke 失败:", exc)
        try:
            cell.GetLegacyIAccessiblePattern().DoDefaultAction()
            print("  已用 LegacyIAccessible 默认动作")
        except Exception as exc2:  # noqa: BLE001
            print("  默认动作也失败:", exc2)
            return 4
    time.sleep(1.2)
    print("  当前会话 =", uia.current_chat())

    # 2) 写入文字（ValuePattern，不聚焦）
    edit = uia._chat_input()  # noqa: SLF001
    print("输入框:", bool(edit))
    if edit is None:
        return 5
    text = f"后台Invoke测试 {time.strftime('%H:%M:%S')}"
    ok = uia._set_text(edit, text)  # noqa: SLF001
    print("  写值:", ok, " 回读:", uia._read_edit_value(edit))  # noqa: SLF001
    if not ok:
        return 6

    # 3) 点「发送」按钮：Invoke
    btn = find_button(win, "发送")
    print("找到发送按钮:", bool(btn), btn.ClassName if btn else "")
    if btn is None:
        return 7
    try:
        btn.GetInvokePattern().Invoke()
        print("  已 Invoke 发送按钮")
    except Exception as exc:  # noqa: BLE001
        print("  Invoke 发送失败:", exc)
        return 8
    time.sleep(1.5)

    print("  发送后输入框内容:", uia._read_edit_value(uia._chat_input()) if uia._chat_input() else None)  # noqa: SLF001
    after_fg, after_cur = fg_title(), cursor()
    print(f"结束后：前台={after_fg!r} 鼠标={after_cur}")
    print("鼠标未移动 =", before_cur == after_cur)
    print("前台未变 =", before_fg == after_fg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
