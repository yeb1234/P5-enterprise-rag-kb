"""P5 Knowledge MCP gateway.

A small HTTP/JSON-RPC surface for the P5 enterprise knowledge base.  It is
intentionally read-only: P10 consumes search and text-structure tools, while
P5 remains the owner of ingestion, permissions and index maintenance.

Protocol: MCP JSON-RPC 2.0 (initialize / tools/list / tools/call).  The transport
is plain HTTP because P5 and P10 run on the same host; a stdio wrapper can be
added later without changing the tool contract.
"""
from __future__ import annotations

import json
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import Action
from app.core.deps import Principal
from app.core.errors import Unauthorized
from app.db.session import get_db
from app.models.identity import Department, User
from app.models.knowledge import Category, Chunk, Document, KnowledgeBase
from app.schemas.search import SearchFilters, SearchRequest
from app.services.audit import record
from app.services.search import hybrid_search

router = APIRouter(prefix="/mcp", tags=["MCP"])

SERVER_INFO = {"name": "p5-knowledge-mcp", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-11-25"

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "knowledge.search",
        "description": "混合检索 P5 知识库，返回文档、片段、章节路径和来源分数",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "mode": {"type": "string", "enum": ["hybrid", "keyword", "semantic"]},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                "kb_ids": {"type": "array", "items": {"type": "string"}},
                "category_ids": {"type": "array", "items": {"type": "string"}},
                "include_descendants": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnly": True},
    },
    {
        "name": "knowledge.text_structure",
        "description": "读取 P5 的知识库、文档和片段结构；传 document_id 时返回该文档的段落结构",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "kb_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnly": True},
    },
]


