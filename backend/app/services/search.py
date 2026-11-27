"""混合检索：PG 全文 + 标签/属性过滤 ⊕ Milvus 语义召回 → RRF 融合 → 权限二次过滤。

红线顺序（设计文档里反复强调的那条）：
  权限条件进 Milvus expr（先滤）→ 召回 → filter_candidates 二次校验（再滤）
  → 融合 → 重排/拼上下文 → LLM。
任何「先召回后过滤」的写法都会让无权限片段短暂进入候选集，一次就是越权。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
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

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
# 中文没有分词器时，用问题里的实词片段召回；问句外壳不参与匹配。
_CJK_STOP_PHRASES = (
    "在哪里", "是什么", "为什么", "怎么样", "怎么", "如何", "多少", "哪些", "哪个",
    "什么", "请问", "介绍", "一下", "能否", "可以", "需要", "是否", "能用多久", "多久",
    "的", "了", "吗", "呢", "啊", "是", "在", "有", "和", "与", "及", "或",
)
_EN_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "do", "does", "did", "how", "what",
    "why", "when", "where", "which", "who", "to", "of", "in", "on", "for", "with",
    "and", "or", "i", "you", "we", "it", "this", "that", "can", "could", "should",
    "would", "please", "tell", "me", "about", "from", "by", "as", "at", "be", "been",
    "being", "my", "your", "our", "their", "there", "here", "if", "than", "then",
}


def query_terms(query: str) -> list[str]:
    """提取中英文问题的实词片段。

    这里不是完整分词器，而是无外部依赖的保守方案：
    - 中文先剥掉问句外壳，再取 2~4 字片段/二元组；
    - 英文去掉停用词，保留实词；
    - 只用于 keyword 路召回，不改变语义路的原文 query。
    """
    terms: list[str] = []
    for run in _TOKEN_RE.findall(query or ""):
        if _CJK_RE.search(run):
            cleaned = run
            for stop in sorted(_CJK_STOP_PHRASES, key=len, reverse=True):
                cleaned = cleaned.replace(stop, "")
            base = cleaned or run
            if 2 <= len(base) <= 4:
                terms.append(base)
            for i in range(max(0, len(base) - 1)):
                piece = base[i:i + 2]
                if len(piece) == 2:
                    terms.append(piece)
        else:
            token = run.lower()
            if token not in _EN_STOPWORDS and len(token) >= 2:
                terms.append(token)
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
    # 长片段优先，避免短虚词把召回面拉得过大。
    unique.sort(key=len, reverse=True)
    return unique[:12]


def _snippet_chars(query: str) -> int:
    return (
        settings.SEARCH_SNIPPET_CHARS_EN
        if not _CJK_RE.search(query or "")
        else settings.SEARCH_SNIPPET_CHARS
    )


def _term_clauses(terms: list[str]) -> list:
    return [Chunk.content.ilike(f"%{term}%") for term in terms]


def _term_hit_expr(terms: list[str]):
    if not terms:
        return 0
    return sum(case((Chunk.content.ilike(f"%{term}%"), 1), else_=0) for term in terms)


def select_answer_context(
    candidates: list[dict],
    doc_limit: int | None = None,
    per_doc: int | None = None,
    max_citations: int | None = None,
) -> list[dict]:
    """把检索候选压成送进 LLM 的上下文。

    召回阶段可以宽（top_k、每路 30 条），但答案上下文不能把同一篇文档的
    5 条片段全塞进去。按文档首次出现顺序保留前 N 篇，每篇最多 M 条，
    最后再按总预算截断；这个顺序已经是融合后的 doc 级排序。
    """
    doc_limit = settings.SEARCH_CONTEXT_DOCS if doc_limit is None else doc_limit
    per_doc = settings.SEARCH_CONTEXT_CITATIONS_PER_DOC if per_doc is None else per_doc
    max_citations = settings.SEARCH_CONTEXT_MAX_CITATIONS if max_citations is None else max_citations

    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in candidates:
        doc_id = str(item.get("doc_id") or item.get("document_id") or "")
        if not doc_id:
            continue
        if doc_id not in grouped:
            grouped[doc_id] = []
            order.append(doc_id)
        if len(grouped[doc_id]) < per_doc:
            grouped[doc_id].append(item)

    selected: list[dict] = []
    for doc_id in order[:doc_limit]:
        selected.extend(grouped[doc_id])
    return selected[:max_citations]


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
    has_cjk = bool(_CJK_RE.search(query))
    terms = query_terms(query)
    if has_cjk:
        # 中文用“实词片段 + pg_trgm 相似度”，而不是整句 ilike。
        # 整句包含问句外壳时，原文往往不会连续出现，旧实现几乎只能靠语义路。
        term_clauses = _term_clauses(terms)
        if term_clauses:
            q = q.filter(or_(*term_clauses, Chunk.content.ilike(f"%{query}%")))
        else:
            q = q.filter(Chunk.content.ilike(f"%{query}%"))
        rank = _term_hit_expr(terms) + case((Chunk.content.ilike(f"%{query}%"), 3), else_=0)
        rank = rank + func.similarity(Chunk.content, query)
    else:
        # search_vector 的构建配置可能是 english 或 simple；两套都保留兜底，
        # 同时用实词 OR，避免 “how do...” 这类带停用词的整句 tsquery 归零。
        tsq = func.websearch_to_tsquery("english", query)
        clauses = [Chunk.search_vector.op("@@")(tsq)]
        clauses.extend(_term_clauses(terms))
        clauses.append(Chunk.content.ilike(f"%{query}%"))
        q = q.filter(or_(*clauses))
        rank = func.ts_rank_cd(Chunk.search_vector, tsq) + _term_hit_expr(terms) * 0.5
        rank = rank + func.similarity(Chunk.content, query)
    q = q.order_by(rank.desc()).limit(max(req.top_k * 3, 30))

    titles = {
        str(r[0]): r[1]
        for r in db.query(Document.id, Document.title)
        .filter(Document.id.in_(doc_ids)).all()
    }
    out = []
    snippet_chars = _snippet_chars(req.query)
    for chunk in q.all():
        out.append({
            "chunk_id": str(chunk.id), "doc_id": str(chunk.document_id),
            "kb_id": str(chunk.kb_id), "dept_id": str(chunk.department_id or ""),
            "sensitivity": chunk.sensitivity, "source": "keyword",
            "document_title": titles.get(str(chunk.document_id), ""),
            "page_no": chunk.page_no, "section_path": chunk.section_path,
            "snippet": chunk.content[:snippet_chars], "acl": chunk.acl,
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
    snippet_chars = _snippet_chars(req.query)
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
            "snippet": chunk.content[:snippet_chars], "acl": chunk.acl,
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
    """把分组结果摊平成带编号的引用列表，供 Agent 编排层拼 Prompt。

    这里不再把所有 hit 的所有 citation 都摊平：答案上下文按文档预算裁剪，
    避免“每篇 5 条 + 全局 12 条”的双重噪声。
    """
    citations = []
    for hit in response.hits:
        citations.extend(c.model_dump() for c in hit.citations)
    return select_answer_context(citations)
