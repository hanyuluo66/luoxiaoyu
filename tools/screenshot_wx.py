"""Capture the WeChat main window to a PNG for visual inspection."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

from PIL import Image, ImageGrab

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

HWND = int(sys.argv[1]) if len(sys.argv) > 1 else 4130672
OUT = sys.argv[2] if len(sys.argv) > 2 else ".tmp/wx_window.png"

user32.ShowWindow(HWND, 9)  # SW_RESTORE
user32.SetForegroundWindow(HWND)
import time

time.sleep(1.0)

rect = wt.RECT()
user32.GetWindowRect(HWND, ctypes.byref(rect))
print(f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")

img = None
# Preferred: PrintWindow captures even partially occluded content.
hdc_screen = user32.GetDC(HWND)
mfc_dc = gdi32.CreateCompatibleDC(hdc_screen)
width = rect.right - rect.left
height = rect.bottom - rect.top
bmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
gdi32.SelectObject(mfc_dc, bmp)
PW_RENDERFULLCONTENT = 0x00000002
ok = user32.PrintWindow(HWND, mfc_dc, PW_RENDERFULLCONTENT)
print(f"PrintWindow ok={ok} size={width}x{height}")

if ok:
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

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mfc_dc, bmp, 0, height, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")

gdi32.DeleteObject(bmp)
gdi32.DeleteDC(mfc_dc)
user32.ReleaseDC(HWND, hdc_screen)

if img is None:
    print("falling back to screen grab")
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)

img.save(OUT)
print(f"saved {OUT} {img.size}")
