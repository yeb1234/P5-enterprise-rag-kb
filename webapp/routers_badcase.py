"""Badcase 路由：采集（任意登录用户）、查看/筛选/导出/改状态（仅 admin）。

闭环：chat 页“标记 badcase” → POST 落库（关联评估任务）→ admin 后台按条件检索/导出。
数据真值在 SQLite；评估任务完成度由 badcases 关联计数派生（见 routers_admin）。
"""
from __future__ import annotations

import csv
import io
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from guardrails import default as guard_default, export_rows
from webapp import db, security
from webapp.deps import get_current_user, require_admin
from webapp.schemas import BadcaseCreate, BadcaseUpdate

router = APIRouter(prefix="/api/badcases", tags=["badcases"])

# badcase 里最可能带用户输入（也最容易被导出）的字段
_TEXT_FIELDS = ("question", "answer", "issue_desc", "source")


def _safe_fields(d: dict, source: str) -> dict:
    """写入 / 回显前统一过 audit 出口；只改字符串字段，None 保持原语义。"""
    g = guard_default()
    out = dict(d)
    for k in _TEXT_FIELDS:
        v = out.get(k)
        if not isinstance(v, str) or not v:
            continue
        r = g.audit(v)
        out[k] = r.text
        db.record_safety_event(r, source)
    return out


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "eval_task_id": r["eval_task_id"],
        "question": r["question"],
        "answer": r["answer"],
        "verdict": r["verdict"],
        "issue_desc": r["issue_desc"],
        "source": r["source"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def _query(filters: dict, limit: int = 2000) -> list[dict]:
    sql = "SELECT * FROM badcases WHERE 1=1"
    params: list = []
    if filters.get("eval_task_id"):
        sql += " AND eval_task_id=?"; params.append(filters["eval_task_id"])
    if filters.get("status"):
        sql += " AND status=?"; params.append(filters["status"])
    if filters.get("source"):
        sql += " AND source=?"; params.append(filters["source"])
    if filters.get("start"):
        sql += " AND created_at>=?"; params.append(filters["start"])
    if filters.get("end"):
        sql += " AND created_at<=?"; params.append(filters["end"] + "T23:59:59Z")
    if filters.get("q"):
        like = f"%{filters['q']}%"
        sql += " AND (question LIKE ? OR answer LIKE ? OR issue_desc LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@router.post("", status_code=201)
def create_badcase(body: BadcaseCreate, user: dict = Depends(get_current_user)):
    conn = db.get_conn()
    try:
        task = conn.execute(
            "SELECT id FROM eval_tasks WHERE id=?", (body.eval_task_id,)
        ).fetchone()
        if not task:
            raise HTTPException(status_code=400, detail="关联的评估任务不存在")
        bid = security.new_id()
        now = _now()
        # 采集即脱敏：badcase 是「会被 admin 查看并导出」的数据，库里就不该存明文
        safe = _safe_fields(
            {"question": body.question, "answer": body.answer,
             "issue_desc": body.issue_desc, "source": body.source},
            f"badcase:{bid[:8]}",
        )
        conn.execute(
            "INSERT INTO badcases(id,eval_task_id,question,answer,verdict,issue_desc,source,status,created_by,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (bid, body.eval_task_id, safe["question"], safe["answer"], body.verdict,
             safe["issue_desc"], safe["source"], "pending", user["id"], now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM badcases WHERE id=?", (bid,)).fetchone()
    finally:
        conn.close()
    db.log_action(user["username"], user["role"], "badcase", bid[:8],
                  f"采集 badcase → {body.eval_task_id}")
    return _row_to_dict(row)


@router.get("")
def list_badcases(
    eval_task_id: str | None = None,
    status: str | None = None,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    limit: int = Query(200, le=2000),
    _: dict = Depends(require_admin),
):
    # 兼容升级前已落库的明文数据：回显前再过一次（已脱敏文本不会二次命中）
    return [_safe_fields(r, "badcase:list") for r in _query(
        {
            "eval_task_id": eval_task_id,
            "status": status,
            "source": source,
            "start": start,
            "end": end,
            "q": q,
        },
        limit=limit,
    )]


@router.patch("/{badcase_id}")
def update_badcase(badcase_id: str, body: BadcaseUpdate, _: dict = Depends(require_admin)):
    fields, params = [], []
    if body.verdict is not None:
        fields.append("verdict=?"); params.append(body.verdict)
    if body.issue_desc is not None:
        r = guard_default().audit(body.issue_desc)
        db.record_safety_event(r, f"badcase:{badcase_id[:8]}")
        fields.append("issue_desc=?"); params.append(r.text)
    if body.status is not None:
        fields.append("status=?"); params.append(body.status)
    if not fields:
        raise HTTPException(status_code=400, detail="无更新字段")
    fields.append("updated_at=?"); params.append(_now())
    params.append(badcase_id)
    conn = db.get_conn()
    try:
        conn.execute(f"UPDATE badcases SET {','.join(fields)} WHERE id=?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM badcases WHERE id=?", (badcase_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.log_action(_["username"], _["role"], "badcase", badcase_id[:8],
                  f"更新判定/状态 → {body.status or '字段'}")
    return _row_to_dict(row)


@router.get("/export")
def export_badcases(
    format: str = Query("csv", pattern="^(csv|json)$"),
    eval_task_id: str | None = None,
    status: str | None = None,
    source: str | None = None,
    start: str | None = None,
    end: str | None = None,
    q: str | None = None,
    _: dict = Depends(require_admin),
):
    # 日志与导出前出口（GATE_AUDIT）：CSV / JSON 都走同一份规则，
    # 不因格式不同而绕过。库里已是脱敏文本，这里是第二道兜底。
    rows = export_rows(_query(
        {
            "eval_task_id": eval_task_id,
            "status": status,
            "source": source,
            "start": start,
            "end": end,
            "q": q,
        }
    ), fields=_TEXT_FIELDS)
    db.log_action(_["username"], _["role"], "export", f"{format}",
                  f"按当前筛选导出 {len(rows)} 条")
    if format == "json":
        content = json.dumps(rows, ensure_ascii=False, indent=2)
        return Response(
            content, media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=badcases.json"},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "eval_task_id", "question", "answer", "verdict",
         "issue_desc", "source", "status", "created_by", "created_at"]
    )
    for r in rows:
        writer.writerow(
            [r["id"], r["eval_task_id"], r["question"], r["answer"], r["verdict"],
             r["issue_desc"], r["source"], r["status"], r["created_by"], r["created_at"]]
        )
    return Response(
        buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=badcases.csv"},
    )
