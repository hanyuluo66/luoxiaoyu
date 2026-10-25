"""收发图片与表情包。

两条线：
  * 收：把微信里的图片消息下载、转成标准图片（jpg/png），表情包顺手存进表情库。
  * 发：把本地图片塞进剪贴板（PowerShell + System.Drawing），再在微信里 Ctrl+V + Enter。

微信 4.x 的图片在库里是 HEVC 封装的 wxgf 容器，转码依赖 ffmpeg。没有 ffmpeg 时
只会落下原始 .wxgf 文件（收得到、发不出去、也看不清）。
"""
from __future__ import annotations

import ctypes
import logging
import random
from pathlib import Path

from . import spawn

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
STICKER_SENT_SUFFIX = "\0"  # 占位，避免误用


def media_root() -> Path:
    from .paths import data_dir

    root = data_dir() / "media"
    root.mkdir(parents=True, exist_ok=True)
    return root


def inbox_dir() -> Path:
    d = media_root() / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sticker_dir() -> Path:
    d = media_root() / "stickers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_image_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def list_stickers() -> list[Path]:
    out: list[Path] = []
    for path in sticker_dir().glob("*"):
        if path.is_file() and is_image_file(path):
            out.append(path)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def save_sticker(src: str | Path) -> Path | None:
    """把一张图收进表情库（按内容去重）。"""
    src_path = Path(src)
    if not src_path.exists() or not is_image_file(src_path):
        return None
    try:
        data = src_path.read_bytes()
    except OSError:
        return None
    import hashlib

    digest = hashlib.sha1(data).hexdigest()[:16]
    target = sticker_dir() / f"{digest}{src_path.suffix.lower()}"
    if target.exists():
        return target
    try:
        target.write_bytes(data)
    except OSError:
        return None
    log.info("表情包 +1：%s（共 %d 张）", target.name, len(list_stickers()))
    return target


def pick_sticker() -> Path | None:
    items = list_stickers()
    if not items:
        return None
    # 偏好最近收藏的，但带一点随机，别每次都同一张
    pool = items[: min(len(items), 40)]
    return random.choice(pool)


def copy_to_clipboard(path: str | Path) -> bool:
    """用 PowerShell 把图片放进剪贴板（前台程序粘贴即可得到图片）。"""
    target = str(Path(path).resolve()).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        f"$img=[System.Drawing.Image]::FromFile('{target}');"
        "[System.Windows.Forms.Clipboard]::SetImage($img);"
        "$img.Dispose()"
    )
    try:
        proc = spawn.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if proc.returncode == 0:
            return True
        log.warning("复制图片到剪贴板失败：%s", (proc.stderr or "").strip()[:160])
        return False
    except Exception:  # noqa: BLE001
        log.exception("复制图片到剪贴板异常")
        return False


def cleanup_inbox(max_files: int = 400) -> None:
    """别让收件箱无限长大。"""
    try:
        files = sorted(
            inbox_dir().glob("*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for old in files[max_files:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass

# --------------------------------------------------------------- 剪贴板（文件）
class _DROPFILES(ctypes.Structure):
    """CF_HDROP 需要的文件拖放结构（参考 WeeMessenger 的实现）。"""

    _fields_ = [
        ("pFiles", ctypes.c_uint),
        ("pt", ctypes.c_long * 2),
        ("fNC", ctypes.c_int),
        ("fWide", ctypes.c_int),
    ]


def copy_file_to_clipboard(path: str | Path) -> bool:
    """把文件以 CF_HDROP 格式放入剪贴板。

    微信粘贴这种格式会当成「图片/文件」发出去，**GIF 能保留动画**；
    而 SetImage 只带一张位图，动图会变成首帧静止图。
    """
    target = str(Path(path).resolve())
    if not Path(target).exists():
        return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # 64 位指针：不声明的话句柄会被截断成 32 位，GlobalLock 必然失败
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    payload = (target + "\x00\x00").encode("utf-16-le")
    header_size = ctypes.sizeof(_DROPFILES)
    total = header_size + len(payload)
    GMEM_MOVEABLE = 0x0002
    CF_HDROP = 15

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, total)
    if not handle:
        log.warning("GlobalAlloc 失败：%s", ctypes.get_last_error())
        return False
    try:
        locked = kernel32.GlobalLock(handle)
        if not locked:
            log.warning("GlobalLock 失败：%s", ctypes.get_last_error())
            return False
        try:
            drop = _DROPFILES()
            drop.pFiles = header_size
            drop.fWide = 1
            ctypes.memmove(locked, ctypes.addressof(drop), header_size)
            ctypes.memmove(locked + header_size, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            log.warning("OpenClipboard 失败：%s", ctypes.get_last_error())
            return False
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_HDROP, handle):
                log.warning("SetClipboardData 失败：%s", ctypes.get_last_error())
                return False
            handle = None  # 内存已交给剪贴板
        finally:
            user32.CloseClipboard()
        return True
    finally:
        if handle:
            kernel32.GlobalFree(handle)

def copy_image_for_paste(path: str | Path) -> bool:
    """动图走 CF_HDROP（保留动画），静态图先用 SetImage，失败再退回文件格式。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".gif":
        if copy_file_to_clipboard(path):
            log.info("GIF 已放入剪贴板（CF_HDROP，保留动画）")
            return True
    if copy_to_clipboard(path):
        return True
    return copy_file_to_clipboard(path)