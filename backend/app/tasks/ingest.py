"""文档入库流水线：解析 → 分块 → 向量化 → 写索引 → 收尾。

三种执行形态：
  1. 普通文件：Celery chain 顺序执行；
  2. 大文件（>100MB 且 >200 页）：chord 按页范围并行解析，再合并继续同一条链；
  3. 在线编辑的富文本：从 content_text 起链，共用下游所有节点。

可靠性：
  · chunk.id = uuid5(doc_id, version_no, seq) 作为跨 PG/Milvus 的幂等键；
  · 每批先写 PG 再写 Milvus；向量写失败不影响文本，交给每日对账补齐；
  · autoretry + 指数退避 + 上限；失败阶段与错误落 IngestJob，前端可一键重试；
  · 中间产物落对象存储，不走 Celery result 传大对象（避免撑爆 Redis）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from celery import chain, chord, group

from app.core.config import settings
from app.core.constants import DocStatus, IngestStage, JobState
from app.core.logging import get_logger
from app.db.session import session_scope
from app.integrations.embedding import get_embedding_provider
from app.integrations.milvus_client import get_vector_store
from app.integrations.object_storage import get_object_store, object_key
from app.integrations.redis_client import get_redis, tenant_key
from app.models.knowledge import Chunk, Document
from app.models.pipeline import IngestJob
from app.services.parser import PageText, ParsedDoc, parse_range, probe_page_count, split_into_chunks
from app.services.progress import build_snapshot, publish_batch_event, report_stage
from app.tasks.celery_app import celery_app

logger = get_logger("app.ingest")
NS = uuid.UUID("6f1a9c20-1d3e-4b8a-9c2d-7e5f0a1b3c4d")


# ---------------- 中间产物 ----------------

def _intermediate_key(tenant_id: str, doc_id: str, name: str) -> str:
    return object_key(tenant_id, "intermediate", doc_id, name)


def _put_json(key: str, payload: Any) -> str:
    get_object_store().put(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json")
    return key


def _get_json(key: str) -> Any:
    return json.loads(get_object_store().get(key).decode("utf-8"))


def _pages_to_dict(parsed: ParsedDoc) -> dict:
    return {
        "pages": [{"page_no": p.page_no, "text": p.text, "headings": p.headings} for p in parsed.pages],
        "page_count": parsed.page_count,
        "source_level": parsed.source_level,
    }


def _dict_to_pages(data: dict) -> list[PageText]:
    return [PageText(page_no=p["page_no"], text=p["text"], headings=p.get("headings", [])) for p in data["pages"]]


# ---------------- 入队 ----------------

def _base_ctx(tenant_id: str, document_id: str, kb_id: str, **extra) -> dict:
    return {
        "tenant_id": tenant_id, "document_id": document_id, "kb_id": kb_id,
        "batch_id": None, "upload_id": None, "version_no": 1,
        "object_key": None, "mime_type": "text/plain", "is_large": False,
        "queue": "ingest", "page_count": 1, "parsed_key": None, "chunks_key": None,
        **extra,
    }


def enqueue_ingest(
    *, tenant_id: str, batch_id: str | None, document_id: str, upload_id: str,
    kb_id: str, object_key: str, mime_type: str, is_large: bool,
):
    """由上传服务在事务提交后调用，返回 Celery AsyncResult。"""
    queue = "ingest_heavy" if is_large else "ingest"
    try:
        page_count = probe_page_count(object_key, mime_type)
    except Exception as exc:
        logger.warning("page probe failed doc=%s: %s", document_id, exc)
        page_count = 1
    ctx = _base_ctx(
        tenant_id, document_id, kb_id, batch_id=batch_id, upload_id=upload_id,
        object_key=object_key, mime_type=mime_type, is_large=is_large,
        queue=queue, page_count=page_count,
    )
    needs_split = is_large and page_count > settings.LARGE_FILE_PAGE_THRESHOLD
    tail = chain(
        chunk_document.s(), embed_and_index.s(), finalize_document.s(),
    )

    if needs_split:
        step = settings.PARSE_PAGE_BATCH
        ranges = [(s, min(s + step, page_count)) for s in range(0, page_count, step)]
        header = group(
            parse_page_range.s(ctx, start, end).set(queue="ingest_heavy")
            for start, end in ranges
        )
        body = chain(merge_parsed.s(ctx).set(queue="ingest_heavy"), *tail.tasks)
        result = chord(header)(body)
        logger.info("large file split doc=%s pages=%d subtasks=%d", document_id, page_count, len(ranges))
        return result

    head = parse_document.s(ctx).set(queue=queue)
    return chain(head, *tail.tasks).apply_async()


def enqueue_richtext_ingest(
    *, tenant_id: str, document_id: str, kb_id: str, version_no: int = 1, batch_id: str | None = None,
):
    """在线创建/编辑/回滚/恢复都走这里：先建任务台账，再入队。"""
    job = IngestJob(
        tenant_id=uuid.UUID(tenant_id), batch_id=uuid.UUID(batch_id) if batch_id else None,
        document_id=uuid.UUID(document_id), queue="ingest", stage="queued", state="queued",
        idempotency_key=f"{tenant_id}:{document_id}:v{version_no}:{uuid.uuid4().hex[:8]}",
        max_retries=settings.INGEST_MAX_RETRIES,
    )
    with session_scope() as db:
        db.add(job)
        db.flush()
        job_id = str(job.id)
    ctx = _base_ctx(
        tenant_id, document_id, kb_id, batch_id=batch_id,
        version_no=version_no, job_id=job_id,
    )
    return chain(
        parse_richtext.s(ctx), chunk_document.s(), embed_and_index.s(), finalize_document.s()
    ).apply_async()


def retry_failed_job(job_id: str) -> str:
    """前端「重试」入口：复用同一 document_id 与 version，幂等键保证不产生重复向量。"""
    with session_scope() as db:
        job = db.query(IngestJob).filter(IngestJob.id == uuid.UUID(job_id)).first()
        if job is None or job.state != JobState.FAILED.value:
            raise ValueError("任务不存在或不是失败状态")
        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if doc is None:
            raise ValueError("文档不存在")
        tenant_id, doc_id, kb_id = str(job.tenant_id), str(doc.id), str(doc.kb_id)
        version = doc.current_version
        batch_id = str(job.batch_id) if job.batch_id else None
        object_key_value, mime = doc.source_uri, doc.mime_type or "text/plain"
        job.state = JobState.QUEUED.value
        job.retry_count = 0
        job.error = None
        job.finished_at = None

    if object_key_value:
        return str(enqueue_ingest(
            tenant_id=tenant_id, batch_id=batch_id, document_id=doc_id,
            upload_id="retry", kb_id=kb_id, object_key=object_key_value,
            mime_type=mime, is_large=False,
        ))
    return str(enqueue_richtext_ingest(
        tenant_id=tenant_id, document_id=doc_id, kb_id=kb_id,
        version_no=version, batch_id=batch_id,
    ))


# ---------------- 阶段任务 ----------------

def _stage_guard(task, ctx: dict, stage: str, fn):
    """统一异常出口：重试中 / 最终失败 / 心跳，全部写进 PG 任务台账。"""
    try:
        return fn()
    except Exception as exc:
        exhausted = task.request.retries >= settings.INGEST_MAX_RETRIES - 1
        report_stage(
            ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"], stage,
            state=JobState.FAILED.value if exhausted else JobState.RETRYING.value,
            error=f"[{type(exc).__name__}] {exc}"[:1000],
            celery_task_id=task.request.id,
        )
        logger.exception("ingest stage failed doc=%s stage=%s exhausted=%s", ctx["document_id"], stage, exhausted)
        raise


def _task_kwargs(**kw):
    return dict(
        bind=True, autoretry_for=(Exception,), retry_backoff=True,
        retry_backoff_max=120, retry_jitter=True,
        max_retries=settings.INGEST_MAX_RETRIES,
        soft_time_limit=settings.INGEST_SOFT_TIME_LIMIT,
        time_limit=settings.INGEST_HARD_TIME_LIMIT,
        **kw,
    )


@celery_app.task(name="app.tasks.ingest.parse_document", **_task_kwargs())
def parse_document(self, ctx: dict) -> dict:
    def run():
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.PARSE.value, 0.0, 0, ctx["page_count"], "开始解析",
                     celery_task_id=self.request.id)
        parsed = parse_range(ctx["object_key"], ctx["mime_type"])
        ctx["parsed_key"] = _put_json(
            _intermediate_key(ctx["tenant_id"], ctx["document_id"], "parsed.json"),
            _pages_to_dict(parsed),
        )
        ctx["page_count"] = parsed.page_count
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.PARSE.value, 1.0, parsed.page_count, parsed.page_count, "解析完成")
        return ctx

    return _stage_guard(self, ctx, IngestStage.PARSE.value, run)


@celery_app.task(name="app.tasks.ingest.parse_richtext", **_task_kwargs())
def parse_richtext(self, ctx: dict) -> dict:
    def run():
        with session_scope() as db:
            doc = db.query(Document).filter(Document.id == uuid.UUID(ctx["document_id"])).first()
            if doc is None:
                raise ValueError("文档不存在")
            text = doc.content_text or ""
        size = 3000
        pages = [PageText(page_no=i // size + 1, text=text[i:i + size])
                 for i in range(0, len(text), size)] or [PageText(page_no=1, text="")]
        parsed = ParsedDoc(pages=pages, page_count=len(pages))
        ctx["parsed_key"] = _put_json(
            _intermediate_key(ctx["tenant_id"], ctx["document_id"], f"parsed-v{ctx['version_no']}.json"),
            _pages_to_dict(parsed),
        )
        ctx["page_count"] = parsed.page_count
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.PARSE.value, 1.0, len(pages), len(pages), "正文解析完成")
        return ctx

    return _stage_guard(self, ctx, IngestStage.PARSE.value, run)


@celery_app.task(name="app.tasks.ingest.parse_page_range", **_task_kwargs())
def parse_page_range(self, ctx: dict, start_page: int, end_page: int) -> str:
    """大文件页级子任务：只解析自己那段，独立重试，失败不影响其他页。"""
    def run():
        parsed = parse_range(ctx["object_key"], ctx["mime_type"], start_page, end_page)
        key = _intermediate_key(ctx["tenant_id"], ctx["document_id"], f"pages-{start_page:05d}-{end_page:05d}.json")
        _put_json(key, _pages_to_dict(parsed))
        try:
            done = int(get_redis().incr(tenant_key(ctx["tenant_id"], "parsed-pages", ctx["document_id"])))
        except Exception:
            done = end_page
        total = max(ctx.get("page_count", 1), 1)
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.PARSE.value, min(done / total, 1.0), min(done, total), total,
                     f"已完成页 {start_page}-{end_page}")
        return key

    return _stage_guard(self, ctx, IngestStage.PARSE.value, run)


@celery_app.task(name="app.tasks.ingest.merge_parsed", **_task_kwargs())
def merge_parsed(self, part_keys: list[str], ctx: dict) -> dict:
    """chord 回调：按文件名里的页范围排序后合并，保证阅读顺序正确。"""
    def run():
        parts = sorted(part_keys, key=lambda k: int(k.split("-")[-2]) if "-" in k else 0)
        all_pages, page_count = [], 0
        for key in parts:
            data = _get_json(key)
            all_pages.extend(data["pages"])
            page_count = max(page_count, data["page_count"])
        all_pages.sort(key=lambda p: p["page_no"])
        merged_key = _intermediate_key(ctx["tenant_id"], ctx["document_id"], "parsed.json")
        _put_json(merged_key, {"pages": all_pages, "page_count": page_count, "source_level": "L1"})
        ctx["parsed_key"] = merged_key
        ctx["page_count"] = page_count
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.PARSE.value, 1.0, page_count, page_count, "页级并行解析完成")
        # 清理中间分片，失败不影响主链路
        try:
            get_object_store().remove(parts)
        except Exception:
            pass
        return ctx

    return _stage_guard(self, ctx, IngestStage.PARSE.value, run)


@celery_app.task(name="app.tasks.ingest.chunk_document", **_task_kwargs())
def chunk_document(self, ctx: dict) -> dict:
    def run():
        data = _get_json(ctx["parsed_key"])
        specs = split_into_chunks(_dict_to_pages(data))
        payload = [
            {
                "seq": c.seq, "content": c.content, "page_no": c.page_no,
                "section_path": c.section_path, "block_type": c.block_type,
                "token_count": c.token_count, "parent_seq": c.parent_seq,
                "confidence": c.confidence, "source_level": c.source_level,
            }
            for c in specs
        ]
        ctx["chunks_key"] = _put_json(
            _intermediate_key(ctx["tenant_id"], ctx["document_id"], f"chunks-v{ctx['version_no']}.json"),
            payload,
        )
        report_stage(ctx["tenant_id"], ctx.get("batch_id"), ctx["document_id"],
                     IngestStage.CHUNK.value, 1.0, len(payload), len(payload),
                     f"语义分块完成，共 {len(payload)} 块")
        return ctx

    return _stage_guard(self, ctx, IngestStage.CHUNK.value, run)


@celery_app.task(name="app.tasks.ingest.embed_and_index", **_task_kwargs())
def embed_and_index(self, ctx: dict) -> dict:
    def run():
        specs = _get_json(ctx["chunks_key"])
        provider = get_embedding_provider()
        store = get_vector_store()
        tenant, doc_id = ctx["tenant_id"], ctx["document_id"]
        total = len(specs)
        batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)

        with session_scope() as db:
            doc = db.query(Document).filter(Document.id == uuid.UUID(doc_id)).first()
            if doc is None:
                raise ValueError("文档不存在")
            dept_id = str(doc.department_id) if doc.department_id else ""
            category_id = doc.category_id
            sensitivity = doc.sensitivity
            version_no = int(ctx.get("version_no") or doc.current_version)

        for offset in range(0, total, batch_size):
            batch = specs[offset:offset + batch_size]
            vectors = provider.embed([s["content"] for s in batch])
            rows = []
            with session_scope() as db:
                for spec, vector in zip(batch, vectors):
                    cid = uuid.uuid5(NS, f"{doc_id}:{version_no}:{spec['seq']}")
                    existing = db.query(Chunk).filter(Chunk.id == cid).first()
                    target = existing or Chunk(
                        id=cid, tenant_id=uuid.UUID(tenant), document_id=uuid.UUID(doc_id),
                        kb_id=uuid.UUID(ctx["kb_id"]), seq=spec["seq"],
                    )
                    target.kb_id = uuid.UUID(ctx["kb_id"])
                    target.department_id = uuid.UUID(dept_id) if dept_id else None
                    target.category_id = category_id
                    target.sensitivity = sensitivity
                    target.version_no = version_no
                    target.content = spec["content"]
                    target.page_no = spec["page_no"]
                    target.section_path = spec["section_path"]
                    target.block_type = spec["block_type"]
                    target.token_count = spec["token_count"]
                    target.confidence = spec["confidence"]
                    target.source_level = spec["source_level"]
                    target.is_deleted = False
                    if existing is None:
                        db.add(target)
                    rows.append({
                        "chunk_id": str(cid), "tenant_id": tenant, "kb_id": ctx["kb_id"],
                        "doc_id": doc_id, "dept_id": dept_id, "sensitivity": sensitivity,
                        "version_no": version_no, "embedding": vector,
                    })
                db.commit()  # 先 PG 落文本，再写 Milvus，顺序不可反
            try:
                store.upsert(rows)
            except Exception as exc:
                # 向量写失败不回滚文本：每日对账会按 chunk_id 补齐缺失向量
                logger.error("milvus upsert failed doc=%s offset=%d: %s", doc_id, offset, exc)
            done = offset + len(batch)
            report_stage(
                tenant, ctx.get("batch_id"), doc_id, IngestStage.EMBED.value,
                done / max(total, 1), done, total, f"向量化 {done}/{total}",
            )
        report_stage(tenant, ctx.get("batch_id"), doc_id, IngestStage.INDEX.value,
                     1.0, total, total, "索引写入完成")
        ctx["indexed_count"] = total
        return ctx

    return _stage_guard(self, ctx, IngestStage.EMBED.value, run)


@celery_app.task(name="app.tasks.ingest.finalize_document", **_task_kwargs())
def finalize_document(self, ctx: dict) -> dict:
    def run():
        tenant, doc_id = ctx["tenant_id"], ctx["document_id"]
        with session_scope() as db:
            doc = db.query(Document).filter(Document.id == uuid.UUID(doc_id)).first()
            if doc:
                doc.chunk_count = int(ctx.get("indexed_count", 0))
                doc.page_count = int(ctx.get("page_count", 0))
                doc.status = DocStatus.INDEXED.value
                doc.indexed_at = datetime.now(timezone.utc)
                doc.error_message = None
            db.commit()
        report_stage(tenant, ctx.get("batch_id"), doc_id, IngestStage.DONE.value, 1.0,
                     state=JobState.SUCCEEDED.value, message="文档已可检索")
        if ctx.get("batch_id"):
            finalize_batch.delay(tenant, ctx["batch_id"])
        return ctx

    return _stage_guard(self, ctx, IngestStage.FINALIZE.value, run)


@celery_app.task(name="app.tasks.ingest.finalize_batch")
def finalize_batch(tenant_id: str, batch_id: str) -> None:
    """批次收口：以 PG 重建一次权威快照并广播 completed 事件。"""
    with session_scope() as db:
        snap = build_snapshot(db, tenant_id, batch_id)
    publish_batch_event(tenant_id, batch_id, snap.status, snap.progress,
                        f"成功 {snap.succeeded} / 失败 {snap.failed}")


@celery_app.task(name="app.tasks.ingest.cleanup_document")
def cleanup_document(tenant_id: str, document_id: str, kb_id: str) -> None:
    """删除三方联动：PG 已软删 → 清 Milvus 向量 → 标记 PG chunks 软删。
    原文件保留（可恢复 / 可审计），由生命周期策略按租户配置的保留天数异步清理。"""
    try:
        get_vector_store().delete_by_doc(tenant_id, document_id)
    except Exception as exc:
        logger.error("milvus cleanup failed doc=%s: %s", document_id, exc)
    with session_scope() as db:
        db.query(Chunk).filter(
            Chunk.tenant_id == uuid.UUID(tenant_id),
            Chunk.document_id == uuid.UUID(document_id),
        ).update({Chunk.is_deleted: True}, synchronize_session=False)
        db.commit()
    logger.info("document cleaned doc=%s kb=%s", document_id, kb_id)
