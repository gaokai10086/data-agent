"""独立应用数据库中的会话与消息持久化。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRepository:
    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("APP_CONVERSATION_DB", "data/conversations.sqlite3"))
        self._lock = threading.RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                request_id TEXT,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                agent_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
                content TEXT NOT NULL DEFAULT '',
                structured_result TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(conversation_id, request_id, role)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
            """)

    def create_conversation(self, conversation_id: str, title: str = "") -> dict[str, Any]:
        now = _now()
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)", (conversation_id, title, now, now))
            row = conn.execute("SELECT * FROM conversations WHERE id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
            if row is None:
                raise ValueError("会话不存在或已删除")
            return dict(row)

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM conversations WHERE deleted_at IS NULL ORDER BY updated_at DESC")]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
            return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE conversations SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL", (_now(), _now(), conversation_id))
            return cur.rowcount > 0

    def add_message(self, conversation_id: str, *, request_id: str | None, role: str, agent_type: str = "", status: str = "completed", content: str = "", structured_result: Any = None) -> dict[str, Any]:
        if role not in {"user", "assistant"} or status not in {"pending", "completed", "failed"}:
            raise ValueError("非法消息角色或状态")
        now = _now()
        encoded = json.dumps(structured_result, ensure_ascii=False, default=str) if structured_result is not None else None
        with self._lock, self._connect() as conn:
            if not self.get_conversation(conversation_id):
                raise ValueError("会话不存在")
            conn.execute("""INSERT OR IGNORE INTO messages(conversation_id,request_id,role,agent_type,status,content,structured_result,created_at) VALUES(?,?,?,?,?,?,?,?)""", (conversation_id, request_id, role, agent_type, status, content, encoded, now))
            row = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND request_id IS ? AND role=? ORDER BY id DESC LIMIT 1", (conversation_id, request_id, role)).fetchone()
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            result = dict(row)
            if result.get("structured_result"):
                result["structured_result"] = json.loads(result["structured_result"])
            return result

    def update_message(self, message_id: int, *, status: str, content: str = "", structured_result: Any = None) -> None:
        encoded = json.dumps(structured_result, ensure_ascii=False, default=str) if structured_result is not None else None
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE messages SET status=?, content=?, structured_result=? WHERE id=?", (status, content, encoded, message_id))

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
            for row in rows:
                if row.get("structured_result"):
                    row["structured_result"] = json.loads(row["structured_result"])
            return rows


conversation_repository = ConversationRepository()
