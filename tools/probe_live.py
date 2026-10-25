"""Live smoke test against the real WeChat 3.9 client.

Only touches 文件传输助手 (File Transfer), so no real contact is disturbed.

Run:  .venv\\Scripts\\python.exe tools\\probe_live.py
Needs to run outside the DSH sandbox (UIA is cross-process).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DAFEIYU_DATA_DIR"] = str(ROOT / ".tmp" / "live-data")

from backend.runtime import BotRuntime  # noqa: E402

TARGET = "文件传输助手"


def wait_login(max_seconds: int = 90) -> bool:
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        if user32.FindWindowW("WeChatMainWndForPC", None):
            return True
        time.sleep(2)
    return False


def main() -> int:
    print("== 1. 检查微信主窗口 ==")
    if not wait_login(5):
        print("   ✗ 没检测到 WeChatMainWndForPC 主窗口：微信还没登录")
        return 2
    print("   ✓ 主窗口存在")

    print("\n== 2. 启动运行时（真实微信模式）==")
    runtime = BotRuntime()
    cfg = runtime.config_store.snapshot()
    cfg["adapter"] = "wxauto"
    cfg["bot"]["enabled"] = True
    cfg["bot"]["private_enabled"] = True
    cfg["bot"]["group_enabled"] = False
    cfg["private"]["mode"] = "include"
    cfg["private"]["chats"] = [TARGET]
    cfg["rules"] = []
    runtime.save_config(cfg)

    runtime.start()
    deadline = time.time() + 90
    detail = ""
    while time.time() < deadline:
        status = runtime.status()
        detail = status["adapter"].get("detail", "")
        if status["adapter"].get("connected"):
            break
        time.sleep(2)
    status = runtime.status()
    adapter = status["adapter"]
    print(f"   连接 = {adapter.get('connected')}")
    print(f"   详情 = {detail}")
    print(f"   我的昵称 = {adapter.get('my_names')}")
    print(f"   wxauto 目标版本 = {adapter.get('wechat_version')}")
    if adapter.get("last_error"):
        print(f"   最近错误 = {adapter['last_error']}")
    if not adapter.get("connected"):
        print("   ✗ 连接失败，后面的检查跳过")
        runtime.shutdown()
        return 3

    print("\n== 3. 拉取会话列表 ==")
    sessions = runtime.refresh_sessions()
    print(f"   拿到 {len(sessions)} 条会话")
    for item in sessions[:12]:
        print(f"     - {item.get('name')!r} type={item.get('type')} unread={item.get('unread')} last={str(item.get('last'))[:20]!r}")
    if not any(s.get("name") == TARGET for s in sessions):
        print(f"   ! 会话列表里没看到「{TARGET}」，如果你没有这个会话就先发一条消息给它")

    print("\n== 4. 监听列表 ==")
    time.sleep(3)
    print(f"   当前监听 = {runtime.status()['listen']}")

    print("\n== 5. 发送自检消息到「文件传输助手」==")
    text = f"落小鱼 自检 {time.strftime('%H:%M:%S')}"
    result = runtime.send_text(TARGET, text)
    print(f"   发送结果 = {result}")

    print("\n== 6. 等 5 秒，检查是否被监听到（应该不会，自己发的不算收到）==")
    time.sleep(5)
    msgs = runtime.store.list_messages(limit=10)
    print(f"   日志 {len(msgs)} 条：")
    for row in msgs:
        print(f"     [{row['direction']}] {row['chat']} | {row['status']} | {str(row['content'])[:40]!r} | {row['note'][:40]}")

    runtime.shutdown()
    print("\n== 完成 ==")
    print(f"请打开微信「{TARGET}」看看是否收到这条消息：{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
