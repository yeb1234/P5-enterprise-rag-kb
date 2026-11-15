"""showcase FastAPI 入口。

启动（项目根目录）：
    python -m uvicorn showcase.server:app --host 127.0.0.1 --port 8767

直接运行等价：
    python -m showcase.server        # 内部调 uvicorn.run，端口 8767

职责：FastAPI app、同源挂载 showcase/static/、CORS、全局异常处理统一
      {"code","message","trace_id"}。10 个路由在 showcase/api.py。

三态：已实现（演示级站点）。静态页由 frontend-engineer 并行实现，
      未落盘时用 check_dir=False 避免启动失败；集成阶段目录齐备后自动生效。
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from showcase import DEFAULT_PORT, SHOWCASE_VERSION
from showcase.api import ApiError, router

logger = logging.getLogger("showcase")

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

app = FastAPI(
    title="KB Showcase · 解析 → 入库预览",
    version=SHOWCASE_VERSION,
    description="离线可跑的演示站点（A/B 档骨架，非上线交付版）",
)

# 单页同源站点；演示切片允许任意来源，生产应收紧到具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


def _trace_id(request: Request) -> str:
    tid = getattr(request.state, "trace_id", None)
    return str(tid) if tid else uuid.uuid4().hex[:16]


def _error_body(code: str, message: str, trace_id: str) -> dict:
    """02-architecture.md §4.3：统一 {"code","message","trace_id"}，不回堆栈。"""
    return {"code": code, "message": message, "trace_id": trace_id}


@app.exception_handler(ApiError)
async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=_error_body(exc.code, exc.message, exc.trace_id or _trace_id(request)),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 404 / 405 等框架异常也归一到同一契约，避免前端解析两套形状
    code = "RUN_NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail), _trace_id(request)),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    # 生产口径：不回传堆栈，只给 trace_id 让日志可追
    trace_id = _trace_id(request)
    logger.exception("showcase unhandled error trace_id=%s", trace_id)
    return JSONResponse(
        status_code=500,
        content=_error_body("INTERNAL_ERROR", "服务内部错误，请联系管理员并提供 trace_id",
                            trace_id),
    )


# 样本静态挂载（集成裁决 · 致命项）：前端两档切换会 fetch
# `/showcase/samples/<name>` 拿真实字节，而 samples/ 不在 static/ 下。
# 顺序必须是「先窄路径、后 /」，否则会被根 StaticFiles 吃掉；
# check_dir=False 与根挂载一致，同伴样本未齐时也不阻止启动。
app.mount(
    "/showcase/samples",
    StaticFiles(directory=str(HERE / "samples"), check_dir=False),
    name="samples",
)

# 静态资源挂在最后，避免遮住 /api/*；check_dir=False 让同伴文件未齐时也能启动。
# 刻意不 mkdir：该目录由 frontend-engineer 独占，本模块不创建、不写入同伴目录。
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True, check_dir=False),
          name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("showcase.server:app", host="127.0.0.1", port=DEFAULT_PORT)