def _jsonrpc_result(req_id: Any, result: Any, *, is_error: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if is_error:
        payload["error"] = result
    else:
        payload["result"] = result
    return payload


def _text_result(data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
        "structuredContent": data,
    }


def _check_key(request: Request) -> None:
    if not settings.MCP_ENABLED:
        raise Unauthorized("P5 MCP 未启用")
    expected = settings.MCP_SERVICE_KEY
    if not expected:
        raise Unauthorized("P5 MCP 服务密钥未配置")
    supplied = request.headers.get("X-P5-MCP-Key", "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise Unauthorized("P5 MCP 服务密钥无效")


def _principal(db: Session) -> Principal:
    try:
        tenant_id = uuid.UUID(settings.MCP_TENANT_ID)
        user_id = uuid.UUID(settings.MCP_USER_ID)
    except (TypeError, ValueError) as exc:
        raise Unauthorized("P5 MCP 服务主体未配置") from exc
    user = (
        db.query(User)
        .filter(User.id == user_id, User.tenant_id == tenant_id, User.status == "active")
        .first()
    )
    if user is None:
        raise Unauthorized("P5 MCP 服务主体不可用")
    department = (
        db.query(Department).filter(Department.id == user.department_id).first()
        if user.department_id
        else None
    )
    return Principal(
        id=user.id,
        tenant_id=user.tenant_id,
        department_id=user.department_id,
        role=user.role,
        email=user.email,
        department_path=department.path if department else None,
    )


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _doc_brief(doc: Document, kb_name: str | None = None) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "kb_id": str(doc.kb_id),
        "kb_name": kb_name,
        "title": doc.title,
        "status": doc.status,
        "sensitivity": doc.sensitivity,
        "page_count": doc.page_count,
        "chunk_count": doc.chunk_count,
        "category_id": str(doc.category_id) if doc.category_id else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def _structure(principal: Principal, db: Session, args: dict[str, Any]) -> dict[str, Any]:
    kb_id = _as_uuid(args.get("kb_id"))
    document_id = _as_uuid(args.get("document_id"))
    limit = max(1, min(int(args.get("limit") or 200), 200))
    kbs = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.tenant_id == principal.tenant_id, KnowledgeBase.is_deleted.is_(False))
        .order_by(KnowledgeBase.name)
        .all()
    )
    kb_names = {str(kb.id): kb.name for kb in kbs}
    categories = (
        db.query(Category)
        .filter(Category.tenant_id == principal.tenant_id)
        .order_by(Category.sort_order, Category.name)
        .all()
    )

    if document_id:
        doc = (
            db.query(Document)
            .filter(
                Document.id == document_id,
                Document.tenant_id == principal.tenant_id,
                Document.is_deleted.is_(False),
            )
            .first()
        )
        if doc is None:
            return {"ok": False, "error": "文档不存在或不可见"}
        chunks = (
            db.query(Chunk)
            .filter(
                Chunk.document_id == doc.id,
                Chunk.tenant_id == principal.tenant_id,
                Chunk.is_deleted.is_(False),
            )
            .order_by(Chunk.seq)
            .all()
        )
        return {
            "ok": True,
            "document": _doc_brief(doc, kb_names.get(str(doc.kb_id))),
            "chunks": [
                {
                    "id": str(chunk.id),
                    "seq": chunk.seq,
                    "section_path": chunk.section_path,
                    "block_type": chunk.block_type,
                    "source_level": chunk.source_level,
                    "page_no": chunk.page_no,
                    "token_count": chunk.token_count,
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
            "knowledge_bases": [
                {"id": str(kb.id), "name": kb.name, "visibility": kb.visibility}
                for kb in kbs
            ],
            "categories": [
                {
                    "id": str(category.id),
                    "name": category.name,
                    "kb_id": str(category.kb_id) if category.kb_id else None,
                    "parent_id": str(category.parent_id) if category.parent_id else None,
                    "level": category.level,
                    "path_ids": [str(item) for item in category.path_ids],
                }
                for category in categories
            ],
        }

    docs_query = db.query(Document).filter(
        Document.tenant_id == principal.tenant_id,
        Document.is_deleted.is_(False),
    )
    if kb_id:
        docs_query = docs_query.filter(Document.kb_id == kb_id)
    docs = docs_query.order_by(Document.created_at.desc()).limit(limit).all()
    return {
        "ok": True,
        "knowledge_bases": [
            {"id": str(kb.id), "name": kb.name, "visibility": kb.visibility}
            for kb in kbs
        ],
        "documents": [_doc_brief(doc, kb_names.get(str(doc.kb_id))) for doc in docs],
        "documents_total": docs_query.count(),
        "categories": [
            {
                "id": str(category.id),
                "name": category.name,
                "kb_id": str(category.kb_id) if category.kb_id else None,
                "parent_id": str(category.parent_id) if category.parent_id else None,
                "level": category.level,
                "path_ids": [str(item) for item in category.path_ids],
            }
            for category in categories
        ],
    }


def _search(principal: Principal, db: Session, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query 不能为空"}
    kb_ids = [uuid.UUID(str(item)) for item in (args.get("kb_ids") or []) if _as_uuid(item)]
    category_ids = [uuid.UUID(str(item)) for item in (args.get("category_ids") or []) if _as_uuid(item)]
    req = SearchRequest(
        query=query,
        mode=str(args.get("mode") or "hybrid"),
        top_k=max(1, min(int(args.get("top_k") or 8), 20)),
        page_size=max(1, min(int(args.get("top_k") or 8), 20)),
        filters=SearchFilters(
            kb_ids=kb_ids,
            category_ids=category_ids,
            include_descendants=bool(args.get("include_descendants", True)),
        ),
    )
    response = hybrid_search(db, principal, req)
    flat: list[dict[str, Any]] = []
    for hit in response.hits:
        for citation in hit.citations:
            flat.append(
                {
                    "chunk_id": citation.chunk_id,
                    "document_id": citation.document_id,
                    "title": citation.document_title,
                    "content": citation.snippet,
                    "snippet": citation.snippet,
                    "section_path": citation.section_path,
                    "page_no": citation.page_no,
                    "score": citation.score,
                    "matched_by": hit.matched_by,
                    "source": citation.source,
                }
            )
    return {
        "ok": True,
        "query": response.query,
        "mode": response.mode,
        "total": response.total,
        "took_ms": response.took_ms,
        "cache_hit": response.cache_hit,
        "hits": flat,
    }


def _call_tool(principal: Principal, db: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "knowledge.search":
        return _search(principal, db, args)
    if name == "knowledge.text_structure":
        return _structure(principal, db, args)
    return {"ok": False, "error": f"未知工具：{name}"}


@router.post("", summary="P5 Knowledge MCP JSON-RPC 入口")
def mcp_entrypoint(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    _check_key(request)
    method = str(payload.get("method") or "")
    req_id = payload.get("id")

    if method == "initialize":
        return _jsonrpc_result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return Response(status_code=202)

    if method == "tools/list":
        return _jsonrpc_result(req_id, {"tools": _TOOLS})

    if method != "tools/call":
        return _jsonrpc_result(
            req_id,
            {"code": -32601, "message": f"Method not found: {method}"},
            is_error=True,
        )

    params = payload.get("params") or {}
    name = str(params.get("name") or "")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return _jsonrpc_result(
            req_id,
            {"code": -32602, "message": "tools/call arguments 必须是对象"},
            is_error=True,
        )

    principal = _principal(db)
    result = _call_tool(principal, db, name, args)
    record(
        db,
        tenant_id=principal.tenant_id,
        user_id=principal.id,
        action=Action.READ.value,
        resource_type="mcp",
        result="success" if result.get("ok") else "failed",
        detail={"tool": name, "query": args.get("query"), "document_id": args.get("document_id")},
        meta={"ip": request.client.host if request and request.client else None, "trace_id": getattr(request.state, "trace_id", None)},
        commit=True,
    )
    if not result.get("ok"):
        return _jsonrpc_result(
            req_id,
            {"code": -32000, "message": str(result.get("error") or "tool failed")},
            is_error=True,
        )
    return _jsonrpc_result(req_id, _text_result(result))
