"""自适应重排：什么时候可以跳过 rerank（不花那 440ms）。

背景（实测）：query 向量缓存之后，检索链路的 83% 时间在外部 rerank（20 条候选约 440ms，
约 22ms/条）。但 rerank 的价值不是均匀分布的——它主要在**向量排序挤成一团**时才有用：
当 top1 明显领先 top2，重排几乎不可能把别的文档提上来，那 440ms 就白花了。

判定规则（可解释、可复现）：
    向量排序后取 s1、s2（cosine 相似度），若 s1 - s2 >= τ 则跳过 rerank，直接用向量顺序。

怎么定 τ 而不是拍脑袋：
1. **一次运行，全量采集**：每题各跑一遍「rerank 开」（得到重排顺序）与「rerank 关」
   （得到向量顺序），把两套 top-20 顺序与分数存下来；
2. **离线扫 τ**：对每个 τ 模拟「距 ≥ τ 就走向量序、否则走重排序」，算指标与跳过率；
   这样几十个 τ 只需 200 次 rerank，而不是几十轮 × 200 次；
3. **算后悔率**：在被跳过的题里，有多少题的结果与「不跳过」不同、有多少题掉了分。
   只看均值会被平均掉，后悔率才是这个规则的风险上限。

延迟不做二次测量，直接用两遍的实测值合成：
    expected = Σ (跳过 ? lat_off : lat_on) / N
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path = [p for p in sys.path if not p or Path(p).resolve() != HERE]
sys.path.insert(0, str(ROOT))

OUT = HERE / "out"

TAUS = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15)


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round((len(xs) - 1) * p))))]


def _connect() -> str:
    from webapp.envfile import load_env
    load_env()
    import psycopg
    cands = []
    if os.environ.get("WEBAPP_DATABASE_URL"):
        cands.append((os.environ["WEBAPP_DATABASE_URL"], "env"))
    cands.append(("postgresql://kb:kb_demo_only@127.0.0.1:5432/kb", "default"))
    for url, src in cands:
        try:
            psycopg.connect(url, connect_timeout=4).close()
            os.environ["WEBAPP_DATABASE_URL"] = url
            print(f"[db] {src} -> {url.split('@')[-1]}", flush=True)
            return url
        except Exception as e:  # noqa: BLE001
            print(f"[db] {src} 不可用: {type(e).__name__}", flush=True)
    raise SystemExit("没有可用数据库")


def _metrics(order: list[str], gold: set[str]) -> dict:
    from evalfmt.publicbench import metrics_for_ranking
    return metrics_for_ranking(order, gold, ks=(1, 5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="kb_depth", help="复用已入库的 schema")
    ap.add_argument("--docs", type=int, default=3000)
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=5, help="线上 top_k")
    ap.add_argument("--depth", type=int, default=20, help="rerank 深度")
    ap.add_argument("--json-out", default=str(OUT / "adaptive_rerank.json"))
    ap.add_argument("--records", default=str(OUT / "adaptive_rerank_records.jsonl"))
    ap.add_argument("--rerank", default="external", choices=["external", "llm"],
                    help="写进隔离 schema 的 app_settings（不写就退回默认 off，两遍都不会重排）")
    ap.add_argument("--recollect", action="store_true", help="忽略已有 records，重新采集")
    args = ap.parse_args()

    _connect()
    os.environ["WEBAPP_DB_SCHEMA"] = args.schema
    from webapp import db, providers, rag
    from webapp.eval_retrieval_cmedqa import build_pool

    # 隔离 schema 里 app_settings 是空的，settings.get_all() 会退回 DEFAULTS（rerank=off）。
    # 不显式写入，两遍都会「优雅跳过」重排——实测踩过：200 题两遍顺序完全一样。
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            ("rerank_provider", args.rerank, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit()
        rp = conn.execute("SELECT value FROM app_settings WHERE key=?",
                          ("rerank_provider",)).fetchone()["value"]
    finally:
        conn.close()
    print(f"[settings] rerank_provider={rp}", flush=True)

    _, cases = build_pool(args.docs, args.cases)
    cases = cases[: args.cases]
    print(f"[cfg] schema={args.schema} cases={len(cases)} top_k={args.top_k} "
          f"depth={args.depth}", flush=True)

    rec_path = Path(args.records)
    rows: list[dict] = []
    if rec_path.exists() and not args.recollect:
        rows = [json.loads(x) for x in rec_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        print(f"[reuse] 复用采集结果 {len(rows)} 题", flush=True)

    if not rows:
        # 采集：每题两遍。rerank 关的那遍用 stub 顶掉（返回 None 即「优雅跳过」，
        # retrieve 会退回向量顺序），这样不必改库里的 rerank_provider 来回写。
        orig_rerank = providers.rerank
        t_warm = time.perf_counter()
        with open(rec_path, "w", encoding="utf-8") as fh:
            for i, case in enumerate(cases, 1):
                q = case["question"]
                t0 = time.perf_counter()
                on = rag.retrieve(q, top_k=args.depth, rerank_depth=args.depth)
                lat_on = (time.perf_counter() - t0) * 1000

                # 关重排那一遍必须**清掉 query 缓存**：否则它复用上一遍的向量，
                # lat_off 少算一次 embed（实测 281ms），合成出来的均值会系统性偏乐观。
                providers.QUERY_VEC_CACHE.clear()
                providers.rerank = lambda *a, **kw: None  # noqa: E731
                try:
                    t0 = time.perf_counter()
                    off = rag.retrieve(q, top_k=args.depth, rerank_depth=args.depth)
                    lat_off = (time.perf_counter() - t0) * 1000
                finally:
                    providers.rerank = orig_rerank

                rows.append({
                    "qid": case["qid"], "gold": case["gold"],
                    "emb_ids": [h["chunk_id"] for h in off],
                    "emb_titles": [h["doc_title"] for h in off],
                    "emb_scores": [round(float(h["score"]), 6) for h in off],
                    "rr_ids": [h["chunk_id"] for h in on],
                    "rr_titles": [h["doc_title"] for h in on],
                    "rr_scores": [round(float(h["score"]), 6) for h in on],
                    "lat_on": round(lat_on, 1), "lat_off": round(lat_off, 1),
                })
                fh.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
                fh.flush()
                if i % 20 == 0:
                    print(f"  [{i}/{len(cases)}] {(time.perf_counter()-t_warm)/i:.2f}s/题", flush=True)
        print(f"[collect] {len(rows)} 题采集完成", flush=True)

    def topk(order: list[str], titles: list[str], k: int) -> list[str]:
        """按 doc 去重后取前 k（production 就是 chunk→doc 去重再算指标）。"""
        uniq, seen = [], set()
        for t in titles:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
            if len(uniq) >= k:
                break
        return uniq

    base_on, base_off = [], []
    lat_on_all, lat_off_all = [], []
    for r in rows:
        gold = set(r["gold"])
        base_on.append(_metrics(topk(r["rr_ids"], r["rr_titles"], args.top_k), gold))
        base_off.append(_metrics(topk(r["emb_ids"], r["emb_titles"], args.top_k), gold))
        lat_on_all.append(r["lat_on"])
        lat_off_all.append(r["lat_off"])

    def agg(rs: list[dict]) -> dict:
        return {k: round(statistics.fmean(x[k] for x in rs), 4) for k in rs[0]}

    # 间距分布：先看看 s1-s2 到底长什么样，再决定 τ 的取值范围
    margins = [r["emb_scores"][0] - r["emb_scores"][1] if len(r["emb_scores"]) > 1 else 0.0
               for r in rows]
    margins.sort()

    sweep = []
    for tau in TAUS:
        rs, lat, skipped, regret, regret_r5, regret_r1 = [], [], 0, 0, 0, 0
        for r, on_m, off_m, lon, loff in zip(rows, base_on, base_off, lat_on_all, lat_off_all):
            m = r["emb_scores"][0] - r["emb_scores"][1] if len(r["emb_scores"]) > 1 else 0.0
            if m >= tau:
                skipped += 1
                pick, lat_pick = off_m, loff
                if r["emb_titles"][:args.top_k] != r["rr_titles"][:args.top_k]:
                    regret += 1
                if off_m["recall@5"] < on_m["recall@5"]:
                    regret_r5 += 1
                if off_m["recall@1"] < on_m["recall@1"]:
                    regret_r1 += 1
            else:
                pick, lat_pick = on_m, lon
            rs.append(pick)
            lat.append(lat_pick)
        m_agg = agg(rs)
        sweep.append({
            "tau": tau, "rerank_rate": round(1 - skipped / len(rows), 4),
            "skip": skipped, **m_agg,
            # p50 在「98ms / 810ms」双峰分布上没有代表性：跳过率一低，中位数就落在贵的那一峰。
            # 所以同时给均值和 p95，看收益请看 mean。
            "mean_ms": round(statistics.fmean(lat), 0),
            "p50_ms": round(_pct(lat, 0.5), 0), "p95_ms": round(_pct(lat, 0.95), 0),
            "regret_any": regret, "regret_r1": regret_r1, "regret_r5": regret_r5,
            "regret_rate_on_skipped": round(regret / skipped, 4) if skipped else 0.0,
        })

    report = {
        "schema": args.schema, "n": len(rows), "top_k": args.top_k, "depth": args.depth,
        "always_rerank": {**agg(base_on), "mean_ms": round(statistics.fmean(lat_on_all), 0),
                          "p50_ms": round(_pct(lat_on_all, 0.5), 0),
                          "p95_ms": round(_pct(lat_on_all, 0.95), 0), "rerank_rate": 1.0},
        "never_rerank": {**agg(base_off), "mean_ms": round(statistics.fmean(lat_off_all), 0),
                         "p50_ms": round(_pct(lat_off_all, 0.5), 0),
                         "p95_ms": round(_pct(lat_off_all, 0.95), 0), "rerank_rate": 0.0},
        "margin_pctl": {"p10": round(_pct(margins, 0.1), 4), "p25": round(_pct(margins, 0.25), 4),
                        "p50": round(_pct(margins, 0.5), 4), "p75": round(_pct(margins, 0.75), 4),
                        "p90": round(_pct(margins, 0.9), 4), "max": round(max(margins), 4)},
        "sweep": sweep,
    }
    out = Path(args.json_out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 基线 ==")
    for name in ("always_rerank", "never_rerank"):
        b = report[name]
        print(f"  {name:<14} nDCG@10 {b['ndcg@10']} R@1 {b['recall@1']} R@5 {b['recall@5']} "
              f"mean {b['mean_ms']}ms p50 {b['p50_ms']}ms p95 {b['p95_ms']}ms")
    print(f"== 间距 s1-s2 分位 ==", report["margin_pctl"])
    print("\n== 阈值扫描 ==")
    print(f"{'τ':>6} {'跳过':>6} {'rerank率':>9} {'nDCG@10':>8} {'R@1':>7} {'R@5':>7} "
          f"{'mean':>8} {'p95':>7} {'R5掉':>5} {'R1掉':>5}")
    for s in sweep:
        print(f"{s['tau']:>6.3f} {s['skip']:>6} {s['rerank_rate']:>9.2%} {s['ndcg@10']:>8.4f} "
              f"{s['recall@1']:>7.4f} {s['recall@5']:>7.4f} {s['mean_ms']:>7.0f}ms "
              f"{s['p95_ms']:>6.0f}ms {s['regret_r5']:>5} {s['regret_r1']:>5}")
    print(f"[out] {out}")


if __name__ == "__main__":
    main()
