"""测试里改配置表的小工具。

改造前测试直接写 `INSERT OR REPLACE INTO ...`（SQLite 专有语法）。换成 PostgreSQL 后
必须写 `ON CONFLICT ... DO UPDATE`，而且布尔列不能塞 `1`/`0`。把这套写法收敛到两个
函数里，测试只表达意图（「把 ip 配额设成 3」），不重复方言细节。
"""
from __future__ import annotations

import time

import psycopg  # noqa: F401  # 让缺依赖时的报错发生在导入期而不是断言期


def _now() -> str:
    """合法的 ISO-8601 UTC。SQLite 时代这里可以随便传 'test'，PG 会直接拒绝。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def upsert_rate_rule(conn, dim: str, limit: int, window: int,
                     enabled: bool = True, ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT (dimension) DO UPDATE SET"
        "   limit_count=excluded.limit_count,"
        "   window_s=excluded.window_s,"
        "   enabled=excluded.enabled,"
        "   updated_at=excluded.updated_at",
        (dim, limit, window, enabled, ts or _now()),
    )


def upsert_budget(conn, scope: str, key: str, limit_cny: float,
                  policy: str = "warn", ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO budgets(scope,key,limit_cny,policy,updated_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT (scope,key) DO UPDATE SET"
        "   limit_cny=excluded.limit_cny,"
        "   policy=excluded.policy,"
        "   updated_at=excluded.updated_at",
        (scope, key, limit_cny, policy, ts or _now()),
    )


def upsert_eval_config(conn, key: str, value: str, ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO eval_configs(key,value,updated_at) VALUES (?,?,?)"
        " ON CONFLICT (key) DO UPDATE SET"
        "   value=excluded.value, updated_at=excluded.updated_at",
        (key, value, ts or _now()),
    )


def columns(conn, table: str) -> set[str]:
    """表的列名集合。替代 SQLite 的 `PRAGMA table_info`。

    用 current_schema() 而不是硬编码 public：测试跑在独立 schema 里，
    查 public 会拿到别人的表或空集合（正是它替代 PRAGMA 的理由）。
    """
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = ?", (table,)
    ).fetchall()
    return {r["column_name"] for r in rows}
