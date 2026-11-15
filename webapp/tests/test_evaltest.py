"""评估测试子页 / 可观测治理能力的验收测试（隔离库 + 离线）。

覆盖：新接口权限边界、成本与预算规则、超限拒绝、三维限流 429 反馈、
用量统计的「不查而非隐藏」、静态前端与 i18n/主题资源完整性。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webapp import db, observability as obs
from webapp.tests._dbutil import columns
from webapp.main import app


@pytest.fixture(autouse=True)
def _isolate_eval_runs(tmp_path, monkeypatch):
    """离线评估会写 run 目录；测试里改指向临时目录，避免污染 evalbench/runs。"""
    p = tmp_path / "runs"
    p.mkdir()
    monkeypatch.setattr(obs, "EVALBENCH_RUNS", p)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _headers(client, username="admin", password="admin123"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _register(client, name="u_eval_1"):
    r = client.post("/api/auth/register", json={"username": name, "password": "test1234",
                                                "email": "e@demo.local"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_new_tables_and_defaults(client):
    conn = db.get_conn()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT table_name AS name FROM information_schema.tables"
            " WHERE table_schema = current_schema()")}
        for t in ("usage_events", "budgets", "rate_rules", "rate_hits",
                  "eval_configs", "eval_runs", "eval_logs"):
            assert t in tables
        rules = {r["dimension"]: r["limit_count"] for r in conn.execute(
            "SELECT dimension,limit_count FROM rate_rules")}
        # 限流配额本体是用户 / 接口 / 模型调用三维；部门只作成本归因，不产生按部门 429。
        # ip 是**接入层防刷附加维度**（不进 RATE_DIMENSIONS），用于挡未登录/匿名刷接口。
        assert {"user", "endpoint", "model"} <= set(rules)
        assert rules.get("ip", 0) > 0
        assert all(v > 0 for v in rules.values())
        # 口径锁定：台账物理列名必须是 department_id，旧 tenant 列必须已迁移掉。
        cols = columns(conn, "usage_events")
        assert "department_id" in cols and "tenant" not in cols
        cfg_keys = {r["key"] for r in conn.execute("SELECT key FROM eval_configs")}
        assert "department" in cfg_keys and "tenant" not in cfg_keys
        assert conn.execute("SELECT policy FROM budgets WHERE scope='global'").fetchone()[0] == "warn"
    finally:
        conn.close()


def test_evaltest_read_endpoints(client):
    h = _headers(client)
    for path in ("/api/evaltest/config", "/api/evaltest/runs?limit=5",
                 "/api/evaltest/metrics", "/api/evaltest/logs",
                 "/api/evaltest/usage?dim=department_id", "/api/evaltest/budget",
                 "/api/evaltest/ratelimit", "/api/evaltest/plugins",
                 "/api/evaltest/permissions"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"{path}: {r.text[:200]}"
    p = client.get("/api/evaltest/plugins", headers=h).json()
    assert p["total"] >= 10
    assert {"已落地", "架构预留", "未做"} <= {x["status"] for x in p["plugins"]}


def test_write_endpoints_admin_only(client):
    admin, user = _headers(client), _register(client, "u_eval_write")
    cases = [
        ("PUT", "/api/evaltest/config", {"patch": {"samples": "all"}}),
        ("POST", "/api/evaltest/ratelimit/probe", None),
        ("PUT", "/api/evaltest/budget", {"limit_cny": 10, "policy": "warn"}),
        ("PUT", "/api/evaltest/ratelimit", {"rules": [{"dimension": "user", "limit_count": 100, "window_s": 60}]}),
        ("POST", "/api/evaltest/run", None),
    ]
    for method, path, body in cases:
        r = client.request(method, path, headers=user, json=body)
        assert r.status_code == 403, f"{method} {path} 普通用户应 403，实际 {r.status_code}"
        r2 = client.request(method, path, headers=admin, json=body)
        assert r2.status_code in (200, 201), f"{method} {path} admin 应通过，实际 {r2.status_code}: {r2.text[:200]}"


def test_cost_rules_and_estimation():
    assert obs.estimate_tokens("") == 0
    assert obs.estimate_tokens("配网") >= 2
    c = obs.cost_of("minimax-m3", 1000, 1000)
    assert 0 < c < 1
    assert obs.cost_of("default", 0, 0) == 0
    assert "演示估算" in obs.PRICE_NOTE or "非供应商报价" in obs.PRICE_NOTE


def test_usage_ledger_and_scope_isolation(client):
    admin, user = _headers(client), _register(client, "u_eval_scope")
    obs.record_usage(user_id="admin-uid", call_type="chat", model="minimax-m3",
                     prompt_text="配网步骤", completion_text="长按三秒", estimated=1)
    obs.record_usage(user_id="other-uid", call_type="eval", model="offline_placeholder",
                     prompt_text="问题", completion_text="答案", estimated=1)
    all_rows = client.get("/api/evaltest/usage?dim=call_type", headers=admin).json()
    assert all_rows["scope"] == "全局（admin）"
    assert all_rows["totals"]["calls"] >= 2
    mine = client.get("/api/evaltest/usage?dim=call_type", headers=user).json()
    assert mine["scope"] == "仅本人调用"
    assert mine["totals"]["calls"] == 0   # 不查而非隐藏


def test_budget_block_rejects_new_eval_run(client):
    h = _headers(client)
    r = client.put("/api/evaltest/budget", headers=h,
                   json={"limit_cny": 0, "policy": "block"})
    assert r.status_code == 200
    b = client.get("/api/evaltest/budget", headers=h).json()
    assert b["exceeded"] is True and b["allowed"] is False
    run = client.post("/api/evaltest/run", headers=h).json()
    assert run["ok"] is False and run["reason"] == "budget_exceeded"
    logs = client.get("/api/evaltest/logs?level=ERROR", headers=h).json()["logs"]
    assert any(x["event"] == "eval_rejected_budget" for x in logs)
    # 恢复默认，避免污染后续用例
    client.put("/api/evaltest/budget", headers=h, json={"limit_cny": 50, "policy": "warn"})


def test_offline_eval_run_is_real_and_leaves_trace(client):
    h = _headers(client)
    before = client.get("/api/evaltest/runs?limit=200", headers=h).json()["runs"]
    r = client.post("/api/evaltest/run", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["comparable"] is False and body["n_records"] >= 1
    assert body["branch"] and body["commit"]
    after = client.get("/api/evaltest/runs?limit=200", headers=h).json()["runs"]
    assert len(after) == len(before) + 1
    logs = client.get("/api/evaltest/logs", headers=h).json()["logs"]
    assert any(x["event"] == "eval_done" and x["run_id"] == body["run_id"] for x in logs)
    d = client.get("/api/evaltest/runs/" + body["run_id"], headers=h).json()
    assert d["summary"]["comparable"] is False


def test_rate_limit_returns_429_with_headers(client):
    h = _headers(client)
    obs._WINDOWS.clear()
    try:
        client.put("/api/evaltest/ratelimit", headers=h,
                   json={"rules": [{"dimension": "user", "limit_count": 2, "window_s": 60}]})
        obs._WINDOWS.clear()
        s1 = client.get("/api/evaltest/config", headers=h)
        s2 = client.get("/api/evaltest/config", headers=h)
        s3 = client.get("/api/evaltest/config", headers=h)
        assert s1.status_code == 200 and s2.status_code == 200
        assert s3.status_code == 429, f"第 3 次应限流，实际 {s3.status_code}: {s3.text[:200]}"
        body = s3.json()
        # 向后兼容字段
        assert body["dimension"] == "user" and body["limit"] == 2
        assert "retry_after" in body and body["policy"]
        assert s3.headers["Retry-After"]
        assert s3.headers["X-RateLimit-Dimension"] == "user"
        # 本轮新增的统一 429 契约：业务错误码 / kind / message / trace_id / 部门归因
        assert body["code"] == "RATE_LIMIT_USER_EXCEEDED"
        assert body["kind"] == "rate_limit_exceeded"
        assert body["message"] and isinstance(body["detail"], str)
        assert len(body["trace_id"]) == 16
        assert body["department"] == "default"
        assert s3.headers["X-RateLimit-Code"] == body["code"]
        assert s3.headers["X-Trace-Id"] == body["trace_id"]
    finally:
        # 先清窗口再发恢复请求：否则恢复请求本身会被刚设的 2/60s 规则挡下，
        # 规则表残留成 2，污染同进程后续测试（本轮新增解析 API 测试时发现）。
        obs._WINDOWS.clear()
        client.put("/api/evaltest/ratelimit", headers=h,
                   json={"rules": [{"dimension": "user", "limit_count": 1200, "window_s": 60}]})
        obs._WINDOWS.clear()
    # 恢复规则后再查命中台账（否则查询本身也会被限流）
    hits = client.get("/api/evaltest/ratelimit", headers=h).json()["hits"]
    assert any(x["dimension"] == "user" for x in hits)


def test_static_assets_and_i18n_theme(client):
    html = client.get("/").text
    assert "企业级知识库" in html
    for path, marker in (("/i18n.js", "window.setLang"), ("/theme.js", "window.KBTheme"),
                         ("/style.css", 'html[data-mode="dark"]'), ("/app.js", "loadEvalTest")):
        r = client.get(path)
        assert r.status_code == 200 and marker in r.text, path
    assert 'data-theme=' in html or 'data-mode=' in html or "kb-swatches" in html
    for v in ("view-evaltest", "data-view=\"evaltest\"", "id=\"et-config\"", "id=\"et-cost\""):
        assert v in html


def test_completion_rate_clamped(client):
    """累积 badcase 超过目标数时，完成率不得 >1，pending 不得为负。"""
    h = _headers(client)
    conn = db.get_conn()
    try:
        for i in range(25):
            conn.execute(
                "INSERT INTO badcases(id,eval_task_id,question,answer,verdict,issue_desc,source,"
                "status,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"bc_clamp_{i}", "ET-001", "q", "a", "错误", None, "chat", "pending",
                 "u", obs._now(), obs._now()),
            )
        conn.commit()
    finally:
        conn.close()
    r = client.get("/api/admin/completion", headers=h).json()
    row = next(x for x in r["tasks"] if x["eval_task_id"] == "ET-001")
    assert row["completion_rate"] <= 1.0
    assert row["pending"] >= 0
    assert r["overall"]["completion_rate"] <= 1.0
