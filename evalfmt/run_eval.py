"""多格式文档评估：上传 → 解析 → 检索 → 生成，四层指标一把跑完。

    python -m evalfmt.run_eval --mode lexical --answer off      # 只测检索（快，零 LLM 费用）
    python -m evalfmt.run_eval --mode vector  --answer on       # 全链路（真嵌入 + 真 LLM）

设计要点（面试会被追问的都在这里）：

1. **每次跑用独立 schema**（`evalfmt_<mode>`，跑前 DROP 重建）。
   演示库里已有 80 多份历史文档，混在一起算召回率是「不可复现」的——
   同一套题两次跑出不同数字，那就不是指标是噪声。
2. **语料是生成的、答案唯一**。真实业务文档没有 ground truth，
   所以这里每份文档埋唯一事实（`850W` / `IP54` / `18443`），问题与答案可机器判定。
3. **指标分四层**，因为「答错」有四种完全不同的原因：
   解析没抽到（保真率）、检索没召回（召回率）、召回了但没引用对（引用准确率）、
   引用了却编（幻觉率）。混成一个「准确率」就查不出问题在哪。
4. **陷阱题单独算**：把 A 型号的参数安到 B 型号上，是 grounding 检查抓不到的幻觉。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from evalfmt import corpus, labels, metrics

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "out"
UPLOAD_DIR = ROOT / ".data" / "webapp" / "uploads"

sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- HTTP 小工具

def _request(method: str, url: str, *, token: str = "", body: bytes | None = None,
             ctype: str = "application/json", timeout: int = 120):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw.decode()) if raw else {})


def login(base: str) -> str:
    _, data = _request("POST", base + "/api/auth/login",
                       body=json.dumps({"username": "admin", "password": "admin123"}).encode())
    return data["access_token"]


def _multipart(files: dict[str, bytes], field: str = "file") -> tuple[bytes, str]:
    boundary = "----evalfmt" + uuid.uuid4().hex
    out = bytearray()
    for name, payload in files.items():
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        out += payload + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def search_preview(base: str, token: str, q: str, top_k: int) -> dict:
    from urllib.parse import quote
    _, data = _request("GET", f"{base}/api/search/preview?q={quote(q)}&top_k={top_k}",
                       token=token)
    return data


def chat(base: str, token: str, question: str, top_k: int = 5,
         timeout: int = 180) -> tuple[str, dict, float]:
    """打 SSE，拼完整答案；返回 (答案, 元信息, 耗时秒)。"""
    body = json.dumps({"question": question, "top_k": top_k}).encode()
    req = urllib.request.Request(base + "/api/chat/stream", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token})
    started = time.time()
    buf: list[str] = []
    meta: dict = {}
    full_answer = ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if evt.get("token"):
                buf.append(evt["token"])
            # `done` 帧带完整答案（token 流会被流式脱敏闸扣住一部分，
            # 扣住的那段只在 done.answer / done.tail 里补齐）。以它为权威。
            if isinstance(evt.get("answer"), str) and evt["answer"]:
                full_answer = evt["answer"]
            for key in ("sources", "degraded", "degrade_reason", "conversation_id"):
                if key in evt:
                    meta[key] = evt[key]
    return (full_answer or "".join(buf)), meta, time.time() - started


# ---------------------------------------------------------------- 数据库

def _dsn() -> str:
    import pgkit
    return pgkit.dsn()


def schema_reset(name: str) -> None:
    """DROP + CREATE 独立 schema。**跑前重建**，保证同一套题每次数字可比。"""
    import pgkit
    saved = os.environ.pop("WEBAPP_DB_SCHEMA", None)
    try:
        pgkit.reset_schema(name, "")
    finally:
        if saved is not None:
            os.environ["WEBAPP_DB_SCHEMA"] = saved


def schema_drop(name: str) -> None:
    import psycopg
    import pgkit

    saved = os.environ.pop("WEBAPP_DB_SCHEMA", None)
    try:
        base = psycopg.connect(pgkit.dsn(), connect_timeout=5, autocommit=True)
        try:
            with base.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        finally:
            base.close()
    finally:
        if saved is not None:
            os.environ["WEBAPP_DB_SCHEMA"] = saved


def chunks_by_doc(schema: str) -> dict[str, str]:
    """doc_id → 该文档全部分块拼起来的文本（用于解析保真率）。"""
    import pgkit
    saved = os.environ.get("WEBAPP_DB_SCHEMA")
    os.environ["WEBAPP_DB_SCHEMA"] = schema
    try:
        conn = pgkit.connect()
        try:
            rows = conn.execute(
                "SELECT doc_id, idx, text FROM chunks ORDER BY doc_id, idx").fetchall()
        finally:
            conn.close()
    finally:
        if saved is None:
            os.environ.pop("WEBAPP_DB_SCHEMA", None)
        else:
            os.environ["WEBAPP_DB_SCHEMA"] = saved
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["doc_id"], []).append(row["text"])
    return {k: "\n".join(v) for k, v in out.items()}


# ---------------------------------------------------------------- 服务进程

def spawn(base_port: int, schema: str, mode: str, answer: bool) -> subprocess.Popen:
    env = os.environ.copy()
    env["WEBAPP_DB_SCHEMA"] = schema
    env.pop("WEBAPP_REDIS_URL", None)          # 评估不依赖 Redis，别让限流串到别的实例
    env["WEBAPP_RATE_BACKEND"] = "memory"
    if mode == "vector":
        env["WEBAPP_EMBEDDING_PROVIDER"] = "external"
        env["WEBAPP_RERANK_PROVIDER"] = "external"
    else:
        env["WEBAPP_EMBEDDING_PROVIDER"] = "offline"
        env["WEBAPP_RERANK_PROVIDER"] = "off"
    env["WEBAPP_LLM_PROVIDER"] = "discovery" if answer else "offline"
    cmd = [sys.executable, "-m", "uvicorn", "webapp.main:app",
           "--host", "127.0.0.1", "--port", str(base_port), "--log-level", "warning"]
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(base: str, proc: subprocess.Popen | None, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出，returncode={proc.returncode}")
        try:
            # 首页返回 HTML，不能走 json 解析；只判状态码
            with urllib.request.urlopen(base + "/", timeout=3) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.6)
    raise RuntimeError("服务 90s 内未就绪")


def wait_docs(base: str, token: str, ids: list[str], timeout: int = 240) -> dict[str, dict]:
    deadline = time.time() + timeout
    last: dict[str, dict] = {}
    while time.time() < deadline:
        _, rows = _request("GET", base + "/api/docs", token=token)
        docs = rows if isinstance(rows, list) else rows.get("items", [])
        last = {d["id"]: d for d in docs if d["id"] in ids}
        if len(last) == len(ids) and all(
                d["status"] in ("ready", "error") for d in last.values()):
            return last
        time.sleep(1.0)
    return last


# ---------------------------------------------------------------- 图片识别（子进程）

def image_probe(path: Path, offline: bool) -> dict:
    """跑 parsing 子系统识别铭牌图；offline=True 时只走结构化降级。"""
    from parsing import ParseRouter

    router = ParseRouter()
    result = router.parse_document(path.read_bytes(), filename=path.name,
                                   mime="image/png", doc_id="evalfmt-image")
    text = "\n".join(r.get("text", "") for r in result["results"])
    first = result["results"][0] if result["results"] else {}
    return {
        "offline": offline,
        "level": first.get("level"),
        "degraded": first.get("degraded"),
        "text": text,
        "facts_hit": [f for f in labels.IMAGE_FACTS if metrics.contains(text, f)],
        "facts_total": len(labels.IMAGE_FACTS),
        "latency_ms": result["summary"].get("total_latency_ms"),
    }


# ---------------------------------------------------------------- 主流程

def run(args) -> dict:
    corpus_dir = corpus.build()
    base = args.base or f"http://127.0.0.1:{args.port}"
    report: dict = {
        "mode": args.mode,
        "answer_mode": args.answer == "on",
        "schema": args.schema,
        "base": base,
        "corpus": [{"name": p.name, "bytes": p.stat().st_size}
                   for p in sorted(corpus_dir.iterdir())],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    proc = None
    try:
        if not args.base:
            print(f"[1/6] 准备 schema `{args.schema}` 并启动服务 {base} …")
            schema_reset(args.schema)
            proc = spawn(args.port, args.schema, args.mode, args.answer == "on")
        else:
            print(f"[1/6] 复用已有服务 {base}（--base 模式，不改 schema）")
        wait_ready(base, proc)

        token = login(base)

        print("[2/6] 上传 10 份语料并等待解析完成 …")
        uploaded: dict[str, str] = {}
        upload_errors: dict[str, str] = {}
        for name, payload in sorted(corpus.FILES.items()) + [
                (p.name, p.read_bytes()) for p in sorted(corpus_dir.iterdir())
                if p.name not in corpus.FILES]:
            body, ctype = _multipart({name: payload})
            try:
                _, doc = _request("POST", base + "/api/docs/upload", token=token,
                                  body=body, ctype=ctype)
                uploaded[name] = doc["id"]
            except urllib.error.HTTPError as exc:
                upload_errors[name] = f"HTTP {exc.code}"
        waited = wait_docs(base, token, list(uploaded.values()))
        for name, doc_id in uploaded.items():
            d = waited.get(doc_id) or {}
            if d.get("status") == "error":
                upload_errors[name] = str(d.get("error_msg") or "解析失败")
        report["ingest"] = {
            "uploaded": len(uploaded),
            "ready": sum(1 for d in waited.values() if d["status"] == "ready"),
            "error": {k: v for k, v in upload_errors.items()},
        }

        print("[3/6] 解析保真率（字节 → 文本有没有丢事实）…")
        chunk_map = chunks_by_doc(args.schema) if not args.base else {}
        fidelity = []
        for name, facts in labels.PARSE_FIDELITY:
            text = chunk_map.get(uploaded.get(name, ""), "")
            hit = [f for f in facts if metrics.contains(text, f)]
            fidelity.append({
                "file": name, "facts": list(facts), "hit": hit,
                "ratio": len(hit) / len(facts) if facts else 0.0,
                "chars": len(text),
            })
        report["fidelity"] = fidelity

        print(f"[4/6] 检索召回（top-{args.top_k}）…")
        ret_scores: list[metrics.RetrievalScore] = []
        ret_detail = []
        for case in labels.FACTUAL:
            expect_id = uploaded.get(case.doc, "")
            res = search_preview(base, token, case.question, args.top_k)
            hits = res.get("hits", [])
            rank = next((i + 1 for i, h in enumerate(hits)
                         if h.get("doc_id") == expect_id), None)
            top1_text = hits[0].get("text", "") if hits else ""
            score = metrics.RetrievalScore(
                case_id=case.case_id, doc=case.doc, rank=rank,
                top1_doc=(hits[0].get("title") or "")[:40] if hits else "",
                citation_hit=metrics.contains(top1_text, case.fact))
            ret_scores.append(score)
            ret_detail.append({
                "case": case.case_id, "q": case.question, "expect": case.doc,
                "rank": rank, "hits": len(hits),
                "top1_has_fact": score.citation_hit,
                "retrieval_mode": res.get("retrieval_mode"),
            })
        report["retrieval"] = metrics.aggregate_retrieval(ret_scores)
        report["retrieval_detail"] = ret_detail

        print("[5/6] 生成层指标（正确率 / 幻觉率 / 拒答 / 陷阱）…")
        ans_scores: list[metrics.AnswerScore] = []
        ans_detail = []
        if args.answer == "on":
            for case in (*labels.FACTUAL, *labels.NEGATIVE, *labels.TRAP):
                answer, meta, elapsed = chat(base, token, case.question, args.top_k)
                evidence = "\n".join(
                    h.get("text", "") for h in
                    search_preview(base, token, case.question, args.top_k).get("hits", []))
                rate, bad, total = metrics.hallucination(answer, evidence, case.question)
                ans_scores.append(metrics.AnswerScore(
                    case_id=case.case_id, kind=case.kind, answer=answer,
                    fact_hit=metrics.contains(answer, case.fact) if case.fact else False,
                    hallucination_rate=rate, unsupported=bad, fact_tokens=total,
                    refused=metrics.refused(answer),
                    trap_violation=(metrics.trap_violated(answer, case.fact)
                                    if case.kind == "trap" else False),
                    latency_s=elapsed))
                ans_detail.append({
                    "case": case.case_id, "kind": case.kind, "q": case.question,
                    "answer": answer[:400], "fact_hit": ans_scores[-1].fact_hit,
                    "hallucination": rate, "unsupported": bad,
                    "refused": ans_scores[-1].refused,
                    "degraded": meta.get("degraded"),
                    "latency_s": round(elapsed, 2),
                })
        report["answers"] = metrics.aggregate_answers(ans_scores) if ans_scores else None
        report["answers_detail"] = ans_detail

        print("[6/6] 无字图片识别（parsing 子系统，离线 vs 视觉）…")
        image_path = corpus_dir / "09-铭牌照片.png"
        probes = []
        for offline in (True, False):
            env = os.environ.copy()
            if offline:
                env["PARSING_OFFLINE"] = "1"
            else:
                env.pop("PARSING_OFFLINE", None)
            out = subprocess.run(
                [sys.executable, "-m", "evalfmt.run_eval", "--image-probe",
                 str(image_path), "--offline" if offline else "--no-offline"],
                cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
            if out.returncode != 0:
                probes.append({"offline": offline, "error": out.stderr.strip()[-400:]})
            else:
                probes.append(json.loads(out.stdout.strip().splitlines()[-1]))
        report["image"] = probes

        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return report
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.cleanup and not args.base:
            _cleanup_uploads()
            schema_drop(args.schema)


def _cleanup_uploads() -> None:
    """删掉本次评估写进共享上传目录的文件（schema 会 DROP，磁盘文件不会自己走）。"""
    keep = {p.name for p in (Path(__file__).resolve().parent / "corpus").iterdir()}
    for f in UPLOAD_DIR.glob("*"):
        for name in keep:
            if f.name.endswith("_" + name):
                f.unlink(missing_ok=True)
                break


# ---------------------------------------------------------------- 报告

def write_report(report: dict) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{report['mode']}-{'ans' if report['answer_mode'] else 'ret'}"
    json_path = OUT_DIR / f"report_{tag}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = OUT_DIR / f"report_{tag}.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(r: dict) -> str:
    pct = metrics.format_pct
    lines = [
        f"# 多格式文档评估报告 · {r['mode']} 模式"
        f"{'（含生成层）' if r['answer_mode'] else '（仅检索层）'}",
        "",
        f"- 生成时间：{r.get('finished_at') or r['started_at']}",
        f"- 语料：{len(r['corpus'])} 份，覆盖 txt / md / csv / json / py / log / yaml / pdf / png / dxf",
        f"- 数据隔离：独立 schema `{r['schema']}`（跑前重建，不复用演示库）",
        "",
        "## 1. 入库（上传 → 解析 → 落块）",
        "",
        f"- 上传 {r['ingest']['uploaded']} 份，解析成功 **{r['ingest']['ready']}** 份，"
        f"报错 {len(r['ingest']['error'])} 份（预期内：无字图片与 dxf）",
    ]
    for name, msg in r["ingest"]["error"].items():
        lines.append(f"  - `{name}` → {msg[:90]}")
    lines += ["", "## 2. 解析保真率（字节 → 文本有没有丢事实）", "",
              "| 文件 | 格式 | 命中事实 | 保真率 | 提取字符数 |", "|---|---|---|---|---|"]
    for f in r["fidelity"]:
        fmt = Path(f["file"]).suffix.lstrip(".")
        lines.append(f"| {f['file']} | {fmt} | {' / '.join(f['hit']) or '—'} | "
                     f"**{pct(f['ratio'])}** | {f['chars']} |")
    avg = sum(x["ratio"] for x in r["fidelity"]) / len(r["fidelity"]) if r["fidelity"] else 0
    lines += ["", f"**加权平均保真率：{pct(avg)}**（{len(r['fidelity'])} 份可解析样本）", ""]

    ret = r["retrieval"]
    lines += ["## 3. 检索召回（文档级，top-5）", "",
              "| 指标 | 值 |", "|---|---|",
              f"| 样本数 | {ret['n']} |",
              f"| Recall@1 | **{pct(ret['recall@1'])}** |",
              f"| Recall@3 | **{pct(ret['recall@3'])}** |",
              f"| Recall@5 | {pct(ret['recall@5'])} |",
              f"| MRR | {ret['mrr']:.3f} |",
              f"| 引用准确率@1（top1 分块含期望事实） | **{pct(ret['citation_precision@1'])}** |",
              "",
              "| 题号 | 问题 | 期望文档 | 命中名次 | 状态 |", "|---|---|---|---|---|"]
    for d in r["retrieval_detail"]:
        rank = d["rank"]
        lines.append(f"| {d['case']} | {d['q']} | {d['expect']} | "
                     f"{rank if rank else '未召回'} | {'✅' if rank else '❌'} |")

    ans = r.get("answers")
    lines += ["", "## 4. 生成层指标", ""]
    if not ans:
        lines += ["> 本次未生成（`--answer off`）。开 `--answer on` 才有正确率/幻觉率/拒答率。", ""]
    else:
        lines += ["| 指标 | 值 | 说明 |", "|---|---|---|",
                  f"| 答案正确率 | **{pct(ans['answer_accuracy'])}** | 期望事实出现在答案里 |",
                  f"| 幻觉率（规则级 grounding） | **{pct(ans['hallucination_rate'])}** | "
                  f"无证据支持的事实 token 占比（{ans['hallucination_tokens'][0]}/"
                  f"{ans['hallucination_tokens'][1]}） |",
                  f"| 拒答正确率 | {pct(ans['refusal_accuracy'])} | 无证据问题是否承认没找到 |",
                  f"| 陷阱违规率 | {pct(ans['trap_violation_rate'])} | "
                  f"把别的型号参数套上去的比例（越低越好） |",
                  f"| 平均回答耗时 | {ans['avg_latency_s']:.2f}s | 含检索 + 生成 |",
                  ""]
        for d in r["answers_detail"]:
            lines.append(f"- **{d['case']}**（{d['kind']}）{d['q']}")
            lines.append(f"  - 答案：{(d['answer'] or '（空）')[:150]}")
            lines.append(f"  - 正确={d['fact_hit']} 幻觉率="
                         f"{pct(d['hallucination']) if d['hallucination'] is not None else '无事实断言'}"
                         f" 拒答={d['refused']} 耗时={d['latency_s']}s")
            if d["unsupported"]:
                lines.append(f"  - 无证据 token：{', '.join(d['unsupported'])}")

    lines += ["", "## 5. 无字图片识别（parsing 子系统）", "",
              "| 条件 | 策略级别 | 降级原因 | 识别到 | 识别率 | 耗时 |", "|---|---|---|---|---|---|"]
    for p in r["image"]:
        if p.get("error"):
            lines.append(f"| {'离线' if p['offline'] else '视觉'} | — | 调用失败 | — | — | "
                         f"`{p['error'][:60]}` |")
            continue
        lines.append(f"| {'离线（PARSING_OFFLINE=1）' if p['offline'] else '视觉（真模型）'} | "
                     f"{p['level']} | {p['degraded'] or '—'} | "
                     f"{' / '.join(p['facts_hit']) or '未识别出内容'} | "
                     f"**{pct(len(p['facts_hit']) / p['facts_total'])}** | "
                     f"{p['latency_ms']}ms |")

    lines += ["", "## 6. 口径与局限（面试如实说）", "",
              "- **召回率是文档级**：只判「期望文档在不在 top-k」，不看分块序号。分块级会更低。",
              "- **幻觉率是规则级**：抽型号/编号/错误码/带单位数字，看是否在召回证据里出现。"
              "它抓不到「有证据但归属错」，所以陷阱题单独一个指标。",
              "- **语料是合成的**：10 份全部由脚本生成，答案唯一。"
              "真实业务语料需要人工标注，量级至少百条才能谈统计意义。",
              "- **`lexical` 模式是词法检索**（`OfflineEmbeddingProvider`），"
              "`vector` 模式才是生产形态（硅基流动 bge-m3 + bge-reranker）。两个都要跑，"
              "对比差值才说明「向量到底值不值得」——只报 vector 会被问「基线呢」。",
              "- **无字图片走的是另起链路**：`parsing/` 子系统能识别，但 webapp 主链路"
              "还没接它，所以图片内容目前**检索不到**。这是明确的未做项，不是本期遗漏。",
              ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="多格式文档评估")
    ap.add_argument("--mode", choices=("lexical", "vector"), default="lexical",
                    help="lexical=词法检索（离线）；vector=真嵌入 + 真重排")
    ap.add_argument("--answer", choices=("on", "off"), default="off",
                    help="on=额外跑生成层（真 LLM，有费用）；off=只测检索")
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--base", default="", help="复用已在跑的服务，不自己起进程")
    ap.add_argument("--schema", default="", help="隔离 schema 名，默认 eval_<mode>")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--cleanup", action="store_true", help="跑完删上传文件并 DROP schema")
    ap.add_argument("--image-probe", default="", help=argparse.SUPPRESS)
    ap.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-offline", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.image_probe:
        print(json.dumps(image_probe(Path(args.image_probe), offline=args.offline),
                         ensure_ascii=False))
        return 0

    args.schema = args.schema or f"eval_{args.mode}"
    report = run(args)
    json_path, md_path = write_report(report)
    print("\n" + "=" * 68)
    ret = report["retrieval"]
    print(f"检索：Recall@1={metrics.format_pct(ret['recall@1'])} "
          f"Recall@3={metrics.format_pct(ret['recall@3'])} MRR={ret['mrr']:.3f} "
          f"引用准确率@1={metrics.format_pct(ret['citation_precision@1'])}")
    if report.get("answers"):
        a = report["answers"]
        print(f"生成：正确率={metrics.format_pct(a['answer_accuracy'])} "
              f"幻觉率={metrics.format_pct(a['hallucination_rate'])} "
              f"拒答正确率={metrics.format_pct(a['refusal_accuracy'])} "
              f"陷阱违规率={metrics.format_pct(a['trap_violation_rate'])}")
    print(f"报告：{md_path}")
    print(f"      {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
