"""Development server: run the backend without the tray icon or browser popup.

Usage:
    python tools/run_dev.py                # 真实微信模式
    python tools/run_dev.py --mock         # 模拟模式（不连微信）
    python tools/run_dev.py --port 8799 --data-dir .tmp/devdata
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mock", action="store_true", help="用模拟适配器启动")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--no-start", action="store_true", help="只起控制台，不启动机器人")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["DAFEIYU_DATA_DIR"] = str((ROOT / args.data_dir).resolve())

    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    import uvicorn

    from backend.api import create_app
    from backend.runtime import BotRuntime

    runtime = BotRuntime()
    if args.mock:
        cfg = runtime.config_store.snapshot()
        cfg["adapter"] = "mock"
        runtime.config_store.save(cfg)

    if not args.no_start:
        runtime.start()

    app = create_app(runtime)
    print(f"控制台： http://{args.host}:{args.port}/")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
    finally:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
