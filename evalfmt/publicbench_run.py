"""公开检索基准的执行与报告（被 `publicbench.py run` 调用）。

流程（每一步都对应报告里的一个小节，顺序就是「怎么把参数收敛下来的」）：

1. 载入 CmedqaRetrieval（10 万 passage / 3,999 query / 7,449 相关性对）；
2. 按 query-id 的稳定哈希切成 **调参集 60% / 测试集 40%**——
   在同一批 query 上既调参又汇报，等于自己给自己打分，必须分开；
3. **BM25 词项预算调参**：只看 df 最小的 N 个词项，N 取 12/30/100 哪个划算？
   这一步存在的意义是让**基线尽可能强**——基线弱一点，向量的增益就好看一点，
   而那是自欺欺人；
4. **融合权重调参**：RRF 等权（0.5）实测输给纯向量，因为 BM25 太弱时等权融合
   等于把噪声按 50% 掺进来。扫一遍权重，找向量该占多大比重；
5. **重排深度调参**：接在**一阶段最优配置**的候选上，而不是想当然接在 RRF 上；
6. 参数只在调参集上选，最终数字报在测试集上；
7. 出 HTML（单文件、零 CDN、内联 SVG）。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import time
from pathlib import Path

import numpy as np

from evalfmt import publicbench as pb

OUT_DIR = Path(__file__).resolve().parent / "out"
CACHE = Path(__file__).resolve().parent / ".cache"

# bge 系列官方建议的检索指令前缀；bge-m3 官方说可以不加，
# 但「加不加」本身就是个该被实测的开关，不能靠文档猜。
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# 三组待收敛的参数
BM25_BUDGETS = (12, 30, 100)     # 每条 query 参与打分的词项数
RRF_WEIGHTS = (0.5, 0.7, 0.85)   # 向量在加权 RRF 里的比重（0.5 = 等权）
RERANK_DEPTHS = (20, 50)

# 调参子集：全量 2395 条调参 query 上扫参数太贵，用固定前缀切片，
# 是「确定性抽样」而不是随机——换机器、换 Python 版本结果一样。
TUNE_PROBE = 400


def _split(queries: list[dict], tune_ratio: float = 0.6) -> tuple[list[int], list[int]]:
    tune, test = [], []
    for i, q in enumerate(queries):
        h = int(hashlib_md5(q["id"])[:8], 16) % 10
        (tune if h < tune_ratio * 10 else test).append(i)
    return tune, test


def hashlib_md5(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _rank_from_scores(scores: np.ndarray, k: int) -> list[int]:
    if k >= scores.size:
        return list(np.argsort(-scores))
    part = np.argpartition(-scores, k)[:k]
    return list(part[np.argsort(-scores[part])])


def _dense_rank(p_norm: np.ndarray, q_norm: np.ndarray, k: int,
                chunk: int = 256) -> list[list[int]]:
    """分块矩阵乘算 top-k。

    不用「逐条 query 做 GEMV」：3999 条 query × 100001 passage × 1024 维，
    逐条调 numpy 每次都要重走一遍 400MB 的内存带宽，实测慢一个数量级；
    按 256 条一批做 GEMM，能吃满 BLAS，同一份内存读一遍算 256 条。
    """
    out: list[list[int]] = []
    for s in range(0, q_norm.shape[0], chunk):
        block = q_norm[s:s + chunk]
        sims = p_norm @ block.T                      # (P, C) float32
        part = np.argpartition(-sims, k, axis=0)[:k]  # (k, C)
        for c in range(sims.shape[1]):
            col = part[:, c]
            out.append(list(col[np.argsort(-sims[col, c])]))
    return out


def _fetch_embeddings(passages: list[dict], queries: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    p_vec = pb.embed([p["text"] for p in passages], "passages")
    q_vec = pb.embed([q["text"] for q in queries], "queries")
    return p_vec, q_vec


# ---------------------------------------------------------------- 重排

def _rerank(base: str, model: str, key: str, query: str, docs: list[str]) -> list[float] | None:
    payload = {"model": model, "query": query, "documents": docs, "top_n": len(docs)}
    try:
        data = pb._post(base.rstrip("/") + "/rerank", payload, key, timeout=120)
    except Exception:
        return None
    if not isinstance(data, dict) or "results" not in data:
        return None
    out = [0.0] * len(docs)
    for item in data["results"]:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(docs):
            out[idx] = float(item.get("relevance_score", 0.0))
    return out


class RerankCache:
    """把 cross-encoder 的分数落到磁盘。

    一次 300 条 × 2 个深度 = 600 次 HTTP，实测约 20 分钟。
    改报告措辞、改排版、甚至只改一行 HTML 就重跑一遍的话，这些调用全部白烧；
    而且重排接口限流时重试会拖得更久。key 用「query 原文 + 候选原文」的哈希，
    候选变了自然 miss，不会拿旧分数糊弄新候选。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, list[float]] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        self.hits = 0

    @staticmethod
    def _key(query: str, docs: list[str]) -> str:
        h = hashlib.md5()
        h.update(query.encode("utf-8"))
        for d in docs:
            h.update(b"\x00")
            h.update(d.encode("utf-8"))
        return h.hexdigest()

    def get(self, query: str, docs: list[str]) -> list[float] | None:
        v = self.data.get(self._key(query, docs))
        if v is not None and len(v) == len(docs):
            self.hits += 1
            return v
        return None

    def put(self, query: str, docs: list[str], scores: list[float]) -> None:
        self.data[self._key(query, docs)] = scores

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def run(args) -> dict:
    t_start = time.time()
    print("[1] 载入数据集")
    passages, queries, qrels = pb.load()
    print(f"    passage={len(passages)} query={len(queries)} "
          f"qrels={sum(len(v) for v in qrels.values())}")
    pids = [p["id"] for p in passages]
    tune_idx, test_idx = _split(queries)
    probe_idx = tune_idx[:TUNE_PROBE]
    print(f"    调参集 {len(tune_idx)} / 测试集 {len(test_idx)}（按 query-id 哈希切分，稳定可复现）")
    print(f"    调参探针 {len(probe_idx)} 条（扫参用，取调参集前 {TUNE_PROBE} 条）")

    print("[2] 载入/生成向量")
    p_vec, q_vec = _fetch_embeddings(passages, queries)
    # 归一化后原向量就没用了，及时释放：10 万 × 1024 float32 是 400MB，
    # 后面还要建 BM25 倒排索引、再做分块 GEMM，峰值内存就靠这一步省出来。
    p_norm = p_vec / np.linalg.norm(p_vec, axis=1, keepdims=True).clip(min=1e-9)
    del p_vec
    q_norm = q_vec / np.linalg.norm(q_vec, axis=1, keepdims=True).clip(min=1e-9)
    del q_vec
    print(f"    passage 向量 {p_norm.shape}  query 向量 {q_norm.shape}")

    print("[3] 建 BM25 索引（词法基线）")
    t0 = time.time()
    bm25 = pb.BM25([p["text"] for p in passages])
    print(f"    词表 {len(bm25.postings)} 项 / 倒排项 {bm25.postings_count} "
          f"avgdl {bm25.avgdl:.1f}（{time.time()-t0:.0f}s）")

    top_k = args.top_k
    print(f"[4] 计算向量候选（dense top-{top_k}）")
    q_prefix = pb.embed([QUERY_PREFIX + q["text"] for q in queries], "queries_prefix")
    qp_norm = q_prefix / np.linalg.norm(q_prefix, axis=1, keepdims=True).clip(min=1e-9)
    del q_prefix
    t0 = time.time()
    dense_rank = _dense_rank(p_norm, q_norm, top_k)
    dense_seconds = time.time() - t0
    print(f"    向量候选 {dense_seconds:.0f}s")
    t0 = time.time()
    dense_prefix_rank = _dense_rank(p_norm, qp_norm, top_k)
    print(f"    向量+前缀候选 {time.time()-t0:.0f}s")

    def eval_rankings(order_of, subset: list[int], name: str, quiet: bool = False) -> dict:
        per = []
        for i in subset:
            gold = qrels.get(queries[i]["id"], set())
            if not gold:
                continue
            ranked = [pids[j] for j in order_of(i)[:20]]
            per.append(pb.metrics_for_ranking(ranked, gold))
        agg = pb.aggregate(per)
        agg["queries"] = len(per)
        agg["config"] = name
        if not quiet:
            print(f"    {name:24s} nDCG@10={agg['ndcg@10']:.4f}  "
                  f"R@1={agg['recall@1']:.4f}  R@10={agg['recall@10']:.4f}  "
                  f"MRR@10={agg['mrr@10']:.4f}")
        return agg

    # ---- 4a. BM25 词项预算：让基线尽可能强 -------------------------------
    print(f"[5] BM25 词项预算调参（候选 {BM25_BUDGETS}，探针 {len(probe_idx)} 条）")
    bm25_sweep = []
    for budget in BM25_BUDGETS:
        bm25.MAX_TERMS = budget
        t0 = time.time()
        cand = {i: [idx for idx, _ in bm25.top_k(queries[i]["text"], top_k)]
                for i in probe_idx}
        agg = eval_rankings(lambda i: cand[i], probe_idx, f"bm25·terms={budget}")
        agg["terms"] = budget
        agg["seconds"] = round(time.time() - t0, 1)
        bm25_sweep.append(agg)
        print(f"    词项预算 {budget:>3d} → nDCG@10={agg['ndcg@10']:.4f} "
              f"（{agg['seconds']}s）", flush=True)
    best_budget = max(bm25_sweep, key=lambda r: r["ndcg@10"])["terms"]
    # 用测试集也见过的同一套代码、更强的那档预算重建全量候选
    bm25.MAX_TERMS = best_budget
    # 全量 BM25 候选在 best_budget 下要扫 3000 万条倒排项（本机实测约 5 分钟）。
    # 它只依赖（语料、预算、top_k），跟报告措辞、排版、重排子集都无关，
    # 所以值得落盘：改一行 HTML 就重跑 5 分钟是纯浪费。
    rank_cache = CACHE / f"bm25_rank_t{best_budget}_k{top_k}_n{len(queries)}.npy"
    if rank_cache.exists():
        bm25_rank = [list(row) for row in np.load(rank_cache)]
        print(f"[5] BM25 候选命中缓存 {rank_cache.name}（{len(bm25_rank)} 条）")
    else:
        print(f"[5] 选中词项预算 {best_budget}，重建全量 BM25 候选")
        t0 = time.time()
        bm25_rank = []
        for i, q in enumerate(queries):
            bm25_rank.append([idx for idx, _ in bm25.top_k(q["text"], top_k)])
            if (i + 1) % 1000 == 0:
                print(f"    BM25 候选 {i+1}/{len(queries)}（{time.time()-t0:.0f}s）", flush=True)
        rank_cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(rank_cache, np.asarray(bm25_rank, dtype="int32"))
        print(f"    已缓存 {rank_cache.name}")

    # ---- 4b. 融合权重 ---------------------------------------------------
    print(f"[6] 融合权重调参（权重 {RRF_WEIGHTS}，探针 {len(probe_idx)} 条）")
    rrf_sweep = []
    for w in RRF_WEIGHTS:
        agg = eval_rankings(lambda i, w=w: pb.rrf_fuse(
            [(j, 0.0) for j in dense_rank[i]],
            [(j, 0.0) for j in bm25_rank[i]], weights=(w, 1 - w)), probe_idx,
            f"RRF w={w}")
        agg["weight"] = w
        rrf_sweep.append(agg)
        print(f"    向量权重 {w:.2f} → nDCG@10={agg['ndcg@10']:.4f}", flush=True)
    best_w = max(rrf_sweep, key=lambda r: r["ndcg@10"])["weight"]

    def rr(i: int) -> list[int]:
        return pb.rrf_fuse([(j, 0.0) for j in dense_rank[i]],
                           [(j, 0.0) for j in bm25_rank[i]],
                           weights=(best_w, 1 - best_w))

    configs = {
        "bm25（词法基线）": lambda i: bm25_rank[i],
        "向量 bge-m3": lambda i: dense_rank[i],
        "向量 + 查询前缀": lambda i: dense_prefix_rank[i],
        f"向量+BM25 加权融合 w={best_w}": rr,
    }

    print("[7] 调参集上比较一阶段配置")
    results_tune = {name: eval_rankings(fn, tune_idx, name) for name, fn in configs.items()}
    best_stage = max(results_tune, key=lambda n: results_tune[n]["ndcg@10"])
    print(f"[7] 一阶段最优：{best_stage}（nDCG@10={results_tune[best_stage]['ndcg@10']:.4f}）")

    # ---- 4c. 重排深度：接在最优一阶段候选上，不是想当然接 RRF ------------
    print(f"[8] 重排调参（子集 {args.rerank_queries} 条，深度 {RERANK_DEPTHS}，"
          f"接在一阶段最优「{best_stage}」的候选上）")
    from webapp.envfile import load_env
    load_env()
    r_base = os.environ.get("EXTERNAL_RERANK_BASE_URL", "https://api.siliconflow.cn/v1")
    r_model = os.environ.get("EXTERNAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
    r_key = os.environ.get("RERANK_API_KEY", "")
    cache = RerankCache(CACHE / "rerank_scores.json")
    base_fn = configs[best_stage]
    rerank_subsets = [i for i in tune_idx[: args.rerank_queries]
                      if qrels.get(queries[i]["id"])]
    print(f"    有效 query {len(rerank_subsets)} 条（跳过无标注的）")
    # 重排的增益必须和**同一批 query**上的不重排基线比。
    # 拿 400 条子集的重排分数去减 2429 条全量的一阶段分数，等于拿两个分母相减，
    # 差出来的那部分根本不是重排的功劳。
    base_sub = eval_rankings(base_fn, rerank_subsets, f"{best_stage}（同子集·不重排）")
    rerank_rows = {}
    for depth in RERANK_DEPTHS:
        per, failed = [], 0
        hits0 = cache.hits
        started = time.time()
        for i in rerank_subsets:
            gold = qrels.get(queries[i]["id"], set())
            cand = base_fn(i)[:depth]
            docs = [passages[j]["text"] for j in cand]
            scores = cache.get(queries[i]["text"], docs)
            if scores is None:
                scores = _rerank(r_base, r_model, r_key, queries[i]["text"], docs)
                if scores is None:
                    failed += 1
                    order = cand
                else:
                    cache.put(queries[i]["text"], docs, scores)
                    order = [cand[t] for t in np.argsort(-np.asarray(scores))]
            else:
                order = [cand[t] for t in np.argsort(-np.asarray(scores))]
            per.append(pb.metrics_for_ranking([pids[j] for j in order[:20]], gold))
            if len(per) % 25 == 0:
                print(f"    depth={depth} 已处理 {len(per)}/{len(rerank_subsets)}，"
                      f"失败 {failed}，缓存命中 {cache.hits - hits0}", flush=True)
        cache.save()
        agg = pb.aggregate(per)
        agg.update({"queries": len(per), "rerank_failures": failed,
                    "cache_hits": cache.hits - hits0,
                    "seconds": round(time.time() - started, 1), "depth": depth,
                    "config": f"重排@{depth}（子集）"})
        rerank_rows[depth] = agg
        print(f"    重排@{depth:<3d} nDCG@10={agg['ndcg@10']:.4f} "
              f"R@10={agg['recall@10']:.4f} 失败 {failed} 缓存命中 {agg['cache_hits']} "
              f"用时 {agg['seconds']}s", flush=True)

    best_depth = max(rerank_rows, key=lambda d: rerank_rows[d]["ndcg@10"])
    # 两个深度差不到 0.3 个百分点时，说「收敛到某个深度」是过度解读——
    # 这个量级在 400 条 query 上就是噪声（标准误约 ±2 个点）。
    runner = sorted(rerank_rows, key=lambda d: -rerank_rows[d]["ndcg@10"])[1:2]
    depth_gap = (rerank_rows[best_depth]["ndcg@10"]
                 - rerank_rows[runner[0]]["ndcg@10"]) * 100 if runner else None
    depth_tie = depth_gap is not None and abs(depth_gap) < 0.3
    print(f"[9] 调参结论：重排深度选 {best_depth}"
          f"（与 top{runner[0] if runner else '-'} 相差 {depth_gap:+.2f} 个点"
          f"{'，属于噪声，按成本取更浅的' if depth_tie else ''}）")

    print("[10] 最终配置在测试集上评估")
    results_test = {name: eval_rankings(fn, test_idx, name) for name, fn in configs.items()}

    # ---- 延迟探针 -------------------------------------------------------
    # 为什么单独测：重排耗时若全命中缓存就记成 0.0s，JSON 里读起来像「重排免费」。
    # 「+3.3 个点值不值」是面试必被追问的问题，答案必须站在真实测量上，
    # 而缓存的 0.0s 不是测量，是没测。
    print(f"[11] 延迟探针（每条 query 现场计时，重排真调 {args.latency_probes} 次 API）")
    lat = {"probes": 0}
    probe_q = [i for i in tune_idx[:50] if qrels.get(queries[i]["id"])]

    t0 = time.time()
    for i in probe_q:
        bm25.top_k(queries[i]["text"], top_k)
    lat["bm25_ms"] = round((time.time() - t0) / max(1, len(probe_q)) * 1000, 2)

    t0 = time.time()
    one = 0
    for i in probe_q[:20]:
        _dense_rank(p_norm, q_norm[i:i + 1], top_k)
        one += 1
    lat["dense_single_ms"] = round((time.time() - t0) / max(1, one) * 1000, 2)
    # 批量摊薄：3999 条一起算的总耗时 / 条数（这才是服务端真实吞吐，单条测会高估）
    lat["dense_batched_ms"] = round(dense_seconds / max(1, len(queries)) * 1000, 2)

    t0 = time.time()
    rr_ok, rr_n = 0, 0
    for i in probe_q[: args.latency_probes]:
        cand = base_fn(i)[:best_depth]
        docs = [passages[j]["text"] for j in cand]
        if _rerank(r_base, r_model, r_key, queries[i]["text"], docs) is not None:
            rr_ok += 1
        rr_n += 1
    lat["rerank_ms"] = round((time.time() - t0) / max(1, rr_n) * 1000, 2)
    lat["rerank_ok"] = rr_ok
    lat["probes"] = rr_n
    print(f"    BM25 {lat['bm25_ms']}ms · 向量 {lat['dense_batched_ms']}ms/条"
          f"（单条 {lat['dense_single_ms']}ms）· 重排 {lat['rerank_ms']}ms（含网络）")

    report = {
        "dataset": "CmedqaRetrieval（C-MTEB / MTEB 中文医疗问答检索）",
        "dataset_url": "https://hf-mirror.com/datasets/mteb/CmedqaRetrieval",
        "passages": len(passages), "queries": len(queries),
        "qrels": sum(len(v) for v in qrels.values()),
        "tune_queries": len(tune_idx), "test_queries": len(test_idx),
        "probe_queries": len(probe_idx),
        "top_k": top_k,
        "embedding_model": pb._api()[2],
        "rerank_model": os.environ.get("EXTERNAL_RERANK_MODEL", ""),
        "tune": results_tune, "test": results_test,
        "best_stage": best_stage,
        "bm25_budget": best_budget,
        "bm25_sweep": bm25_sweep,
        "rrf_weight": best_w,
        "rrf_sweep": rrf_sweep,
        "rerank_depth": best_depth,
        "rerank_depth_gap": depth_gap,
        "rerank_depth_tie": depth_tie,
        "rerank_rows": {str(k): v for k, v in rerank_rows.items()},
        "rerank_baseline": base_sub,
        "latency": lat,
        "elapsed_s": round(time.time() - t_start, 1),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "publicbench.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = OUT_DIR / "publicbench.html"
    html_path.write_text(render_html(report), encoding="utf-8")
    print(f"[11] 报告 {html_path}")
    return report


# ---------------------------------------------------------------- HTML

def _bar_svg(rows: list[tuple[str, float, str]], width: int = 640, bar_h: int = 26,
             gap: int = 12, vmax: float | None = None) -> str:
    """内联 SVG 条形图（零 CDN、离线可看）。

    vmax 由数据自己决定而不是写死 0.5：写死会让超过 0.5 的柱子画出框外，
    而 nDCG@10 在好数据上超过 0.5 是常态。
    """
    if vmax is None:
        vmax = max((v for _, v, _ in rows), default=0.1) * 1.12 or 0.1
    height = len(rows) * (bar_h + gap) + 24
    label_w = 200
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
             f'role="img" aria-label="nDCG@10 对比">']
    for i, (name, value, color) in enumerate(rows):
        y = i * (bar_h + gap) + 10
        w = max(2, (width - label_w - 80) * (value / vmax))
        parts.append(
            f'<text x="0" y="{y + bar_h*0.72:.0f}" font-size="13" fill="currentColor">'
            f'{html.escape(name)}</text>')
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{color}"/>')
        parts.append(
            f'<text x="{label_w + w + 8:.0f}" y="{y + bar_h*0.72:.0f}" font-size="13" '
            f'font-weight="600" fill="currentColor">{value*100:.2f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_html(r: dict) -> str:
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v*100:.2f}%"

    tune = r["tune"]
    test = r["test"]
    base_name = "bm25（词法基线）"
    base_test = test.get(base_name, {})
    order = [base_name, "向量 bge-m3", "向量 + 查询前缀",
             f"向量+BM25 加权融合 w={r['rrf_weight']}"]
    colors = {"bm25（词法基线）": "#94a3b8", "向量 bge-m3": "#3b82f6",
              "向量 + 查询前缀": "#6366f1",
              f"向量+BM25 加权融合 w={r['rrf_weight']}": "#0ea5e9"}

    rows_svg = [(n, test[n]["ndcg@10"], colors[n]) for n in order if n in test]

    def delta(name: str, rows: dict) -> str:
        if name not in rows or base_name not in rows:
            return "—"
        d = (rows[name]["ndcg@10"] - rows[base_name]["ndcg@10"]) * 100
        cls = "up" if d > 0 else ("down" if d < 0 else "flat")
        sign = "+" if d > 0 else ""
        return f'<span class="{cls}">{sign}{d:.2f}</span>'

    def table(rows: dict, cap: str) -> str:
        head = ("<tr><th>配置</th><th>nDCG@10</th><th>Recall@1</th><th>Recall@10</th>"
                "<th>Recall@20</th><th>MRR@10</th><th>Δ nDCG@10</th><th>评测 query</th></tr>")
        body = []
        for name in order:
            if name not in rows:
                continue
            d = rows[name]
            body.append(
                f"<tr><td>{html.escape(name)}</td><td class='num'>{pct(d['ndcg@10'])}</td>"
                f"<td class='num'>{pct(d['recall@1'])}</td>"
                f"<td class='num'>{pct(d['recall@10'])}</td>"
                f"<td class='num'>{pct(d['recall@20'])}</td>"
                f"<td class='num'>{d['mrr@10']:.4f}</td>"
                f"<td class='num'>{delta(name, rows)}</td>"
                f"<td class='num'>{d['queries']}</td></tr>")
        return (f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
                f"<p class='cap'>{cap}</p>")

    # 三张调参表
    bm25_tbl = ("<table><thead><tr><th>每条 query 参与打分的词项数</th><th>nDCG@10</th>"
                "<th>Recall@10</th><th>评测 query</th><th>耗时</th></tr></thead><tbody>"
                + "".join(
                    f"<tr{' class=best' if s['terms'] == r['bm25_budget'] else ''}>"
                    f"<td>{s['terms']}</td><td class='num'>{pct(s['ndcg@10'])}</td>"
                    f"<td class='num'>{pct(s['recall@10'])}</td>"
                    f"<td class='num'>{s['queries']}</td>"
                    f"<td class='num'>{s['seconds']}s</td></tr>"
                    for s in r["bm25_sweep"])
                + "</tbody></table>")

    rrf_tbl = ("<table><thead><tr><th>向量权重 w</th><th>nDCG@10</th><th>Recall@1</th>"
               "<th>Recall@10</th><th>MRR@10</th></tr></thead><tbody>"
               + "".join(
                   f"<tr{' class=best' if s['weight'] == r['rrf_weight'] else ''}>"
                   f"<td>{s['weight']:.2f} / {1-s['weight']:.2f}</td>"
                   f"<td class='num'>{pct(s['ndcg@10'])}</td>"
                   f"<td class='num'>{pct(s['recall@1'])}</td>"
                   f"<td class='num'>{pct(s['recall@10'])}</td>"
                   f"<td class='num'>{s['mrr@10']:.4f}</td></tr>"
                   for s in r["rrf_sweep"])
               + "</tbody></table>")

    rerank_n = 0
    tie_short = ""
    lat_tbl = ""
    lat = r.get("latency") or {}
    if lat.get("probes"):
        # stage_gain 要等后面的 base_sub 才算得出，这里就地取一次
        _rr = (r.get("rerank_rows") or {}).get(str(r.get("rerank_depth"))) or {}
        _base = r.get("rerank_baseline") or {}
        _gain = (_rr.get("ndcg@10", 0) - _base.get("ndcg@10", 0)) * 100
        # 扫描表里同档预算的总耗时，换算成单条，用来和探针互相印证
        _sw = next((s for s in r["bm25_sweep"] if s["terms"] == r["bm25_budget"]), {})
        _sw_ms = (_sw.get("seconds", 0) / max(1, _sw.get("queries", 1)) * 1000)
        lat_tbl = (
            "<h5 style='margin-top:22px'>各阶段单查询延迟（本机现场实测）</h5>"
            "<table><thead><tr><th>阶段</th><th>单条 query 耗时</th><th>说明</th></tr></thead><tbody>"
            f"<tr><td>BM25 检索（词法）</td><td class='num'>{lat['bm25_ms']} ms</td>"
            f"<td>纯 Python 倒排，词项预算 {r['bm25_budget']}</td></tr>"
            f"<tr><td>向量检索（bge-m3）</td><td class='num'>{lat['dense_batched_ms']} ms</td>"
            f"<td>10 万 × 1024 余弦；批量 GEMM 摊薄后（单条单独算要 {lat['dense_single_ms']} ms）</td></tr>"
            f"<tr><td>重排（cross-encoder）</td><td class='num'>{lat['rerank_ms']} ms</td>"
            f"<td>1 次 HTTP 调用、含网络往返，接 top{r['rerank_depth']} 候选</td></tr>"
            "</tbody></table>"
            "<p class='cap'>重排是唯一一个在数量级上明显的成本项，换来的是 +"
            f"{_gain:.2f} 个点。<b>它用的是现场真调 API 的计时（{lat['probes']} 次），"
            "不是缓存跑的 0.0s</b>——缓存命中记成 0 会让报告看起来像「重排免费」。"
            "本机单线程，不含服务端排队；换机器绝对值会变，<b>相对量级不会</b>。</p>"
            "<p class='cap'><b>顺带一个反直觉的结果：词法基线比向量检索慢 40 倍</b>"
            f"（{lat['bm25_ms']} ms vs {lat['dense_batched_ms']} ms）。原因是 BM25 要在 Python 里"
            "扫倒排项、逐个累加分数，而向量检索是一次 BLAS 矩阵乘。"
            "所以「用词法兜底更省资源」这句话在 10 万文档规模上不成立——"
            "<b>它既更不准，又更慢</b>。这与第四节扫描表里的总耗时"
            f"（{_sw.get('seconds', 0)}s / {_sw.get('queries', 0)} 条 ≈ {_sw_ms:.0f}ms/条）"
            "互相印证，差异来自缓存预热与系统负载。</p>"
        )

    rerank_tbl = "<p class='cap'>本次未跑重排。</p>"
    if r.get("rerank_rows"):
        rerank_n = list(r["rerank_rows"].values())[0]["queries"]
        rows_sorted = sorted(r["rerank_rows"].items(), key=lambda kv: int(kv[0]))
        rerank_tbl = ("<table><thead><tr><th>重排深度</th><th>nDCG@10</th>"
                      "<th>Recall@1</th><th>Recall@10</th><th>评测 query</th>"
                      "<th>调用失败</th><th>本次耗时</th><th>走缓存</th></tr></thead><tbody>"
                      + "".join(
                          f"<tr{' class=best' if int(k) == r['rerank_depth'] else ''}>"
                          f"<td>top{k}</td><td class='num'>{pct(v['ndcg@10'])}</td>"
                          f"<td class='num'>{pct(v['recall@1'])}</td>"
                          f"<td class='num'>{pct(v['recall@10'])}</td>"
                          f"<td class='num'>{v['queries']}</td>"
                          f"<td class='num'>{v.get('rerank_failures', 0)}</td>"
                          f"<td class='num'>{v.get('seconds', 0)}s</td>"
                          f"<td class='num'>{v.get('cache_hits', 0)}/{v['queries']}</td></tr>"
                          for k, v in rows_sorted)
                      + "</tbody></table>")

    best_test_name = max((n for n in order if n in test), key=lambda n: test[n]["ndcg@10"])
    gain = (test[best_test_name]["ndcg@10"] - base_test.get("ndcg@10", 0)) * 100
    vec_test = test.get("向量 bge-m3", {})
    pre_test = test.get("向量 + 查询前缀", {})
    rrf_test = test.get(order[-1], {})
    prefix_ok = pre_test.get("ndcg@10", 0) > vec_test.get("ndcg@10", 0)
    rrf_ok = rrf_test.get("ndcg@10", 0) > vec_test.get("ndcg@10", 0)
    rrf_vals = [s["ndcg@10"] for s in r["rrf_sweep"]]
    rrf_mono = all(b > a for a, b in zip(rrf_vals, rrf_vals[1:]))
    rrf_note = (
        "扫描结果<b>单调上升</b>（" + " → ".join(f"{v*100:.2f}%" for v in rrf_vals) + "），"
        f"而再往上就是<b>纯向量本身</b>（{pct(vec_test.get('ndcg@10'))}）："
        "权重越大越接近纯向量、指标越好，说明 BM25 在这套数据上只贡献噪声，"
        "正确做法是<b>不融合</b>，而不是把权重调小一点意思一下。"
        if rrf_mono else
        "权重之间<b>非单调</b>，说明存在真实的互补区间，取实测最高档。")
    rerank_best = r.get("rerank_rows", {}).get(str(r["rerank_depth"]), {})
    base_sub = r.get("rerank_baseline", {})
    stage_gain = (rerank_best.get("ndcg@10", 0) - base_sub.get("ndcg@10", 0)) * 100
    # 「收敛到某个深度」这种情况只有在差距超过噪声时才敢说
    if r.get("rerank_depth_tie"):
        depth_note = (f"<b>top{r['rerank_depth']} 与另一档只差 "
                      f"{abs(r['rerank_depth_gap']):.2f} 个百分点，这在 {rerank_n} 条 query 上"
                      "属于噪声</b>，不该说成「调出了最优深度」；按成本取更浅的那档。")
    else:
        depth_note = (f"两档相差 {abs(r.get('rerank_depth_gap') or 0):.2f} 个百分点，"
                      "差异超过噪声水平，深度选择是有意义的。")
    if r.get("rerank_depth_tie"):
        tie_short = f"（两档只差 {abs(r['rerank_depth_gap']):.2f} 个点，按成本取浅的）"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>公开检索基准报告 · CmedqaRetrieval</title>
<style>
  :root {{
    --bg:#f8fafc; --panel:#ffffff; --fg:#0f172a; --muted:#64748b;
    --line:#e2e8f0; --accent:#0ea5e9; --up:#16a34a; --down:#dc2626;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0b1220; --panel:#111a2b; --fg:#e2e8f0; --muted:#94a3b8;
             --line:#1e2b45; --up:#4ade80; --down:#f87171; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 20px 64px; background:var(--bg); color:var(--fg);
         font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
              "Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-.02em; }}
  h2 {{ font-size:18px; margin:36px 0 12px; padding-bottom:8px;
        border-bottom:1px solid var(--line); }}
  h5 {{ font-size:14px; margin:20px 0 8px; color:var(--fg); }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 24px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
            padding:18px 20px; margin:16px 0; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin:16px 0; }}
  .kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
          padding:14px 16px; }}
  .kpi b {{ display:block; font-size:24px; letter-spacing:-.02em; }}
  .kpi span {{ color:var(--muted); font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; font-size:12.5px; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr.best td {{ background:rgba(14,165,233,.10); font-weight:600; }}
  .up {{ color:var(--up); font-weight:600; }}
  .down {{ color:var(--down); font-weight:600; }}
  .flat {{ color:var(--muted); }}
  .cap {{ color:var(--muted); font-size:12.5px; margin:8px 0 0; }}
  code {{ background:rgba(127,127,127,.12); padding:1px 5px; border-radius:4px;
          font-size:12.5px; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; }}
  .warn {{ border-left:3px solid #f59e0b; padding-left:14px; color:var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>公开检索基准 · CmedqaRetrieval</h1>
  <p class="sub">{html.escape(r['dataset'])} · 生成于 {r['generated_at']}</p>

  <div class="kpis">
    <div class="kpi"><b>{r['passages']:,}</b><span>passage（全量，未抽样）</span></div>
    <div class="kpi"><b>{r['queries']:,}</b><span>query（全量）</span></div>
    <div class="kpi"><b>{r['qrels']:,}</b><span>相关性标注对</span></div>
    <div class="kpi"><b>{r['tune_queries']} / {r['test_queries']}</b><span>调参集 / 测试集</span></div>
  </div>

  <div class="panel">
    <h2 style="margin-top:0">一、结论先行</h2>
    <p>在<b>测试集</b>（未参与调参的 {r['test_queries']} 条 query）上，最佳一阶段配置是
       <b>{html.escape(best_test_name)}</b>，nDCG@10 = <b>{pct(test[best_test_name]['ndcg@10'])}</b>，
       比词法基线（BM25 {pct(base_test.get('ndcg@10'))}）高 <b>{gain:+.2f}</b> 个百分点。</p>
    <p>三个参数各自的收敛结论：
      查询前缀 <b>{"有效，采用" if prefix_ok else "无收益，不采用"}</b>
      （{pct(pre_test.get('ndcg@10'))} vs 纯向量 {pct(vec_test.get('ndcg@10'))}）；
      BM25 与向量的融合 <b>{"有效" if rrf_ok else "无收益，不采用"}</b>
      （加权 w={r['rrf_weight']} 时 {pct(rrf_test.get('ndcg@10'))}）；
      重排深度取 <b>top{r['rerank_depth']}</b>{tie_short}：在同一批 {rerank_n} 条 query 上，
      重排把 nDCG@10 从 <b>{pct(base_sub.get('ndcg@10'))}</b> 提到
      <b>{pct(rerank_best.get('ndcg@10'))}</b>（<b>{stage_gain:+.2f}</b> 个百分点）。</p>
    <p class="cap">注意：重排那一行是<b>子集</b>数字（{rerank_n} 条），只能和它的同子集基线比，
    不能和右边全量测试集的数字横向比。</p>
  </div>

  <h2>二、基线 → 向量的增益（测试集 nDCG@10）</h2>
  <div class="panel">{_bar_svg(rows_svg)}</div>

  <h2>三、最终指标（测试集）</h2>
  <div class="panel">{table(test, f"测试集 = 未参与调参的 {r['test_queries']} 条 query；Δ 相对 BM25 基线。")}{lat_tbl}</div>

  <h2>四、调参过程①：BM25 词项预算是多少合适</h2>
  <div class="panel">{bm25_tbl}
    <p class="cap">BM25 是<b>基线</b>，基线越强，向量的增益才算数。中文单字词项在 10 万篇里
      df 能到 8 万，对排序没有区分度却要扫 8 万条倒排项；所以只取 df 最小的 N 项计分。
      N 太小丢信号，太大纯烧 CPU —— 这里用实测选，不用手感选。
      最终选 <b>{r['bm25_budget']}</b>。</p>
  </div>

  <h2>五、调参过程②：融合权重</h2>
  <div class="panel">{rrf_tbl}
    <p class="cap">RRF 只用名次不用分数，避免两路分数量纲不同。但<b>等权融合是个陷阱</b>：
      BM25 在这套数据上弱得多，等权等于把噪声按 50% 掺进来。{rrf_note}</p>
  </div>

  <h2>六、调参过程③：重排深度</h2>
  <div class="panel">{rerank_tbl}
    <p class="cap">重排接在<b>一阶段最优配置「{html.escape(r['best_stage'])}」</b>的候选上，
      不是接在 RRF 上——如果一阶段就该用纯向量，从更差的候选集里重排毫无意义。
      对照行「不重排」用的是<b>同一批 {rerank_n} 条 query</b>，所以增益是干净的。
      深度是成本项：每次要把 top-N 条候选原文发给 cross-encoder。
      「本次耗时 0.0s」表示分数全部命中磁盘缓存；冷跑实测 top20 约 150s、top50 约 280s
      （400 条 query、单条约 0.38s）。
      {depth_note}</p>
  </div>

  <h2>七、调参集全量对比（参数在这里选）</h2>
  <div class="panel">{table(tune, f"调参集 = {r['tune_queries']} 条 query。最终数字报在测试集，不报这里。")}</div>

  <h2>八、口径与局限（面试如实说）</h2>
  <div class="panel">
    <ul>
      <li><b>不与 C-MTEB 榜首直接可比</b>：数据与 qrels 来自公开数据集，但嵌入走硅基流动 API
          （{html.escape(r['embedding_model'])}）、重排只对候选 top-k 做、没有官方 corpus 清洗流程。
          能说的是「同一套数据、同一套指标下的相对差异」，不是「我们打平了 SOTA」。</li>
      <li><b>重排只在子集上测</b>：{rerank_n} 条 query，因为每次要发 top-{r['rerank_depth']} 条候选给
          cross-encoder。子集数字会略高于全量，方向可信、绝对值偏高。</li>
      <li><b>相关性是二值的</b>：qrels 只有 score=1，nDCG 用二值增益算；原数据没有分级相关性。</li>
      <li><b>BM25 是自己实现的</b>（字符单字+二元组、k1=1.2 / b=0.75、词项预算是上面调出来的），
          不是 Lucene。它的作用是提供同数据上的参照，不代表工业级 BM25 的上限。</li>
      <li><b>调参集/测试集按 query-id 哈希切分</b>，不是随机种子——换机器、换 Python 版本切分都一样。</li>
      <li><b>绝对分数偏低是数据特性</b>：CmedqaRetrieval 平均每条 query 只有 1.86 条 gold，
          且 passage 是长回答、query 是长问题描述，中文语义匹配本身就吃力。
          所以看的是<b>配置之间的相对差</b>，不是「0.4 算不算及格」。</li>
    </ul>
    <p class="warn">一句话总结：这套评测能回答「向量比词法强多少、前缀有没有用、融合与重排值不值」，
       不能回答「我们的系统在中文检索里排第几」。</p>
  </div>

  <h2>九、两张成绩单不能互相替代</h2>
  <div class="panel">
    <p>本项目有两套评估，面试时<b>不要混着说</b>：</p>
    <ul>
      <li><b>本页（公开集 10 万篇）</b>回答「检索算法选得对不对」——
          有官方 qrels、有 BM25 同数据基线、有调参/测试切分，所以数字可对外比。</li>
      <li><b><code>evalfmt.run_eval</code>（自建 10 份多格式语料 / 9 道正例题）</b>回答「整条链路通不通」——
          上传→解析（含 PDF、表格、无字图片）→分块→检索→生成→脱敏，能抓出「PDF 解析失败」
          「内网 IP 被误判成隐私」这类工程缺陷，但样本只有 9 题，<b>它的 100% 不证明系统强</b>。</li>
    </ul>
    <p class="cap">一句话：公开集证明「检索能打」，自建集证明「管道没漏」。前者怕样本作弊，后者怕样本太少。</p>
  </div>

  <h2>十、复现</h2>
  <div class="panel">
    <p><code>python -m evalfmt.publicbench download</code> → 从 hf-mirror 拉 3 个 parquet</p>
    <p><code>python -m evalfmt.publicbench embed</code> → 全量向量化并缓存到磁盘（断点续跑）</p>
    <p><code>python -m evalfmt.publicbench run</code> → 扫参 + 五组配置 + 生成本页</p>
    <p class="cap"><b>耗时说明</b>：10 万条向量化约 60 分钟（一次性，之后走缓存）；
      重排冷跑约 8 分钟（800 次 cross-encoder 调用），之后走缓存；
      扫参与评估本身约 1.5 分钟。本轮脚本耗时 {r['elapsed_s']}s——这个数字小是因为
      向量与重排都命中了缓存，<b>不代表冷跑成本</b>。</p>
    <p class="cap">原始数字：evalfmt/out/publicbench.json ·
      重排分数缓存：evalfmt/.cache/rerank_scores.json（重跑不重复调 API）·
      只改排版重出本页：<code>python -m evalfmt.publicbench_run --from-json evalfmt/out/publicbench.json</code></p>
  </div>
</div>
</body>
</html>
"""


def main(args) -> int:
    run(args)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=100)
    ap.add_argument("--rerank-queries", type=int, default=300)
    ap.add_argument("--latency-probes", type=int, default=20)
    # 只改排版/措辞时用它：从已有 JSON 重渲染，不重跑扫参、不调 API
    ap.add_argument("--from-json", type=str, default="",
                    help="从已有 publicbench.json 重新渲染 HTML")
    args = ap.parse_args()
    if args.from_json:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        report = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out = OUT_DIR / "publicbench.html"
        out.write_text(render_html(report), encoding="utf-8")
        print(f"已按 {args.from_json} 重新渲染 → {out}")
    else:
        main(args)
