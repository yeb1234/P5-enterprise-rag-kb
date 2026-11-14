"""知识治理 API 与迁移的离线回归（零外部调用）。"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from webapp import db, rag
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
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    finally:
        conn.close()
    assert {"valid_from", "expires_at", "last_verified_at", "revision",
            "authority", "scope_json"} <= cols


def test_migrate_knowledge_columns_is_idempotent_on_legacy_db():
    """旧库（无时效列）→ 迁移 → 二次迁移不报错且列不重复。"""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE documents (
            id TEXT PRIMARY KEY, owner_id TEXT, filename TEXT, title TEXT,
            size_bytes INTEGER, status TEXT, chunk_count INTEGER, error_msg TEXT,
            created_at TEXT, updated_at TEXT);
        INSERT INTO documents(id, filename, created_at, updated_at)
          VALUES ('d1','a.md','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
    """)
    db._migrate_knowledge_columns(con)
    db._migrate_knowledge_columns(con)
    cols = [r["name"] for r in con.execute("PRAGMA table_info(documents)")]
    assert cols.count("valid_from") == 1 and cols.count("authority") == 1
    row = con.execute("SELECT revision, authority, scope_json FROM documents WHERE id='d1'").fetchone()
    assert row["revision"] == "1" and row["authority"] == 1
    con.close()


def test_status_exposes_policy_and_integration_state(client, auth):
    r = client.get("/api/knowledge/status", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["scenario_id"] == "DEMO-FRESHNESS-CONFLICT-001"
    assert d["policy"]["half_life_days"] == 90 and d["policy"]["blend"] == 0.65
    assert d["integration"]["algorithm_package"] == "已实现（knowledge/）"
    assert "generation_chain" in d["integration"]


def test_scenario_has_ranking_contrast_and_two_resolution_kinds(client, auth):
    r = client.get("/api/knowledge/scenario", headers=auth)
    assert r.status_code == 200
    d = r.json()
    f, e = d["freshness"], d["evidence"]
    assert f["order_with"] != f["order_without"]          # 排序确实翻转
    assert "D5" not in f["order_with"]                     # 未来生效被排除
    assert len(d["documents"]) == 6 and len(e["claims"]) >= 8
    kinds = {c["kind"] for c in e["conflicts"]}
    resolutions = {c["resolution"] for c in e["conflicts"]}
    assert "freshness" in kinds and "authority" in kinds
    assert "trust_newer_with_review" in resolutions
    assert any(c["needs_review"] for c in e["conflicts"])
    assert e["conflict_signal"] is True
    assert len(d["controls"]["negative_controls"]) == 3


def test_scenario_negative_controls_hold(client, auth):
    """GB 地区与同值断言不能被误报成冲突。"""
    d = client.get("/api/knowledge/scenario", headers=auth).json()
    keys = [c["key"] for c in d["evidence"]["conflicts"]]
    assert not any("region=GB" in k for k in keys)
    assert not any("接口超时" in k for k in keys)


def test_policy_write_requires_admin_and_validates(client, auth):
    assert client.put("/api/knowledge/policy", json={"blend": 0.5}).status_code in (401, 403)
    assert client.put("/api/knowledge/policy", headers=auth,
                      json={"blend": 1.8}).status_code == 400
    assert client.put("/api/knowledge/policy", headers=auth,
                      json={"half_life_days": 0}).status_code == 400
    # 改完复原，避免污染其它用例
    r = client.put("/api/knowledge/policy", headers=auth, json={"half_life_days": 60})
    assert r.status_code == 200 and r.json()["policy"]["half_life_days"] == 60
    client.put("/api/knowledge/policy", headers=auth, json={"half_life_days": 90})


def test_custom_detect(client, auth):
    payload = {"texts": [
        {"doc_id": "a", "text": "退款期限为 7 天。", "scope": {"region": "CN"}},
        {"doc_id": "b", "text": "退款期限为 15 天。", "scope": {"region": "CN"}},
        {"doc_id": "c", "text": "退款期限为 20 天。", "scope": {"region": "GB"}},
    ]}
    r = client.post("/api/knowledge/detect", headers=auth, json=payload)
    assert r.status_code == 200
    ev = r.json()["evidence"]
    assert ev["conflict_signal"] is True
    assert len(ev["conflicts"]) == 1                    # GB 不该报
    assert not any("region=GB" in c["key"] for c in ev["conflicts"])


def test_retrieval_freshness_switch_is_off_by_default(monkeypatch):
    """默认关闭，既有检索链行为不变（33 项端到端自测依赖这一点）。"""
    monkeypatch.delenv("WEBAPP_FRESHNESS_ENABLED", raising=False)
    hits, meta = rag._apply_freshness([{"doc_id": "x", "score": 0.9, "chunk_id": "c"}])
    assert meta == {"freshness": "off"} and "freshness" not in hits[0]


def test_retrieval_freshness_switch_on_enriches_hits(monkeypatch):
    monkeypatch.setenv("WEBAPP_FRESHNESS_ENABLED", "1")
    hits, meta = rag._apply_freshness([{"doc_id": "x", "score": 0.9, "chunk_id": "c"}])
    assert meta["freshness"] in ("on", "unavailable:OperationalError")
    if meta["freshness"] == "on":
        assert "freshness" in hits[0] and "effective_score" in hits[0]
    monkeypatch.delenv("WEBAPP_FRESHNESS_ENABLED", raising=False)
