"""把向量从 SQLite/PG 的 `chunk_embeddings` 搬到真 Milvus（生产化第 1 项的后半段）。

设计约束（与 `migrate_sqlite_to_pg.py` 保持同一套习惯，别让它变成两套风格）：

- **默认只读**：不加 `--apply` 就是 dry-run，只统计与抽样，绝不写 Milvus；
- **可重入**：Milvus 主键是 `chunk_id`，`upsert` 重复执行只覆盖不新增；
- **只迁一个嵌入命名空间**：`chunk_embeddings` 里可能同时存在多个模型名，
  维度相同但**不在同一向量空间**的向量混在一起，余弦照样能算、分数全是噪声。
  所以 `--model` 必选其一，其余显式跳过并报数（见 `docs/production/01-data-migration.md` §5.2）；
- **迁完自证**：`--verify-top1 N` 会拿 N 条真实向量回查，断言**自己必须出现在 top-k 且分数≈1**。
  检索对不上说明字段错位或向量写坏，这一步比「写入 N 行」有意义得多。
  **不断言 top1 一定是自己**：演示库里有逐元素完全相同的重复向量（同一份样本被反复上传），
  同向量之间的先后是未定义的，断言严格到 top1 只会得到假失败，反而掩盖真问题；
- **复用后端实现**：直接 import `backend/app/integrations/milvus_client.py` 的
  `MilvusVectorStore`，不另抄一份 schema —— 抄一份的唯一结果就是两边慢慢漂移。

用法：
    # 1) 只看（默认）
    python scripts/migrate_vectors_to_milvus.py

    # 2) 真写（先起 infra：KB_MILVUS_STORAGE=local 可不依赖 minio）
    python scripts/migrate_vectors_to_milvus.py --apply

    # 3) 指定来源 / 命名空间 / 目标
    python scripts/migrate_vectors_to_milvus.py --apply \
      --model external:BAAI/bge-m3 \
      --pg-dsn postgresql://kb:kb_demo_only@127.0.0.1:55432/kb \
      --milvus-uri http://127.0.0.1:19530

    # 4) 换目标 Milvus
    python scripts/migrate_vectors_to_milvus.py --apply --milvus-uri http://127.0.0.1:19530
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

DEFAULT_MODEL = "external:BAAI/bge-m3"
DEFAULT_PG = "postgresql://kb:kb_demo_only@127.0.0.1:5432/kb"


# ---------------------------------------------------------------------------
# 取数：返回 [{chunk_id, doc_id, kb_id, dept_id, sensitivity, version_no, embedding}]
# 部门优先取 `documents.owner_id -> users.department`；取不到就空串（不编）。
# ---------------------------------------------------------------------------
# SQLite 用 `?`、psycopg 用 `%s`，不能共用一条字符串。
# 这里只把占位符抽出来，SQL 主体保持一份 —— 否则两边会慢慢改歪。
_SQL_TPL = """
SELECT e.chunk_id,
       c.doc_id,
       COALESCE(u.department, '') AS dept_id,
       e.vec
  FROM chunk_embeddings e
  JOIN chunks c ON c.id = e.chunk_id
  LEFT JOIN documents d ON d.id = c.doc_id
  LEFT JOIN users u ON u.id = d.owner_id
 WHERE e.model = {ph}
 ORDER BY e.chunk_id
