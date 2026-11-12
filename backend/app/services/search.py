"""混合检索：PG 全文 + 标签/属性过滤 ⊕ Milvus 语义召回 → RRF 融合 → 权限二次过滤。

红线顺序（设计文档里反复强调的那条）：
  权限条件进 Milvus expr（先滤）→ 召回 → filter_candidates 二次校验（再滤）
  → 融合 → 重排/拼上下文 → LLM。
任何「先召回后过滤」的写法都会让无权限片段短暂进入候选集，一次就是越权。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.constants import Action
from app.core.logging import get_logger
from app.integrations.embedding import get_embedding_provider
from app.integrations.milvus_client import get_vector_store
from app.integrations.redis_client import get_json, set_json, tenant_key
from app.models.knowledge import Chunk, Document
from app.schemas.search import Citation, SearchHit, SearchRequest, SearchResponse
from app.services.document import apply_filters
from app.services.permission import PermissionService

if TYPE_CHECKING:
    from app.core.deps import Principal

logger = get_logger("app.search")
RRF_K = 60


# ---------------- 纯函数 ----------------

def rrf_fuse(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion：不需要调权重，对两路量纲不一致的分数天然稳健。"""
    fused: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            key = str(item["chunk_id"])
            entry = fused.setdefault(key, {**item, "score": 0.0, "matched_by": []})
            entry["score"] += 1.0 / (k + rank)
            source = item.get("source", "unknown")
            if source not in entry["matched_by"]:
                entry["matched_by"].append(source)
    return sorted(fused.values(), key=lambda x: x["score"], reverse=True)


def _cache_key(user: "Principal", req: SearchRequest) -> str:
    """缓存 key 必带 tenant + user + role + dept：否则 A 的检索结果会被 B 命中。"""
    raw = json.dumps(
        {"tenant": str(user.tenant_id), "user": str(user.id), "role": user.role,
         "dept": str(user.department_id), **req.model_dump(mode="json", exclude={"enable_answer"})},
        sort_keys=True, ensure_ascii=False,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return tenant_key(str(user.tenant_id), "search", digest)


def _visible_doc_ids(db: Session, user: "Principal", req: SearchRequest) -> list[str]:
    q = apply_filters(db.query(Document.id), user, req.filters)
    return [str(r[0]) for r in q.all()]


# ---------------- 关键词召回 ----------------

def keyword_search(db: Session, user: "Principal", req: SearchRequest) -> list[dict]:
    doc_ids = _visible_doc_ids(db, user, req)
    if not doc_ids:
        return []
    query = req.query.strip()
    if not query:
        return []

    q = db.query(Chunk).filter(
        Chunk.tenant_id == user.tenant_id,
        Chunk.is_deleted.is_(False),
        Chunk.document_id.in_(doc_ids),
    )
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in query)
    if has_cjk:
        # simple 配置对中文不分词，用 pg_trgm / ilike 兜底；生产换 zhparser
        q = q.filter(Chunk.content.ilike(f"%{query}%"))
        rank = func.length(Chunk.content).label("rank")
    else:
        tsq = func.websearch_to_tsquery("simple", query)
        q = q.filter(Chunk.search_vector.op("@@")(tsq))
        rank = func.ts_rank_cd(Chunk.search_vector, tsq).label("rank")
    q = q.order_by(rank.desc()).limit(max(req.top_k * 3, 30))

    titles = {
        str(r[0]): r[1]
        for r in db.query(Document.id, Document.title)
        .filter(Document.id.in_(doc_ids)).all()
    }
    out = []
    for chunk in q.all():
        out.append({
            "chunk_id": str(chunk.id), "doc_id": str(chunk.document_id),
            "kb_id": str(chunk.kb_id), "dept_id": str(chunk.department_id or ""),
            "sensitivity": chunk.sensitivity, "source": "keyword",
            "document_title": titles.get(str(chunk.document_id), ""),
            "page_no": chunk.page_no, "section_path": chunk.section_path,
            "snippet": chunk.content[:400], "acl": chunk.acl,
        })
    return out


# ---------------- 语义召回 ----------------

