"""解析实验室 API：策略开关 + 逐页解析轨迹。

设计取舍：
- `parsing/` 是可独立导入的纯 Python 包，这里只做 HTTP 适配，不把业务逻辑塞进路由；
- 策略切换只写进程内环境变量（演示切片单进程），生产应落配置中心并带版本号；
- 轨迹存内存 deque（Celery + PG 真值层的演示替身），重启即清空，绝不冒充持久化；
- 写接口（切策略）仅 admin，与既有「写接口仅 admin」约定一致；
  解析探测是只读演示动作，登录用户即可用，方便面试现场换文件试。
"""
from __future__ import annotations

from collections import deque

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from webapp import db
from webapp.deps import get_current_user, require_admin

# parsing/ 位于项目根，webapp/ 是子包；从项目根启动时可直接 import
from parsing.config import current_strategy, set_strategy
from parsing.contracts import STRATEGIES
from parsing.router import ParseRouter

router = APIRouter(prefix="/api/parse", tags=["parsing"])

MAX_PROBE_BYTES = 8 * 1024 * 1024   # 演示探测上限，避免把大文件读进内存
_TRACE_HISTORY: deque = deque(maxlen=20)


class StrategyPatch(BaseModel):
    strategy: str


@router.get("/status")
def get_status(_: dict = Depends(get_current_user)):
    """策略列表、三级可用性、预算闸门与网关模型探测结果。"""
    st = ParseRouter().status()
    st["trace_history"] = len(_TRACE_HISTORY)
    st["max_probe_bytes"] = MAX_PROBE_BYTES
    return st


@router.put("/strategy")
def put_strategy(body: StrategyPatch, user: dict = Depends(require_admin)):
    try:
        value = set_strategy(body.strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.log_action(user["username"], user["role"], "parsing", "strategy",
                  f"strategy={value}")
    return {"strategy": value, "allowed": list(STRATEGIES),
            "effective": "本进程即时生效；生产需落配置中心并多进程同步"}


@router.post("/probe")
async def probe(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """上传一个文件，返回每一页的解析轨迹（不落业务库、不写向量）。"""
    data = await file.read(MAX_PROBE_BYTES + 1)
    if len(data) > MAX_PROBE_BYTES:
        raise HTTPException(status_code=413, detail="演示探测文件上限 8 MiB")
    filename = file.filename or "unnamed"
    doc_id = "probe-" + user["id"][:8]
    report = ParseRouter().parse_document(
        data, filename=filename, mime=file.content_type or "", doc_id=doc_id)
    _TRACE_HISTORY.appendleft({
        "filename": filename, "user": user["username"],
        "strategy": report["strategy"], "page_count": report["page_count"],
        "summary": report["summary"], "budget": report["budget"],
    })
    db.log_action(user["username"], user["role"], "parsing", "probe",
                  f"file={filename} pages={report['page_count']} "
                  f"strategy={report['strategy']}")
    return report


@router.get("/traces")
def get_traces(_: dict = Depends(get_current_user)):
    return {"history": list(_TRACE_HISTORY), "note": "进程内最近 20 次探测，重启即清空"}
