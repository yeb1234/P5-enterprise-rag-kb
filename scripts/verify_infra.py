"""本机基础设施自检：PostgreSQL 与 Redis 是否真的能用（生产化第 1/3 项的现场证据）。

为什么单独写一个脚本：
- `01-data-migration.md` 的迁移脚本只负责「搬数据」，不负责「证明搬对了」；
- `03-ratelimit-and-metrics.md` 讲「多副本不能用进程内窗口」，但这件事**只在真 Redis 上才测得出**。
  用两个独立进程各打同一个 key，内存实现会各算各的（配额翻倍），Redis 实现共享同一窗口。

用法：
    # PG + Redis 都在本机（宿主端口按需覆盖）
    python scripts/verify_infra.py \
      --pg-dsn postgresql://kb:kb_demo_only@127.0.0.1:55432/kb \
      --redis-url redis://127.0.0.1:56379/0

    # 只验 PG / 只验 Redis
    python scripts/verify_infra.py --pg-dsn ... --skip-redis
    python scripts/verify_infra.py --redis-url ... --skip-pg

退出码：0 全通过；1 有不通过项；2 依赖缺失。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 子进程用 sys.executable 直接跑本文件，`webapp` 包不在默认搜索路径上；
# 显式注入项目根，避免 "No module named 'webapp'"（子进程的 cwd 不一定等于项目根）。
CHILD_ENV_EXTRA = {"PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}

# 与 webapp/db.py::RATE_RULE_DEFAULTS 对齐的期望值（不是随手写的）
EXPECTED_RULES = {"user": (1200, 60), "endpoint": (900, 60), "model": (600, 60), "ip": (600, 60)}
EXPECTED_TABLES = 20


# ---------------------------------------------------------------------------
# 子进程模式：在**独立进程**里跑 N 次限流检查，返回被放行的次数。
# 必须是独立进程——同一进程内的 MemoryLimiter 共享 obs._WINDOWS，测不出放大效应。
# ---------------------------------------------------------------------------
def child_main(argv: list[str]) -> int:
    backend, key, limit, window, n = argv[0], argv[1], int(argv[2]), int(argv[3]), int(argv[4])
    if backend == "memory":
        os.environ["WEBAPP_RATE_BACKEND"] = "memory"
        os.environ.pop("WEBAPP_REDIS_URL", None)
    else:
        os.environ["WEBAPP_RATE_BACKEND"] = "redis"
        os.environ["WEBAPP_REDIS_URL"] = os.environ["CHILD_REDIS_URL"]
    from webapp import ratelimit_dist

    allowed = 0
    for _ in range(n):
        # 绕过 check_rate 的规则查库，直接打 Limiter：本测试只关心「计数共享」这一件事
        hit = ratelimit_dist.get_limiter().check(key, limit, window)
        if hit is None:
            allowed += 1
        else:
            break  # 超过上限后不再继续打，避免无意义写入
    print(json.dumps({"allowed": allowed}))
    return 0


def run_pair(backend: str, redis_url: str, limit: int, attempts: int) -> dict:
    """两个独立进程并发打同一个 key，返回两边放行数之和。"""
    key = f"kb:verify:{uuid.uuid4().hex}"
    env = dict(os.environ)
    env["CHILD_REDIS_URL"] = redis_url
    env.update(CHILD_ENV_EXTRA)
    procs = [
        subprocess.Popen(
            [sys.executable, __file__, "--child", backend, key, str(limit), "60", str(attempts)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(ROOT),
        )
        for _ in range(2)
    ]
    out = []
    for p in procs:
        so, se = p.communicate(timeout=60)
        if p.returncode != 0:
            raise RuntimeError(f"子进程失败：{se[-400:]}")
        out.append(json.loads(so.strip().splitlines()[-1]))
    return {
        "backend": backend,
        "processes": 2,
        "limit": limit,
        "allowed_total": sum(x["allowed"] for x in out),
        "per_process": [x["allowed"] for x in out],
    }


def check_pg(dsn: str) -> dict:
    try:
        import psycopg  # type: ignore
    except ImportError:
        return {"ok": False, "error": "未安装 psycopg（pip install 'psycopg[binary]'）"}

    problems: list[str] = []
    detail: dict = {}
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
                n_tables = cur.fetchone()[0]
                detail["tables"] = n_tables
                if n_tables != EXPECTED_TABLES:
                    problems.append(f"表数量 {n_tables} != 期望 {EXPECTED_TABLES}")

                cur.execute("SELECT dimension, limit_count, window_s FROM rate_rules")
                rules = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
                detail["rate_rules"] = rules
                if rules != EXPECTED_RULES:
                    problems.append(f"限流种子与 db.RATE_RULE_DEFAULTS 不一致：{rules}")

                cur.execute("SELECT policy, limit_cny FROM budgets WHERE scope='global' AND key='default'")
                row = cur.fetchone()
                detail["budget"] = {"policy": row[0], "limit_cny": float(row[1])} if row else None
                if not row:
                    problems.append("缺全局预算种子")

                cur.execute("SELECT count(*) FROM documents")
                detail["documents"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM chunks")
                detail["chunks"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*) FROM documents d"
                    " WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.id)"
                )
                orphans = cur.fetchone()[0]
                detail["documents_without_chunks"] = orphans

                # 时间类型必须是 timestamptz，而不是 text（搬错类型最典型的症状是偏 8 小时）
                cur.execute(
                    "SELECT data_type FROM information_schema.columns"
                    " WHERE table_name='users' AND column_name='created_at'"
                )
                ts_type = cur.fetchone()[0]
                detail["users.created_at_type"] = ts_type
                if "timestamp" not in ts_type:
                    problems.append(f"users.created_at 类型是 {ts_type}，应为 timestamp with time zone")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": not problems, "problems": problems, "detail": detail}


def check_redis(url: str, limit: int = 3, attempts: int = 5) -> dict:
    try:
        import redis  # type: ignore
    except ImportError:
        return {"ok": False, "error": "未安装 redis（pip install redis）"}

    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=3)
        if not client.ping():
            return {"ok": False, "error": "ping 失败"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    mem = run_pair("memory", url, limit, attempts)
    red = run_pair("redis", url, limit, attempts)

    problems: list[str] = []
    # 内存实现：两个进程各算各的 → 放行数翻倍（这就是「多副本配额被放大」）
    if mem["allowed_total"] != limit * 2:
        problems.append(
            f"内存实现预期放行 {limit * 2}（各算各的），实测 {mem['allowed_total']}；"
            "说明测试没真正隔离进程，结论不可信"
        )
    # Redis 实现：两个进程共享同一窗口 → 总放行数 == limit
    if red["allowed_total"] != limit:
        problems.append(
            f"Redis 实现预期放行 {limit}（共享窗口），实测 {red['allowed_total']}；"
            "说明窗口没有真正共享，多副本部署会放大配额"
        )

    return {
        "ok": not problems,
        "problems": problems,
        "detail": {
            "memory_backend": mem,
            "redis_backend": red,
            "conclusion": (
                f"limit={limit}：内存 {mem['allowed_total']} 次 vs Redis {red['allowed_total']} 次，"
                f"放大倍数 {mem['allowed_total'] / max(red['allowed_total'], 1):.1f}×"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--child":
        return child_main(argv[1:])

    ap = argparse.ArgumentParser(description="本机基础设施自检（PG / Redis）")
    ap.add_argument("--pg-dsn", default="postgresql://kb:kb_demo_only@127.0.0.1:55432/kb")
    ap.add_argument("--redis-url", default="redis://127.0.0.1:56379/0")
    ap.add_argument("--skip-pg", action="store_true")
    ap.add_argument("--skip-redis", action="store_true")
    ap.add_argument("--limit", type=int, default=3, help="限流共享验证用的窗口上限")
    args = ap.parse_args(argv)

    started = time.time()
    report: dict = {"pg": None, "redis": None}
    if not args.skip_pg:
        print("== PostgreSQL ==")
        report["pg"] = check_pg(args.pg_dsn)
        if report["pg"].get("ok"):
            d = report["pg"]["detail"]
            print(f"  表 {d['tables']} 张 · documents {d['documents']} · chunks {d['chunks']} · "
                  f"无限流种子偏差 · created_at={d['users.created_at_type']}")
        else:
            print(f"  不通过：{report['pg'].get('error') or report['pg'].get('problems')}")

    if not args.skip_redis:
        print("== Redis（多副本窗口共享）==")
        report["redis"] = check_redis(args.redis_url, limit=args.limit)
        if report["redis"].get("ok"):
            print(f"  {report['redis']['detail']['conclusion']}")
        else:
            print(f"  不通过：{report['redis'].get('error') or report['redis'].get('problems')}")

    ok = all(v is None or v.get("ok") for v in report.values())
    report["verdict"] = "INFRA_READY" if ok else "INFRA_NOT_READY"
    report["elapsed_s"] = round(time.time() - started, 2)
    print("=== SUMMARY ===")
    print(json.dumps({"verdict": report["verdict"], "elapsed_s": report["elapsed_s"]},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
