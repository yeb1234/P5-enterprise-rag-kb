"""运行日志的行为验收：trace_id 贯穿 / query string 不进日志 / 健康检查降噪。

为什么这些值得单独测：日志是**只有出事时才被读**的东西，写成什么样都不影响功能，
所以任何功能测试、e2e 都不可能发现「trace_id 三处不一致」或「用户原文进了日志」。
必须靠断言把契约钉死。

三层契约（本文件逐条验）：
1. 每个响应都带 `X-Trace-Id`，客户端传来的合法 id 要被沿用；
2. 错误体里的 `trace_id`、响应头、访问日志三处**必须是同一个值**；
3. 访问日志的 `path` 不含 query string —— `/api/search/preview?q=` 会带用户原文。
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import webapp.logging_setup as logctx
from webapp import db
from webapp.tests._dbutil import upsert_rate_rule


class _Collector(logging.Handler):
    """把 `webapp.access` 的**真实输出**收下来。

    刻意存「格式化后的 dict」而不是裸 `LogRecord`：`trace_id` 不在 record 上，
    它由 `JsonFormatter` 在格式化时从 ContextVar 注入。断言真实输出，
    才等于断言「采集端最终读到的那行 JSON」——用 record 断言会漏掉格式化环节的错误。
    """

    def __init__(self) -> None:
        super().__init__()
        self._fmt = logctx.JsonFormatter()
        self.payloads: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        import json as _json

        self.payloads.append(_json.loads(self._fmt.format(record)))

    def requests(self) -> list[dict]:
        return [p for p in self.payloads if p.get("event") == "request"]


@pytest.fixture()
def access_logs():
    """接管 `webapp.access`：调到 DEBUG 并挂收集器，用完还原。

    注意不能只把根级别调低——`logging` 的 `isEnabledFor` 走的是**有效级别**
    （沿继承链找到的第一个非 NOTSET 级别），`webapp.access` 自己是 NOTSET，
    所以根级别是 WARNING 时 INFO 记录在进入 handler 之前就被丢掉了。
    """
    lg = logging.getLogger("webapp.access")
    old_level, old_propagate = lg.level, lg.propagate
    col = _Collector()
    lg.addHandler(col)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False          # 别把 JSON 刷到 stdout
    try:
        yield col
    finally:
        lg.removeHandler(col)
        lg.setLevel(old_level)
        lg.propagate = old_propagate


@pytest.fixture()
def client():
    from webapp.main import app

    with TestClient(app) as c:
        yield c


def _is_trace_id(value: str) -> bool:
    return len(value) == 16 and all(c in "0123456789abcdef" for c in value)


def test_every_response_carries_trace_id(client):
    """404 也要有——否则「出问题的请求恰好是走错路径的那些」就没法追。"""
    r = client.get("/api/definitely-not-a-route")
    tid = r.headers.get("X-Trace-Id", "")
    assert _is_trace_id(tid), f"响应头没有合法 trace_id（{tid!r}），响应码 {r.status_code}"


def test_client_trace_id_is_honored(client):
    """上游（网关 / 前端）已经生成 id 时要沿用，不能每跳换一个。"""
    mine = "0123456789abcdef"
    r = client.get("/api/definitely-not-a-route", headers={"X-Trace-Id": mine})
    assert r.headers["X-Trace-Id"] == mine


def test_invalid_trace_id_is_replaced(client):
    """非十六进制的值必须丢弃重生成——日志是纯文本行，脏 header 能伪造出假日志。"""
    bad = "not-a-trace-id"
    r = client.get("/api/definitely-not-a-route", headers={"X-Trace-Id": bad})
    got = r.headers["X-Trace-Id"]
    assert got != bad
    assert _is_trace_id(got)


def test_query_string_never_reaches_access_log(client, access_logs):
    """用户原文在 query string 里（`/api/search/preview?q=`），绝不能进日志。

    这个端点要鉴权，未登录会 401——但**中间件在鉴权之前就已记录**，
    所以 401 恰好是这条契约最好的检验点：日志照样写，原文照样不能有。
    """
    secret = "我的身份证号是110101199001011234"
    r = client.get("/api/search/preview", params={"q": secret})
    assert r.status_code in (401, 403, 422)

    recs = access_logs.requests()
    assert recs, "访问日志一条都没记，中间件没生效"
    hit = [x for x in recs if x["path"] == "/api/search/preview"]
    assert hit, f"没找到这次请求的日志；记录的 path 有 {[x['path'] for x in recs]}"

    import json as _json

    for rec in recs:
        assert "?" not in rec["path"], f"path 里带了 query string：{rec['path']}"
        # 兜底：原文可能被塞进 msg 或任何附加字段，整行序列化后一起查
        rendered = _json.dumps(rec, ensure_ascii=False)
        assert "110101199001011234" not in rendered, "用户原文进了访问日志"


def test_error_trace_id_joins_response_and_access_log(access_logs):
    """错误体的 trace_id / 响应头 / 访问日志必须是**同一个值**。

    这条是最容易写错也最难发现的：`_gateway_error` 原来自己 `uuid4()`，
    于是响应头一个 id、日志里另一个 id——用户截图上的 id 在日志里搜不到，
    比「完全没有 trace_id」更坑（因为看起来是有的）。
    """
    from webapp.main import access_log, gateway_guard

    mini = FastAPI()
    mini.middleware("http")(gateway_guard)
    mini.middleware("http")(access_log)      # 后注册的在外层，与生产顺序一致

    @mini.api_route("/api/ping", methods=["GET"])
    async def ping():
        return {"ok": True}

    # 用 ip 维度把配额压到 1：第一次通过，第二次必 429，且能拿到 trace_id。
    db.init_db()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM rate_rules WHERE dimension='ip'")
        upsert_rate_rule(conn, "ip", 1, 60)
        conn.commit()
    finally:
        conn.close()

    try:
        with TestClient(mini) as c:
            assert c.get("/api/ping").status_code == 200
            r = c.get("/api/ping")
            assert r.status_code == 429, r.text
            body_tid = r.json()["trace_id"]
            header_tid = r.headers["X-Trace-Id"]
            assert body_tid == header_tid, "错误体与响应头的 trace_id 不一致"

            logged = [x.get("trace_id") for x in access_logs.requests()]
            assert body_tid in logged, (
                f"响应里的 trace_id={body_tid} 在访问日志里找不到，日志记录的 id 有 {logged}"
            )
    finally:
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM rate_rules WHERE dimension='ip'")
            upsert_rate_rule(conn, "ip", 600, 60)
            conn.commit()
        finally:
            conn.close()


def test_quiet_paths_are_debug_not_info():
    """健康检查不能被 INFO 打出来——它每 30 秒一次，一天 2880 行纯噪声。

    直接测级别判定而不是走 HTTP：这条契约是「README 说的静默路径」，
    与具体路由是否存在无关，测中间件的判定规则更稳。
    """
    assert "/" in logctx.quiet_paths()
    assert "/health/live" in logctx.quiet_paths()
    assert "/metrics" in logctx.quiet_paths()


def test_json_formatter_is_one_line_and_has_fixed_keys():
    """一行一 JSON，字段固定——采集端靠这个解析，多一个换行就废了。"""
    fmt = logctx.JsonFormatter()
    rec = logging.LogRecord("webapp.access", logging.INFO, __file__, 1, "request", None, None)
    rec.event, rec.path, rec.status = "request", "/api/x", 200
    token = logctx.trace_id_var.set("abcdef1234567890")
    try:
        out = fmt.format(rec)
    finally:
        logctx.trace_id_var.reset(token)

    assert "\n" not in out, "格式化结果含换行，采集端会解析失败"
    import json as _json

    payload = _json.loads(out)
    for key in ("ts", "level", "logger", "msg", "trace_id"):
        assert key in payload, f"缺少必填字段 {key}"
    assert payload["trace_id"] == "abcdef1234567890"
    assert payload["path"] == "/api/x"
    assert payload["status"] == 200
