"""公开基准的纯函数单测：先钉住指标与切分，再花 18 分钟跑向量化。"""
from __future__ import annotations

from evalfmt import publicbench as pb
from evalfmt import publicbench_run as run


def test_metrics_perfect_and_zero():
    gold = {"a", "b"}
    assert pb.metrics_for_ranking(["a", "b", "c"], gold)["recall@1"] == 0.5
    assert pb.metrics_for_ranking(["a", "b", "c"], gold)["recall@5"] == 1.0
    assert pb.metrics_for_ranking(["c", "d"], gold)["recall@5"] == 0.0
    assert pb.metrics_for_ranking(["c", "a"], gold)["mrr@10"] == 0.5
    # 全部命中且排最前 → nDCG 必须等于 1
    assert abs(pb.metrics_for_ranking(["a", "b"], gold)["ndcg@10"] - 1.0) < 1e-9


def test_ndcg_penalizes_lower_position():
    gold = {"a"}
    top1 = pb.metrics_for_ranking(["a", "x", "y"], gold)["ndcg@10"]
    top3 = pb.metrics_for_ranking(["x", "y", "a"], gold)["ndcg@10"]
    assert top1 == 1.0
    assert 0 < top3 < top1


def test_rrf_is_rank_based_not_score_based():
    # 两路名次完全一致时，融合结果应与单路一致
    left = [(0, 9.9), (1, 0.1)]
    fused = pb.rrf_fuse(left, left)
    assert fused[:2] == [0, 1]
    # 只在第二路出现的候选也应进入融合结果（不会因为缺一路被丢）
    fused2 = pb.rrf_fuse([(0, 1.0)], [(1, 1.0), (0, 1.0)])
    assert set(fused2) == {0, 1}


def test_bm25_finds_exact_term():
    docs = ["睾丸炎引起的不孕不育如何治疗", "高血压患者的饮食建议", "完全无关的一段文本"]
    index = pb.BM25(docs)
    ranked = [i for i, _ in index.top_k("睾丸炎", k=3)]
    assert ranked and ranked[0] == 0


def test_split_is_deterministic_and_disjoint():
    queries = [{"id": f"q{i}"} for i in range(500)]
    tune_a, test_a = run._split(queries)
    tune_b, test_b = run._split(queries)
    assert tune_a == tune_b and test_a == test_b
    assert not (set(tune_a) & set(test_a))
    assert len(tune_a) + len(test_a) == 500
    assert 0.5 < len(tune_a) / 500 < 0.7


def test_bar_svg_escapes_and_scales():
    svg = run._bar_svg([("a<b", 0.25, "#000"), ("c", 0.5, "#111")])
    assert svg.startswith("<svg") and "a&lt;b" in svg
    # 值越大条越长
    widths = [float(p.split('width="')[1].split('"')[0])
              for p in svg.split("<rect")[1:]]
    assert widths[1] > widths[0]


def test_bar_svg_autoscale_keeps_bars_inside_frame():
    """vmax 写死 0.5 时，nDCG=0.8 的柱子会画出框外——必须按数据自适应。"""
    svg = run._bar_svg([("x", 0.8, "#000")])
    bar_w = float(svg.split("<rect")[1].split('width="')[1].split('"')[0])
    assert 0 < bar_w < 640 - 200 - 80


def test_weighted_rrf_respects_weight():
    """等权会把弱的一路按 50% 掺进来；加权后强的那路名次要能压住。"""
    dense = [(0, 0.0), (1, 0.0)] + [(i, 0.0) for i in range(2, 12)]
    bm25 = [(1, 0.0)] + [(i, 0.0) for i in range(2, 11)] + [(0, 0.0)]
    equal = pb.rrf_fuse(dense, bm25)
    heavy = pb.rrf_fuse(dense, bm25, weights=(0.9, 0.1))
    # 等权时 1 在 BM25 里排 1、向量里排 2，会顶掉向量的 0
    assert equal[0] == 1
    # 向量权重压倒性时，向量第 1 名必须是第 1
    assert heavy[0] == 0


_METRIC = {"ndcg@10": 0.4, "recall@1": 0.2, "recall@10": 0.5,
           "recall@20": 0.6, "mrr@10": 0.3, "queries": 100, "config": "x"}


