"""统一错误码契约：前端与机器人都按 {code, message, trace_id} 渲染，不让异常裸奔。"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.constants import ERROR_CODES


class AppError(Exception):
    def __init__(self, code: str, message: str | None = None, detail: Any = None, status: int | None = None):
        self.code = code
        self.message = message or code
        self.detail = detail
        self.status = status or ERROR_CODES.get(code, 500)
        super().__init__(self.message)


class Unauthorized(AppError):
    def __init__(self, message: str = "未登录或登录已失效", code: str = "AUTH_REQUIRED"):
        super().__init__(code, message)


class Forbidden(AppError):
    def __init__(self, message: str = "无权限执行该操作"):
        super().__init__("FORBIDDEN", message)


class NotFound(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__("NOT_FOUND", message)


class Conflict(AppError):
    def __init__(self, message: str = "资源状态冲突"):
        super().__init__("CONFLICT", message)


class BadRequest(AppError):
    def __init__(self, message: str = "请求参数不合法", detail: Any = None):
        super().__init__("VALIDATION_ERROR", message, detail)


class PayloadTooLarge(AppError):
    def __init__(self, message: str = "文件超过大小上限"):
        super().__init__("PAYLOAD_TOO_LARGE", message)


def install_exception_handlers(app) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "detail": exc.detail,
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        # 生产环境不回传堆栈，只给 trace_id，让日志可追
        import logging

        logging.getLogger("app").exception("unhandled error trace_id=%s", getattr(request.state, "trace_id", "-"))
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "服务内部错误，请联系管理员并提供 trace_id",
                "trace_id": getattr(request.state, "trace_id", None),
            },
        )
