"""Windows child-session helpers (the "desktop clone" used for isolation).

A child session is a second, fully logged-in Windows session. It has its own
input queue, so UI automation running there never moves the mouse or steals
focus from the console session. WeChat and the bot both live inside it.

Three pieces:
  * `keeper_*`   — talk to 落小鱼引擎.exe (creates/holds the session,
                   TCP control channel on 127.0.0.1:18999)
  * `launch_into_session` — start a process inside the child session through a
                   temporary Task Scheduler entry + RunEx(TASK_RUN_USE_SESSION_ID).
                   This needs no elevation.
  * `child_session_id` / `console_session_id` — WTS state.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import time
from pathlib import Path

import logging

from . import spawn

log = logging.getLogger(__name__)

wtsapi32 = ctypes.WinDLL("wtsapi32")
kernel32 = ctypes.WinDLL("kernel32")

KEEPER_NAME = "落小鱼引擎.exe"
KEEPER_PORT = 18999
NO_SESSION = 0xFFFFFFFF


# --------------------------------------------------------------------- sessions
def current_session_id() -> int:
    sid = wt.DWORD()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    return int(sid.value)


def console_session_id() -> int:
    return int(kernel32.WTSGetActiveConsoleSessionId())


def child_session_id() -> int | None:
    """The active RDP child session, or None when there is none."""
    sid = wt.DWORD()
    if wtsapi32.WTSGetChildSessionId(ctypes.byref(sid)) and sid.value != NO_SESSION:
        return int(sid.value)
    return None


def child_sessions_enabled() -> bool:
    enabled = wt.BOOL()
    if wtsapi32.WTSIsChildSessionsEnabled(ctypes.byref(enabled)):
        return bool(enabled.value)
    return False


def enable_child_sessions() -> bool:
    return bool(wtsapi32.WTSEnableChildSessions(True))


# ----------------------------------------------------------------------- keeper
def keeper_path() -> Path:
    from .paths import install_dir

    return install_dir() / KEEPER_NAME


def keeper_running() -> bool:
    try:
        return _keeper_cmd("status", timeout=2.0) is not None
    except Exception:  # noqa: BLE001
        return False


def _keeper_cmd(command: str, timeout: float = 3.0) -> str | None:
    import socket

    with socket.create_connection(("127.0.0.1", KEEPER_PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((command + "\n").encode("utf-8"))
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
        return b"".join(chunks).decode("utf-8", "replace").strip()


def keeper_status() -> dict:
    raw = _keeper_cmd("status")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def keeper_command(command: str) -> bool:
    try:
        _keeper_cmd(command)
        return True
    except Exception:  # noqa: BLE001
        return False


def _kill_keeper() -> None:
    """Terminate every keeper process (used when its RDP client wedges)."""
    try:
        spawn.run(
            ["taskkill", "/F", "/IM", KEEPER_NAME],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        pass


def _start_keeper() -> bool:
    exe = keeper_path()
    if not exe.exists():
        return False
    spawn.popen(
        [str(exe)],
        cwd=str(exe.parent),
        close_fds=True,
        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if keeper_running():
            return True
        time.sleep(0.5)
    return False


def ensure_keeper(timeout: float = 60.0) -> bool:
    """Start 落小鱼引擎.exe if needed and wait until RDP is connected.

    The RDP ActiveX occasionally wedges in the "connecting" state (usually after
    a session was logged off uncleanly) and never recovers inside that process.
    When that happens we kill the keeper and start a fresh one — its first
    Connect() is clean again.
    """
    if not keeper_running() and not _start_keeper():
        return False

    deadline = time.time() + timeout
    stuck_since = 0.0
    restarts_left = 2
    while time.time() < deadline:
        status = keeper_status()
        state = status.get("connected")
        if state == 1 and int(status.get("child", -1)) >= 0:
            return True
        if state == 2:
            stuck_since = stuck_since or time.time()
            if time.time() - stuck_since > 120 and restarts_left > 0:
                restarts_left -= 1
                stuck_since = 0.0
                log.warning("桌面分身卡在「连接中」，重启引擎后重试（还剩 %d 次）", restarts_left)
                _kill_keeper()
                time.sleep(2)
                if not _start_keeper():
                    return False
        else:
            stuck_since = 0.0
        time.sleep(1.0)
    return False


# ------------------------------------------------------------ launch in session
def launch_into_session(
    session_id: int,
    exe: str | Path,
    workdir: str | Path | None = None,
    args: str = "",
) -> tuple[bool, str]:
    """Start `exe` inside `session_id` using a temporary scheduled task.

    Mirrors what BetterGI does for its desktop clone: register a task bound to
    the interactive token of the current user, then RunEx with
    TASK_RUN_USE_SESSION_ID. RunLevel "highest" needs admin, so we fall back to
    a limited token (enough for the bot).
    """
    exe_path = str(Path(exe).resolve())
    work = str(Path(workdir).resolve()) if workdir else str(Path(exe_path).parent)
    who = spawn.run(
        ["whoami"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.strip()

    def _ps(value: object) -> str:
        """PowerShell 单引号字符串转义（防止路径里的 ' 破坏脚本/注入命令）。"""
        return str(value).replace("'", "''")

    act_args = _ps(args or "")
    exe_ps = _ps(exe_path)
    work_ps = _ps(work)
    user_ps = _ps(who)
    script = f"""
$ErrorActionPreference = 'Stop'
$sid = {int(session_id)}
$exe = '{exe_ps}'
$wd  = '{work_ps}'
$user = '{user_ps}'
$sched = New-Object -ComObject Schedule.Service
$sched.Connect()
$root = $sched.GetFolder('\\')
$name = 'DFY-ChildLaunch-' + [guid]::NewGuid().ToString('N')
$def = $sched.NewTask(0)
$def.RegistrationInfo.Author = '落小鱼'
$def.Settings.Enabled = $true
$def.Settings.Hidden = $true
$def.Settings.AllowDemandStart = $true
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Settings.ExecutionTimeLimit = 'PT0S'
$def.Principal.UserId = $user
$def.Principal.LogonType = 3
$act = $def.Actions.Create(0)
$act.Path = $exe
$act.Arguments = '{act_args}'
$act.WorkingDirectory = $wd
$registered = $null
foreach ($level in 1, 0) {{
  $def.Principal.RunLevel = $level
  try {{
    $registered = $root.RegisterTaskDefinition($name, $def, 2, $user, $null, 3, $null)
    Write-Output ('registered:' + $level)
    break
  }} catch {{
    Write-Output ('register-failed:' + $level + ':' + $_.Exception.Message)
  }}
}}
if ($registered -eq $null) {{ exit 3 }}
try {{
  $run = $registered.RunEx($null, 4, $sid, $null)
  if ($run -eq $null) {{ Write-Output 'run-null'; exit 4 }}
  Write-Output 'launched'
}} catch {{
  Write-Output ('run-failed:' + $_.Exception.Message)
  exit 5
}} finally {{
  try {{ $root.DeleteTask($name, 0) }} catch {{ }}
}}
"""
    proc = spawn.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    out = (proc.stdout or "").strip()
    if "launched" in out:
        return True, out
    return False, (out + " " + (proc.stderr or "").strip())[:400]


