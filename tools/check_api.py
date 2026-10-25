"""Verify the running console API: adapter, connection, send, and stale-write guard.

Uses Python/httpx so Chinese never goes through PowerShell's encoding.
"""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8760"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=60) as client:
        health = client.get("/healthz").json()
        print(f"healthz: {health}")

        boot = client.get("/api/bootstrap").json()
        cfg = boot["config"]
        print(f"adapter = {cfg.get('adapter')}  saved_at = {cfg.get('saved_at')}")
        print(f"persona 前 20 字 = {str(cfg['ai']['persona'])[:20]!r}")

        print("\n=== 等待微信连接 ===")
        for _ in range(25):
            st = client.get("/api/status").json()
            if st["adapter"].get("connected"):
                print(f"  已连接：{st['adapter']['detail']}  引擎={st['adapter_kind']}")
                break
            time.sleep(3)
        else:
            print(f"  ✗ 未连接：{client.get('/api/status').json()['adapter']}")
            return 2

        print("\n=== 发送测试（文件传输助手）===")
        r = client.post(
            "/api/send",
            json={"chat": "文件传输助手", "text": f"落小鱼 修复后自检 {time.strftime('%H:%M:%S')}"},
            timeout=180,
        )
        print(f"  {r.status_code} {r.json()}")

        print("\n=== 模拟旧标签页用过期时间戳覆盖配置 ===")
        stale = json.loads(json.dumps(cfg))
        stale["saved_at"] = 1234567890
        stale["adapter"] = "wxauto"
        r = client.put("/api/config", json=stale)
        if r.status_code == 409:
            print(f"  ✅ 已阻止：{r.json().get('detail')}")
        else:
            print(f"  ⚠ 未被阻止：{r.status_code} {r.text[:160]}")

        print("\n=== 用正确时间戳保存（正常路径）===")
        fresh = json.loads(json.dumps(cfg))
        fresh["private"]["chats"] = ["文件传输助手"]
        r = client.put("/api/config", json=fresh)
        print(f"  {r.status_code} ok={r.json().get('ok') if r.status_code == 200 else r.text[:120]}")

        st = client.get("/api/status").json()
        print(f"\n最终：引擎={st['adapter_kind']} 连接={st['adapter']['connected']} 监听={st['listen']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
