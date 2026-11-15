"""【一次性 ETL 工具】把历史 SQLite 演示库导入 PostgreSQL。

**它不是运行时代码**：`webapp/` 已经完全不依赖 SQLite，本脚本只是为了让
「已存在的旧 .db 文件」能被搬进真库，保证这次迁移**可复现**而不是一次性手工操作。
放在 `scripts/legacy/` 下就是为了让这个边界一眼可见——依赖方向是单向的：
    legacy 工具 → 读旧文件 → 写 PG
反过来不存在，运行时永远不会回去读 SQLite。

若确定没有旧库需要导入，整个目录可以删掉，不影响任何业务代码与测试。

设计约束：
- **默认只读**：不传 `--apply` 时等同于 `--dry-run`，只统计与抽样，绝不写目标库；
- **可重入**：按主键 `ON CONFLICT DO NOTHING`，中断后重跑不会重复插入；
- **类型转换**：SQLite 的时间是字符串、布尔是 0/1、`scope_json` 是文本，
  迁到 PG 时要转 `timestamptz` / `boolean` / `jsonb`（见下方列集合）；
- **依赖可选**：`psycopg` 不在 requirements 里，未安装时给出明确报错而不是 ImportError 栈。

用法：
    # 1) 只看不写
    python scripts/legacy/import_legacy_sqlite.py --dry-run

    # 2) 真迁（先起 infra：docker compose -f docker-compose.infra.yml up -d）
    python scripts/legacy/import_legacy_sqlite.py --apply

    # 3) 指定源/目标
    python scripts/legacy/import_legacy_sqlite.py --apply \
      --sqlite .data/webapp/app.db \
      --pg-dsn postgresql://kb:kb_demo_only@127.0.0.1:5432/kb

注意：
- 本脚本**不迁 `chunk_embeddings.vec` 到 Milvus**，只保留 PG 元数据；向量迁移见
  `docs/production/01-data-migration.md` §4（Milvus collection + 双写策略）。
- `--truncate` 会先清空目标表，默认拒绝；只在确认目标库是空库/测试库时使用。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterator

# 迁移顺序：被引用的一方在前（PG 侧未建外键，但保持顺序便于人工核对）
TABLE_ORDER = (
    "users", "eval_tasks", "documents", "chunks", "chat_logs", "chat_sources",
    "badcases", "audit_logs", "app_settings", "chunk_embeddings",
    "usage_events", "budgets", "rate_rules", "rate_hits",
    "eval_configs", "eval_runs", "eval_logs", "safety_events",
    # 缓存与运行时状态（cache/store.py）。放在最后：它们是最容易重建的派生数据，
    # 前面的真值表先落地，缓存即便迁失败也不影响业务正确性。
    "cache_entries", "runtime_state",
)

# 需要从 0/1 转 boolean 的列
BOOL_COLS = {"degraded", "comparable", "enabled", "estimated", "blocked", "is_negative"}
# 需要转 timestamptz 的列。SQLite 存的是 ISO 字符串，末尾带 'Z'；
# psycopg 多数情况能直接解析，但 'Z' 在不同驱动/版本上口径不一，
# 统一改写成 '+00:00' 显式 UTC，避免被当成本地时区。
TIME_COLS = {"created_at", "updated_at", "ts", "valid_from", "expires_at",
             "last_verified_at", "last_access"}
# 需要转 jsonb 的列
JSON_COLS = {"scope_json"}

# 每张表的主键（用于 ON CONFLICT）
PK = {
    "users": ("id",),
    "eval_tasks": ("id",),
    "documents": ("id",),
    "chunks": ("id",),
    "chat_logs": ("id",),
    "chat_sources": ("id",),
    "badcases": ("id",),
    "audit_logs": ("id",),
    "app_settings": ("key",),
    "chunk_embeddings": ("chunk_id", "model"),
    "usage_events": ("id",),
    "budgets": ("scope", "key"),
    "rate_rules": ("dimension",),
    "rate_hits": ("id",),
    "eval_configs": ("key",),
    "eval_runs": ("run_id",),
    "eval_logs": ("id",),
    "safety_events": ("id",),
    "cache_entries": ("key",),
    "runtime_state": ("key",),
}

# dry-run 抽样时绝不回显的列（凭据 / 内容）
SENSITIVE_COLS = {"password_hash", "password_salt", "vec", "content", "chunk_text", "answer"}


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _convert(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif k in BOOL_COLS:
            out[k] = bool(int(v))
        elif k in TIME_COLS and isinstance(v, str) and v.endswith("Z"):
            out[k] = v[:-1] + "+00:00"
        elif k in JSON_COLS and isinstance(v, str):
            out[k] = v  # psycopg 会按 jsonb 接收字符串
        else:
            out[k] = v
    return out


def _iter_rows(conn: sqlite3.Connection, table: str, batch: int) -> Iterator[list[dict]]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f'SELECT * FROM "{table}"')
    while True:
        rows = cur.fetchmany(batch)
        if not rows:
            return
        yield [_convert(dict(r)) for r in rows]


def dry_run(sqlite_path: Path, only: tuple[str, ...]) -> int:
    if not sqlite_path.exists():
        print(f"[FAIL] SQLite 不存在：{sqlite_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(sqlite_path))
    try:
        present = _existing_tables(conn)
        total = 0
        print(f"源库：{sqlite_path}")
        print(f"{'表':<20}{'行数':>8}   样例（脱敏）")
        print("-" * 78)
        for t in TABLE_ORDER:
            if only and t not in only:
                continue
            if t not in present:
                print(f"{t:<20}{'-':>8}   （源库无此表，跳过）")
                continue
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            total += n
            sample = ""
            if n:
                cols = _table_columns(conn, t)
                row = conn.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchone()
                shown = []
                for c, v in zip(cols, row):
                    if c in SENSITIVE_COLS:
                        shown.append(f"{c}=***")
                    elif isinstance(v, str) and len(v) > 24:
                        shown.append(f"{c}={v[:20]}…")
                    else:
                        shown.append(f"{c}={v}")
                sample = ", ".join(shown[:5])
            print(f"{t:<20}{n:>8}   {sample}")
        print("-" * 78)
        print(f"合计 {total} 行（dry-run，未写入目标库）")
        return 0
    finally:
        conn.close()


def apply(sqlite_path: Path, dsn: str, batch: int, truncate: bool,
          only: tuple[str, ...]) -> int:
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("[FAIL] 未安装 psycopg。安装：pip install 'psycopg[binary]'\n"
              "       （它不在项目 requirements 里，只在真正迁移时需要）", file=sys.stderr)
        return 3
    if not sqlite_path.exists():
        print(f"[FAIL] SQLite 不存在：{sqlite_path}", file=sys.stderr)
        return 2

    src = sqlite3.connect(str(sqlite_path))
    present = _existing_tables(src)
    attempted = 0
    inserted = 0
    try:
        with psycopg.connect(dsn) as dst:
            with dst.cursor() as cur:
                if truncate:
                    targets = [t for t in TABLE_ORDER if t in present and (not only or t in only)]
                    print(f"[WARN] --truncate：将清空 {len(targets)} 张目标表")
                    cur.execute("TRUNCATE " + ", ".join(targets) + " RESTART IDENTITY CASCADE")
                for t in TABLE_ORDER:
                    if only and t not in only:
                        continue
                    if t not in present:
                        continue
                    cols = _table_columns(src, t)
                    if not cols:
                        continue
                    pk = PK.get(t)
                    sql = (f'INSERT INTO {t} ({", ".join(cols)}) '
                           f'VALUES ({", ".join(["%s"] * len(cols))})')
                    if pk:
                        sql += f' ON CONFLICT ({", ".join(pk)}) DO NOTHING'
                    n = 0
                    hit = 0
                    for chunk in _iter_rows(src, t, batch):
                        cur.executemany(sql, [tuple(r[c] for c in cols) for r in chunk])
                        # rowcount 是**实际影响行数**：ON CONFLICT DO NOTHING 跳过的行不计。
                        # 用它而不是 len(chunk)，否则重跑会虚报「写入 903 行」而实际写 0 行。
                        hit += max(cur.rowcount or 0, 0)
                        n += len(chunk)
                    dst.commit()
                    attempted += n
                    inserted += hit
                    skipped = n - hit
                    extra = f"，已存在跳过 {skipped}" if skipped else ""
                    print(f"  {t:<20} 尝试 {n:>4} 行 / 实际写入 {hit:>4} 行{extra}")
        print(f"完成：尝试 {attempted} 行，实际写入 {inserted} 行，"
              f"跳过 {attempted - inserted} 行（主键已存在）。")
        print("下一步必须人工核对：逐表 COUNT(*) 与源库一致，且 MAX(created_at) 未落后。")
        return 0
    finally:
        src.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SQLite → PostgreSQL 迁移")
    ap.add_argument("--sqlite", default=".data/webapp/app.db")
    ap.add_argument("--pg-dsn", default="postgresql://kb:kb_demo_only@127.0.0.1:5432/kb")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--truncate", action="store_true", help="先清空目标表（危险）")
    ap.add_argument("--apply", action="store_true", help="真正写入；不加则为 dry-run")
    ap.add_argument("--dry-run", action="store_true",
                    help="显式只读（与不加 --apply 等价；与 --apply 同时给出时以 --dry-run 为准）")
    ap.add_argument("--tables", nargs="*", default=(), help="只迁指定表")
    args = ap.parse_args(argv)

    if args.apply and args.dry_run:
        print("[WARN] 同时给了 --apply 与 --dry-run，按只读处理", file=sys.stderr)
        args.apply = False

    only = tuple(args.tables)
    unknown = [t for t in only if t not in TABLE_ORDER]
    if unknown:
        print(f"[FAIL] 未知表：{unknown}", file=sys.stderr)
        return 2
    if args.apply:
        return apply(Path(args.sqlite), args.pg_dsn, args.batch, args.truncate, only)
    return dry_run(Path(args.sqlite), only)


if __name__ == "__main__":
    raise SystemExit(main())
