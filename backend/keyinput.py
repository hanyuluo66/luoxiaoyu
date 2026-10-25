"""真实按键输入（SendInput）。

微信 4.x 的搜索框只认**真实键盘事件**：用 UIA 的 ValuePattern 写进去的文字
不会触发搜索，界面看起来有字，但列表没过滤、回车也不会打开会话。
这里用 SendInput + KEYEVENTF_UNICODE 逐字输入，等价于人手敲键盘。
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_A = 0x41
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs: _INPUT) -> None:
    user32 = ctypes.windll.user32
    array = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


def _key(vk: int, up: bool = False) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    return inp


def _char(ch: str) -> list[_INPUT]:
    down = _INPUT()
    down.type = INPUT_KEYBOARD
    down.ki = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, None)
    up = _INPUT()
    up.type = INPUT_KEYBOARD
    up.ki = _KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
    return [down, up]


def type_text(text: str, delay: float = 0.03) -> None:
    """逐字输入（支持中文）。"""
    for ch in text:
        _send(*_char(ch))
        if delay:
            time.sleep(delay)


def press_enter() -> None:
    _send(_key(VK_RETURN), _key(VK_RETURN, True))


def press_escape() -> None:
    _send(_key(VK_ESCAPE), _key(VK_ESCAPE, True))


def select_all() -> None:
    _send(_key(VK_CONTROL), _key(VK_A), _key(VK_A, True), _key(VK_CONTROL, True))