def pids_in_session(session_id: int, name_filter: str = "") -> list[int]:
    """PIDs living in the given session (optionally filtered by exe name)."""
    TH32CS_SNAPPROCESS = 0x00000002

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
            ("szExeFile", wt.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out: list[int] = []
    if snap in (0, -1):
        return out
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
    wanted = name_filter.lower()
    while ok:
        sid = wt.DWORD()
        if kernel32.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(sid)) and sid.value == session_id:
            name = entry.szExeFile
            if not wanted or wanted in name.lower():
                out.append(int(entry.th32ProcessID))
        ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    kernel32.CloseHandle(snap)
    return out

# ------------------------------------------------------------------ 分身瘦身
# 子会话登录时会照着用户的启动项再开一遍桌面：Steam、QQ、网盘、驱动托盘……
# 这些在分身里毫无用处，只吃内存和 CPU。这里按「名字白名单式黑名单」清理：
# 只杀这些明确无关的程序，系统进程和微信永远不动。
SLIM_KILL_NAMES: set[str] = {
    "steam.exe",
    "steamwebhelper.exe",
    "qq.exe",
    "qqnt.exe",
    "qqpcexternal.exe",
    "qqpctray.exe",
    "qqliveservice.exe",
    "onedrive.exe",
    "msedge.exe",
    "wujie.exe",
    "dnplayer.exe",
    "ldvboxheadless.exe",
    "ldconsole.exe",
    "nox.exe",
    "mumuplayer.exe",
    "mchose hub watcher.exe",
    "translucenttb.exe",
    "wegame.exe",
    "xboxstat.exe",
    "ace-tray.exe",
    "desktopmgr64.exe",
    "desktopaitaskcenter.exe",
    "nahimicapo4volume.exe",
    "thunder.exe",
    "baidunetdisk.exe",
    "dingtalk.exe",
    "wechat.exe",
}

