"""Launch a process inside another Windows session by duplicating a token.

No SeTcbPrivilege needed: we borrow the token of a process that already lives
in the target session (same user), so the child runs in that session with the
same identity. Used to start the bot inside the RDP child session so its UI
automation never touches the user's own session.

Usage:
    python tools/session_launch.py --session 2 --exe "E:\\path\\app.exe" [--args "..."]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_DEFAULT = 0x0080
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_ADJUST_SESSIONID = 0x0100
MAXIMUM_ALLOWED = 0x02000000
SecurityImpersonation = 2
TokenPrimary = 1
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NEW_CONSOLE = 0x00000010


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


def _err(msg: str) -> str:
    return f"{msg} (Win32 {ctypes.get_last_error()})"


def pids_in_session(session_id: int) -> list[tuple[int, str]]:
    """Enumerate (pid, name) pairs living in the given session."""
    import ctypes.wintypes as w

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", w.DWORD),
            ("cntUsage", w.DWORD),
            ("th32ProcessID", w.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", w.DWORD),
            ("cntThreads", w.DWORD),
            ("th32ParentProcessID", w.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", w.DWORD),
            ("szExeFile", w.WCHAR * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out: list[tuple[int, str]] = []
    if snap in (0, -1):
        return out
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        sid = wt.DWORD()
        if kernel32.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(sid)) and sid.value == session_id:
            out.append((entry.th32ProcessID, entry.szExeFile))
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    return out


def _enable_privileges(*names: str) -> list[str]:
    """Enable the given privileges on our own token (needs an elevated token)."""

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 8)]

    kernel32.GetCurrentProcess.restype = wt.HANDLE
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.OpenProcessToken.restype = wt.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
    advapi32.AdjustTokenPrivileges.argtypes = [
        wt.HANDLE,
        wt.BOOL,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.AdjustTokenPrivileges.restype = wt.BOOL
    token = wt.HANDLE()
    results: list[str] = []
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_QUERY | TOKEN_ADJUST_PRIVILEGES,
        ctypes.byref(token),
    ):
        return [f"OpenProcessToken 失败 {ctypes.get_last_error()}"]
    try:
        for name in names:
            luid = LUID()
            if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                results.append(f"{name}: 没有该特权")
                continue
            tp = TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED
            ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
            err = ctypes.get_last_error()
            results.append(f"{name}: {'已启用' if ok and err == 0 else f'失败({err})'}")
    finally:
        kernel32.CloseHandle(token)
    return results


def launch_in_session(session_id: int, exe: str, args: str = "", workdir: str | None = None) -> int:
    for line in _enable_privileges(
        "SeAssignPrimaryTokenPrivilege", "SeIncreaseQuotaPrivilege", "SeImpersonatePrivilege"
    ):
        print("  特权:", line)
    candidates = [p for p in pids_in_session(session_id) if p[1].lower() != "system"]
    if not candidates:
        raise RuntimeError(f"会话 {session_id} 里没有任何可用进程，无法借用令牌")

    last_err = ""
    for pid, name in candidates:
        proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not proc:
            last_err = _err(f"OpenProcess({name}#{pid})")
            continue
        token = wt.HANDLE()
        try:
            if not advapi32.OpenProcessToken(
                proc, TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_QUERY, ctypes.byref(token)
            ):
                last_err = _err(f"OpenProcessToken({name})")
                continue

            primary = wt.HANDLE()
            if not advapi32.DuplicateTokenEx(
                token,
                MAXIMUM_ALLOWED,
                None,
                SecurityImpersonation,
                TokenPrimary,
                ctypes.byref(primary),
            ):
                last_err = _err("DuplicateTokenEx")
                continue

            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = "winsta0\\default"
            pi = PROCESS_INFORMATION()
            cmdline = f'"{exe}" {args}'.strip()
            cmd_buf = ctypes.create_unicode_buffer(cmdline)

            # The child needs the *user's* environment (APPDATA/TEMP/USERPROFILE),
            # otherwise libraries look under the SYSTEM profile and fail.
            advapi32.CreateEnvironmentBlock.argtypes = [
                ctypes.POINTER(ctypes.c_void_p),
                wt.HANDLE,
                wt.BOOL,
            ]
            advapi32.CreateEnvironmentBlock.restype = wt.BOOL
            env_block = ctypes.c_void_p()
            have_env = advapi32.CreateEnvironmentBlock(
                ctypes.byref(env_block), primary, False
            )
            if not have_env:
                env_block = ctypes.c_void_p()

            # SYSTEM 有 SeAssignPrimaryToken，直接 CreateProcessAsUser 最可靠。
            ok = advapi32.CreateProcessAsUserW(
                primary,
                exe,
                cmd_buf,
                None,
                None,
                False,
                CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE,
                env_block,
                workdir,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if not ok:
                last_err = _err("CreateProcessAsUser")
                ok = advapi32.CreateProcessWithTokenW(
                    primary,
                    0,
                    exe,
                    cmd_buf,
                    CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_CONSOLE,
                    env_block,
                    workdir,
                    ctypes.byref(si),
                    ctypes.byref(pi),
                )
                if not ok:
                    last_err = _err("CreateProcessWithTokenW")
                    if have_env and env_block:
                        advapi32.DestroyEnvironmentBlock(env_block)
                    continue
            if have_env and env_block:
                advapi32.DestroyEnvironmentBlock(env_block)

            new_pid = int(pi.dwProcessId)
            sid = wt.DWORD()
            kernel32.ProcessIdToSessionId(new_pid, ctypes.byref(sid))
            print(f"已启动 pid={new_pid}（借用了 {name}#{pid} 的令牌），实际会话 = {sid.value}")
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)
            return new_pid
        finally:
            if token:
                kernel32.CloseHandle(token)
            kernel32.CloseHandle(proc)
    raise RuntimeError(f"启动失败：{last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--args", default="")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--list", action="store_true")
    ns = ap.parse_args()

    if ns.list:
        for pid, name in pids_in_session(ns.session):
            print(f"  {pid:>6}  {name}")
        return 0

    pid = launch_in_session(ns.session, ns.exe, ns.args, ns.workdir)
    time.sleep(2)
    verify = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).SessionId",
        ],
        capture_output=True,
        text=True,
    )
    print(f"复核：进程 {pid} 的 SessionId = {verify.stdout.strip() or '进程已退出'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
