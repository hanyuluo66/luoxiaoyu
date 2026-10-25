"""Diagnose which accessibility API can see inside the Qt-based WeChat 4.x window.

Uses raw COM (comtypes) so we control the view walker explicitly:
  1. IUIAutomation ElementFromHandle + Raw/Control/Content view walkers
  2. MSAA IAccessible via oleacc.AccessibleObjectFromWindow (OBJID_CLIENT / OBJID_WINDOW)
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

import comtypes
import comtypes.client
from comtypes import GUID

HWND = 4130672
OBJID_WINDOW = 0
OBJID_CLIENT = -4


def uia_views() -> None:
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA

    iuia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    element = iuia.ElementFromHandle(HWND)
    if element is None:
        print("ElementFromHandle returned None")
        return
    print(f"UIA element: name={element.CurrentName!r} type={element.CurrentControlType}")

    for label, walker_getter in (
        ("raw", lambda: iuia.RawViewWalker),
        ("control", lambda: iuia.ControlViewWalker),
        ("content", lambda: iuia.ContentViewWalker),
    ):
        try:
            walker = walker_getter()
            child = walker.GetFirstChildElement(element)
            items = []
            while child is not None and len(items) < 25:
                try:
                    items.append(
                        f"(type={child.CurrentControlType},name={child.CurrentName!r},"
                        f"class={child.CurrentClassName!r})"
                    )
                except Exception as exc:  # noqa: BLE001
                    items.append(f"<err {exc}>")
                child = walker.GetNextSiblingElement(child)
            print(f"  UIA {label} children: {len(items)}")
            for it in items:
                print(f"      {it}")
        except Exception as exc:  # noqa: BLE001
            print(f"  UIA {label} FAILED: {type(exc).__name__}: {exc}")


def msaa(objid: int, label: str) -> None:
    try:
        comtypes.client.GetModule("oleacc.dll")
    except Exception as exc:  # noqa: BLE001
        print(f"  (module gen warning: {exc})")
    from comtypes.gen import Accessibility as Acc

    iid = GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")
    pacc = ctypes.POINTER(Acc.IAccessible)()
    hr = ctypes.windll.oleacc.AccessibleObjectFromWindow(
        wt.HWND(HWND), ctypes.c_long(objid), ctypes.byref(iid), ctypes.byref(pacc)
    )
    print(f"  MSAA objid={objid} ({label}) hr=0x{hr & 0xFFFFFFFF:08X} ptr={bool(pacc)}")
    if hr != 0 or not pacc:
        return
    try:
        name = pacc.accName(0)
        role = pacc.accRole(0)
        count = pacc.accChildCount
        print(f"    name={name!r} role={role!r} childCount={count}")
        for i in range(1, min(int(count or 0), 25) + 1):
            try:
                child = pacc.accChild(i)
                print(f"      child[{i}] name={child.accName(0)!r} role={child.accRole(0)!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"      child[{i}] <err {exc}>")
    except Exception as exc:  # noqa: BLE001
        print(f"    IAccessible access FAILED: {type(exc).__name__}: {exc}")


def main() -> int:
    print("=== UIA views ===")
    try:
        uia_views()
    except Exception as exc:  # noqa: BLE001
        print(f"UIA diagnostic FAILED: {type(exc).__name__}: {exc}")
    print("=== MSAA ===")
    for objid, label in ((OBJID_CLIENT, "OBJID_CLIENT"), (OBJID_WINDOW, "OBJID_WINDOW")):
        try:
            msaa(objid, label)
        except Exception as exc:  # noqa: BLE001
            print(f"  MSAA {label} FAILED: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
