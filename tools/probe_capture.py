"""Test whether the Qt WeChat window can be captured while NOT in the foreground.

Tries PrintWindow (several flags) and BitBlt from the window DC, saving each
result so they can be compared visually.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

HWND = 4130672
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000

user32.ShowWindow(HWND, 9)
time.sleep(0.8)

rect = wt.RECT()
user32.GetWindowRect(HWND, ctypes.byref(rect))
width = rect.right - rect.left
height = rect.bottom - rect.top
print(f"window rect=({rect.left},{rect.top},{rect.right},{rect.bottom}) {width}x{height}")


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def read_dib(mem_dc, bmp) -> Image.Image:
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bi), 0)
    return Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")


def make_dc():
    screen_dc = user32.GetDC(HWND)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(mem_dc, bmp)
    return screen_dc, mem_dc, bmp


def cleanup(screen_dc, mem_dc, bmp):
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(HWND, screen_dc)


# --- test 1: PrintWindow flags 0, 1, 2
for flags, label in ((0, "pw0"), (1, "pw1"), (2, "pw2")):
    sdc, mdc, bmp = make_dc()
    ok = user32.PrintWindow(HWND, mdc, flags)
    img = read_dib(mdc, bmp)
    # non-blank heuristic
    colors = img.getcolors(maxcolors=1_000_000) or []
    uniq = len(colors)
    path = f".tmp/cap_{label}.png"
    img.save(path)
    print(f"PrintWindow flags={flags} ok={ok} uniqueColors={uniq} -> {path}")
    cleanup(sdc, mdc, bmp)

# --- test 2: BitBlt from window DC (fails when occluded, sanity check)
sdc, mdc, bmp = make_dc()
ok = gdi32.BitBlt(mdc, 0, 0, width, height, sdc, 0, 0, SRCCOPY | CAPTUREBLT)
img = read_dib(mdc, bmp)
uniq = len(img.getcolors(maxcolors=1_000_000) or [])
img.save(".tmp/cap_bitblt.png")
print(f"BitBlt ok={ok} uniqueColors={uniq} -> .tmp/cap_bitblt.png")
cleanup(sdc, mdc, bmp)
