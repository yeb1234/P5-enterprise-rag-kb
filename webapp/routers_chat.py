"""流式问答路由（SSE）。

降级与恢复设计（面试谈资）：
- 检索为空 → 占位生成器返回降级话术（degraded=true），前端提示“未命中知识库”；
- 全程 SSE 注释心跳（: ping）保活，客户端断开由 request.is_disconnected() 检测；
- 断线后客户端带 resume_from 重连，服务端跳过已发 token 续传（断线恢复），不重复落库。
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from webapp import db, rag, security
from webapp.deps import get_current_user
from webapp.schemas import ChatIn

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _source_dict(h: dict) -> dict:
    """把 retrieve 命中的分块整理成可序列化、可持久化的来源记录。"""
    return {
        "doc_id": h.get("doc_id"),
        "doc_title": h.get("doc_title"),
        "chunk_id": h.get("chunk_id"),
        "idx": h.get("idx"),
        "score": h.get("score"),
        "snippet": h.get("snippet"),
        "retrieval_mode": h.get("retrieval_mode"),
        "rerank": h.get("rerank"),
        "text": h.get("text"),
    }


def _row_to_source(r) -> dict:
    return {
        "doc_id": r["doc_id"], "doc_title": r["doc_title"], "chunk_id": r["chunk_id"],
        "idx": r["chunk_idx"], "score": r["score"], "snippet": r["snippet"],
        "retrieval_mode": r["retrieval_mode"], "rerank": r["rerank"], "text": r["chunk_text"],
    }


@router.post("/stream")
async def chat_stream(
    body: ChatIn, request: Request, user: dict = Depends(get_current_user)
):
    conv_id = body.conversation_id or security.new_id()
    retrieved = rag.retrieve(body.question)
    degraded = len(retrieved) == 0
    degrade_reason = "retrieval_empty" if degraded else None
    state = {"degraded": degraded, "degrade_reason": degrade_reason}
    # 把检索命中的来源整形成可序列化负载，随 meta/done 下发，并在流结束落库（溯源真值）
    sources = [_source_dict(h) for h in retrieved]

    async def gen():
        yield _sse(
            "meta",
            {
                "conversation_id": conv_id,
                "model": rag.get_model_label(),
                "retrieved": len(retrieved),
                "sources": sources,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "resume_from": body.resume_from,
            },
        )
        full: list[str] = []
        alive = True
        for i, item in enumerate(rag.stream_answer(body.question, retrieved, body.resume_from, state)):
            if await request.is_disconnected():
                alive = False
                break
            full.append(item["token"])
            yield (
                "data: "
                + json.dumps(
                    {
                        "index": item["index"],
                        "token": item["token"],
                        "degraded": item["degraded"],
                        "degrade_reason": item["degrade_reason"],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            if i % 40 == 0:
                yield ": ping\n\n"  # SSE 保活心跳
        if alive:
            # 流式过程中可能触发 model_error 降级，以 state 的最终值为准
            final_degraded = state["degraded"]
            final_reason = state["degrade_reason"]
            yield _sse(
                "done",
                {
                    "conversation_id": conv_id,
                    "answer": "".join(full),
                    "degraded": final_degraded,
                    "degrade_reason": final_reason,
                    "retrieved": len(retrieved),
                    "sources": sources,
                },
            )
            # 流正常收尾才落库；断线续传也会走到这里，靠 conv_id 去重避免重复写
            _persist(conv_id, user["id"], body.question, "".join(full), final_degraded, sources)
            db.log_action(user["username"], user["role"], "chat", conv_id[:8],
                          f"retrieved={len(retrieved)}, sources={len(sources)}, "
                          f"degraded={final_degraded}, reason={final_reason}")
            # 旁路记录 token 用量：估算口径明确标注，任何异常都不影响本次问答
            try:
                from webapp import observability as obs
                cfg = obs.get_config()["config"]
                prompt_text = body.question + "\n" + "\n".join(
                    str(s.get("text") or "") for s in sources)
                obs.record_usage(
                    project=cfg.get("project", "default"), tenant=cfg.get("tenant", "default"),
                    user_id=user["id"], call_type="chat", model=rag.get_model_label(),
                    prompt_text=prompt_text, completion_text="".join(full),
                    estimated=1, detail=f"conv={conv_id[:8]} degraded={final_degraded}")
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _persist(conv_id: str, user_id: str, question: str, answer: str, degraded: bool,
             sources: list[dict] | None = None) -> None:
    conn = db.get_conn()
    try:
        # 幂等：断线续传可能多次收尾，同一会话只落一次用户/助手消息
        exists = conn.execute(
            "SELECT 1 FROM chat_logs WHERE conversation_id=? AND role='user' LIMIT 1",
            (conv_id,),
        ).fetchone()
        if exists:
            return
        now = _now()
        conn.execute(
            "INSERT INTO chat_logs(id,conversation_id,user_id,role,content,degraded,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (security.new_id(), conv_id, user_id, "user", question, 0, now),
        )
        assistant_id = security.new_id()
        conn.execute(
            "INSERT INTO chat_logs(id,conversation_id,user_id,role,content,degraded,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (assistant_id, conv_id, user_id, "assistant", answer, 1 if degraded else 0, now),
        )
        # 溯源落库：把本次回答引用的分块写成 chat_sources，关联助手消息 id
        for i, s in enumerate(sources or []):
            conn.execute(
                "INSERT INTO chat_sources(id,conversation_id,msg_id,seq,doc_id,doc_title,"
                "chunk_id,chunk_idx,snippet,score,retrieval_mode,rerank,chunk_text,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (security.new_id(), conv_id, assistant_id, i, s.get("doc_id"), s.get("doc_title"),
                 s.get("chunk_id"), s.get("idx"), s.get("snippet"), s.get("score"),
                 s.get("retrieval_mode"), s.get("rerank"), s.get("text"), now),
            )
        conn.commit()
    finally:
        conn.close()


@router.get("/sources")
def chat_sources(conversation_id: str, user: dict = Depends(get_current_user)):
    """按会话取回该次回答引用的全部来源（溯源的真值读取）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_sources WHERE conversation_id=? ORDER BY seq", (conversation_id,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_source(r) for r in rows]


