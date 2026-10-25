"""Launch a program inside a specific Windows session via Task Scheduler COM.

This mirrors what BetterGI does for its desktop clone: register a temporary
task bound to the interactive token of the current user, then start it with
`RunEx(..., TASK_RUN_USE_SESSION_ID, sessionId, ...)` so the process is created
inside that session instead of the caller's console session.

No SeTcb / SeAssignPrimaryToken is needed, so it works without elevation (a
"highest" run level does need admin, so we try highest first and fall back).
"""
from __future__ import annotations

import argparse
import sys

try:
    import win32com.client  # type: ignore
except ImportError:  # pragma: no cover - fallback to PowerShell
    win32com = None


TASK_CREATE = 2
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUN_USE_SESSION_ID = 0x4


def launch_via_powershell(session_id: int, exe: str, workdir: str) -> int:
    """Same idea, driven through the COM object from PowerShell."""
    import subprocess

    user = subprocess.run(
        ["whoami"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.strip()
    ps = f"""
$ErrorActionPreference = 'Stop'
$sid = {session_id}
$exe = '{exe}'
$wd  = '{workdir}'
$user = '{user}'
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
$def.Principal.RunLevel = 1
$act = $def.Actions.Create(0)
$act.Path = $exe
$act.WorkingDirectory = $wd
$registered = $null
foreach ($level in 1, 0) {{
  $def.Principal.RunLevel = $level
  try {{
    $registered = $root.RegisterTaskDefinition($name, $def, 2, $user, $null, 3, $null)
    Write-Output "注册成功 runLevel=$level"
    break
  }} catch {{
    Write-Output ("注册失败 runLevel=" + $level + ": " + $_.Exception.Message)
  }}
}}
if ($registered -eq $null) {{ exit 3 }}
try {{
  $run = $registered.RunEx($null, 4, $sid, $null)
  if ($run -eq $null) {{ Write-Output 'RunEx 返回 null'; exit 4 }}
  Write-Output '已提交到会话'
}} catch {{
  Write-Output ('RunEx 失败: ' + $_.Exception.Message)
  exit 5
}} finally {{
  try {{ $root.DeleteTask($name, 0) }} catch {{ }}
}}
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print("STDERR:", proc.stderr.strip()[:800], file=sys.stderr)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--exe", required=True)
    ap.add_argument("--workdir", default=None)
    ns = ap.parse_args()
    workdir = ns.workdir or "."
    return launch_via_powershell(ns.session, ns.exe, workdir)


if __name__ == "__main__":
    raise SystemExit(main())
