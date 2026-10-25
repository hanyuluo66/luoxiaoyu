"""Can WeChat 4.x be driven purely through UIA patterns (no focus, no mouse)?

Checks whether the tree exposes:
  * session-list items that support Select/Invoke  -> open a chat without clicking
  * a 发送 button that supports Invoke             -> send without pressing Enter
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from backend.adapters.wechatauto_adapter import _patch_library, _weixin_pids

    pids = _weixin_pids()
    _patch_library(lambda: pids)
    from wechatauto.param import WxParam

    WxParam.ENABLE_OCR = False
    from wechatauto.uia_driver import WeChatUIA

    uia = WeChatUIA()
    print("材料化控件树:", uia.ensure_materialized(timeout=10))
    win = uia._find_main()  # noqa: SLF001
    print("主窗口:", win.ClassName if win else None, "当前会话:", uia.current_chat())

    hits: list[str] = []

    def walk(ctrl, depth: int = 0):
        if depth > 22 or len(hits) > 4000:
            return
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for ch in children:
            try:
                name = ch.Name or ""
                aid = ch.AutomationId or ""
                cls = ch.ClassName or ""
                if "发送" in name or "send" in aid.lower() or "send" in name.lower():
                    hits.append(f"{cls} | name={name!r} | aid={aid!r} | type={ch.ControlTypeName}")
            except Exception:
                pass
            walk(ch, depth + 1)

    walk(win)
    print(f"\n=== 含「发送/send」的控件 {len(hits)} 个 ===")
    for h in hits[:15]:
        print("  ", h)

    # 会话列表项支持什么模式
    print("\n=== 会话列表项的模式支持 ===")
    from wechatauto.uia_driver import SESSION_LIST_AIDS, _aid_hit

    def find_session_list(c, d=0):
        if d > 20:
            return None
        for ch in c.GetChildren():
            try:
                if _aid_hit(getattr(ch, "AutomationId", ""), SESSION_LIST_AIDS):
                    return ch
            except Exception:
                pass
            r = find_session_list(ch, d + 1)
            if r is not None:
                return r
        return None

    lst = find_session_list(win)
    print("  会话列表控件:", (lst.ClassName, lst.AutomationId) if lst else None)
    if lst:
        items = lst.GetChildren()[:3]
        for it in items:
            try:
                pats = []
                for pname in ("SelectionItem", "Invoke", "LegacyIAccessible"):
                    try:
                        p = it.GetPattern(10000)  # placeholder
                    except Exception:
                        pass
                print(
                    f"  项: {it.ClassName} name={it.Name!r} aid={it.AutomationId!r} "
                    f"types={[m for m in dir(it) if 'Pattern' in m][:6]}"
                )
            except Exception as exc:  # noqa: BLE001
                print("   读取失败:", exc)

    # 输入框和发送按钮
    print("\n=== 输入框 ===")
    edit = uia._chat_input()  # noqa: SLF001
    print("  ", (edit.ClassName, edit.Name, edit.AutomationId) if edit else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
