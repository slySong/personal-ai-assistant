"""对话历史管理：SQLite 存储 + 滑动窗口裁剪。

每个会话（session）有多条消息（user/assistant/tool）。
trim_for_context() 按滑动窗口裁剪历史，避免超出模型上下文。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class ConversationStore:
    """对话历史持久化。SQLite 单文件存储。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        """连接上下文管理器：成功时 commit，无论如何都 close。

        sqlite3.Connection 自带的 __enter__/__exit__ 只管事务，
        不关闭连接，Windows 上会导致文件锁不释放。这里显式 close。
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    tool_calls TEXT DEFAULT '',
                    tool_call_id TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session "
                "ON messages(session_id, id)"
            )

    def create_session(self, title: str = "") -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO sessions (title) VALUES (?)", (title or "新对话",)
            )
            return cur.lastrowid

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str = "",
        tool_calls: str = "",
        tool_call_id: str = "",
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, tool_calls, tool_call_id),
            )
            # 更新会话时间戳
            c.execute(
                "UPDATE sessions SET updated_at = datetime('now','localtime') WHERE id = ?",
                (session_id,),
            )
            return cur.lastrowid

    def get_messages(self, session_id: int, limit: int = 100) -> list[dict]:
        """读取会话最近的 limit 条消息，按时间从旧到新返回。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT role, content, tool_calls, tool_call_id FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        msgs = [dict(r) for r in rows]
        msgs.reverse()
        return msgs

    def list_sessions(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, created_at, updated_at FROM sessions "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def clear_session(self, session_id: int) -> None:
        """清空会话的所有消息（保留会话本身）。"""
        with self._conn() as c:
            c.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def update_session_title(self, session_id: int, title: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE sessions SET title = ?, updated_at = datetime('now','localtime') "
                "WHERE id = ?",
                (title, session_id),
            )

    def trim_for_context(
        self, messages: list[dict], max_tokens: int = 16000
    ) -> list[dict]:
        """滑动窗口裁剪。粗略按 1 字符 ≈ 0.5 token 估算。

        保留策略：始终保留 system 消息；保留最近的若干条消息，
        使得总估算 token 数不超过 max_tokens。
        """
        # 分离 system 和其余
        system_msgs = [m for m in messages if m["role"] == "system"]
        rest = [m for m in messages if m["role"] != "system"]

        # 估算 system 消息 token 数
        def _est(msg: dict) -> int:
            text = (msg.get("content") or "") + (msg.get("tool_calls") or "")
            # 中文约 1 字符 ≈ 1 token，英文约 4 字符 ≈ 1 token，取折中 0.6
            return max(1, int(len(text) * 0.6))

        system_tokens = sum(_est(m) for m in system_msgs)
        budget = max_tokens - system_tokens

        # 从后往前保留，直到预算耗尽
        kept: list[dict] = []
        used = 0
        for m in reversed(rest):
            t = _est(m)
            if used + t > budget:
                break
            kept.insert(0, m)
            used += t

        # 如果裁剪后第一条是 tool 消息（没有对应 assistant tool_calls），丢弃
        while kept and kept[0]["role"] == "tool":
            kept.pop(0)

        return system_msgs + kept

    def to_api_messages(self, messages: list[dict]) -> list[dict]:
        """把存储格式转为 OpenAI API 消息格式。

        - role=assistant 且有 tool_calls：附加 tool_calls 字段
        - role=tool：保留 tool_call_id
        - 其余：content
        """
        api_msgs: list[dict] = []
        for m in messages:
            role = m["role"]
            msg: dict = {"role": role, "content": m.get("content") or ""}
            if role == "assistant" and m.get("tool_calls"):
                try:
                    msg["tool_calls"] = json.loads(m["tool_calls"])
                    # OpenAI 要求 assistant 消息含 tool_calls 时 content 可为 null
                    if not msg["content"]:
                        msg["content"] = None
                except json.JSONDecodeError:
                    pass
            elif role == "tool":
                msg["tool_call_id"] = m.get("tool_call_id") or ""
            api_msgs.append(msg)
        return api_msgs
