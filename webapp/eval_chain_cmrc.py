"""CMRC2018 端到端链路评估：真实公开集 + 真实分块 + 真实检索 + 真实 LLM。

与 `eval_chain.py` 的区别（那套测的是自造 5 份说明书的链路连通性）：
- 语料来自 CMRC2018 validation（CLUE 官方中文抽取式 QA，hf-mirror 下载），不是自己编的；
- 每篇 context **走 `rag.chunk_text` 真实切分**再入库，而不是整篇塞成 1 个 chunk
  （旧脚本把每篇文档当 1 chunk，检索命中率结构上必然接近 100%，那个 100% 不可用）；
- 检索池 = 100 篇文档（其中 60 篇只做干扰、不提问），top-k 才有区分度；
- 判分同时看「检索是否召回到 gold 文档」与「答案是否对上 gold 答案」。

参数化：`--top-k` / `--threshold` 用于收敛实验；`--label` 区分各组产出。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# 直接跑脚本时 sys.path[0] 是 webapp/，会让 webapp/queue.py 遮蔽标准库 queue。
sys.path = [p for p in sys.path if not p or Path(p).resolve() != HERE]
sys.path.insert(0, str(ROOT))

OUT = HERE / "out"
RAW = ROOT / "evalbench" / "datasets" / "public" / "raw" / "cmrc2018_validation.parquet"


# ---------- 中文判分 ----------

def norm(s: str) -> str:
    """归一化：小写、去空白、去标点符号。中英标点都去掉，只留字与数字。"""
    s = (s or "").lower()
    s = re.sub(r"\s+", "", s)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", s)


def char_f1(pred: str, gold: str) -> float:
    """字符级 F1（CMRC2018 官方口径的简化实现）。

    为什么不用 EM：模型输出是自然句（「由光荣和 ω-force 开发」），
    EM 要求与 gold 完全相等，会把大量正确回答判成错，指标失去区分度。
    """
    p, g = norm(pred), norm(gold)
    if not p or not g:
        return 0.0
    common = sum((Counter(p) & Counter(g)).values())
    if common == 0:
        return 0.0
    prec, rec = common / len(p), common / len(g)
    return 2 * prec * rec / (prec + rec)


# 多答案 gold 的拆法：CMRC 的 gold 有时用顿号把多个标注答案拼成一串
# （"现代货箱码头、现代货箱"），整串匹配会因为答案里多了个"或"就判错。
# 按分隔符与并列词拆成候选片段，任一命中即算包含。
_GOLD_SPLIT = re.compile(r"[、，,;；/｜|]|以及|或者|或")


def gold_candidates(golds: list[str]) -> list[str]:
    out: list[str] = []
    for g in golds:
        g = (g or "").strip()
        if g:
            out.append(g)
        for part in _GOLD_SPLIT.split(g):
            part = part.strip()
            if len(part) >= 2:
                out.append(part)
    return list(dict.fromkeys(out))


def best_answer(answer: str, golds: list[str]) -> tuple[float, bool, bool, bool]:
    """返回 (最大字符F1, 是否包含任一 gold 片段, 是否完全相等, 是否判对)。

    「判对」= 含 gold 片段 或 F1≥0.7：后者专治音译差异
    （gold「阿德雷得」/ 答案「阿德莱德」，字面不同但指同一个地方）。
    为什么不用 EM：模型输出是自然句，EM 会把几乎全部正确回答判成错。
    """
    na = norm(answer)
    f1 = max((char_f1(answer, g) for g in golds), default=0.0)
    contains = any(norm(c) and norm(c) in na for c in gold_candidates(golds))
    exact = any(na and na == norm(g) for g in golds)
    return round(f1, 4), contains, exact, bool(contains or f1 >= 0.7)


# ---------- 数据 ----------

def build_pool(doc_pool: int, n_cases: int, seed: int = 42):
    """按 context 去重后抽样，返回 (文档池, 题目)。

    同一 context 在 CMRC 里有 4–5 个问题，只取第一个：
    多问同一篇会让指标被「同一段文字」重复加权，看起来题目多、实际独立样本少。
    文档池前 n_cases 篇用于提问，其余只作干扰——这样检索是「100 选 4」，
    而不是「40 选 4」那种天然容易命中的设置。
    """
    import pyarrow.parquet as pq

    rows = pq.read_table(RAW).to_pylist()
    by_ctx: dict[str, list[dict]] = {}
    for r in rows:
        by_ctx.setdefault(r["context"], []).append(r)

    items = list(by_ctx.items())
    random.Random(seed).shuffle(items)
    pool = items[:doc_pool]

    docs = [{"title": f"CMRC_{i:03d}", "text": ctx} for i, (ctx, _) in enumerate(pool)]
    cases = []
    for i, (ctx, qs) in enumerate(pool[:n_cases]):
        r = qs[0]
        golds = list(dict.fromkeys(r["answers"]["text"]))  # 保序去重
        cases.append({
            "case_id": r["id"], "title": f"CMRC_{i:03d}", "doc_index": i,
            "context_len": len(ctx), "question": r["question"], "golds": golds,
        })
    return docs, cases


def parse_sse(text: str) -> dict:
    """解析 SSE 文本，取 token 增量、done 终态与 meta。"""
    out = {"tokens": [], "done": None, "meta": None}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "token" in ev:
            out["tokens"].append(ev["token"])
        if ev.get("done") or "answer" in ev:
            out["done"] = ev
        if ev.get("meta"):
            out["meta"] = ev["meta"]
    return out


def percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return float(s[k])


# ---------- 主流程 ----------

def rescore(label: str) -> int:
    """改判分口径后重算分数：只重放已落盘的 answer，不连库、不调模型。"""
    rp = OUT / f"cmrc_chain_{label}_records.jsonl"
    sp = OUT / f"cmrc_chain_{label}.json"
    if not rp.exists():
        print(f"[error] 找不到 {rp}", flush=True)
        return 2
    recs = [json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in recs:
        f1, contains, exact, correct = best_answer(r["answer"], r["golds"])
        r.update({"f1": f1, "contains": contains, "exact": exact,
                  "correct": correct, "answer_chars": len(r["answer"])})
    rp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")

    s = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    n = len(recs) or 1
    s.setdefault("config", {})["n_cases"] = len(recs)
    s["retrieval"] = {
        "recall_at_k": round(sum(r["recall_hit"] for r in recs) / n, 4),
        "mrr": round(sum(r["mrr"] for r in recs) / n, 4),
        "rank1_rate": round(sum(1 for r in recs if r["gold_rank"] == 1) / n, 4),
    }
    s["generation"] = {
        "correct_rate": round(sum(r["correct"] for r in recs) / n, 4),
        "f1_mean": round(sum(r["f1"] for r in recs) / n, 4),
        "contains_rate": round(sum(r["contains"] for r in recs) / n, 4),
        "exact_rate": round(sum(r["exact"] for r in recs) / n, 4),
        "f1_ge_0.5": round(sum(1 for r in recs if r["f1"] >= 0.5) / n, 4),
        "answer_len": {"mean": round(sum(r["answer_chars"] for r in recs) / n, 1),
                       "p50": round(percentile([r["answer_chars"] for r in recs], 50))},
    }
    s["rescore"] = True
    sp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"retrieval": s["retrieval"], "generation": s["generation"]},
                     ensure_ascii=False, indent=2))
    print(f"[out] {sp}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CMRC2018 端到端链路评估")
    ap.add_argument("--label", default="A", help="本组标签（用于产出文件名）")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--rerank-depth", type=int, default=0,
                    help="送进 rerank 的候选数（0=等于 top_k）")
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="相对阈值：只保留 score >= top1*threshold 且 >=0.02 的块；0 表示关闭")
    ap.add_argument("--doc-pool", type=int, default=100)
    ap.add_argument("--cases", type=int, default=40)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    ap.add_argument("--rerank", choices=["off", "llm", "external"], default=None,
                    help="覆盖 app_settings 里的 rerank_provider（环境变量会被表覆盖，故必须写表）")
    ap.add_argument("--keep-schema", action="store_true")
    ap.add_argument("--rescore", action="store_true",
                    help="只按新判分口径重算已有 records，不连库不调模型")
    ap.add_argument("--db-url", default=None)
    args = ap.parse_args()

    if args.rescore:
        return rescore(args.label)

    # 连接串：.env 的 55432 在本机不通，先按 .env 探活，失败退回 5432。
    from webapp.envfile import load_env
    load_env()
    db_url = args.db_url or os.environ.get("WEBAPP_DATABASE_URL") or ""
    if "55432" in db_url:
        import socket
        s = socket.socket()
        s.settimeout(0.8)
        try:
            ok = s.connect_ex(("127.0.0.1", 55432)) == 0
        finally:
            s.close()
        if not ok:
            db_url = db_url.replace(":55432/", ":5432/")
    os.environ["WEBAPP_DATABASE_URL"] = db_url
    schema = f"kb_cmrc_{os.getpid()}"
    os.environ["WEBAPP_DB_SCHEMA"] = schema
    if not os.environ.get("RERANK_API_KEY"):
        key = os.environ.get("WEBAPP_EMBEDDING_API_KEY")
        if key:
            os.environ["RERANK_API_KEY"] = key

    import psycopg
    from fastapi.testclient import TestClient

    import pgkit
    from webapp import db, rag, settings
    from webapp.main import app

    docs, cases = build_pool(args.doc_pool, args.cases)
    if args.limit:
        cases = cases[: args.limit]

    def ensure_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                cur.execute(f'CREATE SCHEMA "{schema}"')

    def drop_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def seed() -> int:
        """真实切分入库。返回 chunk 总数，用于确认检索池规模。"""
        conn = db.get_conn()
        total = 0
        try:
            conn.execute("DELETE FROM chunk_embeddings")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for d in docs:
                doc_id = uuid.uuid4().hex
                chunks = rag.chunk_text(d["text"])
                conn.execute(
                    "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, "eval", d["title"], d["title"], len(d["text"].encode()),
                     "ready", len(chunks), now, now),
                )
                for idx, c in enumerate(chunks):
                    conn.execute(
                        "INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                        (uuid.uuid4().hex, doc_id, idx, c),
                    )
                total += len(chunks)
            conn.commit()
        finally:
            conn.close()
        return total

    # 参数收敛靠 patch 而非改业务代码：routers_chat 调的是 rag.retrieve(question)，
    # 默认 top_k 在函数定义时就绑定了，改模块常量无效，只能替换模块属性。
    orig_retrieve = rag.retrieve
    TOPK, THR = args.top_k, args.threshold
    # 0 = 不解耦（深度等于 top_k），与改造前行为一致；不能传 None，否则会取业务默认 20
    DEPTH = args.rerank_depth or args.top_k

    def patched_retrieve(question, top_k=None, with_meta=False):
        # rerank_depth 与 top_k 解耦：depth 决定「从多少条里重排挑」，top_k 决定「挑完留几条」
        res, meta = orig_retrieve(question, top_k=TOPK, with_meta=True, rerank_depth=DEPTH)
        if THR > 0 and res:
            top = float(res[0].get("score") or 0.0)
            floor = max(top * THR, 0.02)
            kept = [r for r in res if float(r.get("score") or 0.0) >= floor]
            meta = {**meta, "threshold": {"ratio": THR, "floor": round(floor, 4),
                                          "before": len(res), "after": len(kept)}}
            res = kept
        return (res, meta) if with_meta else res

    rag.retrieve = patched_retrieve

    ensure_schema()
    db.init_db()
    n_chunks = seed()

    # rerank 不能靠环境变量切：settings.get_all() 会用 app_settings 表覆盖 DEFAULTS，
    # 而 init_db 已把 .env 的值灌进表里。要改必须直接写表，否则实验组等于没变。
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
    print(f"[env] llm={cfg.get('discovery_model')} embed={eff.get('model')} "
          f"rerank={cfg.get('rerank_provider')} top_k={TOPK} threshold={THR}", flush=True)
    print(f"[seed] docs={len(docs)} chunks={n_chunks} cases={len(cases)}", flush=True)

    records: list[dict] = []
    t_start = time.time()
    try:
        with TestClient(app) as client:
            tok = client.post("/api/auth/login",
                              json={"username": "admin", "password": "admin123"}).json()["access_token"]
            headers = {"Authorization": "Bearer " + tok}

            for i, case in enumerate(cases, 1):
                t0 = time.time()
                resp = client.post("/api/chat/stream", headers=headers,
                                   json={"question": case["question"]})
                wall_ms = int((time.time() - t0) * 1000)
                parsed = parse_sse(resp.text)
                done = parsed["done"] or {}
                meta = parsed["meta"] or {}
                answer = done.get("answer") or ""
                sources = done.get("sources") or meta.get("sources") or []
                titles = [s.get("doc_title") for s in sources if s.get("doc_title")]
                scores = [float(s.get("score") or 0.0) for s in sources]

                rank = next((k + 1 for k, t in enumerate(titles) if t == case["title"]), None)
                f1, contains, exact, correct = best_answer(answer, case["golds"])

                rec = {
                    "case_id": case["case_id"], "title": case["title"],
                    "question": case["question"], "golds": case["golds"],
                    "answer": answer,
                    "retrieved_titles": titles, "n_sources": len(sources),
                    "top_score": round(max(scores), 4) if scores else None,
                    "min_score": round(min(scores), 4) if scores else None,
                    "gold_rank": rank, "recall_hit": rank is not None,
                    "mrr": round(1.0 / rank, 4) if rank else 0.0,
                    "f1": f1, "contains": contains, "exact": exact,
                    "correct": correct, "answer_chars": len(answer),
                    "degraded": done.get("degraded"),
                    "degrade_reason": done.get("degrade_reason"),
                    "rerank": meta.get("rerank") or done.get("rerank"),
                    "retrieval_mode": meta.get("retrieval_mode"),
                    "threshold": meta.get("threshold"),
                    "http_status": resp.status_code, "wall_ms": wall_ms,
                }
                records.append(rec)
                print(f"  [{i:>2}/{len(cases)}] {case['case_id']:<18} "
                      f"rank={'-' if rank is None else rank:<3} f1={f1:.2f} "
                      f"hit={'Y' if rank else 'N'} n={len(sources)} {wall_ms}ms", flush=True)
    finally:
        rag.retrieve = orig_retrieve
        OUT.mkdir(exist_ok=True)
        tag = f"cmrc_chain_{args.label}"
        with (OUT / f"{tag}_records.jsonl").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        n = len(records) or 1
        summary = {
            "label": args.label,
            "config": {"top_k": TOPK, "threshold": THR, "doc_pool": len(docs),
                       "n_cases": len(records), "rerank": cfg.get("rerank_provider"),
                       "embedding": eff.get("model"), "llm": cfg.get("discovery_model")},
            "retrieval": {
                "recall_at_k": round(sum(r["recall_hit"] for r in records) / n, 4),
                "mrr": round(sum(r["mrr"] for r in records) / n, 4),
                "rank1_rate": round(sum(1 for r in records if r["gold_rank"] == 1) / n, 4),
            },
            "generation": {
                # correct_rate 是主指标：CMRC 的 gold 是短 span，模型给自然句，
                # 字符 F1 会系统性低估（实测 gold 中位 6 字、答案中位 36 字）。
                # 两者一起看才不误导：F1 低可能是答错，也可能只是答得长。
                "correct_rate": round(sum(r["correct"] for r in records) / n, 4),
                "f1_mean": round(sum(r["f1"] for r in records) / n, 4),
                "contains_rate": round(sum(r["contains"] for r in records) / n, 4),
                "exact_rate": round(sum(r["exact"] for r in records) / n, 4),
                "f1_ge_0.5": round(sum(1 for r in records if r["f1"] >= 0.5) / n, 4),
                "answer_len": {
                    "mean": round(sum(r["answer_chars"] for r in records) / n, 1),
                    "p50": round(percentile([r["answer_chars"] for r in records], 50)),
                },
            },
            "sources": {
                "avg_n": round(sum(r["n_sources"] for r in records) / n, 2),
                "with_0": sum(1 for r in records if r["n_sources"] == 0),
                "degraded": sum(1 for r in records if r["degraded"]),
            },
            "latency_ms": {
                "p50": round(percentile([r["wall_ms"] for r in records], 50)),
                "p95": round(percentile([r["wall_ms"] for r in records], 95)),
                "total_s": round(time.time() - t_start, 1),
            },
            "env": {"schema": schema, "db": db_url.split("@")[-1],
                    "chunks_indexed": n_chunks, "docs": len(docs)},
        }
        (OUT / f"{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n" + json.dumps(summary["retrieval"], ensure_ascii=False))
        print(json.dumps(summary["generation"], ensure_ascii=False))
        print(f"[out] {OUT / (tag + '.json')}", flush=True)

        if not args.keep_schema:
            try:
                drop_schema()
            except Exception as e:  # noqa: BLE001
                print(f"[warn] schema 清理失败: {type(e).__name__}: {e}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
