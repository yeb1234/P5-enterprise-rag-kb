"""公开检索基准 · CmedqaRetrieval（C-MTEB / MTEB 收录的中文医疗问答检索集）。

    python -m evalfmt.publicbench download     # 从 hf-mirror 拉数据（国内可直连）
    python -m evalfmt.publicbench embed        # 全量 10 万条 passage 向量化（缓存到磁盘）
    python -m evalfmt.publicbench run          # 跑多组配置 + 出 HTML 报告

为什么选它：
- **公认**：C-MTEB 是中文 MTEB 榜的官方中文子集，CmedqaRetrieval 是其中 6 个检索任务之一，
  MTEB 官方仓库（`mteb/CmedqaRetrieval`）也有同名镜像，带标准 `qrels`（query-id / corpus-id / score）。
- **规模合适**：100,001 条 passage、3,999 条 query、7,449 个相关性对，单机 numpy 可全量算，
  **不需要抽样**——抽样会让召回率失真，能全量就别抽。
- **有区分度**：医疗问答里大量「症状描述 → 建议」的长文本，词法重叠弱，正好能看出向量检索的价值。

口径提醒（对外说明别说错）：
- 这里的 nDCG@10 / Recall@k 是**本机复现**，语料与 qrels 来自公开数据集，
  但**不与 C-MTEB 榜首数字直接可比**：我们的 embedding 走硅基流动 API、rerank 只对 top-k 做，
  且没有做官方的 corpus 去重/清洗。要对比只能说「同一套数据、同一套指标下的相对差异」。
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
from array import array
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / ".cache"
DATASET = "mteb/CmedqaRetrieval"
HF = "https://hf-mirror.com/datasets/" + DATASET + "/resolve/main/"

FILES = {
    "corpus": "corpus/dev-00000-of-00001.parquet",
    "queries": "queries/dev-00000-of-00001.parquet",
    "qrels": "data/dev-00000-of-00001.parquet",
}

sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- 下载与装载

def _local(name: str) -> Path:
    return CACHE / f"cmedqa_{name}.parquet"


def download(force: bool = False) -> dict[str, Path]:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, rel in FILES.items():
        target = _local(name)
        if target.exists() and not force:
            print(f"  已存在 {name}: {target.stat().st_size/1e6:.1f} MB")
            continue
        url = HF + rel
        print(f"  下载 {name} ← {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=600) as resp, open(target, "wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        print(f"    {target.stat().st_size/1e6:.1f} MB")
    return {n: _local(n) for n in FILES}


def load() -> tuple[list[dict], list[dict], dict[str, set[str]]]:
    """返回 (passages, queries, qrels)。字段名在 C-MTEB / MTEB 两种布局间自适应。"""
    import pyarrow.parquet as pq

    corpus = pq.read_table(_local("corpus")).to_pylist()
    queries = pq.read_table(_local("queries")).to_pylist()
    rel_rows = pq.read_table(_local("qrels")).to_pylist()

    def pick(row: dict[str, object], *names: str) -> str:
        for n in names:
            if n in row:
                return str(row[n])
        raise KeyError(f"字段都不存在：{names} / 实际 {list(row)}")

    passages = [{"id": pick(r, "id", "_id"), "text": pick(r, "text")} for r in corpus]
    qs = [{"id": pick(r, "_id", "id", "query-id"), "text": pick(r, "text")} for r in queries]
    qrels: dict[str, set[str]] = defaultdict(set)
    for r in rel_rows:
        if int(r.get("score", 1)) > 0:
            qrels[str(r["query-id"])].add(str(r["corpus-id"]))
    return passages, qs, dict(qrels)


# ---------------------------------------------------------------- 嵌入

def _api() -> tuple[str, str, str]:
    from webapp.envfile import load_env
    load_env()
    base = os.environ.get("EXTERNAL_EMBED_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    key = os.environ.get("WEBAPP_EMBEDDING_API_KEY") or os.environ.get("RERANK_API_KEY", "")
    model = os.environ.get("EXTERNAL_EMBED_MODEL", "BAAI/bge-m3")
    return base, key, model


_RETRYABLE = (429, 500, 502, 503, 504)


def _post(url: str, payload: dict, key: str, timeout: int = 120) -> dict:
    """带退避的 POST。

    429 与 5xx 必须**分开对待**，这是 10 万条跑崩两次之后才想明白的：
    5xx 是抖动，2/4/8s 退几次就过去了；429 是服务端按**时间窗口**限速，
    重试 4 次（合计 15s）根本跨不过一个窗口，结果是「整批 128 条失败 →
    异常冒到 embed() → 20 分钟的活在第 80% 判死刑」。
    所以 429 给 13 次机会、退避封顶 30s，宁可慢也不能重头再来。
    """
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    tries = 13
    for attempt in range(tries):
        last = attempt == tries - 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read()[:300].decode("utf-8", "ignore")
            except Exception:
                pass
            # 400 是**输入**问题（典型：单条超模型上下文），重试没有意义，
            # 必须把响应体带出来，否则只看到 "HTTP Error 400" 根本没法定位。
            if exc.code not in _RETRYABLE:
                raise RuntimeError(f"HTTP {exc.code}: {body}") from None
            if last:
                raise RuntimeError(f"HTTP {exc.code}（重试 {tries} 次仍失败）: {body}") from None
            ceiling = 30 if exc.code == 429 else 8
            wait = min(ceiling, 2 ** attempt) + random.uniform(0, 1)
            print(f"    {exc.code}，{wait:.0f}s 后重试（{attempt+1}/{tries-1}）", flush=True)
            time.sleep(wait)
        except Exception as exc:
            if last:
                raise
            wait = min(10, 2 ** attempt) + random.uniform(0, 1)
            print(f"    {type(exc).__name__}，{wait:.0f}s 后重试（{attempt+1}/{tries-1}）",
                  flush=True)
            time.sleep(wait)
    return {}


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX_CHARS = 6000  # bge-m3 上下文 8192 token；6k 字符对中文足够安全


def _sanitize(text: str) -> str:
    """清洗后送模型：控制字符、超长、空串。

    不洗会直接 400：数据集里最长一条 60,975 字符，远超模型上下文，
    整批 64 条一起失败——报错只说「HTTP 400」的时候最难查的就是这种。
    """
    out = _CTRL.sub(" ", str(text))
    out = re.sub(r"\s+", " ", out).strip()
    return out[:MAX_CHARS] if out else "（空文本）"


def embed(texts: list[str], cache_name: str, batch: int = 64,
          model: str | None = None, workers: int = 2) -> "object":
    """带磁盘缓存的批量嵌入（并发 + 断点续跑）。

    三件必须做的事，少一件就会在 10 万条这种量级上翻车：
    1. **预分配 float32**：list[list[float]] 累积 100k×1024 个 Python float 要 3GB+，直接 OOM；
    2. **断点续跑**：APIs 会抖、会限流，跑 20 分钟挂在第 80% 不该从头再来；
    3. **并发**：单请求 128 条约 0.8s，服务端限速下 2 路并发能稳到 ~120 条/秒。
       实测 4 路并发只多出 1.4 倍（128 vs 93 条/秒），却开始大面积吃 429，
       **并发的瓶颈在服务端的窗口配额，不在本机线程**——加线程只会把重试打满。
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import numpy as np

    base, key, default_model = _api()
    model = model or default_model
    texts = [_sanitize(t) for t in texts]
    digest = hashlib.sha256(
        (model + "\x00" + "\x00".join(texts)).encode("utf-8")).hexdigest()[:16]
    cache = CACHE / f"emb_{cache_name}_{digest}.npy"
    if cache.exists():
        arr = np.load(cache)
        print(f"  向量缓存命中 {cache.name} shape={arr.shape}")
        return arr

    part = cache.with_suffix(".part.npy")
    mark = cache.with_suffix(".part.json")
    arr: "np.ndarray | None" = None
    done = 0
    if part.exists() and mark.exists():
        try:
            prev = np.load(part)
            saved = int(json.loads(mark.read_text())["done"])
            if prev.shape[0] == len(texts) and 0 < saved <= len(texts):
                arr, done = prev, saved
                print(f"  断点续跑：已有 {done} 条，从第 {done} 条继续", flush=True)
        except Exception:
            arr, done = None, 0

    print(f"  向量化 {len(texts)} 条（model={model}, batch={batch}, workers={workers}）…",
          flush=True)
    lock = threading.Lock()
    started = time.time()
    remaining = list(range(done, len(texts), batch))

    def work(start: int) -> int:
        nonlocal arr
        chunk = texts[start:start + batch]
        data = _post(base + "/embeddings",
                     {"model": model, "input": chunk, "encoding_format": "float"}, key)
        rows = sorted(data["data"], key=lambda d: d["index"])
        with lock:
            if arr is None:
                arr = np.empty((len(texts), len(rows[0]["embedding"])), dtype="float32")
            target = arr
        for offset, row in enumerate(rows):
            target[start + offset] = row["embedding"]
        return len(rows)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(work, st): st for st in remaining}
            last_flush = time.time()
            for finished, fut in enumerate(as_completed(futures), start=1):
                done += fut.result()
                # 按**时间**落盘而不是按条数：限速时 20 个 chunk 可能要跑好几分钟，
                # 按条数存会让「进程还活着」和「有进度」看起来是两件事。
                if finished % 20 == 0 or done >= len(texts) or time.time() - last_flush > 15:
                    assert arr is not None
                    np.save(part, arr)
                    mark.write_text(json.dumps({"done": done}), encoding="utf-8")
                    last_flush = time.time()
                    rate = done / max(1e-9, time.time() - started)
                    print(f"    {done}/{len(texts)}  {rate:.0f} 条/秒  "
                          f"剩余 {max(0, (len(texts)-done)/max(rate,1e-9)):.0f}s", flush=True)
    except Exception:
        # 失败也要把已完成的部分落盘，下次接着跑
        if arr is not None:
            np.save(part, arr)
            mark.write_text(json.dumps({"done": done}), encoding="utf-8")
            print(f"  中断：已保存 {done} 条进度", flush=True)
        raise

    np.save(cache, arr)
    part.unlink(missing_ok=True)
    mark.unlink(missing_ok=True)
    print(f"  已缓存 {cache.name} shape={arr.shape} "
          f"{cache.stat().st_size/1e6:.0f} MB", flush=True)
    return arr