SLIM_NEVER_KILL = {
    "explorer.exe",
    "weixin.exe",
    "wechatappex.exe",
    "落小鱼.exe",
    "落小鱼引擎.exe",
    "svchost.exe",
    "winlogon.exe",
    "rdpclip.exe",
    "sihost.exe",
    "taskhostw.exe",
    "ctfmon.exe",
    "chsime.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellhost.exe",
    "runtimebroker.exe",
    "dwm.exe",
    "wininit.exe",
    "csrss.exe",
    "lsass.exe",
    "services.exe",
}


def _session_processes(session_id: int) -> list[tuple[int, str]]:
    """(pid, name) for every process living in the given session."""
    out: list[tuple[int, str]] = []
    try:
        proc = spawn.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        return out
    names = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            names[int(parts[1])] = parts[0].lower()
    for pid in pids_in_session(session_id):
        if pid in names:
            out.append((pid, names[pid]))
    return out


def slim_child_session(session_id: int, extra: str = "") -> list[str]:
    """关掉分身里跟微信无关的启动项，返回被杀掉的程序名。"""
    wanted = set(SLIM_KILL_NAMES)
    for raw in (extra or "").replace("，", ",").split(","):
        name = raw.strip().lower()
        if name:
            wanted.add(name if name.endswith(".exe") else name + ".exe")

    killed: list[str] = []
    for pid, name in _session_processes(session_id):
        if name in SLIM_NEVER_KILL or name not in wanted:
            continue
        try:
            spawn.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            killed.append(name)
        except Exception:  # noqa: BLE001
            continue
    if killed:
        log.info("分身瘦身：关掉 %d 个无关程序 %s", len(killed), sorted(set(killed)))
    return killed

# ------------------------------------------------------- 在用户桌面上打开东西
def hide_viewer() -> bool:
    """立刻把分身窗口藏起来。

    RDP 会把分身里的光标映射到本机鼠标上，所以只要分身窗口是可见的，
    机器人在分身里一移动鼠标，用户的真实指针就会跟着动（"抢鼠标"）。
    任何要移动分身鼠标或注入按键的动作之前都应先调用它。
    """
    try:
        if not keeper_running():
            return False
        return keeper_command("hide") is not None
    except Exception:  # noqa: BLE001
        return False


