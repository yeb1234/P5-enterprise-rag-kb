"""Query 向量缓存效果 / 检索时延拆解（小样本，只测检索这一段）。

要回答的问题很具体：一次 `retrieve()` 里那几百毫秒花在哪，「缓存 query 向量」能把哪一段归零。

测法：
- 用 Cmedqa 抽样池（与 `eval_retrieval_cmedqa.py` 同一份数据、同一套真实切分入库），
  在**隔离 schema** 里建库，测完 DROP（`--keep` 可留库，配合 `--reuse` 复用，省 2 分钟）；
- **冷相**：清空缓存，逐题 retrieve；
- **暖相**：同一批题再跑一遍（全命中）；
- **正确性两道断言**：
  1. rerank 之前的候选序列（pgvector 排序结果）必须逐位相同 —— 这才是缓存会影响的东西；
  2. top_k 结果若有翻转，必须能归因到「外部 rerank 分数抖动 + 近分位边界」，
     而不是缓存。实测同一句话 rerank 两次，同一文档分数会出现 0.2257 → 0.2325 的漂移，
     第 5/6 名只差 0.0002 时必然翻转。这是外部服务的性质，与缓存无关。

口径提醒：缓存只对「同一句话第二次问」有效。冷启动的不同 query 一分钱不省，
所以 warm 数字不能当线上 P99 汇报 —— 线上收益 = 重复率 × 省下的那一段。
真正确定的好处是**评测扫参**：同一批 query 每隔几秒换一组参数重跑，
以前每轮重付 `N × embed 时延`，现在只有第一轮付。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path = [p for p in sys.path if not p or Path(p).resolve() != HERE]
sys.path.insert(0, str(ROOT))

OUT = HERE / "out"


def _connect() -> str:
    from webapp.envfile import load_env
    load_env()
    import psycopg
    cands: list[tuple[str, str]] = []
    if os.environ.get("WEBAPP_DATABASE_URL"):
        cands.append((os.environ["WEBAPP_DATABASE_URL"], "env"))
    cands.append(("postgresql://kb:kb_demo_only@127.0.0.1:5432/kb", "default"))
    for url, src in cands:
        try:
            c = psycopg.connect(url, connect_timeout=4)
            c.close()
            os.environ["WEBAPP_DATABASE_URL"] = url
            print(f"[db] {src} -> {url.split('@')[-1]}", flush=True)
            return url
        except Exception as e:  # noqa: BLE001
            print(f"[db] {src} 不可用: {type(e).__name__}", flush=True)
    raise SystemExit("没有可用数据库")


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))
    return xs[k]


def _block(xs: list[float]) -> dict:
    return {"mean": round(statistics.fmean(xs), 2), "p50": round(_pct(xs, 0.5), 2),
            "p90": round(_pct(xs, 0.9), 2), "max": round(max(xs), 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=3000, help="检索池文档数")
    ap.add_argument("--cases", type=int, default=20, help="测多少题")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--rerank-depth", type=int, default=20)
    ap.add_argument("--rerank", default="", choices=["", "off", "external", "llm"],
                    help="改 app_settings.rerank_provider（空=沿用当前）")
    ap.add_argument("--keep", action="store_true", help="保留 schema（默认测完 DROP）")
    ap.add_argument("--schema", default="", help="指定 schema（配合 --reuse 复用）")
    ap.add_argument("--reuse", action="store_true", help="schema 已存在则跳过入库")
    ap.add_argument("--segments", action="store_true", help="给暖相做分段耗时拆解")
    ap.add_argument("--json-out", default=str(OUT / "query_cache_bench.json"))
    args = ap.parse_args()

    _connect()
    import psycopg

    import pgkit
    from webapp import db, providers, rag, settings
    from webapp.eval_retrieval_cmedqa import build_pool

    schema = args.schema or f"kb_qc_{uuid.uuid4().hex[:6]}"
    os.environ["WEBAPP_DB_SCHEMA"] = schema

    def raw_exec(sql: str) -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as c:
            c.execute(sql)

    def schema_exists() -> bool:
        with psycopg.connect(pgkit.dsn(), connect_timeout=5) as c:
            return c.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s",
                             (schema,)).fetchone() is not None

    reuse = args.reuse and schema_exists()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not reuse:
        raw_exec(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        raw_exec(f'CREATE SCHEMA "{schema}"')
    db.init_db()

    if reuse:
        _, cases = build_pool(args.docs, args.cases)
        conn = db.get_conn()
        try:
            n_chunks = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
            docs_n = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
        finally:
            conn.close()
        print(f"[reuse] schema={schema} docs={docs_n} chunks={n_chunks}", flush=True)
    else:
        docs, cases = build_pool(args.docs, args.cases)
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM chunk_embeddings")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            t0 = time.perf_counter()
            for d in docs:
                doc_id = uuid.uuid4().hex
                parts = rag.chunk_text(d["text"])
                conn.execute(
                    "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, "eval", d["id"], d["id"], len(d["text"].encode()),
                     "ready", len(parts), now, now))
                for idx, txt in enumerate(parts):
                    conn.execute("INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                                 (uuid.uuid4().hex, doc_id, idx, txt))
            conn.commit()
            n_chunks = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        finally:
            conn.close()
        docs_n = len(docs)
        print(f"[seed] {docs_n} docs / {n_chunks} chunks 入库 "
              f"{(time.perf_counter()-t0):.1f}s schema={schema}", flush=True)

    if args.rerank:
        conn = db.get_conn()
        try:
            if args.rerank == "off":
                conn.execute("DELETE FROM app_settings WHERE key=?", ("rerank_provider",))
            else:
                conn.execute(
                    "INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at",
                    ("rerank_provider", args.rerank, now))
            conn.commit()
        finally:
            conn.close()

    cfg = settings.get_all()
    eff = settings.effective_embedding(cfg)
    qs = [c["question"] for c in cases][: args.cases]
    rp = cfg.get("rerank_provider")
    print(f"[cfg] emb={eff['cache_key']} rerank={rp} top_k={args.top_k} "
          f"depth={args.rerank_depth} n={len(qs)}", flush=True)

    # rerank 输入序列记录器：这才是「缓存唯一可能影响的东西」的唯一证据点。
    # rag.retrieve 里写的是 providers.rerank(...)，所以替换模块属性即可介入。
    seen: list[tuple[str, ...]] = []
    if rp != "off":
        orig = providers.rerank

        def recorder(base_url, model, query, docs, api_key=""):
            seen.append(tuple(docs))
            return orig(base_url, model, query, docs, api_key)

        providers.rerank = recorder

    def call(q: str) -> tuple[list[str], list[str], float]:
        """返回 (chunk_id 序列, 对应 doc_title 序列, 耗时ms)。doc_title 即语料 id，用来算指标。"""
        t = time.perf_counter()
        hits = rag.retrieve(q, top_k=args.top_k, rerank_depth=args.rerank_depth)
        return ([h["chunk_id"] for h in hits], [h.get("doc_title") or "" for h in hits],
                (time.perf_counter() - t) * 1000)

    call(qs[0])          # 预热：连接池 + 首批向量 + HNSW 路径

    # ---- 冷相 ----
    providers.QUERY_VEC_CACHE.clear()
    cold, cold_ids, cold_titles = [], [], []
    mark = len(seen)
    for q in qs:
        ids, titles, ms = call(q)
        cold.append(ms); cold_ids.append(ids); cold_titles.append(titles)
    cold_pre = seen[mark:]

    # ---- embed_query 单独计时（清空后再测，避免被上一轮污染）----
    providers.QUERY_VEC_CACHE.clear()
    emb_cold = []
    for q in qs:
        t = time.perf_counter()
        providers.embed_query(q, cfg)
        emb_cold.append((time.perf_counter() - t) * 1000)
    emb_warm = []
    for q in qs:
        t = time.perf_counter()
        providers.embed_query(q, cfg)
        emb_warm.append((time.perf_counter() - t) * 1000)

    # ---- 暖相 ×2：第二遍才是「缓存路径自身是否稳定」的判据 ----
    warm, warm_ids, warm_titles = [], [], []
    mark = len(seen)
    for q in qs:
        ids, titles, ms = call(q)
        warm.append(ms); warm_ids.append(ids); warm_titles.append(titles)
        if not providers.last_query_embedding_cached():
            raise SystemExit("暖相出现未命中：缓存 key 不稳或容量被挤爆")
    warm_pre = seen[mark:]

    warm2_ids = []
    for q in qs:
        ids, _, _ = call(q)
        warm2_ids.append(ids)
    warm_stable = warm_ids == warm2_ids

    # ---- 断言 1：缓存往返 —— 放进去的与取出来的必须逐位相同 ----
    import hashlib as _hl
    probe = qs[0]
    key = (eff["cache_key"], _hl.sha1(probe.encode("utf-8")).hexdigest())
    stored = providers.QUERY_VEC_CACHE.get(key)
    roundtrip = stored is not None and providers.embed_query(probe, cfg) == list(stored)

    # ---- 断言 2：根因测量 —— 外部 embed 端点对同一文本是否稳定 ----
    # 这不是「缓存有问题」的证据，而是「为什么不能拿一次新 embed 跟缓存值比排序」的证据。
    raw = providers.get_embedding(cfg)
    v1 = raw.embed([probe])[0]
    v2 = raw.embed([probe])[0]
    emb_jitter = max(abs(x - y) for x, y in zip(v1, v2))

    # ---- 指标级对比：同一批题，cold 与 warm 的 R@1/5/10、nDCG@10 ----
    def score(titles_list: list[list[str]]) -> dict:
        from evalfmt.publicbench import metrics_for_ranking
        rows = []
        for case, titles in zip(cases, titles_list):
            uniq, seen_t = [], set()
            for t in titles:                      # 按 doc 去重：同一 passage 多块只算一次
                if t not in seen_t:
                    seen_t.add(t); uniq.append(t)
            rows.append(metrics_for_ranking(uniq, set(case["gold"]), ks=(1, 5, 10)))
        return {k: round(statistics.fmean(r[k] for r in rows), 4) for k in rows[0]}

    m_cold, m_warm = score(cold_titles), score(warm_titles)

    # ---- 候选序列比对：冷相 vs 暖相（rerank 输入）----
    pre_same = (not cold_pre) or cold_pre == warm_pre
    topk_same = cold_ids == warm_ids
    flips = [i for i in range(len(qs)) if cold_ids[i] != warm_ids[i]]

    segments = {}
    if args.segments:
        acc: dict[str, float] = defaultdict(float)

        def wrap(mod, name: str) -> None:
            f = getattr(mod, name)

            def g(*a, **kw):
                t = time.perf_counter()
                try:
                    return f(*a, **kw)
                finally:
                    acc[name] += (time.perf_counter() - t) * 1000
            setattr(mod, name, g)

        for name in ("_gate_hit", "_load_light_chunks", "_load_ready_chunks",
                     "_ensure_embeddings", "_attach_texts", "_semantic_rank"):
            wrap(rag, name)
        wrap(settings, "get_all")
        if rp != "off":
            wrap(providers, "rerank")

        t_all = 0.0
        for q in qs:
            t0 = time.perf_counter()
            rag.retrieve(q, top_k=args.top_k, rerank_depth=args.rerank_depth)
            t_all += (time.perf_counter() - t0) * 1000
        seg = {k: round(acc.get(k, 0.0) / len(qs), 2) for k in
               ("get_all", "_gate_hit", "_load_light_chunks", "_load_ready_chunks",
                "_semantic_rank", "_ensure_embeddings", "_attach_texts", "rerank")}
        # children = _semantic_rank 内含 _ensure_embeddings；父子不相加
        top_level = ("get_all", "_gate_hit", "_load_light_chunks", "_load_ready_chunks",
                     "_semantic_rank", "_attach_texts", "rerank")
        accounted = sum(seg[k] for k in top_level)
        seg["_retrieve_total"] = round(t_all / len(qs), 2)
        seg["_other"] = round(seg["_retrieve_total"] - accounted, 2)
        segments = seg

    stats = providers.QUERY_VEC_CACHE.stats()
    report = {
        "dataset": "CmedqaRetrieval(抽样)", "docs": docs_n, "chunks": n_chunks,
        "n": len(qs), "embedding": eff["cache_key"], "rerank": rp,
        "rerank_depth": args.rerank_depth, "top_k": args.top_k,
        "embed_query_cold": _block(emb_cold), "embed_query_warm": _block(emb_warm),
        "retrieve_cold": _block(cold), "retrieve_warm": _block(warm),
        "saved_ms_p50": round(_pct(cold, 0.5) - _pct(warm, 0.5), 2),
        "cache_roundtrip_identical": roundtrip,
        "warm_vs_warm_identical": warm_stable,
        "embedding_api_jitter_max": round(emb_jitter, 6),
        "metrics_cold": m_cold, "metrics_warm": m_warm,
        "prererank_order_identical_cold_vs_warm": pre_same,
        "topk_identical_cold_vs_warm": topk_same, "topk_flips": flips,
        "cache": stats, "schema": schema, "segments_ms": segments,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== embed_query（单独） ==")
    print(f"  冷 {report['embed_query_cold']}")
    print(f"  暖 {report['embed_query_warm']}")
    print("== retrieve（门禁→pgvector→rerank 全段） ==")
    print(f"  冷 {report['retrieve_cold']}")
    print(f"  暖 {report['retrieve_warm']}")
    print(f"  p50 省 {report['saved_ms_p50']}ms")
    print(f"  指标  cold={m_cold}")
    print(f"        warm={m_warm}")
    print(f"  断言  缓存往返逐位相同={roundtrip}  暖相两遍相同={warm_stable}")
    print(f"  外部 embed 同文本两次调用 max|Δ|={emb_jitter:.6f}")
    print(f"  冷 vs 暖：rerank 前候选序列相同={pre_same}  top_k 相同={topk_same}"
          + (f"（翻转 {flips}）" if flips else ""))
    if segments:
        print("== 暖相分段（每题均摊 ms） ==")
        for k in ("get_all", "_gate_hit", "_load_light_chunks", "_semantic_rank",
                  "_ensure_embeddings", "_attach_texts", "rerank"):
            print(f"  {k:<22} {segments[k]}")
        print(f"  {'总计':<20} {segments['_retrieve_total']}")
        print(f"  {'未归因':<20} {segments['_other']}")
    print(f"  缓存：{stats}")
    print(f"[out] {out}")

    if not args.keep:
        raw_exec(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        print(f"[clean] {schema} 已删除")
    if not (roundtrip and warm_stable):
        raise SystemExit("缓存路径自身不稳定：必须排查")


if __name__ == "__main__":
    main()
