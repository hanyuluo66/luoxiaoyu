"""Patch the WeChat client's in-memory version integer (version spoof).

Why: Tencent's server rejects old clients at login ("当前微信版本过低"). The client
reports its version from an integer stored in WeChatWin.dll's data section. Rewriting
that integer in the running process makes the server see a supported version while
the actual UI is still 3.9.x — which is what wxauto can automate.

Mechanism is intentionally narrow: locate WeChatWin.dll in the target process, scan
its loaded image for the little-endian 4-byte version value, overwrite with the target
value. Nothing else in the process is touched.

Usage:
    python tools/spoof_version.py --current 3.9.12.17 --target 4.1.15.13 --dry-run
    python tools/spoof_version.py --current 3.9.12.17 --target 4.1.15.13 --apply
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * MAX_PATH),
    ]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def version_to_hex(version: str) -> int:
    """'3.9.12.17' -> 0x63090c11 (same encoding Tencent uses)."""
    parts = [int(p) for p in version.split(".")]
    if len(parts) != 4:
        raise ValueError("版本号必须是 x.y.z.w 四段")
    text = "6" + f"{parts[0]:x}" + "".join(f"{p:02x}" for p in parts[1:])
    return int(text, 16)


def pids_of(exe_name: str) -> list[int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot(process) 失败")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    found: list[int] = []
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                found.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return found


def modules_of(pid: int) -> list[tuple[str, int, int]]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), f"CreateToolhelp32Snapshot(module) 失败 pid={pid}")
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
    out: list[tuple[str, int, int]] = []
    try:
        ok = kernel32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.szModule, int(entry.modBaseAddr or 0), int(entry.modBaseSize)))
            ok = kernel32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


def open_process(pid: int) -> int:
    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
        False,
        pid,
    )
    if not handle:
        raise OSError(
            ctypes.get_last_error(),
            f"OpenProcess 失败 pid={pid}（权限不足？试试用管理员运行）",
        )
    return handle


def read_mem(handle: int, address: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
    )
    if not ok:
        return b""
    return buf.raw[: read.value]


def write_mem(handle: int, address: int, data: bytes) -> bool:
    written = ctypes.c_size_t(0)
    buf = ctypes.create_string_buffer(data, len(data))
    return bool(
        kernel32.WriteProcessMemory(
            handle, ctypes.c_void_p(address), buf, len(data), ctypes.byref(written)
        )
    )


def find_offsets(handle: int, base: int, size: int, needle: int, chunk: int = 4 << 20) -> list[int]:
    pattern = needle.to_bytes(4, "little")
    hits: list[int] = []
    overlap = len(pattern) - 1
    tail = b""
    for offset in range(0, size, chunk):
        data = read_mem(handle, base + offset, min(chunk, size - offset))
        if not data:
            tail = b""
            continue
        buf = tail + data
        start = 0
        while True:
            idx = buf.find(pattern, start)
            if idx < 0:
                break
            absolute = offset - len(tail) + idx
            if absolute >= 0:
                hits.append(absolute)
            start = idx + 1
        tail = buf[-overlap:] if overlap else b""
    return hits


def patch(current: str, target: str, apply: bool, pid_hint: int | None) -> int:
    old = version_to_hex(current)
    new = version_to_hex(target)
    print(f"当前版本 {current} -> 0x{old:08x}")
    print(f"目标版本 {target} -> 0x{new:08x}")

    candidates = [pid_hint] if pid_hint else pids_of("WeChat.exe")
    if not candidates:
        print("✗ 没找到 WeChat.exe 进程，请先启动微信")
        return 2
    print(f"候选进程: {candidates}")

    target_pid = None
    dll_base = dll_size = 0
    for pid in candidates:
        try:
            mods = modules_of(pid)
        except OSError as exc:
            print(f"  pid={pid} 枚举模块失败: {exc}")
            continue
        for name, base, size in mods:
            if name.lower() == "wechatwin.dll":
                target_pid, dll_base, dll_size = pid, base, size
                break
        if target_pid:
            break
    if not target_pid:
        print("✗ 没有进程加载 WeChatWin.dll（微信可能还没开始登录）")
        return 3
    print(f"目标进程 pid={target_pid}, WeChatWin.dll base=0x{dll_base:x} size={dll_size/1024/1024:.1f}MB")

    handle = open_process(target_pid)
    try:
        hits = find_offsets(handle, dll_base, dll_size, old)
        print(f"扫描到 {len(hits)} 处匹配")
        for offset in hits[:20]:
            print(f"   0x{dll_base + offset:x} (offset 0x{offset:x})")
        if not hits:
            print("✗ 没找到版本号，确认 --current 是否与微信实际版本一致")
            return 4
        if not apply:
            print("（只读模式，未修改内存。加 --apply 执行写入）")
            return 0

        ok_count = 0
        for offset in hits:
            if write_mem(handle, dll_base + offset, new.to_bytes(4, "little")):
                ok_count += 1
        print(f"✓ 已写入 {ok_count}/{len(hits)} 处")

        verify = find_offsets(handle, dll_base, dll_size, old)
        now = find_offsets(handle, dll_base, dll_size, new)
        print(f"验证：旧值剩余 {len(verify)} 处，新值存在 {len(now)} 处")
        return 0 if (not verify and now) else 5
    finally:
        kernel32.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pid", type=int, default=None)
    args = parser.parse_args()
    return patch(args.current, args.target, args.apply and not args.dry_run, args.pid)


if __name__ == "__main__":
    sys.exit(main())