# ---------------------------------------------------------------- 词法基线（BM25）

_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """中文按单字 + 相邻二元组，英文按整词。

    不用 jieba 是为了零依赖、结果可复现；二元组对中文短查询已经够用，
    也不会有「分词器版本变了指标就变」的问题。
    """
    text = text.lower()
    words = _TOKEN.findall(text)
    tokens: list[str] = []
    prev_cjk = ""
    for w in words:
        tokens.append(w)
        if len(w) == 1 and "\u4e00" <= w <= "\u9fff":
            if prev_cjk:
                tokens.append(prev_cjk + w)
            prev_cjk = w
        else:
            prev_cjk = ""
    return tokens


class BM25:
    """极简 BM25（k1=1.2, b=0.75），倒排索引，纯标准库。

    存在的意义是给向量检索一个**同数据上的基线**：
    只报向量分数没人能判断它到底好不好，报「向量 vs BM25」才有信息量。

    两处必须这么做，否则 10 万条中文文档跑不完（本机实测：平均 181 个 token/篇，
    全量约 1800 万个倒排项）：
    1. **倒排项用 `array('i')` 平铺存 [idx, tf, idx, tf, …]**：
       `list[tuple]` 在 1800 万项的规模上光对象头就要 1GB 以上，而 `array` 只占 120MB；
    2. **每条 query 只取 df 最小的 12 个词项计分**：中文单字词项（「的」「有」「不」）
       在 10 万篇里 df 能到 8 万，对排序毫无区分度，却要扫 8 万条 posting。
       剪掉它们是**工程近似**，会轻微改变 BM25 的排序——报告里必须写明，
       因为它影响的是「基线有多强」，进而影响「向量增益有多大」这个结论。
    """

    MAX_TERMS = 12
    MAX_DF_RATIO = 0.25

    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.postings: dict[str, "array"] = {}
        self.lengths = [0] * self.n
        for idx, doc in enumerate(docs):
            toks = tokenize(doc)
            self.lengths[idx] = len(toks)
            tf: dict[str, int] = defaultdict(int)
            for t in toks:
                tf[t] += 1
            for t, c in tf.items():
                p = self.postings.get(t)
                if p is None:
                    p = self.postings[t] = array("i")
                p.append(idx)
                p.append(c)
        self.avgdl = (sum(self.lengths) / self.n) if self.n else 0.0
        self.postings_count = sum(len(v) for v in self.postings.values()) // 2

    def _pick_terms(self, query: str) -> list[tuple[int, str, "array"]]:
        """挑出参与打分的词项，返回 [(df, 词项, 倒排), …]。

        排序键必须是 **(df, 词项)** 全序：
        只按 df 排时，df 相同的词项谁先谁后取决于 `set` 的迭代顺序，
        而 Python 对 str 的哈希带进程级随机种子——同一份数据连跑两次，
        nDCG 会给出 0.1098 和 0.1105 两个不同的数。
        「可复现」不是一句口号，排序键必须是全序。
        """
        cand = []
        for t in set(tokenize(query)):
            p = self.postings.get(t)
            if p:
                cand.append((len(p) // 2, t, p))
        cand.sort(key=lambda x: (x[0], x[1]))
        picked = []
        for df, t, p in cand:
            if len(picked) >= self.MAX_TERMS:
                break
            # 前 4 个词项无论 df 多大都保留，避免「整条 query 全是常用字」时无词可用
            if len(picked) >= 4 and df > self.n * self.MAX_DF_RATIO:
                continue
            picked.append((df, t, p))
        return picked

    def top_k(self, query: str, k: int = 100) -> list[tuple[int, float]]:
        scores: dict[int, float] = defaultdict(float)
        for df, _t, posting in self._pick_terms(query):
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            k1, b = self.k1, self.b
            for j in range(0, len(posting), 2):
                idx = posting[j]
                tf = posting[j + 1]
                dl = self.lengths[idx] or 1
                denom = tf + k1 * (1 - b + b * dl / (self.avgdl or 1.0))
                scores[idx] += idf * tf * (k1 + 1) / denom
        # 用 nlargest 而不是整表排序：10 万候选排一次 4000 遍没必要
        return heapq.nlargest(k, scores.items(), key=lambda kv: kv[1])


# ---------------------------------------------------------------- 指标

def metrics_for_ranking(ranked_ids: list[str], gold: set[str],
                        ks: tuple[int, ...] = (1, 5, 10, 20)) -> dict:
    """单条 query 的 Recall@k / MRR@10 / nDCG@10（二值相关性）。"""
    out = {}
    for k in ks:
        topk = set(ranked_ids[:k])
        out[f"recall@{k}"] = len(topk & gold) / len(gold) if gold else 0.0
    rr = 0.0
    for i, doc in enumerate(ranked_ids[:10], start=1):
        if doc in gold:
            rr = 1.0 / i
            break
    out["mrr@10"] = rr
    dcg = 0.0
    for i, doc in enumerate(ranked_ids[:10], start=1):
        if doc in gold:
            dcg += 1.0 / math.log2(i + 1)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), 10) + 1))
    out["ndcg@10"] = dcg / idcg if idcg else 0.0
    return out


