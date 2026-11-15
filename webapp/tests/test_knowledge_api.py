"""知识治理 API 与迁移的离线回归（零外部调用）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp import db, rag
from webapp.tests._dbutil import columns
from webapp.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_knowledge_columns_exist_after_migration():
    conn = db.get_conn()
    try:
        cols = columns(conn, "documents")
    finally:
        conn.close()
    assert {"valid_from", "expires_at", "last_verified_at", "revision",
            "authority", "scope_json"} <= cols


# 已删除 test_migrate_knowledge_columns_is_idempotent_on_legacy_db：
# 它测的是 `db._migrate_knowledge_columns()`——那段「给 SQLite 旧库补列」的代码
# 在改用 PostgreSQL 后整段删除（PG 用显式 DDL，不需要运行时补列）。
# 对应的正向断言（列确实存在）保留在上一条 test_knowledge_columns_exist_after_migration 里。

