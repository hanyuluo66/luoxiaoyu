"""Report the effective privilege this interpreter runs with.

Used to find a launch method that is NOT token-restricted, because WeChat's
process memory can only be read with a normal token.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

out: list[str] = []


def log(text: str) -> None:
    out.append(text)


try:
    import psutil

    procs = list(psutil.process_iter(["pid","name"]))
    log(f"process_count={len(procs)}")
except Exception as exc:  # noqa: BLE001
    log(f"psutil_failed={exc}")
    procs = []

# find a Weixin pid and try to open it
weixin = [p.info.get("pid") for p in procs if (p.info.get("name") or "").lower() == "weixin.exe"]
log(f"weixin_visible={weixin}")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

targets = weixin or []
# also try the pid passed on the command line (resolved by the caller via window enum)
if len(sys.argv) > 1:
    targets = [int(sys.argv[1])] + targets

for pid in targets[:3]:
    h = k32.OpenProcess(0x0010 | 0x0400, False, pid)
    log(f"OpenProcess(pid={pid}) handle={h} err={ctypes.get_last_error()}")
    if h:
        k32.CloseHandle(h)

# integrity level
try:
    import subprocess

    r = subprocess.run(["whoami", "/groups"], capture_output=True, text=True, errors="replace")
    for line in (r.stdout or "").splitlines():
        if "Mandatory" in line:
            log("integrity: " + line.strip())
except Exception:  # noqa: BLE001
    pass

path = sys.argv[2] if len(sys.argv) > 2 else "launch_mode.txt"
import os

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", path), "a", encoding="utf-8") as fh:
    fh.write("---- " + sys.executable + "\n")
    for line in out:
        fh.write("  " + line + "\n")
print("\n".join(out))

