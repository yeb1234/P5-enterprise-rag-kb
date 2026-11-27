"""CmedqaRetrieval 真实链路检索评估（C-MTEB 六个检索任务之一，走 hf-mirror 国内源）。

与 `evalfmt/publicbench.py` 的区别（那套是**离线纯向量打分**，没有链路）：
- 那套把 10 万 passage 预先算好向量放磁盘 npy，用 numpy 余弦排序，不经过 webapp；
- 这套把 passage **真实切分入库**，走 `webapp.rag.retrieve`：词法门禁 → pgvector 排序 → rerank。
  这才是「上传→入库→检索」那条链路的真实表现。

与 `eval_chain_cmrc.py` 的区别（那套测 CMRC 抽取式问答链路）：
- CMRC 的问题几乎照抄原文，二元组撞得上，Recall 恒为 1.0，**检索维度没有区分度**；
- Cmedqa 的 query 是患者口语（「女儿十五岁没来月经乳房发育不好」），gold 是医生术语
  （「女孩儿一般十三或十四岁出现月经…」），实测二元组 Jaccard 中位 **0.024**、
  query 原文出现在 gold 里的比例 **0/20**。词法撞不上，只能靠语义 —— 这才能测出检索能力。

规模（seed=42 固定可复现）：
- 200 个 query（全部带 qrels），其相关 passage 去重后约 364 篇，先全部入池保证每题都有答案；
- 剩余名额用无关 passage 随机填到 3000 篇 → 随机基线 Recall@10 ≈ 0.3%，有足够难度；
- 全量 10 万 passage 要算 10 万条向量（约 60 分钟），对「小批量验证」不划算，故抽样。

判分口径与 `evalfmt/publicbench.py::metrics_for_ranking` **完全一致**（直接 import 复用）：
二值相关性、Recall@k = |topk ∩ gold| / |gold|、nDCG@10、MRR@10。
返回的 chunk 先**按 doc 去重**（同一篇 passage 被切成多块时只取最高排名的那个），
否则一篇长文档能靠多个 chunk 霸占 top-10，把 chunk 级虚高当成 passage 级成绩。

三组对照：
- A `top10+rerank`：链路原样（词法门禁 → pgvector → rerank），这是线上真实行为；
- B `top10`：同上但关 rerank，看重排在候选内还有没有增益；
- C `nogate`：**绕过词法门禁**，直接对全量池做向量排序 —— 用来量化门禁的损失，
  即「如果门禁没把候选掐成空，向量检索本可以做到多少」。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# 直接跑脚本时 sys.path[0] 是 webapp/，会让 webapp/queue.py 遮蔽标准库 queue。
sys.path = [p for p in sys.path if not p or Path(p).resolve() != HERE]
sys.path.insert(0, str(ROOT))

OUT = HERE / "out"
CACHE = ROOT / "evalfmt" / ".cache"
CACHE.mkdir(parents=True, exist_ok=True)

SEED = 42


# ---------- 数据取样 ----------

def _pick(row: dict, *names: str) -> str:
    for n in names:
        if n in row:
            return str(row[n])
    raise KeyError(f"字段都不存在：{names} / 实际 {list(row)}")


def download(force: bool = False) -> None:
    """从 hf-mirror 拉 CmedqaRetrieval 三个 parquet（国内可直连，已有则跳过）。"""
    import urllib.request

    base = "https://hf-mirror.com/datasets/mteb/CmedqaRetrieval/resolve/main/"
    files = {
        "corpus": "corpus/dev-00000-of-00001.parquet",
        "queries": "queries/dev-00000-of-00001.parquet",
        "qrels": "data/dev-00000-of-00001.parquet",
    }
    for name, rel in files.items():
        target = CACHE / f"cmedqa_{name}.parquet"
        if target.exists() and not force:
            print(f"  [cache] {name} {target.stat().st_size/1e6:.1f}MB", flush=True)
            continue
        print(f"  [get] {name} ← {base + rel}", flush=True)
        req = urllib.request.Request(base + rel, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=600) as resp, open(target, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        print(f"  [ok]  {target.stat().st_size/1e6:.1f}MB", flush=True)


def build_pool(n_docs: int, n_cases: int) -> tuple[list[dict], list[dict]]:
    """抽样：先抽 query，再把它们的 gold passage 全部放进池，剩余名额随机填空干扰文档。"""
    import pyarrow.parquet as pq

    corpus = [r for r in pq.read_table(CACHE / "cmedqa_corpus.parquet").to_pylist()]
    queries = [r for r in pq.read_table(CACHE / "cmedqa_queries.parquet").to_pylist()]
    rel: dict[str, set[str]] = defaultdict(set)
    for r in pq.read_table(CACHE / "cmedqa_qrels.parquet").to_pylist():
        if int(r.get("score", 1)) > 0:
            rel[_pick(r, "query-id", "query_id")].add(_pick(r, "corpus-id", "corpus_id"))

    rnd = random.Random(SEED)
    usable = [q for q in queries if _pick(q, "_id", "id", "query-id") in rel]
    picked = rnd.sample(usable, min(n_cases, len(usable)))

    gold_ids: set[str] = set()
    for q in picked:
        gold_ids |= rel[_pick(q, "_id", "id", "query-id")]

    by_id = {_pick(r, "_id", "id"): r for r in corpus}
    docs: list[dict] = []
    for pid in gold_ids:
        if pid in by_id:
            docs.append({"id": pid, "text": _pick(by_id[pid], "text")})
    rest = [r for r in corpus if _pick(r, "_id", "id") not in gold_ids]
    rnd.shuffle(rest)
    for r in rest[: max(0, n_docs - len(docs))]:
        docs.append({"id": _pick(r, "_id", "id"), "text": _pick(r, "text")})

    cases = [
        {"qid": _pick(q, "_id", "id", "query-id"), "question": _pick(q, "text"),
         "gold": sorted(rel[_pick(q, "_id", "id", "query-id")])}
        for q in picked
    ]
    return docs, cases


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="CmedqaRetrieval 真实链路检索评估")
    ap.add_argument("--label", default="A", help="产出标签：A/B/C 或 smoke")
    ap.add_argument("--doc-pool", type=int, default=3000, help="入库 passage 数（检索池规模）")
    ap.add_argument("--cases", type=int, default=200, help="提问数")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--rerank-depth", type=int, default=0,
                    help="送进 rerank 的候选数（0=等于 top_k，即线上原行为）")
    ap.add_argument("--rerank", default="", choices=["", "off", "external", "llm"],
                    help="改 app_settings.rerank_provider（空=沿用当前）")
    ap.add_argument("--nogate", action="store_true",
                    help="绕过词法门禁，直接对全量池做向量排序（量化门禁损失）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    ap.add_argument("--schema", default="", help="固定 schema 名（配合 --reuse 让多组共用一次入库）")
    ap.add_argument("--reuse", action="store_true",
                    help="复用已有 schema 与已入库/已算好的向量，不重建不重灌（省掉重算 3000+ 向量）")
    ap.add_argument("--cleanup", action="store_true", help="跑完删掉 schema")
    ap.add_argument("--no-resume", action="store_true", help="忽略 partial 断点，从头跑")
    ap.add_argument("--keep-schema", action="store_true")
    ap.add_argument("--db-url", default=None)
    ap.add_argument("--download", action="store_true", help="只下载数据")
    args = ap.parse_args()

    if args.download:
        download()
        return 0

    # ---------- 连库：.env 里的 WEBAPP_DATABASE_URL 可能指向未启动的端口，探活回退 ----------
    from webapp.envfile import load_env
    load_env()

    def resolve_db(url: str | None) -> tuple[str, str]:
        """连库探活并**重试等待**：Docker Desktop 重启后 PG 要约 20s 才 ready，
        直接 connect 会 ConnectionTimeout 把整轮跑批判死（实测踩过两次）。"""
        import psycopg
        cands = []
        if url:
            cands.append((url, "explicit"))
        if os.environ.get("WEBAPP_DATABASE_URL"):
            cands.append((os.environ["WEBAPP_DATABASE_URL"], "env"))
        cands.append(("postgresql://kb:kb_demo_only@127.0.0.1:5432/kb", "default"))
        wait_s = 90
        i = 0
        while i * 3 <= wait_s:
            for u, src in cands:
                try:
                    c = psycopg.connect(u, connect_timeout=4)
                    c.close()
                    if i:
                        print(f"  [db] 等待 {i*3}s 后连上（{src}）", flush=True)
                    return u, src
                except Exception:
                    continue
            i += 1
            if i * 3 <= wait_s:
                time.sleep(3)
        raise SystemExit(f"没有可连的 PG：试过 explicit/env/default，等待 {wait_s}s 仍失败")

    db_url, db_note = resolve_db(args.db_url)
    os.environ["WEBAPP_DATABASE_URL"] = db_url

    schema = args.schema or f"kb_ret_{args.label.lower()}_{uuid.uuid4().hex[:6]}"
    os.environ["WEBAPP_DB_SCHEMA"] = schema

    docs, cases = build_pool(args.doc_pool, args.cases)
    if args.limit:
        cases = cases[: args.limit]

    import psycopg

    import pgkit
    from webapp import db, rag, settings
    from evalfmt.publicbench import metrics_for_ranking

    def aggregate(rows: list[dict]) -> dict:
        # 只对指标字段求均值；records 里还有 qid/question 这类字符串，直接交给
        # publicbench.aggregate 会 TypeError（它假设 rows 全是数值）。
        keys = ("recall@1", "recall@5", "recall@10", "ndcg@10", "mrr@10")
        return {k: sum(r[k] for r in rows) / len(rows) for k in keys}

    def ensure_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                cur.execute(f'CREATE SCHEMA "{schema}"')

    def drop_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def seed() -> tuple[int, dict[str, int]]:
        """真实切分入库。title 直接存 corpus-id，检索回来才能映射回 qrels。"""
        conn = db.get_conn()
        total = 0
        per_doc: dict[str, int] = {}
        try:
            conn.execute("DELETE FROM chunk_embeddings")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for d in docs:
                doc_id = uuid.uuid4().hex
                chunks = rag.chunk_text(d["text"])
                per_doc[d["id"]] = len(chunks)
                conn.execute(
                    "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, "eval", d["id"], d["id"], len(d["text"].encode()),
                     "ready", len(chunks), now, now),
                )
                for idx, c in enumerate(chunks):
                    conn.execute("INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                                 (uuid.uuid4().hex, doc_id, idx, c))
                total += len(chunks)
            conn.commit()
        finally:
            conn.close()
        return total, per_doc

    if args.reuse:
        # 复用时不重建：建库会清空 chunk_embeddings，3000+ 条向量要重算约 1-2 分钟，
        # 而 B/C 组与 A 组只差参数，入库内容完全相同，没必要重灌。
        with psycopg.connect(pgkit.dsn(), connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,))
                if cur.fetchone() is None:
                    raise SystemExit(f"schema {schema} 不存在，不能 --reuse")
        db.init_db()
        conn = db.get_conn()
        try:
            n_chunks = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
            n_docs = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
        finally:
            conn.close()
        per_doc = {}
        print(f"[reuse] schema={schema} docs={n_docs} chunks={n_chunks}", flush=True)
    else:
        ensure_schema()
        db.init_db()
        n_chunks, per_doc = seed()
    # rerank 不能靠环境变量切：settings.get_all() 会用 app_settings 表覆盖 DEFAULTS。
    if args.rerank:
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM app_settings WHERE key=?", ("rerank_provider",))
            conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?)",
                         ("rerank_provider", args.rerank))
            conn.commit()
        finally:
            conn.close()

    cfg = settings.get_all()
    eff = settings.effective_embedding(cfg)
    print(f"[env] db={db_url.split('@')[-1]} schema={schema}", flush=True)
    print(f"[env] embed={eff.get('model')} kind={eff.get('kind')} rerank={cfg.get('rerank_provider')}", flush=True)
    print(f"[seed] passages={len(docs)} chunks={n_chunks} cases={len(cases)} top_k={args.top_k} "
          f"nogate={args.nogate}", flush=True)

    # ---------- 检索 ----------
    pool = rag._load_ready_chunks() if args.nogate else None
    if args.nogate:
        print(f"[nogate] 预载 {len(pool)} chunk，直接向量排序（不经词法门禁）", flush=True)

    # 断点续跑：Docker Desktop 在重负载下会整体重启容器（实测 top_k=30+rerank 两次跑到
    # 第 25 / 75 题时 PG 被外部停止），中断不落盘就等于白跑 8 分钟。
    # partial 文件名带参数指纹，top_k/rerank/nogate 变了自然换文件，不会把两组数据串在一起。
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"cmedqa_{args.label}"
    part = OUT / (f"{stem}_k{args.top_k}_rr{cfg.get('rerank_provider')}"
                  f"_g{int(args.nogate)}_d{args.rerank_depth or 0}.partial.jsonl")

    records: list[dict] = []
    if part.exists() and not args.no_resume:
        for line in part.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        if records:
            print(f"[resume] 从断点续跑：已完成 {len(records)}/{len(cases)} 题", flush=True)
    done_qids = {r["qid"] for r in records}

    def flush_partial() -> None:
        tmp = part.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                       encoding="utf-8")
        tmp.replace(part)  # 原子替换：中断时不会留下半行 JSON

    map_fail = 0
    t_all = time.perf_counter()
    for i, case in enumerate(cases, 1):
        if case["qid"] in done_qids:
            continue
        t0 = time.perf_counter()
        if args.nogate:
            ranked, note = rag._semantic_rank(case["question"], [dict(c) for c in pool], cfg)
            hits = ranked[: args.top_k]
            mode = f"nogate+{note}"
            rerank_note = "off"
        else:
            # 评估组显式给定深度：0 表示「等于 top_k」，即未解耦的原始行为。
            # 不能传 None——None 会取业务默认（RERANK_DEPTH=20），那 A 组就不是基线了。
            hits, meta = rag.retrieve(case["question"], top_k=args.top_k, with_meta=True,
                                      rerank_depth=args.rerank_depth or args.top_k)
            mode = meta.get("retrieval_mode")
            rerank_note = meta.get("rerank")
        dt = (time.perf_counter() - t0) * 1000

        # 按 passage 去重：同一 doc 的多个 chunk 只保留最高排名，保证 passage 级口径
        seen: set[str] = set()
        ranked_ids: list[str] = []
        for h in hits:
            # retrieve() 输出的是 doc_title，而 _semantic_rank() 直接返回内部结构（键名 title）。
            # nogate 分支绕过了 retrieve 的组装，必须两个键都认，否则 doc 全映射成空、指标恒为 0。
            did = h.get("doc_title") or h.get("title") or ""
            if did and did not in seen:
                seen.add(did)
                ranked_ids.append(did)

        m = metrics_for_ranking(ranked_ids, set(case["gold"]))
        if hits and not ranked_ids:
            # 有命中却映射不出 doc：说明取错了字段（曾踩过 —— 指标全 0 却看着"跑完了"）
            map_fail += 1
        rec = {
            "qid": case["qid"], "question": case["question"], "gold": case["gold"],
            "mode": mode, "rerank": rerank_note,
            "n_hits": len(hits), "n_unique_docs": len(ranked_ids),
            "ranked_ids": ranked_ids[:10],
            "top_scores": [round(float(h.get("score") or 0), 4) for h in hits[:5]],
            "latency_ms": round(dt, 1),
            "empty": len(hits) == 0,
            **m,
        }
        records.append(rec)
        if len(records) % 20 == 0:
            flush_partial()
            done = records
            print(f"  [{len(done)}/{len(cases)}] ndcg@10={sum(r['ndcg@10'] for r in done)/len(done):.4f} "
                  f"r@10={sum(r['recall@10'] for r in done)/len(done):.4f} "
                  f"empty={sum(r['empty'] for r in done)}", flush=True)

    flush_partial()
    print(f"[done] {len(records)}/{len(cases)} 题", flush=True)

    elapsed = time.perf_counter() - t_all
    agg = aggregate(records)
    lat = sorted(r["latency_ms"] for r in records)
    mode_dist = Counter(r["mode"].split("(")[0] for r in records)
    hits_dist = Counter(r["n_hits"] for r in records)

    summary = {
        "dataset": {
            "name": "CmedqaRetrieval", "source": "mteb/CmedqaRetrieval via hf-mirror",
            "license": "见 MTEB 数据集卡片（C-MTEB 收录）",
            "full_size": {"corpus": 100001, "queries": 3999, "qrels": 7449},
            "used": {"doc_pool": len(docs), "cases": len(cases), "seed": SEED},
            "note": "抽样，分数不可与 publicbench 全量 10 万池的 33.93% 直接比较",
        },
        "config": {"top_k": args.top_k, "rerank": cfg.get("rerank_provider"),
                   "rerank_depth": args.rerank_depth or args.top_k,
                   "nogate": args.nogate, "embedding": eff.get("model"),
                   "schema": schema},
        "retrieval": {
            "recall@1": round(agg["recall@1"], 4),
            "recall@5": round(agg["recall@5"], 4),
            "recall@10": round(agg["recall@10"], 4),
            "ndcg@10": round(agg["ndcg@10"], 4),
            "mrr@10": round(agg["mrr@10"], 4),
            "empty_rate": round(sum(r["empty"] for r in records) / len(records), 4),
            "avg_hits": round(sum(r["n_hits"] for r in records) / len(records), 2),
        },
        "mode_dist": dict(mode_dist),
        "hits_dist": dict(sorted(hits_dist.items())),
        "latency_ms": {"p50": int(lat[len(lat) // 2]), "p95": int(lat[int(len(lat) * .95)]),
                       "mean": int(sum(lat) / len(lat))},
        "elapsed_s": round(elapsed, 1),
        "chunks_per_doc_max": max(per_doc.values()) if per_doc else 0,
    }

    (OUT / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(OUT / f"{stem}_records.jsonl", "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    part.unlink(missing_ok=True)  # 完整跑完才删断点，中断时留着续跑

    print(f"\n=== {args.label} | top_k={args.top_k} rerank={cfg.get('rerank_provider')} "
          f"nogate={args.nogate} ===")
    print(f"  nDCG@10   {agg['ndcg@10']:.4f}")
    print(f"  Recall@1/5/10  {agg['recall@1']:.4f} / {agg['recall@5']:.4f} / {agg['recall@10']:.4f}")
    print(f"  MRR@10    {agg['mrr@10']:.4f}")
    print(f"  空召回    {summary['retrieval']['empty_rate']:.3f}  平均命中 {summary['retrieval']['avg_hits']}")
    print(f"  mode      {dict(mode_dist)}")
    print(f"  延迟      p50={summary['latency_ms']['p50']}ms p95={summary['latency_ms']['p95']}ms")
    print(f"  产出      {OUT / (stem + '.json')}")
    if map_fail:
        print(f"  [WARN] {map_fail} 题有命中但映射不出 doc —— 指标不可信，先查字段名")

    # 默认保留 schema：B/C 组要靠 --reuse 复用同一份入库与向量。要清理就显式 --cleanup。
    if args.cleanup:
        drop_schema()
        print(f"[clean] schema {schema} 已删除")
    else:
        print(f"[keep] schema {schema} 保留（跑完执行 --schema {schema} --cleanup 清理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
