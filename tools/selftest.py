"""End-to-end self-test for the backend, using the mock adapter.

Run:  .venv\\Scripts\\python.exe tools\\selftest.py
It writes into a throwaway data dir so your real config is never touched.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DATA = ROOT / ".tmp" / "selftest-data"
if TEST_DATA.exists():
    import shutil

    shutil.rmtree(TEST_DATA, ignore_errors=True)
os.environ["DAFEIYU_DATA_DIR"] = str(TEST_DATA)

from backend.models import AppConfig  # noqa: E402
from backend.rules import DecisionEngine, IncomingMessage, in_quiet_hours  # noqa: E402
from backend.runtime import BotRuntime  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------- unit: rules
def test_decision_engine() -> None:
    print("\n== 决策引擎 ==")
    cfg = AppConfig()
    cfg.bot.private_enabled = True
    cfg.private.mode = "include"
    cfg.private.chats = ["张三"]
    engine = DecisionEngine(cfg)

    d = engine.resolve(
        IncomingMessage(chat="张三", chat_type="friend", sender="张三", content="你好"), ["小鱼"]
    )
    check("私聊白名单命中 -> 回复", d.reply, d.reason)

    d = engine.resolve(
        IncomingMessage(chat="李四", chat_type="friend", sender="李四", content="你好"), ["小鱼"]
    )
    check("私聊不在白名单 -> 不回复", not d.reply, d.reason)

    cfg.group.mode = "include"
    cfg.group.chats = ["测试群"]
    cfg.group.trigger = "at"
    engine = DecisionEngine(cfg)
    d = engine.resolve(
        IncomingMessage(chat="测试群", chat_type="group", sender="小王", content="大家早"), ["小鱼"]
    )
    check("群聊未@我 -> 不回复", not d.reply, d.reason)

    d = engine.resolve(
        IncomingMessage(
            chat="测试群", chat_type="group", sender="小王", content="@小鱼 在吗", at_me=True
        ),
        ["小鱼"],
    )
    check("群聊@我 -> 回复", d.reply, d.reason)

    cfg.group.trigger = "keyword"
    cfg.group.keywords = ["报数"]
    engine = DecisionEngine(cfg)
    d = engine.resolve(
        IncomingMessage(chat="测试群", chat_type="group", sender="小王", content="请报数一下"),
        ["小鱼"],
    )
    check("群聊关键词命中 -> 回复", d.reply, d.reason)

    cfg.blacklist.ignore_keywords = ["广告"]
    engine = DecisionEngine(cfg)
    d = engine.resolve(
        IncomingMessage(chat="张三", chat_type="friend", sender="张三", content="这是广告"), ["小鱼"]
    )
    check("忽略词 -> 不回复", not d.reply, d.reason)

    # rules beat defaults and honour priority
    from backend.models import Rule

    cfg.rules = [
        Rule(name="低优先", priority=200, patterns=["测试"], action="template", template="低"),
        Rule(name="高优先", priority=10, patterns=["测试"], action="template", template="高"),
    ]
    engine = DecisionEngine(cfg)
    d = engine.resolve(
        IncomingMessage(chat="张三", chat_type="friend", sender="张三", content="测试一下"), ["小鱼"]
    )
    check("规则按优先级命中", d.reply and d.rule_name == "高优先", d.reason)

    cfg.rules = [Rule(name="静默", priority=1, patterns=["闭嘴"], action="ignore")]
    engine = DecisionEngine(cfg)
    d = engine.resolve(
        IncomingMessage(chat="张三", chat_type="friend", sender="张三", content="闭嘴"), ["小鱼"]
    )
    check("ignore 规则 -> 不回复", not d.reply, d.reason)

    import datetime as dt

    check(
        "免打扰跨零点判断",
        in_quiet_hours(dt.datetime(2025, 1, 1, 23, 45), "23:30", "07:30")
        and in_quiet_hours(dt.datetime(2025, 1, 1, 3, 0), "23:30", "07:30")
        and not in_quiet_hours(dt.datetime(2025, 1, 1, 12, 0), "23:30", "07:30"),
    )


# ------------------------------------------------------- integration: pipeline
def test_pipeline_with_mock() -> None:
    print("\n== 流水线（模拟适配器 + 固定话术规则）==")
    runtime = BotRuntime()
    cfg = runtime.config_store.snapshot()
    cfg["adapter"] = "mock"
    cfg["bot"]["enabled"] = True
    cfg["bot"]["private_enabled"] = True
    cfg["bot"]["group_enabled"] = True
    cfg["bot"]["reply_delay_min"] = 0.0
    cfg["bot"]["reply_delay_max"] = 0.0
    cfg["bot"]["per_chat_cooldown"] = 0.0
    cfg["bot"]["global_per_minute"] = 100
    cfg["private"]["mode"] = "include"
    cfg["private"]["chats"] = ["张三"]
    cfg["group"]["mode"] = "include"
    cfg["group"]["chats"] = ["测试群"]
    cfg["group"]["trigger"] = "at"
    cfg["rules"] = [
        {
            "id": "r1",
            "name": "固定话术",
            "enabled": True,
            "scope": "all",
            "chats": [],
            "match": "always",
            "patterns": [],
            "action": "template",
            "template": "收到啦：{msg}",
            "prompt": "",
            "priority": 1,
            "cooldown_seconds": 0.0,
        }
    ]
    runtime.save_config(cfg)
    runtime.start()

    adapter = runtime.adapter
    check("适配器为模拟模式", adapter is not None and adapter.name == "mock")
    check("监听会话已下发", set(runtime.status()["listen"]) == {"张三", "测试群"}, str(runtime.status()["listen"]))

    adapter.inject("张三", "张三", "晚上吃啥")
    adapter.inject("李四", "李四", "不在白名单")
    adapter.inject("测试群", "小王", "@小鱼 在吗", "group")
    adapter.inject("测试群", "小王", "随便聊聊", "group")

    deadline = time.time() + 8
    while time.time() < deadline:
        if len(adapter.sent) >= 2:  # noqa: SIM102
            time.sleep(0.6)
            if len(adapter.sent) >= 2:
                break
        time.sleep(0.2)

    sent = list(adapter.sent)
    chats = [s["chat"] for s in sent]
    check("白名单私聊得到回复", "张三" in chats, str(sent))
    check("白名单外的私聊不回复", "李四" not in chats, str(sent))
    check("群聊@我得到回复", "测试群" in chats, str(sent))
    check(
        "模板占位符被替换",
        any("收到啦：晚上吃啥" == s["text"] for s in sent),
        str(sent),
    )
    logs = runtime.store.list_messages(limit=50)
    check("消息日志已写入", len(logs) >= 4, f"{len(logs)} 条")
    check(
        "跳过原因已记录",
        any(row["status"] == "skipped" for row in logs),
        str([r["status"] for r in logs]),
    )

    # preview must not send anything
    before = len(adapter.sent)
    result = runtime.pipeline.preview(chat="张三", chat_type="friend", sender="张三", text="测试")
    check("试跑返回结果", result.get("reply") is True, str(result))
    check("试跑不真的发送", len(adapter.sent) == before)

    runtime.shutdown()
    check("关闭后状态为未运行", True)


# ------------------------------------------------------------- integration: API
def test_api() -> None:
    print("\n== HTTP 接口 ==")
    from fastapi.testclient import TestClient

    from backend.api import create_app

    runtime = BotRuntime()
    cfg = runtime.config_store.snapshot()
    cfg["adapter"] = "mock"
    cfg["ai"]["api_key"] = ""
    cfg["rules"] = []
    cfg["private"]["mode"] = "all"
    runtime.save_config(cfg)
    runtime.start()

    client = TestClient(create_app(runtime))

    r = client.get("/api/bootstrap")
    check("GET /api/bootstrap", r.status_code == 200 and "config" in r.json(), r.text[:200])

    r = client.get("/api/status")
    check("GET /api/status", r.status_code == 200 and r.json()["running"] is True, r.text[:200])

    payload = runtime.config_store.snapshot()
    payload["private"]["chats"] = ["张三", "王五"]
    r = client.put("/api/config", json=payload)
    check("PUT /api/config", r.status_code == 200 and r.json()["ok"], r.text[:300])

    bad = runtime.config_store.snapshot()
    bad["bot"]["global_per_minute"] = 0
    r = client.put("/api/config", json=bad)
    check("PUT 非法配置被拒绝", r.status_code == 400, r.text[:200])

    r = client.post("/api/mock/inject", json={"chat": "张三", "sender": "张三", "content": "你好"})
    check("POST /api/mock/inject", r.status_code == 200, r.text[:200])

    r = client.get("/api/messages?limit=10")
    check("GET /api/messages", r.status_code == 200 and "items" in r.json(), r.text[:200])

    r = client.post("/api/ai/preview", json={"text": "你好", "chat": "张三", "chat_type": "friend"})
    check("POST /api/ai/preview", r.status_code == 200, r.text[:300])
    body = r.json()
    check(
        "未配 key 时试跑给出可读错误",
        body.get("reply") is True and "API Key" in str(body.get("error", ""))
        or body.get("reply") is False,
        str(body)[:200],
    )

    r = client.post("/api/ai/test", json={"api_key": ""})
    check("未配 key 时测试连接返回 502 且有提示", r.status_code == 502, r.text[:200])

    r = client.post("/api/send", json={"chat": "张三", "text": "手动测试"})
    check("POST /api/send", r.status_code == 200 and r.json()["ok"], r.text[:200])

    r = client.post("/api/bot/stop")
    check("POST /api/bot/stop", r.status_code == 200 and r.json()["status"]["running"] is False, r.text[:200])
    client.post("/api/bot/start")

    r = client.get("/api/events")
    check("GET /api/events", r.status_code == 200 and "items" in r.json(), r.text[:200])

    runtime.shutdown()


def main() -> int:
    print(f"数据目录：{TEST_DATA}")
    test_decision_engine()
    test_pipeline_with_mock()
    test_api()
    print(f"\n===== 结果：通过 {PASS}，失败 {FAIL} =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
