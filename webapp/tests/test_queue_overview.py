"""全局队列可观测性与权限边界（离线，隔离库）。

需求背景：普通用户也应能判断「是我的任务慢，还是全站在排队」，
所以要能看到「哪个部门 / 哪个用户积压多少」；但普通用户**不能**看到
别人的文件名、邮箱与完整用户名。本文件把这条边界锁死。

同时覆盖一个顺带修复的安全问题：`/api/system/busy` 此前**无鉴权**且
直接返回 filename，未登录即可探测正在处理的文档名。
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from webapp import db
from webapp.main import app


@pytest.fixture()
def client(monkeypatch):
    from webapp import rag
    monkeypatch.setattr(rag, "ANALYZE_SIM_SECONDS", 0)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _mk_user(client, name: str, dept: str) -> tuple[dict, str]:
    """注册普通用户并设置部门（演示切片无部门管理接口，直接改库）。"""
    client.post("/api/auth/register",
                json={"username": name, "password": "test1234", "email": f"{name}@demo.local"})
    conn = db.get_conn()
    try:
        conn.execute("UPDATE users SET department=? WHERE username=?", (dept, name))
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()["id"]
    finally:
        conn.close()
    tok = client.post("/api/auth/login",
                      json={"username": name, "password": "test1234"}).json()["access_token"]
    return {"Authorization": "Bearer " + tok}, uid


def _seed_docs(owner_id: str, n: int, status: str, name: str = "secret-plan.pdf") -> None:
    conn = db.get_conn()
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for i in range(n):
            conn.execute(
                "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, owner_id, f"{i:03d}-{name}", name, 1024, status, now, now),
            )
        conn.commit()
    finally:
        conn.close()


# ---------- 鉴权：两个端点都必须登录 ----------

def test_queue_overview_requires_login(client):
    assert client.get("/api/system/queue").status_code in (401, 403)


def test_system_busy_requires_login(client):
    """安全修复：此前无鉴权可直接读到正在处理的文件名。"""
    assert client.get("/api/system/busy").status_code in (401, 403)


# ---------- 普通用户：聚合可见、明细不可见 ----------

def test_user_sees_aggregate_without_filenames(client):
    auth, uid = _mk_user(client, "quser1", "研发部")
    _seed_docs(uid, 100, "queued")

    r = client.get("/api/system/queue", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["scope"] == "aggregate"
    assert d["details"] == []
    row = next(x for x in d["by_user"] if x["user_id_short"] == uid[:8])
    assert row["queued"] == 100 and row["total"] == 100
    assert row["department"] == "研发部"
    # 普通用户名必须是短码，且响应全文不含文件名与邮箱
    assert row["username"].endswith("***")
    assert "secret-plan.pdf" not in r.text
    assert "@demo.local" not in r.text


def test_user_sees_department_rollup(client):
    auth, uid = _mk_user(client, "quser2", "交付部")
    _seed_docs(uid, 30, "queued")
    _seed_docs(uid, 5, "analyzing")

    d = client.get("/api/system/queue", headers=auth).json()
    dept = next(x for x in d["by_department"] if x["department"] == "交付部")
    assert dept["queued"] == 30 and dept["active"] == 5 and dept["total"] == 35
    assert d["user_count"] >= 1 and d["department_count"] >= 1
    assert d["total_pending"] == d["active_count"] + d["queue_count"]


def test_busy_is_redacted_for_user(client):
    auth, uid = _mk_user(client, "quser3", "研发部")
    _seed_docs(uid, 3, "queued")
    d = client.get("/api/system/busy", headers=auth).json()
    assert d["redacted"] is True
    assert d["active"] == [] and d["queued"] == []
    assert "filename" not in str(d)


# ---------- admin：完整明细 ----------

def test_admin_sees_full_details(client, admin):
    auth, uid = _mk_user(client, "quser4", "研发部")
    _seed_docs(uid, 2, "queued", name="admin-visible.pdf")

    d = client.get("/api/system/queue", headers=admin).json()
    assert d["scope"] == "full"
    assert d["details"], "admin 必须能看到文件名明细"
    filenames = [x["filename"] for x in d["details"]]
    assert any("admin-visible.pdf" in f for f in filenames)
    row = next(x for x in d["by_user"] if x["user_id_short"] == uid[:8])
    assert not row["username"].endswith("***"), "admin 看到完整用户名"


def test_admin_busy_keeps_details(client, admin):
    d = client.get("/api/system/busy", headers=admin).json()
    assert "redacted" not in d or d.get("redacted") is not True


# ---------- 迁移：旧库补 department ----------

def test_users_department_column_exists(client):
    conn = db.get_conn()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        admin_dept = conn.execute(
            "SELECT department FROM users WHERE username='admin'").fetchone()["department"]
    finally:
        conn.close()
    assert "department" in cols
    assert admin_dept == "平台运营"