def semantic_search(db: Session, user: "Principal", req: SearchRequest) -> list[dict]:
    provider = get_embedding_provider()
    query_vector = provider.embed([req.query])[0]
    perms = PermissionService(db, user)
    kb_ids = [str(k) for k in req.filters.kb_ids] or None
    expr = perms.milvus_expr(kb_ids=kb_ids, extra="version_no >= 1")
    try:
        raw_hits = get_vector_store().search([query_vector], expr, top_k=max(req.top_k * 3, 30))[0]
    except Exception as exc:
        logger.warning("milvus unavailable, semantic branch degraded: %s", exc)
        return []  # 向量库挂了不影响关键词检索可用，这是有意的降级

    # 召回后立刻二次过滤（deny / 片段级 ACL 在这里生效）
    safe_hits = perms.filter_candidates(raw_hits)
    if not safe_hits:
        return []
    ids = [h["chunk_id"] for h in safe_hits]
    chunks = {
        str(c.id): c
        for c in db.query(Chunk).filter(
            Chunk.tenant_id == user.tenant_id, Chunk.id.in_(ids), Chunk.is_deleted.is_(False)
        ).all()
    }
    titles = {
        str(r[0]): r[1]
        for r in db.query(Document.id, Document.title)
        .filter(Document.tenant_id == user.tenant_id).all()
    }
    out = []
    for h in safe_hits:
        chunk = chunks.get(h["chunk_id"])
        if chunk is None:
            continue  # PG 有、Milvus 无 / 反之：孤儿向量，交给每日对账任务修
        out.append({
            "chunk_id": h["chunk_id"], "doc_id": str(chunk.document_id),
            "kb_id": str(chunk.kb_id), "dept_id": str(chunk.department_id or ""),
            "sensitivity": chunk.sensitivity, "source": "semantic",
            "document_title": titles.get(str(chunk.document_id), ""),
            "page_no": chunk.page_no, "section_path": chunk.section_path,
            "snippet": chunk.content[:400], "acl": chunk.acl,
        })
    return out


# ---------------- 编排入口 ----------------

def hybrid_search(db: Session, user: "Principal", req: SearchRequest) -> SearchResponse:
    start = time.perf_counter()
    cache_key = _cache_key(user, req)
    cached = get_json(cache_key)
    if cached:
        return SearchResponse(**{**cached, "cache_hit": True})

    lists: list[list[dict]] = []
    if req.mode in ("hybrid", "keyword"):
        lists.append(keyword_search(db, user, req))
    if req.mode in ("hybrid", "semantic"):
        lists.append(semantic_search(db, user, req))

    merged = rrf_fuse(lists)
    # 融合后再过一次权限：任何一路实现写错都不会把片段送进上下文
    merged = PermissionService(db, user).filter_candidates(merged)

    grouped: dict[str, dict] = {}
    for item in merged:
        doc_id = item["doc_id"]
        entry = grouped.setdefault(doc_id, {
            "document_id": doc_id, "title": item["document_title"],
            "category_id": None, "tags": [], "attributes": {},
            "score": 0.0, "matched_by": [], "citations": [],
        })
        entry["score"] = max(entry["score"], item["score"])
        for src in item.get("matched_by", [item["source"]]):
            if src not in entry["matched_by"]:
                entry["matched_by"].append(src)
        entry["citations"].append(Citation(**{
            "chunk_id": item["chunk_id"], "document_id": doc_id,
            "document_title": item["document_title"], "page_no": item.get("page_no"),
            "section_path": item.get("section_path", ""), "snippet": item.get("snippet", ""),
            "score": round(item["score"], 6),
            "source": "+".join(item.get("matched_by", [item["source"]])),
        }))
    hits = sorted(grouped.values(), key=lambda x: x["score"], reverse=True)
    for h in hits:
        h["citations"] = h["citations"][:5]
    total = len(hits)
    page_hits = hits[(req.page - 1) * req.page_size: req.page * req.page_size]

    response = SearchResponse(
        query=req.query, mode=req.mode, total=total, page=req.page, page_size=req.page_size,
        took_ms=int((time.perf_counter() - start) * 1000), cache_hit=False,
        hits=[SearchHit(**h) for h in page_hits],
    )
    set_json(cache_key, response.model_dump(mode="json"), ttl=300)
    return response


def build_answer_context(response: SearchResponse) -> list[dict]:
    """把分组结果摊平成带编号的引用列表，供 Agent 编排层拼 Prompt。"""
    citations = []
    for hit in response.hits:
        citations.extend(c.model_dump() for c in hit.citations)
    return citations[:12]
