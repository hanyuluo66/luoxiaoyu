"""Isolate why freshly-created subdirectories deny file writes."""
import os
import shutil
import subprocess

ws = os.getcwd()
base = os.path.join(ws, ".tmp")
os.makedirs(base, exist_ok=True)


def acl(path):
    try:
        out = subprocess.run(["icacls", path], capture_output=True, text=True)
        return out.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<icacls failed: {exc}>"


def probe(label, mk):
    d = os.path.join(base, label)
    shutil.rmtree(d, ignore_errors=True)
    try:
        mk(d)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] mkdir FAILED: {exc}")
        return
    print(f"[{label}] mkdir OK")
    print(acl(d))
    p = os.path.join(d, "x.txt")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        print(f"[{label}] write OK")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] write FAILED: {type(exc).__name__}: {exc}")
    shutil.rmtree(d, ignore_errors=True)


probe("p_makedirs", lambda d: os.makedirs(d, exist_ok=True))
probe("p_mkdir0700", lambda d: os.mkdir(d, 0o700))
probe("p_mkdirdefault", lambda d: os.mkdir(d))

import tempfile

d = tempfile.mkdtemp(dir=base)
print("[p_mkdtemp] dir =", d)
print(acl(d))
try:
    with open(os.path.join(d, "x.txt"), "w", encoding="utf-8") as f:
        f.write("ok")
    print("[p_mkdtemp] write OK")
except Exception as exc:  # noqa: BLE001
    print(f"[p_mkdtemp] write FAILED: {type(exc).__name__}: {exc}")
shutil.rmtree(d, ignore_errors=True)
print("tempdir default =", tempfile.gettempdir())
