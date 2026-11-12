"""Admin 路由：评估任务完成度总览（任务总数 / 已完成 / 待处理 / 完成率 / 状态）。

完成度口径（演示约定，已在 UI 注明）：
- 每个评估任务有目标样本数 target_count；
- 一条关联该任务的 badcase 记录即视为“已评测一个样本”；
- 已完成 = 关联 badcase 数；待处理 = max(0, target - 已完成)；完成率 = 已完成 / target。
这样 badcase 采集与评估任务进度天然关联一致，管理员一眼看到各任务进展。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from webapp import db
from webapp.deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/completion")
def completion(_: dict = Depends(require_admin)):
    conn = db.get_conn()
    try:
        tasks = conn.execute(
            "SELECT id,name,description,status,target_count,created_at FROM eval_tasks"
        ).fetchall()
        rows = []
        tot_target = tot_done = 0
        for t in tasks:
            target = t["target_count"]
            recorded = conn.execute(
                "SELECT COUNT(*) AS c FROM badcases WHERE eval_task_id=?",
                (t["id"],),
            ).fetchone()["c"]
            done = recorded
            pending = max(0, target - done)
            rate = round(done / target, 4) if target else 0.0
            tot_target += target
            tot_done += done
            rows.append(
                {
                    "eval_task_id": t["id"],
                    "name": t["name"],
                    "description": t["description"],
                    "status": t["status"],
                    "target_count": target,
                    "completed": done,
                    "pending": pending,
                    "completion_rate": rate,
                }
            )
    finally:
        conn.close()
    overall_rate = round(tot_done / tot_target, 4) if tot_target else 0.0
    return {
        "tasks": rows,
        "overall": {
            "target_total": tot_target,
            "completed_total": tot_done,
            "pending_total": max(0, tot_target - tot_done),
            "completion_rate": overall_rate,
        },
    }
