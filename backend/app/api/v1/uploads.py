"""上传接口 + 批次进度 SSE 流。

进度反馈方案（重点）：
  · 每个文件分片上传时推 file.progress；worker 每个阶段/每批单元推 stage.progress；
  · 前端只订阅一条 /batches/{id}/stream，就能渲染整批所有文件；
  · SSE 建连先发 PG 权威快照（支持刷新页面 / Last-Event-ID 续传）；
  · 15s 心跳保活，nginx 与浏览器都不会因空闲断开；
  · Redis 不可用自动降级为 2s 轮询 PG，用户看到的进度不会「假死」；
  · 纯 ASGI trace 中间件不包装响应流，避免 SSE 被缓冲。
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.constants import Action
from app.core.deps import CurrentUser, DbSession, request_meta
from app.db.repository import TenantRepository
from app.db.session import SessionLocal
from app.integrations.redis_client import get_redis, progress_channel
from app.models.knowledge import KnowledgeBase
from app.models.pipeline import BatchUpload
from app.schemas.upload import (
    BatchInitOut,
    BatchInitRequest,
    BatchProgress,
    ChunkAck,
    UploadCompleteOut,
    UploadInitOut,
    UploadInitRequest,
    UploadStatusOut,
)
from app.services import upload as upload_service
from app.services.audit import record
from app.services.permission import PermissionService, Resource
from app.services.progress import build_snapshot, snapshot_from_cache

router = APIRouter(prefix="/uploads", tags=["上传与进度"])


@router.post("/batches", response_model=BatchInitOut, summary="创建批量上传批次")
def init_batch(payload: BatchInitRequest, request: Request, db: DbSession, user: CurrentUser):
    result = upload_service.init_batch(db, user, payload)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="batch_create",
           resource_type="batch", resource_id=str(result.batch_id),
           detail={"total_files": payload.total_files, "total_bytes": payload.total_bytes},
           meta=request_meta(request), commit=True)
    return result


@router.get("/batches/{batch_id}/progress", response_model=BatchProgress, summary="批次进度快照（轮询降级用）")
def batch_progress(batch_id: uuid.UUID, db: DbSession, user: CurrentUser):
    TenantRepository(db, user.tenant_id).get(BatchUpload, batch_id)
    return build_snapshot(db, str(user.tenant_id), str(batch_id))


@router.get("/batches/{batch_id}/files", summary="批次内所有文件状态")
def batch_files(batch_id: uuid.UUID, db: DbSession, user: CurrentUser):
    TenantRepository(db, user.tenant_id).get(BatchUpload, batch_id)
    snap = build_snapshot(db, str(user.tenant_id), str(batch_id))
    return {"batch_id": str(batch_id), "status": snap.status, "files": [f.model_dump() for f in snap.files]}


@router.get("/batches/{batch_id}/stream", summary="批次进度 SSE 流")
def batch_stream(
    batch_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    """注意：此路由不注入 db 依赖，长连接不占用数据库连接池连接。
    越权保护靠 channel / 快照查询里强制携带的 tenant_id，跨租户订阅只会拿到空快照。"""
    tenant_id = str(user.tenant_id)
    bid = str(batch_id)
    channel = progress_channel(tenant_id, bid)
    last_seen = int(last_event_id or 0)

    def generate():
        nonlocal last_seen
        # 1) 先给权威快照：PG 重建，刷新页面也不会进度归零
        try:
            with SessionLocal() as db:
                snap = build_snapshot(db, tenant_id, bid)
            cached = snapshot_from_cache(tenant_id, bid)
            seq = max(int((cached or {}).get("seq", 0)), last_seen, 1)
            yield (
                f"id: {seq}\nevent: snapshot\ndata: "
                + json.dumps(snap.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n\n"
            )
        except Exception as exc:
            yield "event: error\ndata: " + json.dumps({"message": str(exc)}, ensure_ascii=False) + "\n\n"

        # 2) 订阅 Redis 实时通道
        pubsub = None
        try:
            pubsub = get_redis().pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(channel)
        except Exception:
            pubsub = None

        last_beat = time.time()
        try:
            while True:
                if pubsub is not None:
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        try:
                            data = json.loads(message["data"])
                        except json.JSONDecodeError:
                            data = {}
                        seq_no = int(data.get("seq", 0))
                        if seq_no > last_seen:
                            last_seen = seq_no
                            yield (
                                f"id: {seq_no}\nevent: {data.get('type', 'progress')}\ndata: "
                                + json.dumps(data, ensure_ascii=False, default=str) + "\n\n"
                            )
                            if data.get("type") == "batch" and data.get("status") in (
                                "completed", "partial", "failed", "cancelled"
                            ):
                                yield "event: close\ndata: {}\n\n"
                                break
                    if time.time() - last_beat > settings.SSE_HEARTBEAT_SECONDS:
                        yield ": keepalive\n\n"
                        last_beat = time.time()
                else:
                    # 3) Redis 不可用：降级轮询 PG，2s 一次
                    time.sleep(2)
                    with SessionLocal() as db:
                        snap = build_snapshot(db, tenant_id, bid)
                    yield (
                        "event: snapshot\ndata: "
                        + json.dumps(snap.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n\n"
                    )
                    if snap.status in ("completed", "partial", "failed", "cancelled"):
                        yield "event: close\ndata: {}\n\n"
                        break
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",   # 明确告诉 nginx 不缓冲 SSE
            "Connection": "keep-alive",
        },
    )


@router.post("/init", response_model=UploadInitOut, summary="初始化单文件上传（含断点续传与秒传）")
def init_upload(payload: UploadInitRequest, request: Request, db: DbSession, user: CurrentUser):
    result = upload_service.init_upload(db, user, payload)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.UPLOAD.value,
           resource_type="document", resource_id=str(result.upload_id),
           detail={"filename": payload.filename, "size": payload.size_bytes, "instant": result.instant},
           meta=request_meta(request), commit=True)
    return result


@router.put("/{upload_id}/chunks/{idx}", response_model=ChunkAck, summary="上传单个分片（幂等）")
async def put_chunk(upload_id: uuid.UUID, idx: int, request: Request, db: DbSession, user: CurrentUser):
    data = await request.body()
    return await run_in_threadpool(upload_service.put_chunk, db, user, upload_id, idx, data)


@router.get("/{upload_id}/status", response_model=UploadStatusOut, summary="查询已收分片（断点续传）")
def upload_status(upload_id: uuid.UUID, db: DbSession, user: CurrentUser):
    return upload_service.upload_status(db, user, upload_id)


@router.post("/{upload_id}/complete", response_model=UploadCompleteOut, summary="合并分片并入队解析")
async def complete(upload_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    result = await run_in_threadpool(upload_service.complete_upload, db, user, upload_id)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="upload_complete",
           resource_type="document", resource_id=str(result.document_id),
           detail={"job_id": str(result.job_id), "queue": result.queue},
           meta=request_meta(request), commit=True)
    return result


@router.delete("/{upload_id}", summary="取消上传并清理分片")
def cancel(upload_id: uuid.UUID, request: Request, db: DbSession, user: CurrentUser):
    upload_service.cancel_upload(db, user, upload_id)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="upload_cancel",
           resource_type="batch", resource_id=str(upload_id), meta=request_meta(request), commit=True)
    return {"ok": True}
