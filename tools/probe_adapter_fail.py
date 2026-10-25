"""Check how the wxauto adapter behaves when WeChat 4.x (unautomatable) is running.

Expected: it must fail fast with a readable Chinese hint, not hang forever.
Run:  .venv\\Scripts\\python.exe tools\\probe_adapter_fail.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DAFEIYU_DATA_DIR"] = str(ROOT / ".tmp" / "adapter-fail-data")

from backend.runtime import BotRuntime  # noqa: E402


def main() -> int:
    runtime = BotRuntime()
    cfg = runtime.config_store.snapshot()
    cfg["adapter"] = "wxauto"
    cfg["bot"]["enabled"] = True
    cfg["private"]["mode"] = "include"
    cfg["private"]["chats"] = ["测试好友"]
    runtime.save_config(cfg)

    started = time.time()
    runtime.start()
    print("已启动，等待连接结果（最多 40 秒）…")

    last_detail = ""
    while time.time() - started < 40:
        st = runtime.status()
        adapter = st["adapter"]
        if adapter.get("connected"):
            print(f"[{time.time() - started:.1f}s] 连接成功：{adapter['detail']}")
            break
        detail = adapter.get("detail", "")
        if detail and detail != last_detail:
            print(f"[{time.time() - started:.1f}s] {detail}")
            last_detail = detail
        if adapter.get("last_error") and not adapter.get("connected"):
            print(f"[{time.time() - started:.1f}s] last_error: {adapter['last_error'][:200]}")
            break
        time.sleep(1.0)

    st = runtime.status()
    print("\n最终状态：")
    print(f"  connected = {st['adapter'].get('connected')}")
    print(f"  detail    = {st['adapter'].get('detail')}")
    print(f"  last_error= {st['adapter'].get('last_error')}")
    runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
