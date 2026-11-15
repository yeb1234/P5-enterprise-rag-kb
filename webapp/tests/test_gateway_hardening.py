"""应用层网关加固验收：IP 维度限流 / 可信代理真实 IP / 全局并发闸 / 请求体上限。

设计要点（与 docs 口径一致）：
- 业务配额本体仍是 user / endpoint / model 三维；IP 是**接入层防刷附加维度**，
  不进 `RATE_DIMENSIONS`，只在 `rate_check(ip=...)` 显式传入且存在启用规则时生效。
- 伪造 `X-Forwarded-For` 不得换掉限流 key：只有直连对端属于可信代理时才采信。
- 并发闸用 `body_iterator` 包装，StreamingResponse 必须流完才释放令牌（防泄漏）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import webapp.observability as obs
from webapp import db, security


@pytest.fixture(autouse=True)
def _reset_rate_state():
    """每个用例前清窗口、后把 ip 规则恢复成演示默认值（600/60s）。

    `_WINDOWS` 与 `rate_rules` 都是进程/库级共享状态，必须显式隔离，
    否则「把 ip 调成 1 次」的用例会污染后续用例。
    """
    db.init_db()  # 幂等建表 + 种子，保证单独运行本文件时表已存在
    obs._WINDOWS.clear()
    yield
    obs._WINDOWS.clear()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM rate_rules WHERE dimension='ip'")
        conn.execute(
            "INSERT OR REPLACE INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
            " VALUES ('ip',600,60,1,'test')"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client():
    from webapp.main import app

    with TestClient(app) as c:
        yield c


def _mini_app():
    """独立小 app，只挂真实 `gateway_guard`。

    刻意不依赖 webapp 真实端点（多数要鉴权），这样网关行为可独立验证；
    DB 由 autouse fixture 初始化，`gateway_guard` 内部照常走 rate_check。
    """
    from webapp.main import gateway_guard

    mini = FastAPI()
    mini.middleware("http")(gateway_guard)

    @mini.api_route("/api/ping", methods=["GET", "POST", "PUT"])
    async def ping():
        return {"ok": True}

    return mini


def _set_ip_rule(limit: int, window: int = 60) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
            " VALUES ('ip',?,?,1,'test')",
            (limit, window),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. client_ip 纯函数：伪造头 / 多级代理 / 空白 / IPv6
# ---------------------------------------------------------------------------
def test_client_ip_ignores_spoofed_forwarded_for_from_untrusted_peer():
    trusted = {"127.0.0.1", "::1"}
    # 不可信对端：即使带 XFF 也一律用直连 host（伪造头无法换 key）
    assert security.client_ip("203.0.113.5", "1.2.3.4", trusted) == "203.0.113.5"
    assert security.client_ip("203.0.113.5", "1.1.1.1, 2.2.2.2", trusted) == "203.0.113.5"


def test_client_ip_trusts_first_hop_only_from_trusted_proxy():
    trusted = {"127.0.0.1", "::1"}
    # 可信代理：取最左（真实客户端），忽略后续代理链
    assert security.client_ip("127.0.0.1", "1.2.3.4, 10.0.0.1, 172.16.0.1", trusted) == "1.2.3.4"


def test_client_ip_handles_blank_and_missing_values():
    trusted = {"127.0.0.1"}
    assert security.client_ip("127.0.0.1", "   ", trusted) == "127.0.0.1"
    assert security.client_ip("127.0.0.1", "", trusted) == "127.0.0.1"
    assert security.client_ip("127.0.0.1", None, trusted) == "127.0.0.1"
    assert security.client_ip("127.0.0.1", ", ,", trusted) == "127.0.0.1"
    # host 缺失兜底为 unknown，避免空串成为共享桶
    assert security.client_ip("", None, trusted) == "unknown"


def test_client_ip_normalizes_ipv6():
    trusted = {"::1", "127.0.0.1"}
    assert security.client_ip("::1", "[2001:db8::1]:443", trusted) == "2001:db8::1"
    assert security.client_ip("::1", "[2001:db8::1]", trusted) == "2001:db8::1"
    assert security.client_ip("::1", "2001:db8::2", trusted) == "2001:db8::2"
    # IPv6 直连 host 且无可信代理命中时原样返回
    assert security.client_ip("2001:db8::9", None, trusted) == "2001:db8::9"


# ---------------------------------------------------------------------------
# 2. IP 维度限流（rate_check 层）
# ---------------------------------------------------------------------------
def test_ip_dimension_counts_and_hits_with_expected_shape(client):
    _set_ip_rule(1)
    obs._WINDOWS.clear()
    assert obs.rate_check(path="/api/x", user_id="u1", ip="203.0.113.9") is None
    hit = obs.rate_check(path="/api/x", user_id="u1", ip="203.0.113.9")
    assert hit is not None
    assert hit["dimension"] == "ip" and hit["key"] == "203.0.113.9"
    # 返回结构保持现有契约
    assert {"dimension", "key", "path", "limit", "window_s",
            "retry_after", "current"} <= set(hit)
    assert hit["limit"] == 1 and hit["current"] >= 1


def test_ip_dimension_skipped_when_ip_not_provided(client):
    _set_ip_rule(1)
    obs._WINDOWS.clear()
    # 不传 ip：IP 规则即使存在也不参与计数
    for _ in range(3):
        assert obs.rate_check(path="/api/x", user_id="anonymous") is None


def test_ip_dimension_skipped_when_rule_disabled(client):
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
            " VALUES ('ip',1,60,0,'test')"
        )
        conn.commit()
    finally:
        conn.close()
    obs._WINDOWS.clear()
    for _ in range(3):
        assert obs.rate_check(path="/api/x", user_id="u", ip="203.0.113.9") is None


# ---------------------------------------------------------------------------
# 3. 中间件级：伪造 XFF 不绕过 / 可信代理采信
# ---------------------------------------------------------------------------
def test_spoofed_xff_cannot_change_rate_limit_key(monkeypatch):
    """对端 testclient 不可信；换不同 XFF 不应产生不同 key。

    限流中间件在鉴权之前执行，所以 `GET /api/docs` 不带 token 返回 401 也会被计数——
    不需要 200 即可验证限流（用 webapp 真实端点，非 showcase 的 /api/params）。
    """
    _set_ip_rule(1)
    obs._WINDOWS.clear()
    monkeypatch.setattr("webapp.main.TRUSTED_PROXY_IPS", {"127.0.0.1", "::1"})
    from webapp.main import app

    mc = TestClient(app)
    r1 = mc.get("/api/docs", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = mc.get("/api/docs", headers={"X-Forwarded-For": "2.2.2.2"})
    assert r1.status_code in (200, 401), r1.text
    assert r2.status_code == 429, r2.text
    body = r2.json()
    assert body["dimension"] == "ip"
    # 关键断言：key 是直连 host，而不是伪造的 XFF
    assert body["key"] == "testclient"
    # IP 维度单列错误码，前端可区分「账号被限流」与「网络出口请求过多」
    assert body["code"] == "RATE_LIMIT_IP_EXCEEDED"
    assert r2.headers["X-RateLimit-Dimension"] == "ip"


def test_trusted_proxy_uses_forwarded_for_as_key(monkeypatch):
    """可信代理下不同 XFF = 不同 key；同一 XFF 第二次才命中。"""
    _set_ip_rule(1)
    obs._WINDOWS.clear()
    monkeypatch.setattr("webapp.main.TRUSTED_PROXY_IPS", {"testclient"})
    from webapp.main import app

    mc = TestClient(app)
    r1 = mc.get("/api/docs", headers={"X-Forwarded-For": "1.1.1.1"})
    r2 = mc.get("/api/docs", headers={"X-Forwarded-For": "2.2.2.2"})
    # 两个不同 XFF 各计 1 次，均未命中 → 说明 key 随 XFF 区分（1.1.1.1 / 2.2.2.2）
    assert r1.status_code in (200, 401), r1.text
    assert r2.status_code in (200, 401), r2.text
    r3 = mc.get("/api/docs", headers={"X-Forwarded-For": "1.1.1.1"})
    assert r3.status_code == 429, r3.text
    assert r3.json()["key"] == "1.1.1.1"


# ---------------------------------------------------------------------------
# 4. 请求体大小上限（Content-Length → 413）
# ---------------------------------------------------------------------------
def test_content_length_over_limit_returns_413(monkeypatch):
    monkeypatch.setattr("webapp.main.MAX_BODY_BYTES", 10)
    mc = TestClient(_mini_app())
    r = mc.post("/api/ping", content=b"x" * 100)
    assert r.status_code == 413, r.text
    body = r.json()
    assert body["code"] == "PAYLOAD_TOO_LARGE" and body["kind"] == "body_size"
    assert set(body) == {"code", "kind", "message", "detail", "trace_id"}
    assert body["trace_id"] and r.headers.get("X-Trace-Id") == body["trace_id"]


def test_within_limit_is_not_blocked(monkeypatch):
    monkeypatch.setattr("webapp.main.MAX_BODY_BYTES", 1024)
    from webapp.main import app

    mc = TestClient(app)
    # /api/auth/login 是 webapp 真实存在的 POST；账号不存在返回 401，属正常业务响应。
    r = mc.post("/api/auth/login", json={"username": "no_such_user", "password": "x"})
    assert r.status_code not in (413, 429), r.text


# ---------------------------------------------------------------------------
# 5. 全局并发闸（503 + Retry-After）
# ---------------------------------------------------------------------------
def test_concurrency_gate_returns_503_when_saturated(client, monkeypatch):
    from webapp.main import gateway_guard

    monkeypatch.setattr("webapp.main.MAX_INFLIGHT", 1)
    monkeypatch.setattr("webapp.main.INFLIGHT_ACQUIRE_TIMEOUT", 0.05)

    small = FastAPI()
    small.middleware("http")(gateway_guard)

    @small.get("/api/slow")
    async def slow():
        await asyncio.sleep(0.3)
        return {"ok": True}

    async def scenario():
        transport = httpx.ASGITransport(app=small)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            return await asyncio.gather(ac.get("/api/slow"), ac.get("/api/slow"))

    r1, r2 = asyncio.run(scenario())
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 503], statuses
    busy = r2 if r2.status_code == 503 else r1
    body = busy.json()
    assert body["code"] == "SERVER_BUSY" and body["kind"] == "concurrency_limit"
    assert body["trace_id"] and busy.headers.get("Retry-After") == "1"
    assert set(body) == {"code", "kind", "message", "detail", "trace_id"}


def test_streaming_response_releases_slot_after_stream_end(client, monkeypatch):
    """StreamingResponse 必须流完才释放令牌；否则第二个请求会 503（令牌泄漏）。"""
    from webapp.main import gateway_guard

    monkeypatch.setattr("webapp.main.MAX_INFLIGHT", 1)

    small = FastAPI()
    small.middleware("http")(gateway_guard)

    @small.get("/api/stream")
    async def stream():
        async def gen():
            for i in range(3):
                yield f"data: {i}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def scenario():
        transport = httpx.ASGITransport(app=small)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            first = await ac.get("/api/stream")
            second = await ac.get("/api/stream")  # 串行：第一次流结束后槽位必须已释放
            return first, second

    first, second = asyncio.run(scenario())
    assert first.status_code == 200 and second.status_code == 200, (
        first.status_code, second.status_code)
    assert first.text.count("data:") == 3


def test_static_paths_bypass_gate(client, monkeypatch):
    """静态资源不走闸：即便并发闸被占满，非 /api/ 路径也应直通。"""
    from webapp.main import gateway_guard

    monkeypatch.setattr("webapp.main.MAX_INFLIGHT", 1)
    monkeypatch.setattr("webapp.main.INFLIGHT_ACQUIRE_TIMEOUT", 0.05)

    small = FastAPI()
    small.middleware("http")(gateway_guard)

    @small.get("/api/slow2")
    async def slow2():
        await asyncio.sleep(0.3)
        return {"ok": True}

    @small.get("/static.txt")
    async def static_txt():
        return {"static": True}

    async def scenario():
        transport = httpx.ASGITransport(app=small)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            slow_task = asyncio.create_task(ac.get("/api/slow2"))
            await asyncio.sleep(0.05)
            static_resp = await ac.get("/static.txt")
            await slow_task
            return static_resp

    resp = asyncio.run(scenario())
    assert resp.status_code == 200 and resp.json() == {"static": True}


# ---------------------------------------------------------------------------
# 6. 按用户配额隔离（check_budget 的 user 级覆盖 + A 超支不影响 B）
# ---------------------------------------------------------------------------
def _add_usage(user_id: str, cost: float) -> None:
    """直接写一条 usage_events（record_usage 已写 user_id，无需改表）。"""
    import uuid

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO usage_events(id,ts,project,department_id,user_id,call_type,model,"
            "prompt_tokens,completion_tokens,total_tokens,cost_cny,estimated,run_id,detail,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "x", "default", "default", user_id, "chat", "m",
             1, 1, 2, cost, 1, None, "", "x"),
        )
        conn.commit()
    finally:
        conn.close()


def _set_user_budget(user_id: str, limit: float, policy: str = "block") -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO budgets(scope,key,limit_cny,policy,updated_at)"
            " VALUES ('user',?,?,?,'x')",
            (user_id, limit, policy),
        )
        conn.commit()
    finally:
        conn.close()


def test_user_budget_overrides_global_and_isolates_users(client):
    """A 设 0.01 元且已超支 → A block；B 未设用户预算走全局 → 不受影响。"""
    import uuid

    ua, ub = f"UA-{uuid.uuid4().hex[:8]}", f"UB-{uuid.uuid4().hex[:8]}"
    _set_user_budget(ua, 0.01, "block")
    _add_usage(ua, 0.05)
    a = obs.check_budget(user_id=ua)
    b = obs.check_budget(user_id=ub)
    print("  A:", {k: a[k] for k in ("scope", "overridden", "spend_cny", "exceeded", "allowed")})
    print("  B:", {k: b[k] for k in ("scope", "overridden", "exceeded", "allowed")})
    assert a["scope"] == "user" and a["overridden"] is True
    assert a["exceeded"] is True and a["allowed"] is False
    assert b["scope"] == "global" and b["overridden"] is False
    assert b["allowed"] is True, "用户 A 超支不应把用户 B 一起拦住"


def test_budget_falls_back_to_global_when_no_user_row(client):
    import uuid

    b = obs.check_budget(user_id=f"UN-{uuid.uuid4().hex[:8]}")
    assert b["scope"] == "global" and b["overridden"] is False
    assert b["limit_cny"] > 0


def test_user_budget_endpoint_admin_only_and_zero_limit_warns(client):
    import uuid

    uid = f"UZ-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={
        "username": f"u{uuid.uuid4().hex[:6]}", "password": "test1234", "email": "z@z.z"})
    tok = r.json()["access_token"]
    r = client.put(f"/api/evaltest/budget/user/{uid}", json={"limit_cny": 1, "policy": "block"},
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403, r.text  # 普通用户不可改预算

    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    atok = r.json()["access_token"]
    r = client.put(f"/api/evaltest/budget/user/{uid}", json={"limit_cny": 0, "policy": "block"},
                   headers={"Authorization": f"Bearer {atok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user" and body["overridden"] is True
    assert "warning" in body and "立即阻断" in body["warning"]
    # 兼容别名路径 /api/eval/... 与主路径 /api/evaltest/... 指向同一处理函数
    r2 = client.put(f"/api/eval/budget/user/{uid}", json={"limit_cny": 5, "policy": "warn"},
                    headers={"Authorization": f"Bearer {atok}"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["scope"] == "user"


def test_chat_rejects_when_budget_blocked(client, monkeypatch):
    """问答入口在调模型前查预算；block 且超额 → SSE 首个 error 事件 + 审计留痕，不静默跑。"""
    import json as _json
    import uuid

    r = client.post("/api/auth/register", json={
        "username": f"u{uuid.uuid4().hex[:6]}", "password": "test1234", "email": "q@q.q"})
    tok = r.json()["access_token"]
    monkeypatch.setattr(obs, "check_budget", lambda **kw: {
        "allowed": False, "scope": "user", "overridden": True, "policy": "block",
        "exceeded": True, "spend_cny": 1.0, "limit_cny": 0.01,
        "remaining_cny": 0.0, "usage_rate": 1.0,
    })
    r = client.post("/api/chat/stream", json={"question": "hi"},
                    headers={"Authorization": f"Bearer {tok}"})
    # 流式端点：HTTP 200 + SSE 首个 error 事件告知（前端按 event 解析）
    assert r.status_code == 200, r.text
    assert "event: error" in r.text, r.text[:300]
    data_line = next(line for line in r.text.splitlines() if line.startswith("data: "))
    body = _json.loads(data_line[len("data: "):])
    assert body["ok"] is False and body["reason"] == "budget_exceeded"
    assert body["budget"]["scope"] == "user" and body["budget"]["policy"] == "block"
    # 必须留审计（不允许静默拒绝）
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM audit_logs WHERE action='budget_block' LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row is not None, "预算拒绝必须写审计台账"
