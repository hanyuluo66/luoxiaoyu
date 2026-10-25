"""Validate the 4.x adapter's message parsing (synthetic rows + live self-detect)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = FAIL = 0
REPORT: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    line = (f"  [PASS] {name}" if cond else f"  [FAIL] {name}  {detail}")
    print(line)
    REPORT.append(line)
    if cond:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    from backend.adapters.wechatauto_adapter import WechatautoAdapter

    inbox: list = []
    selfmsgs: list = []
    adapter = WechatautoAdapter(
        on_message=inbox.append,
        on_self_message=lambda chat, content: selfmsgs.append((chat, content)),
        poll_interval=1.0,
    )
    # 只测解析逻辑：给适配器一个假的名字表和启动时间
    adapter._names = {"wxid_a": "张三", "123@chatroom": "测试群", "wxid_me": "我"}
    adapter._self_username = "wxid_me"
    adapter._started_at = 1000.0
    adapter._set_status(my_names=["小鱼"])

    print("=== 合成行解析 ===")
    adapter._handle_row(
        {
            "chat_username": "wxid_a",
            "sender_username": "wxid_a",
            "type": "text",
            "content": "晚上吃啥",
            "create_time": 2000,
            "local_id": 1,
            "shard": "message_0.db",
        }
    )
    check("私聊消息解析", len(inbox) == 1 and inbox[0].chat == "张三", str(inbox[-1:]))
    check("私聊类型正确", inbox[0].chat_type == "friend" if inbox else False)
    check("内容正确", inbox[0].content == "晚上吃啥" if inbox else False)

    inbox.clear()
    adapter._handle_row(
        {
            "chat_username": "123@chatroom",
            "sender_username": "wxid_a",
            "type": "text",
            "content": "@小鱼 报个数",
            "create_time": 2000,
            "local_id": 2,
        }
    )
    check("群聊消息解析", len(inbox) == 1 and inbox[0].chat == "测试群", str(inbox[-1:]))
    check("群聊类型正确", inbox[0].chat_type == "group" if inbox else False)
    check("群聊发送人解析", inbox[0].sender == "张三" if inbox else False)
    check("识别 @我", inbox[0].at_me is True if inbox else False)

    inbox.clear()
    adapter._handle_row(
        {
            "chat_username": "wxid_a",
            "sender_username": "wxid_me",
            "type": "text",
            "content": "我自己发的",
            "create_time": 2000,
            "local_id": 3,
        }
    )
    check("自己发的消息不进 inbox", len(inbox) == 0)
    check("自己发的消息触发接管检测", len(selfmsgs) == 1, str(selfmsgs))

    inbox.clear()
    adapter._handle_row(
        {
            "chat_username": "wxid_a",
            "sender_username": "wxid_a",
            "type": "image",
            "content": "[图片]",
            "create_time": 2000,
            "local_id": 4,
        }
    )
    check("非文本消息被跳过", len(inbox) == 0)

    inbox.clear()
    adapter._handle_row(
        {
            "chat_username": "wxid_a",
            "sender_username": "wxid_a",
            "type": "text",
            "content": "很久以前的消息",
            "create_time": 100,
            "local_id": 5,
        }
    )
    check("启动前的历史消息被忽略", len(inbox) == 0)

    print("\n=== 真机：监听自己发出的消息（验证 Listener 管道）===")
    adapter.start([])
    deadline = time.time() + 90
    while time.time() < deadline and not adapter.status().get("connected"):
        time.sleep(2)
    check("适配器连接成功", bool(adapter.status().get("connected")), str(adapter.status()))
    if adapter.status().get("connected"):
        selfmsgs.clear()
        ok, detail = adapter.send_text("文件传输助手", f"落小鱼 监听自检 {time.strftime('%H:%M:%S')}")
        check("真机发送成功", ok, detail)
        for _ in range(12):
            time.sleep(1)
            if selfmsgs:
                break
        check("Listener 收到自己发出的消息", len(selfmsgs) >= 1, str(selfmsgs[:3]))

    adapter.stop()
    summary = f"===== 通过 {PASS}，失败 {FAIL} ====="
    print(summary)
    REPORT.append(summary)
    try:
        (ROOT / ".tmp" / "adapter4_report.txt").parent.mkdir(parents=True, exist_ok=True)
        (ROOT / ".tmp" / "adapter4_report.txt").write_text("\n".join(REPORT), encoding="utf-8")
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
