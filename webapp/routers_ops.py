"""工作台/治理类路由：概览、检索调试、成员、审计台账。

这些入口对应生产骨架 README §4 已有的模块（看板 / 任务 / 审计 / 组织），
演示切片用 SQLite 现有表真实聚合，不造数据；纯设计项在 UI 里明确标注。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from webapp import db, queue, rag
from webapp.deps import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["ops"])

# 评测中心直接读 eval/ 的注册表作为事实源，避免页面写死后与评测体系不一致。
REGISTRY = Path(__file__).resolve().parent.parent / "eval" / "config" / "registry.json"


@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    """工作台概览。普通用户只看自己的数据，admin 额外拿到全局完成度与成员数。"""
    is_admin = user["role"] == "admin"
    conn = db.get_conn()
    try:
        where = "" if is_admin else " WHERE owner_id=?"
        p = [] if is_admin else [user["id"]]
        doc_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM documents" + where + " GROUP BY status", p
        ).fetchall()
        docs = {r["status"]: r["c"] for r in doc_rows}
        chunks = conn.execute(
            "SELECT COUNT(*) AS c FROM chunks c JOIN documents d ON d.id=c.doc_id"
            + (" WHERE d.status='ready'" if is_admin else " WHERE d.status='ready' AND d.owner_id=?"),
            [] if is_admin else [user["id"]],
        ).fetchone()["c"]
        chat_rows = conn.execute(
            "SELECT COUNT(DISTINCT conversation_id) AS conv, COUNT(*) AS msg,"
            " SUM(degraded) AS degraded FROM chat_logs"
            + ("" if is_admin else " WHERE user_id=?"),
            [] if is_admin else [user["id"]],
        ).fetchone()
        bc = conn.execute(
            "SELECT COUNT(*) AS c FROM badcases"
            + ("" if is_admin else " WHERE created_by=?"),
            [] if is_admin else [user["id"]],
        ).fetchone()["c"]
        extra = None
        if is_admin:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            audits = conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"]
            target = conn.execute(
                "SELECT COALESCE(SUM(target_count),0) AS c FROM eval_tasks"
            ).fetchone()["c"]
            done = conn.execute("SELECT COUNT(*) AS c FROM badcases").fetchone()["c"]
            extra = {
                "user_count": users,
                "audit_count": audits,
                "eval_target": target,
                "eval_done": done,
                "completion_rate": round(done / target, 4) if target else 0.0,
            }
    finally:
        conn.close()
    return {
        "user": {"username": user["username"], "role": user["role"]},
        "scope": "全局（admin）" if is_admin else "仅本人数据",
        "docs": {
            "total": sum(docs.values()),
            "ready": docs.get("ready", 0),
            "queued": docs.get("queued", 0),
            "analyzing": docs.get("analyzing", 0),
            "error": docs.get("error", 0),
        },
        "chunks": chunks,
        "chat": {
            "conversations": chat_rows["conv"],
            "messages": chat_rows["msg"],
            "degraded": chat_rows["degraded"] or 0,
        },
        "badcases": bc,
        "system": queue.get_busy_state(),
        "admin": extra,
    }


@router.get("/search/preview")
def search_preview(q: str = Query(..., min_length=1), top_k: int = Query(4, ge=1, le=10),
                   user: dict = Depends(get_current_user)):
    """检索调试：只召回不生成，把命中分块与降级判定摊开。"""
    result = rag.search_debug(q, top_k)
    db.log_action(user["username"], user["role"], "search", q[:80],
                  f"hit_count={result['hit_count']}, degraded={result['degraded']}")
    return result


@router.get("/eval/objects")
def eval_objects(_: dict = Depends(get_current_user)):
    """版本化对象清单，直接读 eval/config/registry.json（与评测体系同一事实源）。"""
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {
        "registry_id": data.get("registry_id"),
        "registry_version": data.get("version"),
        "count": len(data.get("objects", [])),
        "objects": data.get("objects", []),
        "version_policy": data.get("version_policy", {}),
        "comparable": False,
        "quality_verdict": "NOT_COMPARABLE",
        "note": "占位档位 offline_placeholder；真实 API 字段仍为 __TBC__，禁止据此冻结质量阈值",
    }


@router.get("/admin/users")
def list_users(_: dict = Depends(require_admin)):
    """成员与角色（对应 README §4 的 /users、/departments，切片只做最小集）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id,username,email,role,created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            dc = conn.execute(
                "SELECT COUNT(*) AS c FROM documents WHERE owner_id=?", (r["id"],)
            ).fetchone()["c"]
            bc = conn.execute(
                "SELECT COUNT(*) AS c FROM badcases WHERE created_by=?", (r["id"],)
            ).fetchone()["c"]
            out.append({
                "id": r["id"], "username": r["username"], "email": r["email"],
                "role": r["role"], "created_at": r["created_at"],
                "doc_count": dc, "badcase_count": bc,
            })
    finally:
        conn.close()
    return out


@router.get("/admin/audit")
def list_audit(action: str | None = None, actor: str | None = None,
               limit: int = Query(200, le=1000), _: dict = Depends(require_admin)):
    """只追加审计台账：可按动作与操作人检索，验证「谁在什么时候做了什么」。"""
    sql = "SELECT id,actor,role,action,target,detail,created_at FROM audit_logs WHERE 1=1"
    params: list = []
    if action:
        sql += " AND action=?"; params.append(action)
    if actor:
        sql += " AND actor=?"; params.append(actor)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
