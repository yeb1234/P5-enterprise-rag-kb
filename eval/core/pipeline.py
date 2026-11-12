"""六链路离线评测实现：分块 → embedding → 检索 → 提示词组装 → 回答生成 → 评分。

每一步都产出统一结构的 step 记录（状态、耗时、对象版本、指标、告警、错误），
任何异常都不静默吞掉；故障场景通过 scenario 确定性注入，不依赖真实网络。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from . import adapters, metrics
from .ids import make_span_id, make_trace_id, stable_hash
from .registry import EVAL_ROOT

STEP_ORDER = ["chunk", "embed", "retrieve", "assemble_prompt", "generate", "score"]

HEADING = re.compile(r"^(#{1,6})\s+(.+)$|^第[一二三四五六七八九十百\d]+[章节篇]\s*(.+)$|^(\d+(?:\.\d+)*)\s+\S.*$")
PAGE_MARK = re.compile(r"^\[\[page:(\d+)\]\]")


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def load_fixtures(root: Path | None = None) -> dict:
    base = Path(root) if root else EVAL_ROOT
    path = base / "datasets" / "fixtures_v0.1.0.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("fixtures", data)


def load_cases(root: Path | None = None) -> list[dict]:
    base = Path(root) if root else EVAL_ROOT
    path = base / "datasets" / "cases_v0.1.0.jsonl"
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


def render_fixture(fixture: dict) -> str:
    ftype = fixture.get("type")
    if ftype == "inline":
        return fixture.get("text", "")
    if ftype == "empty":
        return ""
    if ftype == "generated":
        header = fixture.get("header", "")
        unit = fixture.get("repeat_text", "")
        times = int(fixture.get("repeat_times", 1))
        page_marks = fixture.get("page_marks_every", 0)
        parts = [header] if header else []
        for i in range(times):
            if page_marks and i and i % page_marks == 0:
                parts.append(f"[[page:{i // page_marks + 1}]]")
            parts.append(f"{unit}（第 {i + 1} 段，编号 {fixture.get('seed_tag', 'S')}-{i + 1:05d}）")
        return "\n".join(parts)
    raise ValueError(f"未知 fixture 类型: {ftype}")


def chunk_document(doc_id: str, text: str, params: dict) -> tuple[list[dict], dict]:
    """确定性分块：标题路径 + 段落窗口 + 重叠；返回 (chunks, meta)。"""
    if not text.strip():
        return [], {"empty_document": True, "truncated": False, "unit_count": 0}

    max_chars = int(params["max_chars"])
    overlap = int(params["overlap_chars"])
    cap = int(params["max_chunks_per_document"])
    units: list[tuple[int, str, str]] = []
    heading_stack: list[str] = []
    page_no = 1
    for raw in text.splitlines():
        line = raw.rstrip()
        mark = PAGE_MARK.match(line)
        if mark:
            page_no = int(mark.group(1))
            continue
        if not line.strip():
            continue
        h = HEADING.match(line.strip())
        if h and params.get("heading_detection", True):
            level = len(h.group(1)) if h.group(1) else 1
            title = h.group(2) or h.group(3) or line.strip()
            heading_stack = heading_stack[: max(level - 1, 0)]
            heading_stack.append(title.strip())
            continue
        units.append((page_no, " / ".join(heading_stack), line.strip()))

    chunks: list[dict] = []
    buffer: list[str] = []
    buf_len = 0
    cur_page, cur_section = 1, ""
    seq = 0

    def flush() -> None:
        nonlocal buffer, buf_len, seq
        content = "\n".join(buffer).strip()
        if not content:
            buffer, buf_len = [], 0
            return
        normalized = "".join(content.split()).lower()
        chunks.append({
            "chunk_id": _sha(f"{doc_id}|{seq}|{normalized}"),
            "seq": seq, "content": content, "page_no": cur_page,
            "section_path": cur_section, "char_len": len(content),
            "doc_id": doc_id,
        })
        seq += 1
        tail = content[-overlap:] if overlap else ""
        buffer = [tail] if tail else []
        buf_len = len(tail)

    truncated = False
    for page, section, line in units:
        if len(chunks) >= cap:
            truncated = True
            break
        if buffer and (section != cur_section or buf_len + len(line) > max_chars):
            flush()
        cur_page, cur_section = page, section
        buffer.append(line)
        buf_len += len(line) + 1
    if buffer and not truncated:
        flush()
    return chunks[:cap], {"empty_document": False, "truncated": truncated, "unit_count": len(units)}


def _step(trace_id: str, name: str, status: str, started: float, object_refs: list[str],
          metrics_out: dict, warnings: list[str] | None = None, error: dict | None = None,
          inp: str = "", out: str = "", placeholder: bool = True) -> dict:
    return {
        "step": name,
        "span_id": make_span_id(trace_id, name),
        "parent_trace_id": trace_id,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "object_refs": object_refs,
        "input_ref": _sha(inp),
        "output_ref": _sha(out),
        "metrics": metrics_out,
        "warnings": warnings or [],
        "error": error,
        "is_placeholder": placeholder,
    }


def run_case(case: dict, ctx: dict) -> dict:
    registry = ctx["registry"]
    fixtures = ctx["fixtures"]
    profile = ctx["profile"]
    run_id = ctx["run_id"]
    trace_id = make_trace_id(run_id, case["case_id"])
    refs = {step: registry.chain_object_refs(step) for step in STEP_ORDER}
    chain: list[dict] = []
    errors: list[dict] = []
    warnings: list[str] = []
    scenario = case.get("scenario", "normal")

    fixture = fixtures[case["fixture_id"]]
    document_text = render_fixture(fixture)
    doc_id = f"{case['fixture_id']}@{ctx['dataset_hash'][:8]}"

    # ---- 1. 分块 ----
    t = time.perf_counter()
    chunk_params = registry.content("OBJ-CHUNK-PARAMS")
    chunks, chunk_meta = chunk_document(doc_id, document_text, chunk_params)
    chain.append(_step(
        trace_id, "chunk", "ok", t, refs["chunk"],
        {**chunk_meta, "chunk_count": len(chunks), "document_chars": len(document_text),
         "chunk_params_ref": "OBJ-CHUNK-PARAMS@" + registry.get("OBJ-CHUNK-PARAMS")["version"],
         "estimator": "char_approx"},
        warnings=(["达到 max_chunks_per_document 上限，已截断"] if chunk_meta["truncated"] else []),
        inp=document_text[:512], out="|".join(c["chunk_id"] for c in chunks),
        placeholder=False,
    ))

    # ---- 2. embedding（占位） ----
    t = time.perf_counter()
    emb_cfg = registry.content("OBJ-EMBEDDING-CFG")["offline_placeholder"]
    dim = int(emb_cfg["dimension"])
    vectors = adapters.stub_embed([c["content"] for c in chunks], dim) if chunks else []
    query_vector = adapters.stub_embed([case["query"]], dim)[0]
    chain.append(_step(
        trace_id, "embed", "ok", t, refs["embed"],
        {"vector_count": len(vectors), "dimension": dim, "algorithm": emb_cfg["algorithm"],
         "semantic_capability": False, "model_id": "__TBC__",
         "embedding_cfg_ref": "OBJ-EMBEDDING-CFG@" + registry.get("OBJ-EMBEDDING-CFG")["version"]},
        warnings=["占位向量不具备语义能力，embedding 相关质量指标不可用于横向比较"],
        inp="|".join(c["chunk_id"] for c in chunks), out=str(len(vectors)),
    ))

    # ---- 3. 检索 ----
    t = time.perf_counter()
    ret_cfg = registry.content("OBJ-RETRIEVAL-CFG")
    top_k = int(ret_cfg["top_k"])
    pool = int(ret_cfg["candidate_pool_per_branch"])
    rrf_k = int(ret_cfg["rrf_k"])
    force_empty = (
        scenario in ("empty_retrieval", "no_evidence", "empty_query")
        or not chunks
        or not (case.get("query") or "").strip()
    )
    if force_empty:
        retrieved: list[dict] = []
    else:
        vec_ranked = sorted(
            ((adapters.cosine(query_vector, v), c) for v, c in zip(vectors, chunks)),
            key=lambda x: x[0], reverse=True,
        )[:pool]
        lex_ranked = sorted(
            ((adapters.lexical_score(case["query"], c["content"]), c) for c in chunks),
            key=lambda x: x[0], reverse=True,
        )[:pool]
        fused: dict[str, dict] = {}
        for branch, ranked in (("vector", vec_ranked), ("lexical", lex_ranked)):
            for rank, (score, c) in enumerate(ranked, start=1):
                entry = fused.setdefault(c["chunk_id"], {"chunk": c, "score": 0.0, "branches": []})
                entry["score"] += 1.0 / (rrf_k + rank)
                if branch not in entry["branches"]:
                    entry["branches"].append(branch)
        ordered = sorted(fused.values(), key=lambda x: (x["score"], x["chunk"]["seq"]), reverse=True)
        retrieved = [
            {"chunk_id": e["chunk"]["chunk_id"], "content": e["chunk"]["content"],
             "page_no": e["chunk"]["page_no"], "section_path": e["chunk"]["section_path"],
             "score": round(e["score"], 6), "snippet": e["chunk"]["content"][:500],
             "doc_title": fixture.get("title", doc_id), "branches": e["branches"]}
            for e in ordered[:top_k]
        ]
    context_items = [
        {"index": i + 1, "chunk_id": r["chunk_id"], "snippet": r["snippet"],
         "page_no": r["page_no"], "section_path": r["section_path"], "doc_title": r["doc_title"]}
        for i, r in enumerate(retrieved[:8], start=1)
    ]
    ret_metrics = metrics.retrieval_metrics(case.get("gold_evidence", []), chunks, retrieved, top_k)
    chain.append(_step(
        trace_id, "retrieve", "ok", t, refs["retrieve"],
        {**ret_metrics, "top_k": top_k, "mode": ret_cfg["mode"], "forced_empty": force_empty,
         "retrieval_cfg_ref": "OBJ-RETRIEVAL-CFG@" + registry.get("OBJ-RETRIEVAL-CFG")["version"]},
        warnings=(["内部占位：强制空召回以模拟检索无结果"] if force_empty else []),
        inp=case["query"], out="|".join(r["chunk_id"] for r in retrieved),
        placeholder=False,
    ))

    # ---- 4. 提示词组装 ----
    t = time.perf_counter()
    prompt_obj = registry.content("OBJ-PROMPT-RAG-ANSWER")
    rules = prompt_obj["render_rules"]
    budget = int(rules["max_prompt_chars_offline"])
    rendered_parts = []
    for item in context_items[: int(rules["max_context_items"])]:
        rendered_parts.append(
            prompt_obj["context_item_template"].format(
                index=item["index"], doc_title=item["doc_title"],
                page_no=item["page_no"] or "-", section_path=item["section_path"] or "正文",
                snippet=item["snippet"][: int(rules["max_snippet_chars"])],
            )
        )
    context_block = "\n\n".join(rendered_parts) if rendered_parts else "（无可用资料片段）"
    messages = [
        {"role": m["role"], "content": m["template"].format(context=context_block, query=case["query"])}
        for m in prompt_obj["messages"]
    ]
    prompt_text = "\n".join(m["content"] for m in messages)
    prompt_overflow = len(prompt_text) > budget
    prompt_hash = _sha(prompt_text)
    if prompt_overflow:
        warnings.append(f"提示词字符数 {len(prompt_text)} 超过离线预算 {budget}，已记录溢出标记")
    chain.append(_step(
        trace_id, "assemble_prompt", "ok", t, refs["assemble_prompt"],
        {"prompt_hash": prompt_hash, "prompt_chars": len(prompt_text),
         "context_item_count": len(context_items), "prompt_overflow": prompt_overflow,
         "empty_context": len(context_items) == 0,
         "template_ref": "OBJ-PROMPT-RAG-ANSWER@" + registry.get("OBJ-PROMPT-RAG-ANSWER")["version"],
         "roles": [m["role"] for m in messages]},
        warnings=(["prompt_overflow"] if prompt_overflow else []),
        inp=case["query"] + "|" + "|".join(i["chunk_id"] for i in context_items),
        out=prompt_hash, placeholder=False,
    ))

    # ---- 5. 回答生成（占位，含故障注入） ----
    t = time.perf_counter()
    gen_cfg = registry.content("OBJ-GENERATION-CFG")
    gen_fault = {"upstream_timeout": "upstream_timeout",
                 "upstream_abnormal_response": "upstream_abnormal_response"}.get(scenario)
    answer_text = ""
    gen_result: dict = {}
    gen_error = None
    try:
        gen_result = adapters.extractive_answer(case["query"], context_items, gen_cfg, fault=gen_fault)
        answer_text = gen_result["answer_text"]
        gen_status = "ok"
    except (adapters.PlaceholderUpstreamTimeout, adapters.PlaceholderMalformedResponse) as exc:
        gen_status = "error"
        gen_error = {"code": exc.code, "message": str(exc), "kind": type(exc).__name__,
                     "note": "内部占位错误分类，非供应商错误码"}
        errors.append({"step": "generate", **gen_error})
    chain.append(_step(
        trace_id, "generate", gen_status, t, refs["generate"],
        {"answer_chars": len(answer_text), "abstained": bool(gen_result.get("abstained")),
         "cited_indices": gen_result.get("cited_indices", []),
         "model_id": "__TBC__", "generation_cfg_ref": "OBJ-GENERATION-CFG@" + registry.get("OBJ-GENERATION-CFG")["version"],
         "request_response_schema": "__TBC__", "token_usage": None, "cost": None},
        warnings=(["真实生成参数名/取值范围待确认"]),
        error=gen_error, inp=prompt_hash,
        out=answer_text[:512],
    ))

    # ---- 6. 评分（占位，含不可用故障注入） ----
    t = time.perf_counter()
    score_fault = "scoring_unavailable" if scenario == "scoring_unavailable" else None
    scoring = adapters.rule_score(
        {"answer_text": answer_text, "context_items": context_items,
         "expected_behavior": case.get("expected_behavior"),
         "prompt_overflow_recorded": "prompt_overflow" in chain[3]["metrics"]},
        fault=score_fault,
    )
    chain.append(_step(
        trace_id, "score", "ok" if scoring["status"] == "ok" else "degraded", t, refs["score"],
        {"scoring_status": scoring["status"], "checks": scoring["checks"],
         "composite_score": scoring["composite_score"],
         "composite_freeze_status": scoring.get("composite_freeze_status", "TBC__not_frozen"),
         "comparable": False,
         "scoring_rules_ref": "OBJ-SCORING-RULES@" + registry.get("OBJ-SCORING-RULES")["version"]},
        warnings=(["评分不可用：按策略记录 incomplete，不得静默记 0 分"] if score_fault else []),
        inp=answer_text[:512], out=json.dumps(scoring["checks"], ensure_ascii=False, sort_keys=True),
    ))

    # ---- 最终状态与可判定性 ----
    if scoring["status"] == "unavailable":
        final_status = "incomplete"
    elif gen_status == "error":
        final_status = "degraded"
    else:
        final_status = "ok"
    expected_status = case.get("expected_final_status", "ok")
    fault_handled_correct = final_status == expected_status

    gen_metrics = metrics.generation_metrics(answer_text, case.get("expected_keywords", []),
                                             case.get("expected_behavior", ""))
    record = {
        "schema_version": "eval-record/v0.1",
        "run_id": run_id,
        "trace_id": trace_id,
        "case_run_id": stable_hash({"run_id": run_id, "case_id": case["case_id"], "seed": ctx["seed"]})[:16],
        "case_id": case["case_id"],
        "case_version": ctx["dataset_version"],
        "suite": case.get("suite"),
        "scenario": scenario,
        "profile": profile,
        "experiment_id": ctx.get("experiment_id"),
        "variant_id": ctx.get("variant_id"),
        "seed": ctx["seed"],
        "object_versions": registry.versions(),
        "config_snapshot_hash": ctx["config_hash"],
        "code_hash": ctx["code_hash"],
        "dataset_hash": ctx["dataset_hash"],
        "baseline_ref": "OBJ-BASELINE@" + registry.get("OBJ-BASELINE")["version"],
        "chain": chain,
        "final": {
            "status": final_status,
            "expected_status": expected_status,
            "fault_handled_correct": fault_handled_correct,
            "answer_text": answer_text,
            "citations": context_items,
            "retrieval_metrics": ret_metrics,
            "generation_metrics": gen_metrics,
            "scoring": scoring,
            "quality_comparable": False,
        },
        "warnings": warnings,
        "errors": errors,
        "observability": {
            "trace_fields": {
                "project": "kbqa-eval", "session_id": run_id, "trace_id": trace_id,
                "case_id": case["case_id"], "suite": case.get("suite"), "scenario": scenario,
                "experiment_id": ctx.get("experiment_id"), "variant_id": ctx.get("variant_id"),
                "config_snapshot_hash": ctx["config_hash"], "code_hash": ctx["code_hash"],
                "dataset_hash": ctx["dataset_hash"],
                "object_versions": registry.object_refs(),
                "status": final_status, "seed": ctx["seed"],
                "token_usage": None, "cost": None,
            },
            "log_policy": "仅记录 prompt_hash / 输入摘要哈希 / 脱敏预览，不记录原始提示词、凭证与完整回答",
        },
        "placeholder": True,
        "comparable": False,
    }
    return record
