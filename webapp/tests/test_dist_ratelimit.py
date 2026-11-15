"""分布式限流与 /metrics 验收。

覆盖：
- 未配置 Redis / 未装 redis → 内存实现，限流命中结构正确；
- MemoryLimiter 窗口内超限、窗口外放行（用 monkeypatch 控制时间，不 sleep）；
- 伪造「Redis 不可用」→ get_limiter 回退内存并留 WARN；
- /metrics 200 + text/plain + 关键指标存在 + 不含 @ / .pdf / sk-；
- 动态 path 归一（/api/docs/abc123 → /api/docs/{id}）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webapp.ratelimit_dist as rl


@pytest.fixture(autouse=True)
def _reset_limiter(monkeypatch):
    """每个用例重置 limiter 缓存与降级告警集合，避免跨用例串味。"""
    from webapp import db

    db.init_db()  # 幂等；保证单跑本文件时 rate_rules 等表已存在
    monkeypatch.setattr(rl, "_LIMITER", None)
    monkeypatch.setattr(rl, "_LIMITER_KEY", None)
    monkeypatch.setattr(rl, "_WARNED", set())
    monkeypatch.delenv("WEBAPP_REDIS_URL", raising=False)
    monkeypatch.setenv("WEBAPP_RATE_BACKEND", "memory")
    yield


@pytest.fixture
def client():
    from webapp.main import app

    with TestClient(app) as c:
        yield c


def test_default_backend_is_memory(monkeypatch):
    """未配 WEBAPP_REDIS_URL → MemoryLimiter（单机降级实现）。"""
    monkeypatch.setenv("WEBAPP_RATE_BACKEND", "auto")
    limiter = rl.get_limiter()
    assert isinstance(limiter, rl.MemoryLimiter)


def test_memory_limiter_window_and_structure(monkeypatch):
    """窗口内超限返回非 None，结构与 rate_check 一致；窗口外放行。"""
    limiter = rl.MemoryLimiter()
    key = f"probe-{id(limiter)}"

    monkeypatch.setattr(rl.time, "time", lambda: 1000.0)
    assert limiter.check(key, 2, 10) is None
    assert limiter.check(key, 2, 10) is None
    hit = limiter.check(key, 2, 10)
    assert hit is not None
    assert hit["limit"] == 2 and hit["window_s"] == 10
    assert hit["retry_after"] >= 1 and hit["current"] >= 2

    # 时间推进到窗口外 → 旧记录被清掉，放行
    monkeypatch.setattr(rl.time, "time", lambda: 1011.0)
    assert limiter.check(key, 2, 10) is None


def test_redis_unavailable_falls_back_and_warns(monkeypatch):
    """Redis 不可用 → get_limiter 回退内存并留 WARN（不把业务打挂）。"""
    monkeypatch.setenv("WEBAPP_RATE_BACKEND", "redis")
    monkeypatch.setenv("WEBAPP_REDIS_URL", "redis://127.0.0.1:6399/0")
    # 伪造不可用（不真的连网，避免测试依赖网络与时延）
    monkeypatch.setattr(rl.RedisLimiter, "available", lambda self: False)

    limiter = rl.get_limiter()
    assert isinstance(limiter, rl.MemoryLimiter)
    assert any("redis" in reason for reason in rl._WARNED), rl._WARNED


def test_check_rate_returns_rate_check_compatible_structure(monkeypatch):
    """check_rate 命中结构必须与 observability.rate_check 一致（上层不用改）。"""
    from webapp import db, observability as obs

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
            " VALUES ('endpoint',1,60,1,'test')"
        )
        conn.commit()
    finally:
        conn.close()
    obs._WINDOWS.clear()
    limit_key = f"/api/probe-{id(monkeypatch)}"
    try:
        assert rl.check_rate(path=limit_key, user_id="u") is None
        hit = rl.check_rate(path=limit_key, user_id="u")
        assert hit is not None and hit["dimension"] == "endpoint"
        assert {"dimension", "key", "path", "limit", "window_s",
                "retry_after", "current"} <= set(hit)
    finally:
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM rate_rules WHERE dimension='endpoint'"
                         " AND limit_count=1")
            conn.commit()
        finally:
            conn.close()
        obs._WINDOWS.clear()


def test_metrics_endpoint_shape_and_no_sensitive_data(client):
    client.get("/api/docs")  # 造一条 /api/ 请求样本（401 也计数）
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    for name in ("kb_http_requests_total", "kb_http_request_duration_seconds_bucket",
                 "kb_rate_limit_hits_total", "kb_queue_pending",
                 "kb_budget_spend_cny", "kb_documents_by_status"):
        assert name in text, name
    # 按枚举输出的两个指标必须有**零值样本行**：Prometheus 里只有 HELP/TYPE、
    # 没有样本 = 指标不存在，`rate(...)` 永远 no data，对应告警不会触发。
    assert 'kb_rate_limit_hits_total{dimension="user"} ' in text
    assert 'kb_rate_limit_hits_total{dimension="ip"} ' in text
    assert 'kb_documents_by_status{status="queued"} ' in text
    # 标签基数受控：department 只是成本归因、不参与限流，不得混进 dimension 标签
    assert 'dimension="department"' not in text
    # 绝不输出敏感值
    assert "@" not in text
    assert ".pdf" not in text
    assert "sk-" not in text


def test_metrics_dynamic_path_normalized(client):
    client.get("/api/docs/abc123")
    r = client.get("/metrics")
    assert 'path="/api/docs/{id}"' in r.text


def test_http_path_does_not_share_model_bucket(client):
    """回归：`model` 为空时必须跳过 model 维度，且必须覆盖**真实 HTTP 链路**。

    背景：这个判断曾在单机实现 `observability.rate_check` 里修过，但默认路径是
    `ratelimit_dist.check_rate`（main 的 gateway_guard 调它），于是「修了却没生效」。
    这条用例走 HTTP，保证 main → distributed 这条链路也被锁住。
    """
    from webapp import db

    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE rate_rules SET limit_count=1, window_s=60 WHERE dimension='model'"
        )
        conn.commit()
    finally:
        conn.close()

    # 非 chat 接口不调用模型 → 不应被 model 维度拦（否则全站共用一个 10 QPS 桶）
    codes = [client.get("/api/docs").status_code for _ in range(3)]
    assert 429 not in codes, f"非 chat 请求被 model 维度共桶拦截：{codes}"

    # chat 接口确实调模型 → 第二次应命中 model（限流在鉴权前，401 也计数）
    client.post("/api/chat/stream", json={"question": "probe"})
    r2 = client.post("/api/chat/stream", json={"question": "probe"})
    assert r2.status_code == 429, r2.text
    assert r2.json().get("dimension") == "model"
