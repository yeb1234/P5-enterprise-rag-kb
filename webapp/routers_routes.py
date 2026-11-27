"""三路取证的配置与运维接口：状态查询、图谱同步、Wiki 构建。

- `GET  /api/kb/routes`      三路状态 + 后台任务进度（普通用户只看「通不通」；基础设施细节只给 admin）
- `POST /api/kb/graph/sync`  把 ready 文档的分块同步进 Neo4j（admin，后台任务）
- `POST /api/kb/wiki/build`  用模型把分块整理成 Wiki（admin，后台任务）

两个构建接口都是**先同步做前置检查、再丢给后台线程**，立即返回 202：
- 缺依赖（Neo4j 没起 / 场景没填 / 模型是离线占位 / 同名任务正在跑）→ 当场 409，并写明缺什么；
- 真正的构建要跑几秒到几分钟（Wiki 构建是十几批模型调用），进度靠 `GET /api/kb/routes`
  里的 `jobs` 轮询。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from webapp import db, kb_routes, settings
from webapp.deps import get_current_user, require_admin

router = APIRouter(prefix="/api/kb", tags=["kb-routes"])


class GraphSyncBody(BaseModel):
    doc_ids: list[str] | None = None      # None = 全量重建


def _start(action) -> dict:
    try:
        return action()
    except kb_routes.RouteNotReady as exc:
        # 409：请求本身没错，是依赖没就绪 / 同名任务正在跑
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/routes")
def get_routes(user: dict = Depends(get_current_user)):
    return kb_routes.status(detail=user.get("role") == "admin")


@router.post("/graph/sync", status_code=202)
def sync_graph(body: GraphSyncBody, user: dict = Depends(require_admin)):
    job = _start(lambda: kb_routes.graph_sync_async(body.doc_ids))
    db.log_action(user["username"], user["role"], "graph", "sync",
                  "full" if not body.doc_ids else f"incremental:{len(body.doc_ids)}")
    return job


@router.post("/wiki/build", status_code=202)
def build_wiki(user: dict = Depends(require_admin)):
    job = _start(kb_routes.wiki_build_async)
    db.log_action(user["username"], user["role"], "wiki", "build",
                  f"scenario={settings.get('wiki_scenario')}")
    return job
