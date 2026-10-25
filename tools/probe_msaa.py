"""Enumerate MSAA children with the official oleacc.AccessibleChildren API.

This is the canonical way to walk IAccessible trees (handles simple vs object
children). If WeChat's Qt UI exposes real widgets, we see them here.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

import comtypes
import comtypes.client
from comtypes import GUID

HWND = 4130672
OBJID_CLIENT = -4
OBJID_WINDOW = 0

comtypes.client.GetModule("oleacc.dll")
from comtypes.gen import Accessibility as Acc

VARIANT = comtypes.automation.VARIANT
oleacc = ctypes.windll.oleacc


def enumerate_children(pacc, depth: int = 0, max_depth: int = 4, limit: int = 40) -> None:
    try:
        count = int(pacc.accChildCount or 0)
    except Exception as exc:  # noqa: BLE001
        print("  " * depth + f"<accChildCount failed: {exc}>")
        return
    if count <= 0:
        return
    arr = (VARIANT * count)()
    obtained = ctypes.c_long(0)
    hr = oleacc.AccessibleChildren(
        ctypes.cast(pacc, ctypes.c_void_p), ctypes.c_long(0), ctypes.c_long(count), arr, ctypes.byref(obtained)
    )
    if hr != 0:
        print("  " * depth + f"<AccessibleChildren hr=0x{hr & 0xFFFFFFFF:08X}>")
        return
    for i in range(obtained.value):
        v = arr[i]
        try:
            child = v.value
        except Exception as exc:  # noqa: BLE001
            print("  " * depth + f"[{i}] <variant failed {exc}>")
            continue
        if child is None:
            continue
        try:
            name = child.accName(0)
            role = child.accRole(0)
            state = child.accState(0)
            val = ""
            try:
                val = child.accValue(0) or ""
            except Exception:  # noqa: BLE001
                val = ""
            print(
                "  " * depth
                + f"[{i}] name={name!r} role={role!r} state={state!r} value={val!r}"
            )
            if depth < max_depth and i < limit:
                enumerate_children(child, depth + 1, max_depth, limit)
        except Exception as exc:  # noqa: BLE001
            print("  " * depth + f"[{i}] <query failed {type(exc).__name__}: {exc}>")


def dump(objid: int, label: str) -> None:
    iid = GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")
    pacc = ctypes.POINTER(Acc.IAccessible)()
    hr = oleacc.AccessibleObjectFromWindow(
        wt.HWND(HWND), ctypes.c_long(objid), ctypes.byref(iid), ctypes.byref(pacc)
    )
    print(f"=== {label} hr=0x{hr & 0xFFFFFFFF:08X}")
    if hr != 0 or not pacc:
        return
    print(f"root name={pacc.accName(0)!r} role={pacc.accRole(0)!r} count={pacc.accChildCount}")
    enumerate_children(pacc)


print("note: VARIANT import check ->", VARIANT)
dump(OBJID_CLIENT, "OBJID_CLIENT")
dump(OBJID_WINDOW, "OBJID_WINDOW")
