"""cache 包的测试夹具：每个用例一个独立 PostgreSQL schema。

改造前 `cache` 的单元测试是「零外部依赖」的（用 SQLite 临时文件），换到真 PG 之后
必然要有一个跑着的库。这里的取舍是：

- **隔离强度不降级**：每用例 `CREATE SCHEMA kb_cache_xxx`，`search_path` 指过去，
  业务库的 public 表完全看不见；结束时 `DROP SCHEMA ... CASCADE`。
  这比「共用一个库然后清表」可靠得多——清表漏一张就会串味。
- **DDL 不另抄一份**：新 schema 里直接执行 `scripts/pg_init.sql`。
  这份文件同时是容器 initdb 与 `webapp.db.init_db()` 的事实源，三处共用一份，
  避免出现「测试库结构和生产结构不一致」这种最隐蔽的假绿。
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DDL_PATH = ROOT / "scripts" / "pg_init.sql"
DSN = "postgresql://kb:kb_demo_only@127.0.0.1:55432/kb"


def _dsn() -> str:
    import os
    return os.environ.get("WEBAPP_DATABASE_URL") or DSN


def create_schema(name: str) -> None:
    import psycopg

    ddl = DDL_PATH.read_text(encoding="utf-8")
    with psycopg.connect(_dsn(), autocommit=True, connect_timeout=5) as raw:
        with raw.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            # 无参数 → psycopg 走简单查询协议，允许多条语句（与 db.init_db 同路径）
            cur.execute(ddl)


def drop_schema(name: str) -> None:
    import psycopg

    with psycopg.connect(_dsn(), autocommit=True, connect_timeout=5) as raw:
        with raw.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture()
def schema() -> str:
    name = "kb_cache_" + uuid.uuid4().hex[:8]
    create_schema(name)
    try:
        yield name
    finally:
        drop_schema(name)


@pytest.fixture()
def conn(schema):
    """直连测试 schema 的连接（替代原来手搓 sqlite3 连临时文件）。"""
    import os

    import pgkit

    prev = os.environ.get("WEBAPP_DB_SCHEMA")
    os.environ["WEBAPP_DB_SCHEMA"] = schema
    try:
        c = pgkit.connect()
    finally:
        if prev is None:
            os.environ.pop("WEBAPP_DB_SCHEMA", None)
        else:
            os.environ["WEBAPP_DB_SCHEMA"] = prev
    try:
        yield c
    finally:
        c.close()
