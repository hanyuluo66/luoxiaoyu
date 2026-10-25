"""Windows 凭据管理器读写（不走命令行）。

原先用 `cmdkey /pass:<明文>` 存 RDP 凭据，密码会出现在进程命令行里，
其它本地进程能在 `wmic process get commandline` 里看到 —— 这是个真实的泄露面。
这里直接用 CredWrite/CredRead，密码只在内存里传，不落命令行。
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
TARGET = "TERMSRV/localhost"


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi():
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def save(user: str, password: str, target: str = TARGET) -> bool:
    """把账户密码存进凭据管理器（Generic 凭据）。"""
    api = _advapi()
    blob = password.encode("utf-16-le")
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = user
    ok = bool(api.CredWriteW(ctypes.byref(cred), 0))
    if not ok:
        log.warning("CredWrite 失败，错误码 %s", ctypes.get_last_error())
    return ok


def load(target: str = TARGET) -> tuple[str, str] | None:
    """返回 (用户名, 密码)，不存在时返回 None。"""
    api = _advapi()
    ptr = ctypes.POINTER(CREDENTIALW)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        cred = ptr.contents
        user = cred.UserName or ""
        size = int(cred.CredentialBlobSize)
        if size <= 0:
            return user, ""
        raw = ctypes.string_at(ctypes.cast(cred.CredentialBlob, ctypes.c_void_p), size)
        return user, raw.decode("utf-16-le", errors="replace")
    finally:
        api.CredFree(ptr)


def delete(target: str = TARGET) -> bool:
    api = _advapi()
    return bool(api.CredDeleteW(target, CRED_TYPE_GENERIC, 0))


def exists(target: str = TARGET) -> bool:
    return load(target) is not None
