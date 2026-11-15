"""运行时 API 与重启恢复的离线回归（零外部调用）。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webapp import db, recovery
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


def test_status_has_key_parts_and_recovery_report(client, auth):
    r = client.get("/api/runtime/status", headers=auth)
    assert r.status_code == 200
    d = r.json()
    parts = d["key_example"]["parts"]
    assert {"schema", "org", "query", "embedding", "model", "authz",
            "data_version", "policy_version", "freshness"} <= set(parts)
    assert d["recovery_report"]["healthy"] is True
    assert d["integration"]["answer_cache"] == "未做（依赖 prompt + 生成模型，误命中风险高）"


def test_cache_demo_proves_l2_survives_l1_clear(client, auth):
    r = client.get("/api/runtime/cache-demo", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["after_l1_cleared"]["hit"] is True
    assert d["after_l1_cleared"]["level"] == "l2"
    assert d["after_version_change"]["hit"] is False
    assert d["permission_filter"]["after"] <= d["permission_filter"]["before"]


def test_cache_demo_admin_only_writes(client, auth):
    assert client.post("/api/runtime/invalidate", json={}).status_code in (401, 403)
    r = client.post("/api/runtime/simulate-restart", headers=auth)
    assert r.status_code == 200 and r.json()["l1_cleared"] is True
    assert r.json()["healthy"] is True


def test_cache_policy_write_requires_admin_and_validates(client, auth):
    assert client.put("/api/runtime/cache-policy", json={"ttl_seconds": 10}).status_code in (401, 403)
    assert client.put("/api/runtime/cache-policy", headers=auth,
                      json={"ttl_seconds": 0}).status_code == 400
    r = client.put("/api/runtime/cache-policy", headers=auth, json={"ttl_seconds": 600})
    assert r.status_code == 200 and r.json()["cache_policy"]["ttl_seconds"] == 600
    client.put("/api/runtime/cache-policy", headers=auth, json={"ttl_seconds": 900})


def test_policy_persistence_roundtrip(client, auth):
    r = client.put("/api/knowledge/policy", headers=auth,
                   json={"half_life_days": 45})
    assert r.status_code == 200
    saved = recovery.load_policy("knowledge_policy")
    assert saved and saved["half_life_days"] == 45
    # 模拟重启恢复：新建策略对象并还原
    from webapp import routers_knowledge as rk
    rk.restore_policy(saved)
    assert rk._current().half_life_days == 45
    client.put("/api/knowledge/policy", headers=auth, json={"half_life_days": 90})
    rk.restore_policy({"half_life_days": 90})


def test_recovery_endpoint_returns_last_report(client, auth):
    d = client.get("/api/runtime/recovery", headers=auth).json()
    assert d["healthy"] is True
    cats = {c["category"] for c in d["checks"]}
    assert {"discardable", "rebuildable", "must_persist"} <= cats


def test_startup_idempotent_on_same_store(client):
    """同一个 store 连续两次启动自检：孤儿任务只重投一次。

    改造前这里自己造一个 SQLite 文件塞业务表；现在业务表就在真 PG 的测试 schema 里，
    所以改成「插一条真实 documents 行」——不再有第二个数据库，也没有第二份 DDL。
    """
    import time
    import uuid

    from cache import CacheStore, run_startup_checks

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc_id = "d_" + uuid.uuid4().hex[:10]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,"
            "error_msg,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (doc_id, "u_test", "a.md", "a.md", 10, "analyzing", 0, "", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    s = CacheStore()
    calls = []
    r1 = run_startup_checks(s, requeue=lambda d, f: calls.append(d))
    assert [d for d in calls if d == doc_id] == [doc_id], "孤儿任务没有被重投"

    conn = db.get_conn()
    try:
        conn.execute("UPDATE documents SET status='ready' WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()

    r2 = run_startup_checks(s, requeue=lambda d, f: calls.append(d))
    assert [d for d in calls if d == doc_id] == [doc_id], "同一条孤儿任务被重复重投"
    assert doc_id not in r2.orphan_task_ids
