"""Reply pipeline: turn an inbound message into a sent reply (or a logged skip)."""
from __future__ import annotations

import logging
import queue
import random
import re
import threading
import time
from collections import deque
from typing import Any

from .adapters.base import strip_label
from .ai import AIClient, AIError
from .models import AppConfig
from .rules import Decision, DecisionEngine, IncomingMessage, in_quiet_hours

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{(msg|sender|chat|nickname|time)\}")


class ReplyPipeline:
    """Single consumer thread; serialises model calls and sends."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._queue: queue.Queue[IncomingMessage] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._lock = threading.RLock()
        self._last_reply: dict[str, float] = {}
        self._takeover_until: dict[str, float] = {}
        self._recent_sends: deque[tuple[str, str, float]] = deque(maxlen=200)
        self._global_hits: deque[float] = deque(maxlen=2000)
        self._busy = False

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reply-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=10)
        self._thread = None
        self._queue = queue.Queue()

    # ---------------------------------------------------------------- inbound
    def submit(self, msg: IncomingMessage) -> None:
        maxsize = self.runtime.config.bot.max_queue
        if self._queue.qsize() >= maxsize:
            log.warning("回复队列已满（%s），丢弃消息：%s", maxsize, msg.chat)
            return
        self._queue.put(msg)

    def note_self_message(self, chat: str, content: str = "") -> None:
        """Called when *we* (the account) sent something.

        Distinguish our own sends from the human typing: our sends are recorded
        in `_recent_sends`; anything else means the human took over the chat.
        """
        now = time.time()
        with self._lock:
            for idx, (sent_chat, sent_text, ts) in enumerate(self._recent_sends):
                if sent_chat != chat:
                    continue
                if now - ts > 180:
                    continue
                if not content or content.strip() == sent_text.strip():
                    del self._recent_sends[idx]
                    return
            minutes = self.runtime.config.bot.takeover_minutes
            if minutes <= 0:
                return
            self._takeover_until[chat] = now + minutes * 60
            log.info("检测到人工发言，暂停「%s」自动回复 %s 分钟", chat, minutes)
        self.runtime.notify(
            f"人工接管：「{chat}」自动回复暂停 {self.runtime.config.bot.takeover_minutes} 分钟",
            kind="takeover",
        )

    def remember_sent(self, chat: str, text: str) -> None:
        """Record an out-of-band send so the echo is not mistaken for the human."""
        with self._lock:
            self._recent_sends.append((chat, text, time.time()))

    def takeover_remaining(self, chat: str) -> float:
        with self._lock:
            until = self._takeover_until.get(chat, 0.0)
        return max(0.0, until - time.time())

    def resume_chat(self, chat: str) -> None:
        with self._lock:
            self._takeover_until.pop(chat, None)

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._busy = True
            try:
                self._process(msg)
            except Exception:  # noqa: BLE001
                log.exception("处理消息失败：%s", getattr(msg, "chat", "?"))
                self.runtime.notify("处理消息时出现异常，详见日志", level="error", kind="error")
            finally:
                self._busy = False

    # ---------------------------------------------------------------- decision
    def _process(self, msg: IncomingMessage) -> None:
        cfg = self.runtime.config
        store = self.runtime.store
        store.add_message(
            chat=msg.chat,
            chat_type=msg.chat_type,
            sender=msg.sender,
            direction="in",
            content=msg.content,
            msg_type=msg.msg_type,
        )

        engine = DecisionEngine(cfg)
        decision = engine.resolve(msg, self.runtime.my_names())

        skip_reason = self._pre_check(cfg, msg, decision)
        if skip_reason or not decision.reply:
            reason = skip_reason or decision.reason or "无需回复"
            store.add_message(
                chat=msg.chat,
                chat_type=msg.chat_type,
                sender="小鱼",
                direction="out",
                content="",
                rule=decision.rule_name,
                status="skipped",
                note=reason,
            )
            self.runtime.notify(f"未回复「{msg.chat}」：{reason}", kind="skip", level="debug")
            return

        # ---- 图片 / 表情包 -------------------------------------------------
        image_note = ""
        if msg.msg_type in ("image", "sticker"):
            ai = AIClient(cfg.ai)
            if msg.image_path and ai.vision_ready():
                try:
                    image_note = ai.describe_image(msg.image_path)
                except AIError as exc:
                    self.runtime.notify(f"识图失败：{exc}", kind="error", level="warn")
                except Exception:  # noqa: BLE001
                    log.debug("识图异常", exc_info=True)
            if image_note:
                self.runtime.notify(
                    f"看到「{msg.chat}」发的图：{image_note[:40]}", kind="reply", level="debug"
                )
            elif not cfg.bot.image_reply and msg.msg_type == "image":
                store.add_message(
                    chat=msg.chat, chat_type=msg.chat_type, sender="小鱼", direction="out",
                    content="", rule=decision.rule_name, status="skipped",
                    note="收到图片，但没有配置视觉模型（ai.vision_model）",
                )
                self.runtime.notify(
                    f"未回复「{msg.chat}」：收到图片但没有视觉模型", kind="skip", level="warn"
                )
                return

            # 对方发表情包 → 用表情库回一张（不配文字，像真人）
            if msg.msg_type == "sticker" and cfg.bot.sticker_send:
                from . import media as media_mod

                sticker = media_mod.pick_sticker()
                if sticker is not None:
                    sent, detail = self.runtime.send_image(msg.chat, str(sticker))
                    store.add_message(
                        chat=msg.chat, chat_type=msg.chat_type, sender="小鱼", direction="out",
                        content="[表情]", msg_type="sticker",
                        rule=decision.rule_name,
                        status="ok" if sent else "failed",
                        note=f"表情包：{sticker.name} · {detail}",
                    )
                    if sent:
                        return
                    log.warning("回表情包失败：%s", detail)

        text = self._compose_reply(cfg, msg, decision, image_note=image_note)

        # 模型可以用 [[表情]] 主动要一张表情包（这个标记不发给对面）
        want_sticker = False
        if text:
            for token in ("[[表情]]", "[[表情包]]", "[[sticker]]", "[[emoji]]"):
                if token in text:
                    want_sticker = True
                    text = text.replace(token, "").strip()
            if want_sticker:
                text = text.strip().strip("。，,.") if text.strip() in {"。", "，", ","} else text.strip()
        # 兜底：对方明明要表情包，模型却回「发不出去/做不到」→ 当成要发表情包
        if cfg.bot.sticker_send and text:
            import re as _re

            asked = _re.search(r"(发|来|整|搞|给).{0,4}表情|表情包", msg.content or "")
            refused = _re.search(
                r"发不出去|发不了|做不到|不能发|不会发|没法发|只会打字|只能打字", text
            )
            if asked and refused and not want_sticker:
                want_sticker = True
                log.info("模型拒绝了表情包请求，兜底改为直接发表情包")
                text = ""

        if not text and not (want_sticker and cfg.bot.sticker_send):
            store.add_message(
                chat=msg.chat,
                chat_type=msg.chat_type,
                sender="小鱼",
                direction="out",
                content="",
                rule=decision.rule_name,
                status="skipped",
                note="模型没有给出可用回复",
            )
            return

        delay = random.uniform(cfg.bot.reply_delay_min, cfg.bot.reply_delay_max)
        if delay > 0 and self._stop.wait(delay):
            return

        try:
            self.runtime._hide_viewer_if_needed()  # noqa: SLF001 - keep the clone window hidden
        except Exception:  # noqa: BLE001
            pass
        # 模型可能只发一个 [[表情]]：这种情况只发表情，不发空文本
        if text:
            ok, detail = self.runtime.adapter.send_text(msg.chat, text)
            store.add_message(
                chat=msg.chat,
                chat_type=msg.chat_type,
                sender="小鱼",
                direction="out",
                content=text,
                rule=decision.rule_name,
                status="ok" if ok else "failed",
                note=detail,
            )
        else:
            ok, detail = True, "（只发表情包，没有文字）"
        with self._lock:
            self._last_reply[msg.chat] = time.time()
            self._global_hits.append(time.time())
            if ok:
                self._recent_sends.append((msg.chat, text, time.time()))
        if ok:
            if text:
                self.runtime.notify(f"已回复「{msg.chat}」：{text[:60]}", kind="reply")
        else:
            self.runtime.notify(f"发送失败「{msg.chat}」：{detail}", kind="error", level="error")

        # 模型点了「[[表情]]」→ 补发一张表情包
        if ok and want_sticker and cfg.bot.sticker_send:
            from . import media as media_mod

            sticker = media_mod.pick_sticker()
            if sticker is None:
                self.runtime.notify("想发表情包，但表情库是空的", kind="skip", level="warn")
            else:
                sent, sdetail = self.runtime.send_image(msg.chat, str(sticker))
                store.add_message(
                    chat=msg.chat,
                    chat_type=msg.chat_type,
                    sender="小鱼",
                    direction="out",
                    content="[表情]",
                    msg_type="sticker",
                    rule=decision.rule_name,
                    status="ok" if sent else "failed",
                    note=f"表情包：{sticker.name} · {sdetail}",
                )
                if sent:
                    self.runtime.notify(f"给「{msg.chat}」发了个表情包", kind="reply")
                else:
                    log.warning("补发表情包失败：%s", sdetail)

    def _pre_check(self, cfg: AppConfig, msg: IncomingMessage, decision: Decision) -> str:
        """Environment gates that do not depend on the decision itself."""
        now = time.time()

        if cfg.bot.quiet_hours_enabled:
            import datetime as _dt

            if in_quiet_hours(_dt.datetime.now(), cfg.bot.quiet_start, cfg.bot.quiet_end):
                return f"处于免打扰时段（{cfg.bot.quiet_start}-{cfg.bot.quiet_end}）"

        with self._lock:
            takeover = self._takeover_until.get(msg.chat, 0.0)
        if takeover > now:
            return f"人工接管中，还剩 {int(takeover - now)} 秒"

        cooldown = max(cfg.bot.per_chat_cooldown, decision.cooldown)
        with self._lock:
            last = self._last_reply.get(msg.chat, 0.0)
        if cooldown > 0 and now - last < cooldown:
            return f"会话冷却中（{cooldown:.0f} 秒内只回一次）"

        limit = cfg.bot.global_per_minute
        with self._lock:
            recent = [ts for ts in self._global_hits if now - ts < 60]
        if len(recent) >= limit:
            return f"已达全局频率上限（{limit} 条/分钟）"
        return ""

    # ---------------------------------------------------------------- composing
    def _compose_reply(self, cfg: AppConfig, msg: IncomingMessage, decision: Decision, image_note: str = "") -> str:
        if decision.template.strip():
            return self._fill_template(decision.template, msg)
        try:
            return self.ai_reply(msg, prompt_override=decision.prompt, cfg=cfg, image_note=image_note)
        except AIError as exc:
            self.runtime.notify(f"模型调用失败：{exc}", kind="error", level="error")
            return ""

    @staticmethod
    def _fill_template(template: str, msg: IncomingMessage) -> str:
        import datetime as _dt

        values = {
            "msg": strip_label(msg.content),
            "sender": msg.sender or "",
            "chat": msg.chat or "",
            "nickname": "落小鱼",
            "time": _dt.datetime.now().strftime("%H:%M"),
        }
        return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), template).strip()

    def ai_reply(
        self,
        msg: IncomingMessage,
        *,
        prompt_override: str = "",
        cfg: AppConfig | None = None,
        image_note: str = "",
    ) -> str:
        cfg = cfg or self.runtime.config
        client = AIClient(cfg.ai)
        history = (
            self.runtime.store.history(msg.chat, cfg.ai.history_rounds)
            if cfg.ai.history_rounds > 0
            else []
        )
        user_text = strip_label(msg.content)
        if image_note:
            user_text = f"{user_text}（对方发的图我看过：{image_note}）".strip()
        messages = client.build_messages(
            chat=msg.chat,
            chat_type=msg.chat_type,
            sender=msg.sender,
            user_text=user_text,
            history=history,
            room_rules=prompt_override,
        )
        text = client.complete(messages)
        return text.strip()[: cfg.ai.reply_max_chars]

    # ----------------------------------------------------------------- preview
    def preview(self, *, chat: str, chat_type: str, sender: str, text: str) -> dict[str, Any]:
        """Dry-run used by the console: decision + model output, nothing sent."""
        cfg = self.runtime.config
        msg = IncomingMessage(
            chat=chat or "测试会话",
            chat_type=chat_type if chat_type in ("friend", "group") else "friend",
            sender=sender or chat or "测试用户",
            content=text,
            msg_type="text",
            at_me=False,
            timestamp=time.time(),
        )
        decision = DecisionEngine(cfg).resolve(msg, self.runtime.my_names())
        result: dict[str, Any] = {
            "reply": decision.reply,
            "reason": decision.reason,
            "source": decision.source,
            "rule": decision.rule_name,
            "template": decision.template,
            "reply_text": "",
        }
        if decision.reply:
            try:
                result["reply_text"] = (
                    self._fill_template(decision.template, msg)
                    if decision.template.strip()
                    else self.ai_reply(msg, prompt_override=decision.prompt, cfg=cfg)
                )
            except AIError as exc:
                result["error"] = str(exc)
        return result

    # -------------------------------------------------------------------- state
    def state(self) -> dict[str, Any]:
        with self._lock:
            active_takeover = {
                chat: int(until - time.time())
                for chat, until in self._takeover_until.items()
                if until > time.time()
            }
        return {
            "queued": self._queue.qsize(),
            "busy": self._busy,
            "takeover": active_takeover,
        }
