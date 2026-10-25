"""Persistence: JSON config store + SQLite message/event log."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from . import paths
from .models import AppConfig, default_config

log = logging.getLogger(__name__)




CAPABILITY_BLOCK = (
    "\n\n## 你的能力\n\n"
    "你能发文字、能看懂对方发来的图片、也能发表情包。"
    "想发表情包就在回复里写 [[表情]]，系统会换成一张真表情包发出去（标记对方看不到）。"
    "绝不要说「我发不了表情包 / 只能打字」——那是错的；"
    "也不要在没被要求时主动提这件事。"
)


def _ensure_persona_capability(cfg) -> bool:
    """纠正常人设里的能力描述。

    老人设写着「遇到超出的请求，直接说办不到」+「发不了图片」，
    模型会把「发表情包」也当成超出的请求，于是永远拒绝。
    这里把那句话换成精确版本，并在末尾补上能力说明。
    """
    try:
        persona = cfg.ai.persona or ""
    except Exception:  # noqa: BLE001
        return False
    changed = False

    vague = "遇到超出的请求，直接说办不到，别硬撑，也别编。"
    precise = (
        "遇到真的做不到的（转发文件、打语音/电话、看朋友圈），直接说办不到，别硬撑。"
        "但发表情包你做得到，别说做不到。"
    )
    if vague in persona:
        persona = persona.replace(vague, precise)
        changed = True

    if "[[表情]]" not in persona:
        persona = (persona.rstrip() + CAPABILITY_BLOCK).strip()
        changed = True

    if changed:
        cfg.ai.persona = persona
    return changed


def _migrate_persona(cfg) -> bool:
    """老版本人设写着「发不了图片」，会把新能力压死 → 自动纠正一次。"""
    try:
        persona = cfg.ai.persona or ""
    except Exception:  # noqa: BLE001
        return False
    old_block = "你只会读和回微信文字消息：看不了朋友圈，发不了图片、文件、语音，也打不了电话。"
    if old_block not in persona:
        return False
    new_block = (
        "能做的：读和回微信消息；对方发来的图片你看得懂；需要时你能发表情包——"
        "想发就在回复里写 [[表情]]，系统会换成一张真表情包发出去。\n"
        "做不到的：看不了朋友圈，发不了文件、语音，也打不了电话。这些直接说办不到。\n"
        "绝不要说「我发不了表情包 / 只能打字」这类话——那是错的。"
    )
    cfg.ai.persona = persona.replace(old_block, new_block)
    return True

class ConfigStore:
    """Thread-safe JSON config on disk, validated through pydantic."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config: AppConfig = default_config()
        self._path = paths.config_path()
        self.load()

    # ---------------------------------------------------------------- loading
    def load(self) -> AppConfig:
        with self._lock:
            path = self._path
            if not path.exists():
                self._config = default_config()
                log.info("已生成默认配置: %s", path)
            else:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    self._config = AppConfig.model_validate(raw)
                except (OSError, ValueError) as exc:
                    broken = path.with_suffix(f".broken-{int(time.time())}.json")
                    try:
                        path.replace(broken)
                        log.error("配置文件损坏（%s），已备份为 %s，使用默认配置", exc, broken)
                    except OSError:
                        log.error("配置文件损坏且无法备份（%s），使用默认配置", exc)
                    self._config = default_config()
            # Always carry a revision stamp: the console compares it so a stale
            # browser tab cannot overwrite the whole document (older files have
            # no stamp at all, which used to let them clobber the engine choice).
            self._config.saved_at = time.time()
            if _migrate_persona(self._config):
                log.info("已把老人设里「发不了图片」的说明更新为当前能力")
            if _ensure_persona_capability(self._config):
                log.info("已在人设末尾补充「能发表情包」的能力说明")
            self._write_locked()
            return self._config

    # ----------------------------------------------------------------- access
    @property
    def config(self) -> AppConfig:
        with self._lock:
            return self._config

    def snapshot(self, mask_secrets: bool = True) -> dict[str, Any]:
        """配置快照。默认把 API Key 打码：控制台无鉴权，密钥不该回传。"""
        with self._lock:
            data = self._config.model_dump()
        if mask_secrets:
            ai = data.get("ai") or {}
            key = str(ai.get("api_key") or "")
            ai["api_key_set"] = bool(key.strip())
            ai["api_key_hint"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ""
            ai["api_key"] = ""
            data["ai"] = ai
        return data

    def save(self, data: dict[str, Any] | AppConfig) -> AppConfig:
        """Validate and persist a new config document."""
        with self._lock:
            if isinstance(data, AppConfig):
                cfg = data
            else:
                cfg = AppConfig.model_validate(data)
            # 前端拿到的是打码密钥：留空表示"不修改"，沿用已保存的
            incoming_key = (cfg.ai.api_key or "").strip()
            if not incoming_key:
                existing_key = (self._config.ai.api_key or "").strip()
                if existing_key:
                    cfg.ai.api_key = existing_key
            cfg.saved_at = time.time()  # revision stamp for stale-write rejection
            self._config = cfg
            self._write_locked()
            return cfg

    def patch(self, **sections: Any) -> AppConfig:
        with self._lock:
            current = self._config.model_dump()
            current.update(sections)
            return self.save(current)

    def _write_locked(self) -> None:
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        payload = self._config.model_dump()
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


class MessageStore:
    """SQLite log of every inbound/outbound message plus system events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path = paths.db_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL NOT NULL,
                    chat      TEXT NOT NULL,
                    chat_type TEXT NOT NULL DEFAULT 'friend',
                    sender    TEXT NOT NULL DEFAULT '',
                    direction TEXT NOT NULL,
                    msg_type  TEXT NOT NULL DEFAULT 'text',
                    content   TEXT NOT NULL DEFAULT '',
                    rule      TEXT NOT NULL DEFAULT '',
                    status    TEXT NOT NULL DEFAULT 'ok',
                    note      TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat, ts);
                CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

                CREATE TABLE IF NOT EXISTS events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      REAL NOT NULL,
                    level   TEXT NOT NULL DEFAULT 'info',
                    kind    TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------ write
    def add_message(
        self,
        *,
        chat: str,
        chat_type: str,
        sender: str,
        direction: str,
        content: str,
        msg_type: str = "text",
        rule: str = "",
        status: str = "ok",
        note: str = "",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (ts, chat, chat_type, sender, direction, msg_type,"
                " content, rule, status, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    chat,
                    chat_type,
                    sender,
                    direction,
                    msg_type,
                    content,
                    rule,
                    status,
                    note,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_event(self, kind: str, message: str, level: str = "info") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, level, kind, message) VALUES (?,?,?,?)",
                (time.time(), level, kind, message),
            )
            self._conn.commit()

    # ------------------------------------------------------------------- read
    def list_messages(
        self, limit: int = 50, offset: int = 0, chat: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages"
        params: list[Any] = []
        if chat:
            sql += " WHERE chat = ?"
            params.append(chat)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, chat: str, rounds: int) -> list[dict[str, str]]:
        """Last N messages of a chat shaped as chat-completion history."""
        if rounds <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT direction, sender, content FROM messages"
                " WHERE chat = ? AND direction IN ('in','out') AND content != ''"
                " ORDER BY id DESC LIMIT ?",
                (chat, rounds * 2),
            ).fetchall()
        out: list[dict[str, str]] = []
        for row in reversed(rows):
            role = "user" if row["direction"] == "in" else "assistant"
            content = row["content"]
            if role == "user" and row["sender"] and row["sender"] not in ("Self", "自己"):
                content = f"{row['sender']}: {content}"
            out.append({"role": role, "content": content})
        return out

    def stats(self) -> dict[str, Any]:
        day_start = time.time() - 86400
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
            inbound = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE direction='in'"
            ).fetchone()["c"]
            replied = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE direction='out'"
            ).fetchone()["c"]
            today = self._conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE ts >= ?", (day_start,)
            ).fetchone()["c"]
            chats = self._conn.execute(
                "SELECT COUNT(DISTINCT chat) AS c FROM messages WHERE ts >= ?", (day_start,)
            ).fetchone()["c"]
        return {
            "total": total,
            "inbound": inbound,
            "replied": replied,
            "last24h": today,
            "chats24h": chats,
        }

    def clear_messages(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
