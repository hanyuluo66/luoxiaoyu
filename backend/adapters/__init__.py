"""Adapter package."""
from __future__ import annotations

from .base import AdapterStatus, BaseAdapter, classify_content
from .hook_adapter import HookAdapter
from .mock import MockAdapter
from .wxauto_adapter import WxautoAdapter

__all__ = [
    "AdapterStatus",
    "BaseAdapter",
    "classify_content",
    "HookAdapter",
    "MockAdapter",
    "WxautoAdapter",
    "WechatautoAdapter",
    "build_adapter",
]


def build_adapter(kind: str, on_message, **kwargs):
    """Factory used by the runtime.

    kind:
      * "hook"       — WeChat 4.x with the injected hook DLL (true background:
                       messages are pushed to us, sending goes over local HTTP)
      * "wechatauto" — WeChat 4.x UI automation (local DB read + UIA send)
      * "wxauto"     — WeChat 3.9.x (UI polling), legacy fallback
      * "mock"       — no WeChat at all, for trying the console out
    """
    if kind == "mock":
        return MockAdapter(on_message, auto_emit=kwargs.get("auto_emit", False))
    if kind == "wxauto":
        return WxautoAdapter(
            on_message,
            on_self_message=kwargs.get("on_self_message"),
            poll_interval=kwargs.get("poll_interval", 1.5),
            debug=kwargs.get("debug", False),
        )
    if kind == "hook":
        return HookAdapter(
            on_message,
            on_self_message=kwargs.get("on_self_message"),
            poll_interval=kwargs.get("poll_interval", 1.0),
        )

    from .wechatauto_adapter import WechatautoAdapter

    return WechatautoAdapter(
        on_message,
        on_self_message=kwargs.get("on_self_message"),
        poll_interval=kwargs.get("poll_interval", 1.0),
    )
