"""混合检索与流式问答（SSE）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.orchestrator import build_state, get_orchestrator
from app.core.deps import CurrentUser, DbSession, request_meta
from app.schemas.search import AnswerRequest, SearchRequest, SearchResponse
from app.services.audit import record
from app.services.search import hybrid_search

router = APIRouter(prefix="/search", tags=["检索与问答"])


@router.post("", response_model=SearchResponse, summary="混合检索（PG 全文 + Milvus 语义 + 属性筛选）")
def search(payload: SearchRequest, request: Request, db: DbSession, user: CurrentUser):
    response = hybrid_search(db, user, payload)
    response.trace_id = getattr(request.state, "trace_id", None)
    if payload.enable_answer:
        state = build_state(user, payload.query, response.trace_id)
        orchestrator = get_orchestrator()
        state.candidates = [c.model_dump() for h in response.hits for c in h.citations]
        response.answer = orchestrator.run(state, db, user).answer
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="search",
           resource_type="document", result="success",
           detail={"query_length": len(payload.query), "mode": payload.mode, "total": response.total},
           meta=request_meta(request), commit=True)
    return response


@router.post("/answer", summary="RAG 流式问答（SSE，逐 token 输出）")
def answer(payload: AnswerRequest, request: Request, db: DbSession, user: CurrentUser):
    trace_id = getattr(request.state, "trace_id", None)
    state = build_state(user, payload.query, payload.filters.model_dump(mode="json"), trace_id)
    orchestrator = get_orchestrator()

    def event_stream():
        # 先给一次 meta，让前端立刻拿到 trace_id 与引用列表占位
        yield f"event: meta\ndata: {json.dumps({'trace_id': trace_id}, ensure_ascii=False)}\n\n"
        try:
            for delta in orchestrator.stream(state, db, user):
                yield "event: token\ndata: " + json.dumps({"text": delta}, ensure_ascii=False) + "\n\n"
            done = {
                "answer": state.answer,
                "blocked": state.blocked,
                "citations": state.citations[:12],
                "intent": state.intent,
                "metrics": state.metrics,
            }
            yield "event: done\ndata: " + json.dumps(done, ensure_ascii=False, default=str) + "\n\n"
        except Exception as exc:
            yield "event: error\ndata: " + json.dumps({"message": str(exc)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
