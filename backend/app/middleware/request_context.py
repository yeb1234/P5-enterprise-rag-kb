"""trace_id 全链路贯穿。用纯 ASGI 中间件而不是 BaseHTTPMiddleware：
后者会包装响应流，容易把 SSE 事件攒在缓冲区里不推给浏览器。
"""
from __future__ import annotations

import uuid

from starlette.datastructures import MutableHeaders

from app.core.logging import get_logger, trace_id_var

logger = get_logger("app.http")


class TraceIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-trace-id", b"").decode("latin-1")
        trace_id = incoming or uuid.uuid4().hex
        scope.setdefault("state", {})["trace_id"] = trace_id
        token = trace_id_var.set(trace_id)

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                mutable["X-Trace-ID"] = trace_id
                mutable["X-Content-Type-Options"] = "nosniff"
                mutable["X-Frame-Options"] = "SAMEORIGIN"
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            trace_id_var.reset(token)
            logger.info("request %s %s", scope.get("method"), scope.get("path"))
