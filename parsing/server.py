"""独立解析服务（Docker 用）：最小 FastAPI 外壳，只依赖 parsing/ 包。

为什么不复用 webapp/main.py：webapp 是带 PostgreSQL、进程内队列、鉴权的演示切片，
塞进 compose 会把「演示替身」和「生产骨架」混编。解析服务只需要一个纯函数包，
单独一个进程/镜像，边界清楚，评审时也好解释「哪一层可以独立扩容」。

启动：
    uvicorn parsing.server:app --host 0.0.0.0 --port 8766
"""
from __future__ import annotations

from collections import deque

from fastapi import FastAPI, File, HTTPException, UploadFile

from parsing.config import current_strategy, set_strategy
from parsing.contracts import STRATEGIES
from parsing.router import ParseRouter

app = FastAPI(title="KB Parsing Service", version="0.1.0-demo")
MAX_BYTES = 8 * 1024 * 1024
_HISTORY: deque = deque(maxlen=20)


@app.get("/health/live")
def live() -> dict:
    return {"status": "ok", "service": "parser", "strategy": current_strategy()}


@app.get("/parse/status")
def status() -> dict:
    return ParseRouter().status()


@app.put("/parse/strategy")
def put_strategy(body: dict) -> dict:
    value = str(body.get("strategy", ""))
    try:
        return {"strategy": set_strategy(value), "allowed": list(STRATEGIES)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/parse/probe")
async def probe(file: UploadFile = File(...)) -> dict:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="演示探测文件上限 8 MiB")
    rep = ParseRouter().parse_document(
        data, filename=file.filename or "unnamed",
        mime=file.content_type or "", doc_id="docker-probe")
    _HISTORY.appendleft({"filename": rep["filename"], "page_count": rep["page_count"],
                         "summary": rep["summary"]})
    return rep


@app.get("/parse/traces")
def traces() -> dict:
    return {"history": list(_HISTORY), "note": "进程内最近 20 次探测"}