def aggregate(per_query: list[dict]) -> dict:
    keys = per_query[0].keys()
    return {k: sum(r[k] for r in per_query) / len(per_query) for k in keys}


def rrf_fuse(*rankings: list[tuple[int, float]], k: int = 60,
             weights: tuple[float, ...] | None = None) -> list[int]:
    """Reciprocal Rank Fusion：只用名次，不用分数，避免两路分数量纲不同的问题。

    `weights` 是加权版：两路强弱差得远时，等权（0.5/0.5）等于把弱的那路当噪声掺进来。
    本项目实测就是这种情况——BM25 在这套中文医疗数据上 nDCG@10 只有向量的一半，
    等权融合的结果**比纯向量还差**。所以权重本身也要当参数扫。
    """
    if weights is None:
        weights = tuple(1.0 for _ in rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights 数量必须与 rankings 一致")
    score: dict[int, float] = defaultdict(float)
    for w, ranking in zip(weights, rankings):
        for rank, (idx, _) in enumerate(ranking, start=1):
            score[idx] += w / (k + rank)
    return [idx for idx, _ in sorted(score.items(), key=lambda kv: kv[1], reverse=True)]


# ---------------------------------------------------------------- 主流程（run 见下一步）

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CmedqaRetrieval 公开检索基准")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download")
    p_embed = sub.add_parser("embed")
    p_embed.add_argument("--batch", type=int, default=128)
    p_embed.add_argument("--workers", type=int, default=2)
    p_run = sub.add_parser("run")
    p_run.add_argument("--top-k", type=int, default=100)
    # 400 是文档与历史报告使用的固定子集；默认 300 会让同一命令跑出另一套
    # nDCG（35.09 vs 36.09），看起来像回归，实际只是抽样不同。
    p_run.add_argument("--rerank-queries", type=int, default=400)
    p_run.add_argument("--latency-probes", type=int, default=20,
                       help="延迟探针现场真调重排 API 的次数")
    args = ap.parse_args(argv)

    if args.cmd == "download":
        print("[1] 下载数据（hf-mirror.com）")
        download()
        passages, queries, qrels = load()
        print(f"  passage={len(passages)} query={len(queries)} qrels={sum(len(v) for v in qrels.values())}")
        return 0

    if args.cmd == "embed":
        passages, queries, _ = load()
        print(f"[2] 向量化 passage {len(passages)} 条")
        embed([p["text"] for p in passages], "passages", batch=args.batch,
              workers=args.workers)
        print(f"[2] 向量化 query {len(queries)} 条")
        embed([q["text"] for q in queries], "queries", batch=args.batch,
              workers=args.workers)
        return 0

    from evalfmt import publicbench_run
    return publicbench_run.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
