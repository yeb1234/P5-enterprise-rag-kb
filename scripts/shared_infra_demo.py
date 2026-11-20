"""共享基础设施演示：多项目共用一个 PG + 一个 Redis，P5 如何不撞车。

**这是本机的模拟，不是真的接入了 P1/P2/P4 那台共享实例**——本机没有那台库的连接串。
模拟方式：在本机 PG 的同一个 database 里建三个「邻居 schema」冒充 P1/P2/P4，
再加一个 `p5_kb` 给 P5；Redis 用同一个 db，靠 key 前缀区分项目。

跑法：
    python scripts/shared_infra_demo.py

脚本幂等：每次开头清掉自己的演示 schema 与 key，结尾再清一次（try/finally）。
不碰 public 里的业务表、不碰别人的 Redis key。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import psycopg
import redis as redis_mod

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shared_infra as si  # noqa: E402

DDL_PATH = ROOT / "scripts" / "pg_init.sql"
REPORT_PATH = ROOT / "scripts" / "out" / "shared_infra_demo.json"

PG_DSN = os.environ.get("WEBAPP_DATABASE_URL", "postgresql://kb:kb_demo_only@127.0.0.1:5432/kb")
REDIS_URL = os.environ.get("WEBAPP_REDIS_URL", "redis://127.0.0.1:6379/0")

SCHEMAS = (*si.NEIGHBOR_SCHEMAS, si.P5_SCHEMA)
KEY_PREFIXES = ("p1slim", "p2core", "p4mall", si.PROJECT_PREFIX)
DEMO_USERS = ("alice", "bob")


def _neighbor_key(prefix: str, user: str, *seg: str) -> str:
    return ":".join((prefix, user, *seg))


def _clean_pg(cur) -> None:
    for name in SCHEMAS:
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


def _clean_redis(r) -> None:
    """只删本演示的前缀，不碰别人的 key。"""
    for prefix in KEY_PREFIXES:
        for key in r.scan_iter(match=f"{prefix}:*"):
            r.delete(key)


def _init_pg(cur) -> dict[str, int]:
    """每个 schema 各建一张演示小表，塞不同的行数。

    **P5 的业务表结构从 scripts/pg_init.sql 复用**：先 `SET search_path` 到
    p5_kb，再原样执行那份 DDL——20 张表会在 p5_kb 下建出来，public 不动。
    邻居只建一张最小的 demo_docs，用于证明「跨 schema 读不到」。
    """
    counts: dict[str, int] = {}
    for i, name in enumerate(SCHEMAS):
        cur.execute(f'CREATE SCHEMA "{name}"')
        if name != si.P5_SCHEMA:
            cur.execute(f'CREATE TABLE "{name}".demo_docs (id text primary key, owner_user text, title text)')
            rows = [(f"{name}-{n}", DEMO_USERS[n % len(DEMO_USERS)], f"来自 {name} 的文档 {n}")
                    for n in range(i + 2)]      # 行数故意不同：2/3/4
            cur.executemany(f'INSERT INTO "{name}".demo_docs VALUES (%s,%s,%s)', rows)
            counts[name] = len(rows)

    # P5：同一份 DDL，换个 search_path 就落到 p5_kb —— 不另写一份业务 DDL。
    ddl = DDL_PATH.read_text(encoding="utf-8")
    cur.execute(f'SET search_path TO "{si.P5_SCHEMA}", public')
    cur.execute(ddl)                              # psycopg 无参数 → 简单查询协议，允许多条语句
    cur.execute('RESET search_path')

    cur.execute("SELECT count(*) FROM p5_kb.users")
    counts[si.P5_SCHEMA] = int(cur.fetchone()[0])  # init_db 的种子用户；演示前库里已有 admin 等
    return counts


def _probe_pg() -> dict:
    """验证隔离：P5 的 search_path 只看自己的表；显式读邻居必须失败。"""
    result: dict = {}
    with psycopg.connect(PG_DSN, connect_timeout=5, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 先把演示 schema 建好（_init_pg 在 run() 里调用，这里只读）
            cur.execute(f'SET search_path TO "{si.P5_SCHEMA}", public')
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema() ORDER BY 1")
            result["p5_visible_tables"] = [r[0] for r in cur.fetchall()]

            # 反面用例：用完整限定名去读邻居的表，应该被权限或 search_path 挡住。
            # 注意：这里用的是同一个 DB role（kb 是 superuser），所以**不会**因权限失败；
            # 要证明「看不见」，判据是「不在 search_path 里 → 裸表名解析不到」。
            # 真正的权限隔离靠 DB role + GRANT，本演示只证明命名空间不串。
            try:
                cur.execute("SELECT count(*) FROM demo_docs")  # 不带 schema 前缀
                result["foreign_bare_lookup"] = {"blocked": False, "rows": int(cur.fetchone()[0])}
            except Exception as exc:                            # noqa: BLE001 - 就是要抓这个
                result["foreign_bare_lookup"] = {"blocked": True, "error": type(exc).__name__}

            cur.execute('RESET search_path')
    return result


def _probe_redis(r) -> dict:
    """四个项目共用同一个 Redis；验证 key 前缀互不覆盖。"""
    written: dict[str, list[str]] = {}
    for prefix in KEY_PREFIXES:
        keys = []
        for user in DEMO_USERS:
            if prefix == si.PROJECT_PREFIX:
                key = si.build_p5_key(user, "rl", "user")
                assert si.is_p5_key(key), key
            else:
                key = _neighbor_key(prefix, user, "rl", "user")
            r.set(key, f"{prefix}:{user}")
            keys.append(key)
        written[prefix] = keys

    all_keys = [k for ks in written.values() for k in ks]
    p5_set = set(written[si.PROJECT_PREFIX])
    neighbor_set = set(all_keys) - p5_set
    intersection = p5_set & neighbor_set

    # 弱 key 必须被 is_p5_key 拒掉：p5kb:rl:user 把业务段当用户了
    weak = "p5kb:rl:user"
    r.set(weak, "weak")                            # 写进去是为了证明「写了也会被判非法」
    weak_rejected = not si.is_p5_key(weak)
    r.delete(weak)

    return {
        "keys_per_project": {p: len(ks) for p, ks in written.items()},
        "total_keys": len(all_keys),
        "p5_neighbor_intersection": len(intersection),
        "weak_key_rejected": weak_rejected,
        "p5_values": [r.get(k) for k in written[si.PROJECT_PREFIX]],
    }


def run() -> dict:
    started = time.perf_counter()
    report: dict = {"mode": "LOCAL_SIMULATION_NOT_REAL_SHARED_INSTANCE"}
    r = redis_mod.from_url(REDIS_URL, decode_responses=True)

    conn = psycopg.connect(PG_DSN, connect_timeout=5, autocommit=True)
    try:
        with conn.cursor() as cur:
            _clean_pg(cur)
            report["rows_per_schema"] = _init_pg(cur)
        _clean_redis(r)

        report.update(_probe_pg())
        report.update(_probe_redis(r))

        verdict_ok = (
            report["p5_visible_tables"]
            and report["foreign_bare_lookup"]["blocked"]
            and report["p5_neighbor_intersection"] == 0
            and report["weak_key_rejected"]
        )
        report["verdict"] = "SHARED_INFRA_DEMO_OK" if verdict_ok else "FAILED"
    finally:
        with conn.cursor() as cur:
            _clean_pg(cur)
        conn.close()
        _clean_redis(r)

    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return report


def main() -> int:
    report = run()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "SHARED_INFRA_DEMO_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
