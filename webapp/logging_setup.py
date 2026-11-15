"""运行日志：stdout 出 JSON，每个请求一个 trace_id 贯穿。

## 为什么不用 uvicorn 自带的访问日志

不是「不好看」，是有三个具体毛病，都实测确认过：

1. **格式是纯文本**（`INFO:     127.0.0.1:40246 - "GET / HTTP/1.1" 200 OK`）。
   能 `grep`，但没法按字段聚合——想问「今天 5xx 里有多少是哪些 path」得自己写正则。
2. **打印的是完整 URL**。`/api/search/preview?q=<用户原文>` 是 GET 端点
   （`routers_ops.py:89`），于是用户搜的原文会以明文进容器日志，无脱敏。
   审计表 `audit_logs` 里存原文是**设计**（可追溯），访问日志里留痕是**泄漏**。
3. **噪声压过信号**。`Dockerfile` 的 HEALTHCHECK 每 30 秒 `GET /`，一天 2880 行，
   真出事时想找那一条业务日志，得先从 `GET / 200` 里捞。

所以这里接管两件事：应用日志统一走 JSON（字段见 `_EXTRA_FIELDS`），访问日志由
`webapp/main.py` 的 `access_log` 中间件自己发一条——**path 不含 query**，
静默路径降到 DEBUG。

## 与 backend 的关系

`backend/app/core/logging.py` 是同思路的实现，但它只服务骨架、**不在主链路上**。
真正在跑的是 `webapp/`，所以这里重写一份而不是 import 过去：
两个目录之间有一条硬性的依赖方向（`webapp/` 不 import `backend/`），
且字段口径不同（backend 是 `tenant_id`，本项目是 `department_id`）。

## 级别与降噪

`WEBAPP_LOG_LEVEL` 控制根级别（默认 INFO）。**静默路径**（`WEBAPP_ACCESS_LOG_QUIET_PATHS`，
默认 `/`、`/health/live`、`/metrics`）在状态码 < 400 时降到 DEBUG——
需要时把级别调到 DEBUG 就能看到，平时不占版面。

单测里 `conftest.py` 会把级别钉到 WARNING，否则每个用例的 HTTP 调用都会刷一行 JSON。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextvars import ContextVar

# 请求级 trace_id：`access_log` 中间件在进入时 set，退出时 reset。
# 用 ContextVar 而不是全局变量：并发请求各自独立，不会串号。
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

# 允许出现在日志里的业务字段（白名单）。
# 为什么用白名单而不是「把 record.__dict__ 全倒出来」：
# 后者会把 msg 里可能夹带的敏感串、以及 logging 内部字段一起写进日志；
# 白名单让「新增字段」成为一次显式决定，而不是意外。
_EXTRA_FIELDS = (
    "event", "method", "path", "status", "latency_ms", "client",
    "user_id", "department_id", "doc_id", "batch_id", "stage",
    "provider", "model", "dimension",
)


def current_trace_id() -> str:
    """取当前请求的 trace_id；不在请求上下文里就返回 `-`。

    错误体与响应头必须用**同一个** id，否则「用户截图上的 trace_id」
    在日志里搜不到——这是最常见的一种「有 trace_id 但等于没有」。
    """
    return trace_id_var.get()


def new_trace_id() -> str:
    import uuid

    return uuid.uuid4().hex[:16]


def ensure_trace_id() -> str:
    """当前上下文里有 id 就用它，没有才生成。

    错误体、响应头、访问日志三处必须指向同一个 id，否则用户截图上的 trace_id
    在日志里搜不到——这比「完全没有 trace_id」更难排查，因为看起来是有的。
    """
    tid = trace_id_var.get()
    return tid if tid and tid != "-" else new_trace_id()


def safe_trace_id(raw: str | None) -> str:
    """校验客户端传来的 `X-Trace-Id`，不合法就换新的。

    为什么不能直接信任：日志是纯文本行，一个含换行的 header 就能伪造出
    「另一条日志」（日志注入）。所以只接受短十六进制串，其余一律重新生成。
    """
    s = (raw or "").strip()
    if 8 <= len(s) <= 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.lower()
    return new_trace_id()


class JsonFormatter(logging.Formatter):
    """一行一个 JSON 对象。key 顺序固定，便于人眼和 grep 同时用。"""

    def format(self, record: logging.LogRecord) -> str:
        created = record.created
        ms = int((created - int(created)) * 1000)
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(created)) + f".{ms:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            # 堆栈必须是单行，否则「一行一 JSON」的约定就破了，采集端会解析失败。
            payload["exc"] = self.formatException(record.exc_info).replace("\n", " | ")
        return json.dumps(payload, ensure_ascii=False)


def quiet_paths() -> set[str]:
    raw = os.environ.get("WEBAPP_ACCESS_LOG_QUIET_PATHS", "/,/health/live,/metrics")
    return {p.strip() for p in raw.split(",") if p.strip()}


def configure_logging(level: str | None = None) -> None:
    """把根 logger 换成 stdout + JSON，并关掉 uvicorn 的纯文本访问日志。

    幂等：重复调用只是重设 handler，不会叠出多份输出。
    """
    lvl = (level or os.environ.get("WEBAPP_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(lvl)

    # uvicorn 默认自己挂 handler 并设 propagate=False，不清掉的话我们的 formatter
    # 对它的日志完全无效（还是纯文本）。清空后统一走根 logger。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # 访问日志由 access_log 中间件发，uvicorn 那条必须关——否则一条请求打两行，
    # 且它带 query string（正是我们要避免的 PII 泄漏）。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str = "webapp") -> logging.Logger:
    return logging.getLogger(name)
