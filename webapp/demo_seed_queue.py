"""造/清「队列积压」演示数据，用于观察按用户、按部门的聚合视图。

为什么单独一个脚本而不是接口：这些文档是**静态种子**，不会被进程内队列真正处理，
若做成 admin 接口容易被误当成真实任务。放在脚本里、文件名带固定前缀，可一键清理。

用法（宿主机数据库）：
    python webapp/demo_seed_queue.py --seed
    python webapp/demo_seed_queue.py --clean

用法（Docker 容器内的库）：
    docker exec kb-webapp python -c "import sys; sys.path.insert(0,'/app'); \
      from webapp.demo_seed_queue import main; main(['--seed','--db','/app/.data/webapp/app.db'])"

说明：种子里两个演示用户（`queue_demo_a` / `queue_demo_b`）口令都是 `demo1234`，
部门分别是「研发部」「交付部」；文档名统一 `QUEUE-DEMO-*.pdf`，`--clean` 按前缀清理。

注意：服务/容器重启时，启动自检会把**所有** `queued` 任务幂等重投（并发上限 2），
这些种子没有真实上传文件，会被逐条转成 `error`（约每 2 条约 1.8 秒）。
所以种子是「演示前生成、演示后清理」的临时数据，不要当成长期演示资产；
需要时重新 `--seed` 即可。
"""
from __future__ import annotations

import argparse
import hashlib
import time
import uuid

PREFIX = "QUEUE-DEMO-"
DEMO_USERS = (
    ("queue_demo_a", "研发部", 100),
    ("queue_demo_b", "交付部", 30),
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_user(conn, username: str, department: str) -> str:
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row:
        uid = row[0]
        conn.execute("UPDATE users SET department=? WHERE id=?", (department, uid))
        return uid
    uid = uuid.uuid4().hex
    salt = uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac("sha256", b"demo1234", bytes.fromhex(salt), 100_000).hex()
    conn.execute(
        "INSERT INTO users(id,username,email,password_salt,password_hash,role,department,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (uid, username, f"{username}@demo.local", salt, dk, "user", department, _now()),
    )
    return uid


def seed() -> None:
    from webapp import db

    conn = db.get_conn()
    try:
        total = 0
        for username, dept, n in DEMO_USERS:
            uid = _ensure_user(conn, username, dept)
            now = _now()
            for i in range(n):
                conn.execute(
                    "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, uid, f"{PREFIX}{username}-{i:03d}.pdf",
                     f"{PREFIX}{username}-{i:03d}.pdf", 2048, "queued", now, now),
                )
            total += n
            print(f"  {username:<14} {dept:<6} queued={n}")
        conn.commit()
        print(f"已生成 {total} 条演示积压（文件名前缀 {PREFIX}）；它们不会被真正处理，仅用于观察聚合视图。")
    finally:
        conn.close()


def clean() -> None:
    from webapp import db

    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM documents WHERE filename LIKE ?", (f"{PREFIX}%",))
        users = conn.execute(
            "DELETE FROM users WHERE username IN (?,?)",
            tuple(u for u, _, _ in DEMO_USERS),
        )
        conn.commit()
        print(f"已清理 {cur.rowcount} 条文档、{users.rowcount} 个演示用户。")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="队列积压演示种子")
    ap.add_argument("--schema", default="", help="可选：指定 PG schema（测试隔离用），默认走 search_path")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_true", help="生成演示数据")
    group.add_argument("--clean", action="store_true", help="按前缀清理")
    args = ap.parse_args(argv)

    if args.schema:
        import os
        os.environ["WEBAPP_DB_SCHEMA"] = args.schema
    from webapp import pgkit
    print(f"目标库：{pgkit.dsn()}" + (f" · schema={args.schema}" if args.schema else ""))
    seed() if args.seed else clean()


if __name__ == "__main__":
    main()
