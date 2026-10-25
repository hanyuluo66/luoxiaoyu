"""Live test of the WeChat 4.x adapter (no pipeline, no AI).

Run with an interpreter whose integrity level is Medium (i.e. NOT the in-workspace
venv, which inherits a Low label under this host):
    C:\\Users\\23798\\dfy-venv\\Scripts\\python.exe tools\\probe_adapter4.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET = "文件传输助手"
received: list = []


def main() -> int:
    from backend.adapters.wechatauto_adapter import WechatautoAdapter

    adapter = WechatautoAdapter(on_message=received.append, poll_interval=1.0)
    print("=== 启动 4.x 适配器 ===")
    t0 = time.time()
    adapter.start([TARGET])

    deadline = time.time() + 120
    while time.time() < deadline:
        st = adapter.status()
        if st.get("connected"):
            print(f"  ✅ 已连接（{time.time()-t0:.1f}s）：{st.get('detail')}")
            break
        time.sleep(2)
    else:
        st = adapter.status()
        print(f"  ✗ 连接失败：{st.get('detail')} / {st.get('last_error')}")
        adapter.stop()
        return 2

    time.sleep(2)
    print("\n=== 会话列表 ===")
    sessions = adapter.sessions()
    print(f"  会话数 = {len(sessions)}")
    for s in sessions[:10]:
        print(f"     {s.get('name','')[:20]:22} type={s.get('type')} unread={s.get('unread')}")

    print("\n=== 发送测试 ===")
    text = f"落小鱼 4.x适配器测试 {time.strftime('%H:%M:%S')}"
    ok, detail = adapter.send_text(TARGET, text)
    print(f"  发送结果 = {ok}  {detail}")
    print(f"  内容: {text}")

    print("\n=== 监听 25 秒（看能否收到消息）===")
    print("  提示：现在用手机给「文件传输助手」或别的会话发条消息试试")
    for i in range(5):
        time.sleep(5)
        if received:
            break
        print(f"    ...等待中 {25 - (i+1)*5}s")
    print(f"  收到 {len(received)} 条：")
    for m in received[:8]:
        print(f"     [{m.chat_type}] {m.chat} | {m.sender} | {str(m.content)[:40]}")

    adapter.stop()
    print("\n完成")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
