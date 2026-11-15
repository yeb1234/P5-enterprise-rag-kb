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

from guardrails import RULE_VERSION, default as guard_default
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
        # 新鲜度治理开启时带上（关闭时为 None），供来源卡片与 meta 事件展示
        "freshness": h.get("freshness"),
        "effective_score": h.get("effective_score"),
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
    # 配额隔离：进模型前先查预算（用户级优先，回退全局）。
    # policy=block 且本人已超额 → 结构化拒绝，绝不在超支情况下静默继续消耗额度；
    # 用户 A 超支不影响用户 B（各自按 usage_events.user_id 聚合）。
    from webapp import observability as obs

    budget = obs.check_budget(actor=user["username"], role=user["role"],
                              user_id=user["id"])
    if not budget["allowed"]:
        # 结构化拒绝 + 审计留痕：不在超支时静默继续消耗额度。
        # 流式端点用 SSE **首个 error 事件**告知（前端本就按 event 解析，比裸 JSON 更契合），
        # 同时写审计台账，便于事后追责与成本核对。
        tid = security.new_id()[:16]
        db.log_action(user["username"], user["role"], "budget_block", f"chat/{tid}",
                      f"scope={budget['scope']} spend={budget['spend_cny']} "
                      f"limit={budget['limit_cny']} policy={budget['policy']}")
        blocked = {
            "ok": False,
            "reason": "budget_exceeded",
            "trace_id": tid,
            "budget": {
                "scope": budget["scope"],
                "overridden": budget["overridden"],
                "spend_cny": budget["spend_cny"],
                "limit_cny": budget["limit_cny"],
                "remaining_cny": budget["remaining_cny"],
                "policy": budget["policy"],
            },
        }

        async def _blocked_gen():
            yield _sse("error", blocked)

        return StreamingResponse(
            _blocked_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                     "Connection": "keep-alive"},
        )
    conv_id = body.conversation_id or security.new_id()
    g = guard_default()
    # 检索必须用原文：脱敏后的问题会破坏词法召回，顺序是「先检索、后脱敏」。
    retrieved = rag.retrieve(body.question)
    degraded = len(retrieved) == 0
    degrade_reason = "retrieval_empty" if degraded else None
    state = {"degraded": degraded, "degrade_reason": degrade_reason}

    # 进模型前出口（GATE_CONTEXT）：问题与检索片段在送进 LLM / 落库之前统一脱敏。
    q_result = g.context(body.question or "")
    safe_question = q_result.text
    db.record_safety_event(q_result, f"chat:{conv_id[:8]}")
    sources_raw = [_source_dict(h) for h in retrieved]
    sources, src_report = g.sources(sources_raw)
    for r in src_report.results:
        db.record_safety_event(r, f"chat:{conv_id[:8]}")
    # sources 的字段与 _source_dict 完全同构，直接作为送进模型的上下文，
    # 保证「来源卡片 / chat_sources / 模型输入」读到的是同一份脱敏文本。
    safe_hits = sources

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
        # 输出前出口（GATE_OUTPUT）：逐 token 过流式闸，跨 chunk 的标识符整串扣住。
        # 刻意保持「一个原始 token 对应一个 SSE 包」、index 仍用原始索引，
        # 否则断线续传 resume_from 的连续性契约会被打乱；被扣住的尾部由 done.tail 补齐。
        sr = g.stream()
        alive = True
        for i, item in enumerate(rag.stream_answer(safe_question, safe_hits, body.resume_from, state)):
            if await request.is_disconnected():
                alive = False
                break
            safe_token = sr.push(item["token"])
            full.append(safe_token)
            yield (
                "data: "
                + json.dumps(
                    {
                        "index": item["index"],
                        "token": safe_token,
                        "degraded": item["degraded"],
                        "degrade_reason": item["degrade_reason"],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            if i % 40 == 0:
                yield ": ping\n\n"  # SSE 保活心跳
        tail = sr.flush()
        full.append(tail)
        answer = "".join(full)
        if sr.by_kind:
            db.record_safety_counts("output", sr.by_kind, f"chat:{conv_id[:8]}",
                                    blocked=sr.blocked, action="mask",
                                    rule_version=RULE_VERSION)
        if alive:
            # 流式过程中可能触发 model_error 降级，以 state 的最终值为准
            final_degraded = state["degraded"]
            final_reason = state["degrade_reason"]
            yield _sse(
                "done",
                {
                    "conversation_id": conv_id,
                    "answer": answer,
                    "tail": tail,
                    "degraded": final_degraded,
                    "degrade_reason": final_reason,
                    "retrieved": len(retrieved),
                    "sources": sources,
                    "safety": sr.summary(),
                },
            )
            # 流正常收尾才落库；断线续传也会走到这里，靠 conv_id 去重避免重复写
            _persist(conv_id, user["id"], safe_question, answer, final_degraded, sources)
            db.log_action(user["username"], user["role"], "chat", conv_id[:8],
                          f"retrieved={len(retrieved)}, sources={len(sources)}, "
                          f"degraded={final_degraded}, reason={final_reason}")
            # 旁路记录 token 用量：估算口径明确标注，任何异常都不影响本次问答
            try:
                from webapp import observability as obs
                cfg = obs.get_config()["config"]
                prompt_text = safe_question + "\n" + "\n".join(
                    str(s.get("text") or "") for s in sources)
                obs.record_usage(
                    project=cfg.get("project", "default"),
                    department_id=cfg.get("department", "default"),
                    user_id=user["id"], call_type="chat", model=rag.get_model_label(),
                    prompt_text=prompt_text, completion_text=answer,
                    estimated=True, detail=f"conv={conv_id[:8]} degraded={final_degraded}")
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
            (security.new_id(), conv_id, user_id, "user", question, False, now),
        )
        assistant_id = security.new_id()
        conn.execute(
            "INSERT INTO chat_logs(id,conversation_id,user_id,role,content,degraded,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (assistant_id, conv_id, user_id, "assistant", answer, bool(degraded), now),
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