def open_on_console(
    target: str | Path, args: str = "", workdir: str = ""
) -> tuple[bool, str]:
    """Open a file/folder **on the user's desktop**, not inside the clone.

    The console is usually served by the bot instance living in the child
    session, so ``os.startfile`` there opens things on the clone desktop where
    nobody can see them. When we are not in the console session, hand the job
    to a scheduled task in the console session instead.
    """
    path = str(Path(target).resolve())
    console = int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())
    mine = _current_session_id()

    # 带参数调用（例如 `落小鱼.exe --console-window <url>`）：直接在本会话起进程
    if args:
        if mine == console:
            try:
                spawn.popen([path, *args.split()], cwd=workdir or str(Path(path).parent))
                return True, "已在当前桌面启动"
            except Exception as exc:  # noqa: BLE001
                return False, f"{type(exc).__name__}: {exc}"
        ok, detail = launch_into_session(
            console, path, workdir or str(Path(path).parent), args=args
        )
        if ok:
            log.info("已请主桌面启动：%s %s", path, args)
            return True, "已在你的桌面上启动"
        return False, detail

    if mine == console:
        try:
            import os as _os

            _os.startfile(path)  # type: ignore[attr-defined]
            return True, "已在当前桌面打开"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    target_path = Path(path)
    if target_path.is_dir():
        exe, args = "C:\\Windows\\explorer.exe", f'"{path}"'
    else:
        # 文件（bat / 日志）交给 cmd start，让系统按默认方式打开
        exe, args = "C:\\Windows\\System32\\cmd.exe", f'/c start "" "{path}"'
    ok, detail = launch_into_session(console, exe, "C:\\", args=args)
    if ok:
        log.info("已请主桌面打开：%s", path)
        return True, "已在你的桌面上打开"
    return False, detail


def _current_session_id() -> int:
    import ctypes as _ctypes

    sid = _ctypes.c_ulong()
    _ctypes.windll.kernel32.ProcessIdToSessionId(
        _ctypes.windll.kernel32.GetCurrentProcessId(), _ctypes.byref(sid)
    )
    return int(sid.value)


def is_child_session() -> bool:
    """True when the current process runs outside the physical console session."""
    try:
        console = int(ctypes.windll.kernel32.WTSGetActiveConsoleSessionId())
        return _current_session_id() != console
    except Exception:  # noqa: BLE001
        return False


def credentials_stored() -> bool:
    """localhost 的 RDP 凭据是否已经存进凭据管理器。"""
    try:
        from . import wincred

        return wincred.exists()
    except Exception:  # noqa: BLE001
        return False


def hello_only_login() -> bool | None:
    """True 表示系统只允许 Windows Hello 登录（子会话会登不上）。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\PasswordLess\\Device",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "DevicePasswordLessBuildVersion")
        return int(value) == 2
    except Exception:  # noqa: BLE001
        return None


def stop_keeper_for_login() -> None:
    """登录失败时关掉引擎：那个 RDP 登录弹窗就是它弹的。"""
    _kill_keeper()


# ------------------------------------------------------- 登录失败计数（防弹窗）
LOGIN_STATE = "child-login.json"
LOGIN_MAX_FAILURES = 2


def _login_state_path():
    from .paths import data_dir

    return data_dir() / LOGIN_STATE


def _read_login_state() -> dict:
    import json

    try:
        return json.loads(_login_state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def login_failures() -> int:
    return int(_read_login_state().get("failures") or 0)


def mark_login_failure() -> int:
    import json

    state = _read_login_state()
    failures = int(state.get("failures") or 0) + 1
    state["failures"] = failures
    try:
        _login_state_path().write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return failures


def reset_login_failures() -> None:
    import json

    try:
        _login_state_path().write_text(json.dumps({"failures": 0}), encoding="utf-8")
    except OSError:
        pass


def login_blocked() -> str:
    """连续失败太多次就不再自动尝试，返回原因（空字符串表示可以试）。"""
    failures = login_failures()
    if failures >= LOGIN_MAX_FAILURES:
        return (
            f"分身登录已经连续失败 {failures} 次（Windows 弹「凭据不工作」），"
            "为避免反复弹窗已停止自动尝试。\n"
            "请先确认 Windows 登录密码正确，并【注销重登或重启】让「允许密码登录」生效，"
            "然后点下方「重试建立分身」。"
        )
    return ""
