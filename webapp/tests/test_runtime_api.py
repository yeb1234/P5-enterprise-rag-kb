"""运行时 API 与重启恢复的离线回归（零外部调用）。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webapp import recovery
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


def test_startup_idempotent_on_same_store(tmp_path, monkeypatch):
    """同一个库连续启动两次：孤儿任务不重复重投。"""
    from cache import CacheStore
    from pathlib import Path
    dbp = tmp_path / "restart.db"
    import sqlite3
    con = sqlite3.connect(str(dbp))
    con.executescript(
        "CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT, status TEXT, "
        "updated_at TEXT, chunk_count INTEGER DEFAULT 0, checksum TEXT DEFAULT '');"
        "INSERT INTO documents VALUES ('d1','a.md','analyzing','2026-11-14T12:00:00Z',0,'');")
    con.commit(); con.close()
    s = CacheStore(dbp)
    calls = []
    from cache import run_startup_checks
    r1 = run_startup_checks(s, requeue=lambda d, f: calls.append(d))
    assert r1.requeued == 1
    con = sqlite3.connect(str(dbp))
    con.execute("UPDATE documents SET status='ready' WHERE id='d1'")
    con.commit(); con.close()
    r2 = run_startup_checks(s, requeue=lambda d, f: calls.append(d))
    assert r2.requeued == 0 and len(calls) == 1