"""


def _sql(ph: str) -> str:
    return _SQL_TPL.format(ph=ph)


def load_rows(model: str, pg_dsn: str) -> list[dict]:
    """从 PostgreSQL 读向量。运行时已无 SQLite，来源只有一个。"""
    try:
        import psycopg  # type: ignore
    except ImportError:
        raise SystemExit("[FAIL] 未安装 psycopg。安装：pip install 'psycopg[binary]'")
    with psycopg.connect(pg_dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(_sql("%s"), (model,))
            raw = cur.fetchall()

    rows = []
    bad = 0
    for chunk_id, doc_id, dept_id, vec in raw:
        try:
            arr = json.loads(vec) if isinstance(vec, str) else vec
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(arr, list) or not arr:
            bad += 1
            continue
        rows.append({
            "chunk_id": str(chunk_id),
            "doc_id": str(doc_id or ""),
            "kb_id": "default",
            "dept_id": str(dept_id or ""),
            "sensitivity": "internal",
            "version_no": 1,
            "embedding": [float(x) for x in arr],
        })
    if bad:
        print(f"[WARN] {bad} 条向量的 JSON 解析失败/为空，已跳过（不静默吞掉）")
    return rows


def coverage(model: str, pg_dsn: str) -> dict:
    """统计覆盖：总 chunk、本命名空间、其他命名空间、完全没向量。"""
    import psycopg

    with psycopg.connect(pg_dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chunks")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM chunk_embeddings WHERE model=%s", (model,))
            mine = cur.fetchone()[0]
            cur.execute(
                "SELECT model, COUNT(*) FROM chunk_embeddings WHERE model<>%s GROUP BY model", (model,)
            )
            others = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) FROM chunks c WHERE NOT EXISTS"
                " (SELECT 1 FROM chunk_embeddings e WHERE e.chunk_id=c.id)"
            )
            no_vec = cur.fetchone()[0]
    return {"total_chunks": total, "with_model": mine,
            "other_namespaces": dict(others), "no_vector_at_all": no_vec}


def main(argv: list[str] | None = None) -> int:
    # 抑制 pymilvus 2.6 的 ORM API 弃用告警（它会连源码行一起打印，淹没迁移进度）。
    import warnings

    warnings.filterwarnings("ignore", message=".*ORM-style PyMilvus API.*")

    ap = argparse.ArgumentParser(description="向量迁移：chunk_embeddings → Milvus")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="只迁这一个嵌入命名空间")
    ap.add_argument("--milvus-uri", default="http://127.0.0.1:19530")
    ap.add_argument("--pg-dsn", default=DEFAULT_PG)
    ap.add_argument("--tenant", default="default", help="Milvus partition key 取值（单组织演示填 default）")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--verify-top1", type=int, default=20,
                    help="迁完回查条数（断言自己在 top-k 且 score≈1），0 表示跳过")
    ap.add_argument("--apply", action="store_true", help="真正写入；不加则为 dry-run")
    ap.add_argument("--dry-run", action="store_true", help="显式只读（与 --apply 同时给出时以只读为准）")
    args = ap.parse_args(argv)

    if args.apply and args.dry_run:
        print("[WARN] 同时给了 --apply 与 --dry-run，按只读处理", file=sys.stderr)
        args.apply = False

    print(f"来源：PostgreSQL {args.pg_dsn}")
    print(f"命名空间：{args.model}")

    cov = coverage(args.model, args.pg_dsn)
    print(f"覆盖率：chunks 共 {cov['total_chunks']} 条，其中本命名空间 {cov['with_model']} 条"
          f"（{cov['with_model'] / max(cov['total_chunks'], 1):.1%}），"
          f"完全没向量 {cov['no_vector_at_all']} 条")
    if cov["other_namespaces"]:
        print(f"       其它命名空间（**本次跳过**）：{cov['other_namespaces']}")
        print("       为什么跳过：同维度不同模型的向量不在同一向量空间，混在一起余弦能算但分数无意义")

    rows = load_rows(args.model, args.pg_dsn)
    print(f"待迁向量：{len(rows)} 条，维度 {len(rows[0]['embedding']) if rows else '-'}")

    if not rows:
        print("[FAIL] 没有可迁的向量，检查 --model 是否拼错", file=sys.stderr)
        return 2

    if not args.apply:
        print("\n（dry-run，未写入 Milvus）抽样 3 条：")
        for r in rows[:3]:
            print(f"  {r['chunk_id'][:16]}… doc={r['doc_id'][:12]}… dept={r['dept_id'] or '(空)'} "
                  f"dim={len(r['embedding'])} 首值={r['embedding'][0]:.6f}")
        print(f"\n真跑：python {pathlib.Path(__file__).name} --apply")
        return 0

    # ---- 写 Milvus：复用后端实现，不另抄 schema ----
    try:
        import pymilvus  # noqa: F401
    except ImportError:
        print("[FAIL] 未安装 pymilvus。安装：pip install 'pymilvus>=2.4,<3.0'", file=sys.stderr)
        return 3

    os.environ.setdefault("MILVUS_URI", args.milvus_uri)
    sys.path.insert(0, str(BACKEND))
    from app.integrations.milvus_client import get_vector_store  # 延迟 import，缺依赖时不炸栈

    store = get_vector_store()
    started = time.time()
    store.ensure_collection()

    written = 0
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        for r in chunk:
            r["tenant_id"] = args.tenant
        store.upsert(chunk)
        written += len(chunk)
        print(f"  upsert {i + len(chunk):>4}/{len(rows)}")

    elapsed = time.time() - started
    print(f"写入完成：{written} 条，耗时 {elapsed:.1f}s")

    # ---- 对账：Milvus 主键集合 vs 源库期望集合 ----
    in_milvus = store.list_chunk_ids(args.tenant)
    expected = {r["chunk_id"] for r in rows}
    ghost = in_milvus - expected
    missing = expected - in_milvus
    print(f"对账：Milvus 内 {len(in_milvus)} 条 / 期望 {len(expected)} 条 | "
          f"幽灵 {len(ghost)} | 漏迁 {len(missing)}")
    if ghost:
        print(f"  幽灵样例：{sorted(ghost)[:3]}")
    if missing:
        print(f"  漏迁样例：{sorted(missing)[:3]}")

    # ---- 自证：拿真实向量回查，自己必须出现在 top-k 且分数≈1 ----
    # 为什么不断言「top1 必须是它自己」：演示库里有**逐元素完全相同**的重复向量
    # （同一份样本文档被反复上传，文本相同 → 向量相同，实测 5 组、最多 9 条）。
    # 同向量之间的先后顺序是未定义的，Milvus 返回哪一个都正确。
    # 断言严格到 top1 只会得到一个假失败，反而掩盖真正的错位问题。
    in_topk = 0
    top1_exact = 0
    checked = 0
    if args.verify_top1 > 0:
        expr = store.build_permission_expr(tenant_id=args.tenant)
        print(f"回查断言（expr: {expr}）")
        for r in rows[:args.verify_top1]:
            hits = store.search([r["embedding"]], expr, top_k=5)[0]
            checked += 1
            ids = [h["chunk_id"] for h in hits]
            top1_exact += int(bool(ids) and ids[0] == r["chunk_id"])
            if r["chunk_id"] in ids:
                in_topk += 1
                rank = ids.index(r["chunk_id"]) + 1
                ties = sum(1 for h in hits if h["score"] >= 0.99999)
                note = f"rank={rank}" + (f" 并列 {ties} 条同向量" if ties > 1 else "")
                print(f"  OK   {r['chunk_id'][:16]}… {note} score={hits[0]['score']:.4f}")
            else:
                print(f"  FAIL {r['chunk_id'][:16]}… 未进 top5（向量写坏或字段错位）")

    ok = not ghost and not missing and checked > 0 and in_topk == checked
    print("=== SUMMARY ===")
    print(json.dumps({
        "model": args.model, "written": written, "in_milvus": len(in_milvus),
        "ghost": len(ghost), "missing": len(missing),
        "checked": checked, "in_topk": in_topk, "top1_exact": top1_exact,
        "skipped_other_namespaces": cov["other_namespaces"],
        "verdict": "VECTORS_READY" if ok else "VECTORS_NOT_READY",
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
