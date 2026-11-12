"""文档路由：上传（含繁忙排队与降级）、列表、删除，以及系统繁忙状态。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from webapp import db, queue, rag, security
from webapp.deps import get_current_user

router = APIRouter(prefix="/api", tags=["docs"])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@router.post("/docs/upload")
def upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名缺失")
    size = 0
    doc_id = security.new_id()
    target = rag.files_for(doc_id, file.filename)
    with open(target, "wb") as f:
        while chunk := file.file.read(1024 * 256):
            size += len(chunk)
            f.write(chunk)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, user["id"], file.filename, file.filename, size, "queued",
             _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()
    # 进入繁忙调度（进程内信号量即 Celery+Redis 的演示替身）
    queue.enqueue(doc_id, file.filename)
    db.log_action(user["username"], user["role"], "upload", file.filename,
                  f"size={size}B, 进入分析队列")
    return get_doc(doc_id)


def get_doc(doc_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    return _row_to_dict(row)


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "filename": row["filename"],
        "title": row["title"],
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "chunk_count": row["chunk_count"],
        "error_msg": row["error_msg"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/docs")
def list_docs(user: dict = Depends(get_current_user)):
    conn = db.get_conn()
    try:
        if user["role"] == "admin":
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE owner_id=? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str, user: dict = Depends(get_current_user)):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT owner_id FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="文档不存在")
        if user["role"] != "admin" and row["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="无权删除该文档")
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()
    finally:
        conn.close()
    # 清理磁盘文件（软删的真实落地）
    for p in rag.UPLOAD_DIR.glob(f"{doc_id}_*"):
        try:
            p.unlink()
        except OSError:
            pass
    db.log_action(user["username"], user["role"], "delete", doc_id[:8], "删除文档与分块")
    return {"ok": True}


@router.get("/system/busy")
def system_busy():
    return queue.get_busy_state()


@router.get("/eval-tasks")
def list_eval_tasks(user: dict = Depends(get_current_user)):
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id,name,description,status,target_count,created_at FROM eval_tasks "
            "ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "status": r["status"],
            "target_count": r["target_count"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.post("/docs/seed-sample")
def seed_sample(user: dict = Depends(get_current_user)):
    """一键造一份示例说明书，便于演示检索/流式/badcase 闭环。"""
    content = (
        "智能助手产品说明书\n"
        "一、开机与配网：长按电源键 3 秒开机，进入设置-网络，选择 Wi-Fi 并完成配网。\n"
        "二、账号绑定：扫码绑定企业账号后，可在知识库中上传产品文档。\n"
        "三、问答流程：用户提问后，系统先检索知识库分块，再生成带引用的回答。\n"
        "四、常见问题：若回答为空，请检查文档是否已上传且分析完成。\n"
        "五、权限说明：badcase 记录仅管理员可在管理后台查看与导出。\n"
    )
    doc_id = security.new_id()
    fname = "sample-manual.txt"
    (rag.UPLOAD_DIR / f"{doc_id}_{fname}").write_text(content, encoding="utf-8")
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, user["id"], fname, fname, len(content.encode()), "queued",
             _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()
    queue.enqueue(doc_id, fname)
    db.log_action(user["username"], user["role"], "upload", fname, "一键生成示例说明书")
    return get_doc(doc_id)
