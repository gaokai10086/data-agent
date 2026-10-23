"""data_agent_app 独立 MySQL 应用库的会话 repository。"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from typing import Any
import pymysql
from app.config.app_config import app_config

class MySQLConversationRepository:
    def __init__(self):
        cfg = app_config.db_meta
        self.host, self.port, self.user, self.password = cfg.host, cfg.port, cfg.user, cfg.password
        self.database = os.getenv("APP_DB_DATABASE", "data_agent_app")

    def _connect(self, database: str | None = None):
        return pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                               database=database or self.database, charset="utf8mb4", autocommit=True,
                               cursorclass=pymysql.cursors.DictCursor)
    def initialize(self):
        conn = pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password, charset="utf8mb4", autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        finally: conn.close()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS conversations (id VARCHAR(128) PRIMARY KEY, title VARCHAR(200) NOT NULL DEFAULT '', created_at DATETIME(6) NOT NULL, updated_at DATETIME(6) NOT NULL, deleted_at DATETIME(6) NULL) ENGINE=InnoDB""")
                cur.execute("""CREATE TABLE IF NOT EXISTS messages (id BIGINT PRIMARY KEY AUTO_INCREMENT, conversation_id VARCHAR(128) NOT NULL, request_id VARCHAR(128) NULL, role ENUM('user','assistant') NOT NULL, agent_type VARCHAR(64) NOT NULL DEFAULT '', status ENUM('pending','completed','failed') NOT NULL, content LONGTEXT NOT NULL, structured_result JSON NULL, created_at DATETIME(6) NOT NULL, UNIQUE KEY uq_message_request (conversation_id,request_id,role), KEY idx_messages_conversation (conversation_id,id), CONSTRAINT fk_messages_conversation FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE) ENGINE=InnoDB""")
        finally: conn.close()
    @staticmethod
    def _now(): return datetime.now(timezone.utc).replace(tzinfo=None)
    def create_conversation(self, conversation_id, title=""):
        now=self._now(); conn=self._connect()
        try:
            with conn.cursor() as c:
                c.execute("INSERT IGNORE INTO conversations(id,title,created_at,updated_at) VALUES(%s,%s,%s,%s)",(conversation_id,title,now,now)); c.execute("SELECT * FROM conversations WHERE id=%s AND deleted_at IS NULL",(conversation_id,)); return c.fetchone()
        finally: conn.close()
    def get_conversation(self, conversation_id):
        conn=self._connect()
        try:
            with conn.cursor() as c: c.execute("SELECT * FROM conversations WHERE id=%s AND deleted_at IS NULL",(conversation_id,)); return c.fetchone()
        finally: conn.close()
    def list_conversations(self):
        conn=self._connect()
        try:
            with conn.cursor() as c: c.execute("SELECT * FROM conversations WHERE deleted_at IS NULL ORDER BY updated_at DESC"); return c.fetchall()
        finally: conn.close()
    def delete_conversation(self, conversation_id):
        conn=self._connect()
        try:
            with conn.cursor() as c: c.execute("UPDATE conversations SET deleted_at=%s,updated_at=%s WHERE id=%s AND deleted_at IS NULL",(self._now(),self._now(),conversation_id)); return c.rowcount>0
        finally: conn.close()
    def add_message(self, conversation_id, *, request_id, role, agent_type="", status="completed", content="", structured_result=None):
        if not self.get_conversation(conversation_id): raise ValueError("会话不存在")
        conn=self._connect()
        try:
            with conn.cursor() as c:
                c.execute("INSERT IGNORE INTO messages(conversation_id,request_id,role,agent_type,status,content,structured_result,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(conversation_id,request_id,role,agent_type,status,content,json.dumps(structured_result,ensure_ascii=False,default=str) if structured_result is not None else None,self._now())); c.execute("SELECT * FROM messages WHERE conversation_id=%s AND request_id<=>%s AND role=%s ORDER BY id DESC LIMIT 1",(conversation_id,request_id,role)); row=c.fetchone(); c.execute("UPDATE conversations SET updated_at=%s WHERE id=%s",(self._now(),conversation_id));
                if row and row.get("structured_result"): row["structured_result"]=json.loads(row["structured_result"])
                return row
        finally: conn.close()
    def update_message(self, message_id, *, status, content="", structured_result=None):
        conn=self._connect()
        try:
            with conn.cursor() as c: c.execute("UPDATE messages SET status=%s,content=%s,structured_result=%s WHERE id=%s",(status,content,json.dumps(structured_result,ensure_ascii=False,default=str) if structured_result is not None else None,message_id))
        finally: conn.close()
    def list_messages(self, conversation_id):
        conn=self._connect()
        try:
            with conn.cursor() as c: c.execute("SELECT * FROM messages WHERE conversation_id=%s ORDER BY id",(conversation_id,)); rows=c.fetchall();
            for row in rows:
                if row.get("structured_result"): row["structured_result"]=json.loads(row["structured_result"])
            return rows
        finally: conn.close()

conversation_repository = MySQLConversationRepository()