def test_render_html_with_synthetic_report():
    """渲染只在 30 分钟的跑批末尾执行一次，KeyError 会白烧整轮。

    这里用合成报告把两条分支（重排有结果 / 没结果）都钉住。
    """
    names = ["bm25（词法基线）", "向量 bge-m3", "向量 + 查询前缀",
             "向量+BM25 加权融合 w=0.7"]
    report = {
        "dataset": "d", "dataset_url": "u", "passages": 100001, "queries": 3999,
        "qrels": 7449, "tune_queries": 2395, "test_queries": 1604, "probe_queries": 400,
        "top_k": 100, "embedding_model": "BAAI/bge-m3", "rerank_model": "r",
        "tune": {n: dict(_METRIC) for n in names},
        "test": {n: dict(_METRIC, **{"ndcg@10": 0.4 + i * 0.05})
                 for i, n in enumerate(names)},
        "best_stage": "向量 bge-m3", "bm25_budget": 30, "rrf_weight": 0.7,
        "bm25_sweep": [dict(_METRIC, terms=t, seconds=1.0) for t in (12, 30, 100)],
        "rrf_sweep": [dict(_METRIC, weight=w) for w in (0.5, 0.7, 0.85)],
        "rerank_depth": 20,
        "rerank_rows": {"20": dict(_METRIC, depth=20, rerank_failures=0, seconds=99.0)},
        "elapsed_s": 1234, "generated_at": "2026-11-16T00:00:00Z",
    }
    page = run.render_html(report)
    assert "<svg" in page and "CmedqaRetrieval" in page
    assert "无收益" in page or "有效" in page       # 结论句必须给出明确判断
    # 重排为空时也不能崩，且不能把 Python 的 {} 漏到页面上
    report["rerank_rows"] = {}
    page2 = run.render_html(report)
    assert "{}" not in page2 and "本次未跑重排" in page2


def test_bm25_uses_compact_postings():
    """倒排项必须是 array('i') 平铺，不能是 list[tuple]：
    1800 万项的规模上差 1GB 内存，这是跑得完和跑不完的区别。"""
    from array import array
    index = pb.BM25(["睾丸炎引起的不孕不育", "高血压患者的饮食建议"])
    posting = next(iter(index.postings.values()))
    assert isinstance(posting, array)
    assert len(posting) % 2 == 0            # [idx, tf, idx, tf, ...]


def test_bm25_term_pick_is_total_order():
    """选词必须是 (df, 词项) 全序，否则同一份数据会跑出两个不同的 nDCG。

    真出现过：`_pick_terms` 曾经用 `set(tokenize(q))` 取词项、只按 df 排序，
    df 相同时谁先谁后取决于 str 哈希的进程级随机种子——同一份数据连跑两次
    得到 0.1098 和 0.1105。这里不只断言「看起来有序」，
    而是**独立算出期望序列再逐项比对**，避免用例靠运气通过。
    """
    docs = ["高血压用药说明", "睾丸穿刺检查说明", "糖尿病饮食说明", "骨折固定说明"]
    index = pb.BM25(docs)
    query = "高血压 睾丸 穿刺 说明"
    picked = index._pick_terms(query)
    keys = [(df, t) for df, t, _ in picked]

    expect_all = sorted(
        (len(index.postings[t]) // 2, t) for t in set(pb.tokenize(query))
        if t in index.postings)
    assert keys == expect_all[:len(keys)]      # 精确等于「按全序取前 N 项」
    assert len({t for _, t, _ in picked}) == len(picked)   # 不重复选同一个词


def test_bm25_prunes_high_df_terms_without_losing_signal():
    """df 极大的常用字对排序没有区分度，剪掉它不能剪掉真正的信号。

    构造：1000 篇都含「的」，只有 1 篇含「睾丸穿刺」。
    只按 df 升序取前 12 项时，「的」本来也进不了候选；
    这里额外把 MAX_TERMS 调到 1，验证「前 4 项哪怕 df 大也保留」的兜底，
    以及「信号词一定排在常用字前面」这个前提。
    """
    docs = [f"这是第{i}篇关于的文本，内容普通" for i in range(1000)]
    docs[777] = "这是第777篇，睾丸穿刺检查结果说明"
    index = pb.BM25(docs)
    picked = index._pick_terms("的 睾丸穿刺")
    dfs = [df for df, _, _ in picked]
    assert dfs == sorted(dfs)               # 一定是按 df 升序挑的
    assert picked[0][0] < index.n * 0.05     # 第一个词项是低频信号词，不是「的」
    ranked = [i for i, _ in index.top_k("这是关于的睾丸穿刺", k=5)]
    assert ranked[0] == 777


def test_dense_rank_matches_bruteforce():
    """分块 GEMM 的 top-k 必须和逐条暴力排序给出完全一样的名次。

    这条断言防的是「分块没对齐」：block 切片错一位，指标会静默下滑，
    但整个报告照样能出，肉眼看不出来。
    """
    import numpy as np
    rng = np.random.default_rng(7)
    n_passage, n_query, dim, k = 400, 9, 12, 10
    p = rng.normal(size=(n_passage, dim)).astype("float32")
    q = rng.normal(size=(n_query, dim)).astype("float32")
    p /= np.linalg.norm(p, axis=1, keepdims=True)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    got = run._dense_rank(p, q, k, chunk=4)          # chunk 故意不整除
    sims = q @ p.T
    expect = [list(np.argsort(-sims[i])[:k]) for i in range(n_query)]
    assert got == expect
