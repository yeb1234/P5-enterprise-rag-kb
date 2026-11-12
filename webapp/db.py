"""SQLite 数据层（标准库实现，零外部依赖，离线可跑）。

设计取舍（与项目生产架构对齐的说明见 docs/next-version-messaging-and-plugins.md）：
- 演示切片用 SQLite 作为“唯一真值”载体，对应生产里的 PostgreSQL（ingest_jobs / audit 台账）。
- 文档分块、badcase、评估任务、聊天日志全部落库，保证“采集—保存—查看—导出”闭环可复现。
- 不使用外部消息中间件；webapp/queue.py 里的进程内信号量是 Celery+Redis/RabbitMQ 的演示级替身，
  用于展示“繁忙排队 / 降级”行为，生产里换成真实 broker 即可。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

# 数据目录放在项目根 .data/webapp 下，已被 .gitignore 忽略
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".data" / "webapp"
DATA_DIR.mkdir(parents=True, exist_ok=True)
# 允许通过环境变量注入独立数据库（测试隔离 / 多实例），默认仍是 app.db。
DB_PATH = Path(os.environ.get("WEBAPP_DB_PATH", str(DATA_DIR / "app.db")))

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    email        TEXT,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_tasks (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'running', -- draft|running|paused|done
    target_count INTEGER NOT NULL DEFAULT 0,      -- 计划评测的样本总数
    created_by   TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    title        TEXT,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'queued',  -- queued|analyzing|ready|error
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    error_msg    TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS chat_logs (
    id           TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL,  -- 'user' | 'assistant'
    content      TEXT NOT NULL,
    degraded     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_conv ON chat_logs(conversation_id);

-- 问答来源追溯：每条助手回答对应的检索命中分块（doc/chunk/得分/方式/全文）。
-- 与 chat_logs 通过 conversation_id + msg_id 关联，是「智能问答可溯源」的真值载体，
-- 对应生产里 answer → citations → chunk 的引用链（Milvus chunk_id + PG 元数据）。
CREATE TABLE IF NOT EXISTS chat_sources (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    msg_id          TEXT NOT NULL,         -- 关联的助手消息 id（chat_logs.id）
    seq             INTEGER NOT NULL,       -- 来源排序（与 retrieve 的 top_k 顺序一致）
    doc_id          TEXT,
    doc_title       TEXT,
    chunk_id        TEXT,
    chunk_idx       INTEGER,
    snippet         TEXT,
    score           REAL,
    retrieval_mode  TEXT,
    rerank          TEXT,
    chunk_text      TEXT,                   -- 命中分块全文，便于前端弹窗查看与审计回溯
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_src_conv ON chat_sources(conversation_id);
CREATE INDEX IF NOT EXISTS ix_chat_src_msg ON chat_sources(msg_id);

CREATE TABLE IF NOT EXISTS badcases (
    id           TEXT PRIMARY KEY,
    eval_task_id TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT,
    verdict      TEXT,                  -- 标注/判定结果：正确|错误|部分正确|不确定
    issue_desc   TEXT,                  -- 问题说明
    source       TEXT NOT NULL DEFAULT 'chat', -- chat|manual|eval
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|in_review|resolved
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_badcase_task ON badcases(eval_task_id);
CREATE INDEX IF NOT EXISTS ix_badcase_status ON badcases(status);
CREATE INDEX IF NOT EXISTS ix_badcase_created ON badcases(created_at);

-- 审计台账：只追加（append-only），记录「谁 / 何时 / 对什么 / 做了什么」。
-- 对应生产骨架 README §4 的 /audit-logs 与 C 档缺口「谁在什么时候查了什么」。
-- 演示切片只埋关键动作，不追求字段完备；生产里还要接 trace_id、留存与脱敏策略。
CREATE TABLE IF NOT EXISTS audit_logs (
    id         TEXT PRIMARY KEY,
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    action     TEXT NOT NULL,   -- login|register|upload|delete|chat|badcase|export|search
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_logs(actor);

-- 运行时配置（LLM / embedding / rerank 提供者选择与本地端点）。
-- 生产里对应配置中心/环境注入；演示切片落 SQLite，切换即时生效、无需重启。
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 分块向量缓存：按 (chunk_id, model) 缓存，避免每次检索重算。
-- 真实系统里向量在 Milvus；这里作为「可运行替身」，契约与 embed 输出一致。
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id   TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vec        TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model)
);
"""


def get_conn() -> sqlite3.Connection:
    """每个请求一个连接；sqlite3 默认串行，靠 _lock 兜底写并发。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def log_action(actor: str, role: str, action: str, target: str = "", detail: str = "") -> None:
    """只追加一条审计记录。审计是旁路：任何异常都不能影响业务主流程。"""
    try:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_logs(id,actor,role,action,target,detail,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor, role, action, target, detail,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.executescript(SCHEMA)
            _seed(conn)
            conn.commit()
        finally:
            conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    """首次启动种入演示账号与评估任务，保证开箱即可演示。"""
    cur = conn.cursor()
    # 管理员账号 admin / admin123（口令哈希口径与 security.py 完全一致）
    if not cur.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        salt = uuid.uuid4().hex
        dk = hashlib.pbkdf2_hmac(
            "sha256", "admin123".encode(), bytes.fromhex(salt), 100_000
        ).hex()
        cur.execute(
            "INSERT INTO users(id,username,email,password_salt,password_hash,role,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "admin", "admin@example.com", salt, dk, "admin",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
    # 演示评估任务
    tasks = [
        ("ET-001", "产品说明书问答评测", "验证分块问答在说明书场景的准确率与引用完整性", "running", 20),
        ("ET-002", "客服话术合规评测", "检查回答是否符合合规与品牌口径", "running", 15),
        ("ET-003", "知识库检索召回评测", "评估检索为空/超时等边界场景的降级表现", "paused", 10),
    ]
    for tid, name, desc, status, target in tasks:
        if not cur.execute("SELECT 1 FROM eval_tasks WHERE id=?", (tid,)).fetchone():
            cur.execute(
                "INSERT INTO eval_tasks(id,name,description,status,target_count,created_by,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (tid, name, desc, status, target, "admin",
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
