#!/usr/bin/env python3
"""真实 RAG 链路端到端评估：批量打 /api/chat/stream，按标注打分。

与 webapp/eval_report.py 的分工：
- eval_report.py = 单点「连通性体检」，1 个问题，证明链路活着；
- 本脚本       = 带标注的「批量准确率」，60 条，回答「这条链路答得准不准」。

测的是完整链路：鉴权 → 词法门禁 → pgvector 语义排序 → rerank → 提示词组装
→ DeepSeek 生成 → SSE 输出 → 落库。不是裸模型基准。

隔离：独立 schema（进程号命名），跑完默认删除；--keep-schema 保留供排查。

运行：
    python webapp/eval_chain.py --limit 3      # 冒烟
    python webapp/eval_chain.py                # 全量 60 条
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
# 直接跑脚本时 sys.path[0] 是 webapp/，会让 webapp/queue.py 遮蔽标准库 queue。
sys.path = [p for p in sys.path if not p or Path(p).resolve() != _SCRIPT_DIR]
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
DATA = HERE / "eval_datasets"
OUT = HERE / "out"

from evalbench.core.scoring import is_rejection, normalize  # noqa: E402

# evalbench 的拒答词表只认「没有找到」，实测模型更常说「未找到 / 未说明 / 未涉及」，
# 会被判成「没拒答」——这是假红，不是幻觉。这里只补充「答案开头」的明确否认语，
# 避免正文里「某细节未说明」这类从句被误判为整条拒答。
_REJECT_HEAD_MARKERS = (
    "未找到", "未提及", "未说明", "未涉及", "未涵盖", "未提供", "暂无", "没有相关", "无相关内容",
)


def is_rejection_ext(answer: str) -> bool:
    if is_rejection(answer):
        return True
    head = normalize((answer or "")[:60])
    return any(normalize(m) in head for m in _REJECT_HEAD_MARKERS)



def _read_env_value(key: str) -> str:
    path = ROOT / ".env"
    if not path.exists():
        return ""
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _port_open(url: str, timeout: float = 1.0) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", u.port or 5432), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_db_url(explicit: str | None) -> tuple[str, str]:
    """.env 里的端口可能过期（实测写 55432，实际库在 5432）。先探活再回退。"""
    url = explicit or os.environ.get("WEBAPP_DATABASE_URL") or _read_env_value("WEBAPP_DATABASE_URL")
    if not url:
        return "postgresql://kb:kb_demo_only@127.0.0.1:5432/kb", "default"
    if _port_open(url):
        return url, "as-configured"
    u = urlparse(url)
    for port in (5432, 55432, 12505, 12516):
        cand = urlunparse(u._replace(netloc=f"{u.username}:{u.password}@{u.hostname}:{port}"))
        if _port_open(cand):
            return cand, f"port-fallback:{u.port}->{port}"
    return url, "unreachable"


def parse_sse(text: str) -> dict:
    meta, tokens, done = {}, [], {}
    for block in text.split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        ev, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if not data:
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if ev == "meta":
            meta = obj
        elif ev == "done":
            done = obj
        elif ev == "token":
            tokens.append(obj)
    return {"meta": meta, "tokens": tokens, "done": done}


def percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * p))))
    return float(s[k])


def main() -> int:
    ap = argparse.ArgumentParser(description="真实 RAG 链路端到端评估")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（冒烟）")
    ap.add_argument("--keep-schema", action="store_true", help="保留隔离 schema 供排查")
    ap.add_argument("--db-url", default=None, help="显式指定 PG 连接串")
    ap.add_argument("--rescore", action="store_true",
                    help="只重算已有 records 的分数（改标注/判定口径后用），不连库不调模型")
    args = ap.parse_args()

    if args.rescore:
        return do_rescore()

    db_url, db_note = resolve_db_url(args.db_url)
    schema = f"kb_chain_{os.getpid()}"
    # 必须在 import webapp 之前落进环境：pgkit 在 connect() 时按这两个变量切库/切 schema。
    os.environ["WEBAPP_DATABASE_URL"] = db_url
    os.environ["WEBAPP_DB_SCHEMA"] = schema
    # rerank 分支只读 RERANK_API_KEY；.env 只有 WEBAPP_EMBEDDING_API_KEY，不回退就会静默跳过重排。
    if not os.environ.get("RERANK_API_KEY"):
        embed_key = _read_env_value("WEBAPP_EMBEDDING_API_KEY")
        if embed_key:
            os.environ["RERANK_API_KEY"] = embed_key

    import psycopg
    from fastapi.testclient import TestClient

    import pgkit
    from webapp import db, settings
    from webapp.envfile import load_env
    from webapp.main import app

    load_env()

    corpus = json.loads((DATA / "corpus_v1.json").read_text(encoding="utf-8"))["documents"]
    cases = [json.loads(ln) for ln in (DATA / "chain_qa_v1.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.limit:
        cases = cases[: args.limit]
    title_by_name = {d["name"]: d["title"] for d in corpus}

    def ensure_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                cur.execute(f'CREATE SCHEMA "{schema}"')

    def drop_schema() -> None:
        with psycopg.connect(pgkit.dsn(), autocommit=True, connect_timeout=5) as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def seed() -> None:
        """只保留受控语料：init_db 会灌演示数据，先清空再插，避免检索池被污染。"""
        conn = db.get_conn()
        try:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            for d in corpus:
                doc_id = uuid.uuid4().hex
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                conn.execute(
                    "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, "eval", d["name"], d["title"], len(d["text"].encode()),
                     "ready", 1, now, now),
                )
                conn.execute(
                    "INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                    (uuid.uuid4().hex, doc_id, 0, d["text"]),
                )
            conn.commit()
        finally:
            conn.close()

    print(f"[env] db={db_note} schema={schema}", flush=True)
    print(f"[env] rerank_key={'set' if os.environ.get('RERANK_API_KEY') else 'MISSING（重排会被跳过）'}", flush=True)
    cfg = settings.get_all()
    eff = settings.effective_embedding(cfg)
    print(f"[env] llm={cfg.get('discovery_model')} embed={eff.get('model')} rerank={cfg.get('external_rerank_model')}", flush=True)

    ensure_schema()
    db.init_db()
    seed()

    records: list[dict] = []
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
                done, meta = parsed["done"] or {}, parsed["meta"] or {}
                answer = done.get("answer") or ""
                sources = done.get("sources") or meta.get("sources") or []
                titles = [s.get("doc_title") for s in sources if s.get("doc_title")]

                ans_norm = normalize(answer)
                facts = case.get("key_facts") or []
                hit_facts = [f for f in facts if normalize(f) in ans_norm]
                accuracy = round(len(hit_facts) / len(facts), 4) if facts else None
                gold = title_by_name.get(case["gold_doc"]) if case.get("gold_doc") else None
                retrieval_hit = (gold in titles) if gold else None
                rejected = is_rejection_ext(answer)

                rec = {
                    "id": case["id"], "type": case["type"], "question": case["question"],
                    "gold_doc": case.get("gold_doc"), "gold_title": gold,
                    "key_facts": facts, "hit_facts": hit_facts, "miss_facts": [f for f in facts if f not in hit_facts],
                    "answer": answer, "rejected": rejected,
                    "answer_accuracy": accuracy,
                    "exact": (accuracy == 1.0) if accuracy is not None else None,
                    "retrieval_hit": retrieval_hit,
                    "retrieved_titles": titles,
                    "retrieved_count": meta.get("retrieved"),
                    "retrieval_mode": meta.get("retrieval_mode"),
                    "rerank": meta.get("rerank") or done.get("rerank"),
                    "degraded": done.get("degraded"),
                    "degrade_reason": done.get("degrade_reason"),
                    "http_status": resp.status_code,
                    "wall_ms": wall_ms,
                    "token_chunks": len(parsed["tokens"]),
                }
                records.append(rec)

                if not answer.strip() and not done:
                    print(f"  [{i:>2}/{len(cases)}] {case['id']} !! 空响应 http={resp.status_code} body={resp.text[:150]!r}", flush=True)
                else:
                    acc = "—" if accuracy is None else f"{accuracy:.2f}"
                    rh = "—" if retrieval_hit is None else ("hit" if retrieval_hit else "MISS")
                    print(f"  [{i:>2}/{len(cases)}] {case['id']} {case['type'][:12]:<12} "
                          f"acc={acc:<5} retr={rh:<4} rej={'Y' if rejected else 'N'} {wall_ms}ms", flush=True)
    finally:
        OUT.mkdir(exist_ok=True)
        with (OUT / "chain_eval_records.jsonl").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        summary = summarize(records, db_note, schema, cfg, eff)
        (OUT / "chain_eval_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / "chain_eval_report.md").write_text(
            render_markdown(summary, records), encoding="utf-8")
        if not args.keep_schema:
            try:
                drop_schema()
            except Exception as e:  # noqa: BLE001
                print(f"[warn] schema 清理失败（不影响结果）: {type(e).__name__}: {e}", flush=True)
        print(f"\n[out] {OUT / 'chain_eval_summary.json'}", flush=True)
        print(f"[out] {OUT / 'chain_eval_report.md'}", flush=True)

    return 0


def do_rescore() -> int:
    """改标注或拒答口径后重算分数：不连库、不调模型，只重放已有 answer。"""
    corpus = json.loads((DATA / "corpus_v1.json").read_text(encoding="utf-8"))["documents"]
    title_by_name = {d["name"]: d["title"] for d in corpus}
    cases = [json.loads(ln) for ln in
             (DATA / "chain_qa_v1.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    case_by_id = {c["id"]: c for c in cases}

    rec_path = OUT / "chain_eval_records.jsonl"
    if not rec_path.exists():
        print("[FAIL] 没有 chain_eval_records.jsonl，先跑一次真实评估", file=sys.stderr)
        return 1
    records = [json.loads(ln) for ln in rec_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    for r in records:
        c = case_by_id.get(r["id"])
        if not c:
            continue
        facts = c.get("key_facts") or []
        ans = normalize(r.get("answer") or "")
        hit = [f for f in facts if normalize(f) in ans]
        r["gold_doc"] = c.get("gold_doc")
        r["gold_title"] = title_by_name.get(c["gold_doc"]) if c.get("gold_doc") else None
        r["key_facts"] = facts
        r["hit_facts"] = hit
        r["miss_facts"] = [f for f in facts if f not in hit]
        r["answer_accuracy"] = round(len(hit) / len(facts), 4) if facts else None
        r["exact"] = (r["answer_accuracy"] == 1.0) if facts else None
        r["rejected"] = is_rejection_ext(r.get("answer") or "")

    prev: dict = {}
    prev_path = OUT / "chain_eval_summary.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    env = prev.get("environment") or {}

    with rec_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = summarize(records, env.get("db", "rescored"), env.get("schema", "rescored"),
                        {}, {}, env_override=env)
    prev_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "chain_eval_report.md").write_text(render_markdown(summary, records), encoding="utf-8")
    print("[OK] rescore 完成（未调用任何模型、未连库）")
    print(f"[out] {prev_path}")
    return 0


def summarize(records: list[dict], db_note: str, schema: str, cfg: dict, eff: dict,
              env_override: dict | None = None) -> dict:
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r["type"], []).append(r)

    def avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 4) if vals else None

    def rate(rows: list[dict], key: str) -> float | None:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

    types: dict[str, dict] = {}
    for t, rows in by_type.items():
        accs = [r["answer_accuracy"] for r in rows if r["answer_accuracy"] is not None]
        types[t] = {
            "n": len(rows),
            "answer_accuracy_mean": avg(accs),
            "exact_rate": rate(rows, "exact"),
            "retrieval_hit_rate": rate(rows, "retrieval_hit"),
            "refusal_rate": rate(rows, "rejected"),
            "degraded_count": sum(1 for r in rows if r.get("degraded")),
            "avg_wall_ms": avg([r["wall_ms"] for r in rows]),
        }

    scored = [r for r in records if r["answer_accuracy"] is not None]  # 可答 + 干扰
    plain = [r for r in records if r["type"] == "answerable"]
    distract = [r for r in records if r["type"] == "distractor"]
    unans = [r for r in records if r["type"] == "unanswerable"]
    walls = [r["wall_ms"] for r in records]

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "真实 RAG 链路端到端准确率（含检索、重排、生成、SSE 全链路）",
        "environment": env_override or {
            "db": db_note, "schema": schema,
            "llm_model": cfg.get("discovery_model"),
            "embedding_model": eff.get("model"),
            "embedding_cache_key": eff.get("cache_key"),
            "rerank_model": cfg.get("external_rerank_model"),
            "rerank_key_present": bool(os.environ.get("RERANK_API_KEY")),
            "retrieval_mode_sample": next((r["retrieval_mode"] for r in records if r["retrieval_mode"]), None),
            "rerank_sample": next((r["rerank"] for r in records if r["rerank"]), None),
        },
        "headline": {
            "n_total": len(records),
            "answer_accuracy_mean": avg([r["answer_accuracy"] for r in scored if r["answer_accuracy"] is not None]),
            "answerable_accuracy_mean": avg([r["answer_accuracy"] for r in plain if r["answer_accuracy"] is not None]),
            "distractor_accuracy_mean": avg([r["answer_accuracy"] for r in distract if r["answer_accuracy"] is not None]),
            "exact_rate": rate(scored, "exact"),
            "retrieval_hit_rate": rate(scored, "retrieval_hit"),
            "unanswerable_refusal_rate": rate(unans, "rejected"),
            "degraded_count": sum(1 for r in records if r.get("degraded")),
            "latency_p50_ms": percentile(walls, 0.5),
            "latency_p95_ms": percentile(walls, 0.95),
        },
        "by_type": types,
        "errors": [{"id": r["id"], "degraded": r["degraded"], "reason": r["degrade_reason"]}
                   for r in records if r.get("degraded")],
    }


def render_markdown(summary: dict, records: list[dict]) -> str:
    h = summary["headline"]
    env = summary["environment"]
    lines = [
        "# 真实 RAG 链路端到端评估",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        "## 测量对象",
        "",
        "走完整链路：鉴权 → 词法门禁 → pgvector 语义排序 → rerank → 提示词组装 → DeepSeek 生成 → SSE。",
        "**不是**裸模型基准（`evalbench/` 那套给原文直接问模型）。",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| LLM | {env['llm_model']} |",
        f"| Embedding | {env['embedding_model']} |",
        f"| Rerank | {env['rerank_model']}（key {'已配置' if env['rerank_key_present'] else '缺失，被跳过'}） |",
        f"| 检索模式 | {env.get('retrieval_mode_sample') or 'SSE 未返回该字段'} |",
        f"| 重排状态 | {env.get('rerank_sample') or 'SSE 未返回该字段'} |",
        f"| DB | {env['db']} |",
        "",
        "## 总览",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
        f"| 样本数 | {h['n_total']} | 30 可答 / 15 不可答 / 15 干扰 |",
        f"| 答案准确率（可答+干扰） | {pct(h['answer_accuracy_mean'])} | key_facts 归一化命中率 |",
        f"| 可答题准确率 | {pct(h['answerable_accuracy_mean'])} | |",
        f"| 干扰题准确率 | {pct(h['distractor_accuracy_mean'])} | 关键词跨文档，考验排序 |",
        f"| 完全命中率 | {pct(h['exact_rate'])} | key_facts 全部命中 |",
        f"| 检索命中率 | {pct(h['retrieval_hit_rate'])} | 金标文档进入返回来源 |",
        f"| 不可答拒答率 | {pct(h['unanswerable_refusal_rate'])} | 期望拒答 |",
        f"| 降级条数 | {h['degraded_count']} | |",
        f"| 延迟 P50 / P95 | {h['latency_p50_ms']:.0f} / {h['latency_p95_ms']:.0f} ms | |",
        "",
        "## 分类明细",
        "",
        "| 类型 | n | 准确率 | 完全命中 | 检索命中 | 拒答率 | 降级 | 平均延迟 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t, v in summary["by_type"].items():
        lines.append(
            f"| {t} | {v['n']} | {pct(v['answer_accuracy_mean'])} | {pct(v['exact_rate'])} | "
            f"{pct(v['retrieval_hit_rate'])} | {pct(v['refusal_rate'])} | {v['degraded_count']} | "
            f"{v['avg_wall_ms']:.0f} ms |"
        )

    misses = [r for r in records if r.get("answer_accuracy") is not None and r["answer_accuracy"] < 1.0]
    if misses:
        lines += ["", "## 未完全命中（可答+干扰）", "",
                  "| ID | 问题 | 未命中 key_fact | 检索命中 | 答案摘录 |",
                  "|---|---|---|---|---|"]
        for r in misses[:20]:
            miss = "、".join(r["miss_facts"]) if r["miss_facts"] else "—"
            rh = "—" if r["retrieval_hit"] is None else ("是" if r["retrieval_hit"] else "否")
            excerpt = (r["answer"] or "").replace("\n", " ")[:70]
            lines.append(f"| {r['id']} | {r['question']} | {miss} | {rh} | {excerpt} |")

    bad_refusal = [r for r in records if r["type"] == "unanswerable" and not r["rejected"]]
    if bad_refusal:
        lines += ["", "## 不可答但未拒答（幻觉风险）", "",
                  "| ID | 问题 | 答案摘录 |", "|---|---|---|"]
        for r in bad_refusal:
            lines.append(f"| {r['id']} | {r['question']} | {(r['answer'] or '')[:90]} |")

    lines += ["", "## 口径说明", "",
              "- `answer_accuracy`：答案经 NFKC/小写/去标点空白后，金标 key_fact 作为子串命中比例。",
              "- 字符串匹配对同义改写不鲁棒，可能低估语义正确但措辞不同的回答；这是确定性打分的已知代价。",
              "- `retrieval_hit`：金标文档标题是否出现在 SSE 返回的 sources 里，反映检索侧是否把正确文档送到模型面前。",
              "- 拒答判定复用 `evalbench` 的 `is_rejection`（标记词 或 否定词×资料指代共现）。",
              ""]
    return "\n".join(lines)


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