@router.get("/conversations")
def chat_conversations(user: dict = Depends(get_current_user)):
    """当前用户的历史会话列表（用于重新打开并追溯来源）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT conversation_id, role, content, created_at, user_id FROM chat_logs "
            "WHERE user_id=? ORDER BY conversation_id, created_at", (user["id"],)
        ).fetchall()
        src_rows = conn.execute(
            "SELECT conversation_id, COUNT(*) AS c FROM chat_sources "
            "WHERE conversation_id IN (SELECT DISTINCT conversation_id FROM chat_logs WHERE user_id=?) "
            "GROUP BY conversation_id", (user["id"],)
        ).fetchall()
    finally:
        conn.close()
    has_src = {r["conversation_id"]: r["c"] for r in src_rows}
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["conversation_id"], []).append(r)
    out = []
    for cid, msgs in groups.items():
        first_q = next((m["content"] for m in msgs if m["role"] == "user"), "")
        out.append({
            "conversation_id": cid,
            "first_question": first_q,
            "last_at": msgs[-1]["created_at"],
            "msg_count": len(msgs),
            "has_sources": has_src.get(cid, 0) > 0,
        })
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return out


@router.get("/history")
def chat_history(conversation_id: str, user: dict = Depends(get_current_user)):
    """取回某一会话的完整消息与每条助手回答的来源，供前端重新渲染与追溯。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, role, content, degraded, created_at, user_id FROM chat_logs "
            "WHERE conversation_id=? ORDER BY created_at", (conversation_id,)
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="会话不存在")
        if rows[0]["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="无权查看该会话")
        src_rows = conn.execute(
            "SELECT * FROM chat_sources WHERE conversation_id=? ORDER BY seq", (conversation_id,)
        ).fetchall()
    finally:
        conn.close()
    src_map: dict[str, list] = {}
    for s in src_rows:
        src_map.setdefault(s["msg_id"], []).append(_row_to_source(s))
    messages = []
    for m in rows:
        msg = {
            "role": m["role"],
            "content": m["content"],
            "degraded": bool(m["degraded"]),
            "created_at": m["created_at"],
        }
        if m["role"] == "assistant":
            msg["sources"] = src_map.get(m["id"], [])
        messages.append(msg)
    return {"conversation_id": conversation_id, "messages": messages}
