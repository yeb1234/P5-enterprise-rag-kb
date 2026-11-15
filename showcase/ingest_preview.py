"""左 `AnalysisView` / 右 `IngestDocument` 组装，以及确定性投影。

硬约束（`docs/showcase/03-phase2-spec.md` §1.1(4)(8)）：
- 两区 MUST 由**同一次** `ParseRouter.parse_document()` 的 `rep` 组装，共用 `run_id`。
- `created_at` 取固定逻辑时间 `params.as_of`；挂钟时间与实时耗时不进右侧。
- `canonical()` 是**路径感知**投影，用于 V5「同参双跑逐字节一致」；
  它剔除决策实例字段（manifest / llm_route 的 route_id·hysteresis·reason / 耗时），
  但**保留** `trace[].reason`（解析原因码）与 `source.sha256`（源文件摘要），
  否则会掩盖真实差异、把验证做成假阳性。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from showcase.guard import protect_text

SCHEMA_VERSION = "showcase-ingest@1.0"
NON_REPLAY_FIELDS = ("latency_ms", "created_at", "run_id", "generated_at")
# 任意层级都剔除的观测字段
_OBS_KEYS = frozenset({"latency_ms", "wall_clock", "elapsed_ms", "generated_at"})
# 只在 llm_route 下剔除的决策实例字段
_ROUTE_VOLATILE = frozenset({"route_id", "hysteresis", "reason"})


def _p(params: Any, key: str, default: Any = None) -> Any:
    """Params 对象优先；同时兼容 dict，避免调用方形态差异直接 500。"""
    if isinstance(params, dict):
        return params.get(key, default)
    return getattr(params, key, default)


def canonical(obj: Any, _path: tuple[str, ...] = ()) -> Any:
    """规范化投影：剔除运行时派生字段，保留入库内容本体。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k in sorted(obj.keys()):
            path = _path + (str(k),)
            if path == ("manifest",):
                continue
            if len(path) >= 2 and path[0] == "llm_route" and k in _ROUTE_VOLATILE:
                continue
            if k in _OBS_KEYS:
                continue
            out[k] = canonical(obj[k], path)
        return out
    if isinstance(obj, (list, tuple)):
        return [canonical(x, _path) for x in obj]
    return obj


def canonical_dumps(obj: Any) -> str:
    """逐字节可比的规范化字符串。"""
    return json.dumps(canonical(obj), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """确定性分块。overlap 必须小于 size；空文本返回空列表。"""
    if overlap >= size or size <= 0:
        raise ValueError("INVALID_PARAM: chunk_overlap_chars 必须小于 chunk_size_chars")
    if not text or not text.strip():
        return []
    step = size - overlap
    return [text[i:i + size] for i in range(0, len(text), step)]


def _chunk_id(page_no: int, seq: int, text: str) -> str:
    raw = f"{page_no}:{seq}:{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _by_kind(chain: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in chain:
        for k, n in (r.get("by_kind") or {}).items():
            out[k] = out.get(k, 0) + n
    return out


def build_views(payload: bytes, *, filename: str, mime: str, params: Any,
                rep: dict, route: dict, pii: dict, run_id: str) -> tuple[dict, dict]:
    """返回 (AnalysisView, IngestDocument)。"""
    pmap = params.as_dict() if hasattr(params, "as_dict") else dict(params)
    as_of = str(_p(params, "as_of", ""))
    fmt = str(_p(params, "ingest_format", "compact"))
    size = int(_p(params, "chunk_size_chars", 800))
    overlap = int(_p(params, "chunk_overlap_chars", 80))
    redact_on = bool(_p(params, "redact_enabled", True))

    results = list(rep.get("results") or [])
    traces = list(rep.get("traces") or [])
    budget = dict(rep.get("budget") or {})
    summary = dict(rep.get("summary") or {})
    source_sha = hashlib.sha256(payload).hexdigest()

    # ---------------- 左：AnalysisView（给人看） ----------------
    pages: list[dict] = []
    for i, r in enumerate(results):
        t = traces[i] if i < len(traces) else {}
        sig = dict(r.get("signals") or {})
        text = str(r.get("text") or "")
        pages.append({
            "page_no": t.get("page_no", i + 1),
            "preview": text[:180],
            "level": t.get("level", r.get("level", "local")),
            "confidence": sig.get("confidence", t.get("confidence", 0.0)),
            "degraded": t.get("degraded", r.get("degraded")),
            "signals": sig,
            "blocks": list(r.get("blocks") or []),
            "trace": dict(t),
        })

    warnings: list[str] = []
    for t in traces:
        if t.get("budget_blocked"):
            warnings.append("视觉升级被预算闸门拦截，已留痕并回退本级正文")
        if t.get("degraded"):
            warnings.append(f"降级：{t.get('degraded')}")
    chain = list(pii.get("chain") or [])
    analysis: dict[str, Any] = {
        "run_id": run_id,
        "doc_id": rep.get("doc_id", ""),
        "filename": filename,
        "mime": mime,
        "page_count": rep.get("page_count", len(results)),
        "strategy": rep.get("strategy", pmap.get("strategy", "auto")),
        "params_snapshot": {**pmap, "params_hash": _p(params, "params_hash", "")},
        "pages": pages,
        "summary": summary,
        "budget": budget,
        "pii": pii.get("report", {}),
        "route": route,
        "warnings": warnings,
    }

    # ---------------- 右：IngestDocument（给机器看） ----------------
    chunks: list[dict] = []
    for pg in pages:
        no = int(pg["page_no"] or 1)
        # 每页文本单独过 P0 规则（同一套 Guardrails），保证 chunks 里不留明文标识符
        safe_text, _ = protect_text(str(results[no - 1].get("text") or "")
                                    if no - 1 < len(results) else "", redact_on)
        for seq, piece in enumerate(chunk_text(safe_text, size, overlap)):
            blocks = pg["blocks"]
            btype = "paragraph"
            section = ""
            if blocks:
                first = blocks[0] if isinstance(blocks[0], dict) else {}
                btype = first.get("block_type", "paragraph")
                section = first.get("section_path", "")
            chunks.append({
                "chunk_id": _chunk_id(no, seq, piece),
                "page_no": no,
                "block_type": btype,
                "text": piece,
                "section_path": section,
                "confidence": pg["confidence"],
                "source_level": pg["level"],
                "cells": None,
                "facts": None,
                "char_start": seq * max(1, size - overlap),
                "char_end": seq * max(1, size - overlap) + len(piece),
                "redacted": redact_on,
                "rule_version": pii.get("rule_version", "PII-RULES@1.0"),
            })

    levels: dict[str, int] = {}
    for t in traces:
        lv = str(t.get("level") or "local")
        levels[lv] = levels.get(lv, 0) + 1

    parser_cfg = {k: pmap.get(k) for k in (
        "confidence_min", "vision_escalation_ratio", "vision_min_slots",
        "chunk_size_chars", "chunk_overlap_chars", "strategy")}
    parser_cfg["offline"] = True

    guardrails = {
        "rule_version": pii.get("rule_version", "PII-RULES@1.0"),
        "gates": [r.get("gate") for r in chain],
        "total_hits": pii.get("report", {}).get("total_hits", 0),
        "blocked": pii.get("report", {}).get("blocked", False),
        "by_kind": _by_kind(chain),
    }

    ingest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": as_of,
        "doc_id": rep.get("doc_id", ""),
        # 复现三要素：manifest 需要 params_hash / config_snapshot，
        # 放在顶层让 snapshot.write_pair() 直接取到（compact 档也保留，体量很小）。
        "params_hash": _p(params, "params_hash", ""),
        "config_snapshot": dict(parser_cfg),
        "params_snapshot": {**pmap, "params_hash": _p(params, "params_hash", "")},
        "source": {
            "filename": filename,
            "mime": mime,
            "size_bytes": len(payload),
            "sha256": source_sha,
            "page_count": rep.get("page_count", len(results)),
        },
        "parser": {
            "strategy": analysis["strategy"],
            "levels": levels,
            "config": parser_cfg,
            "budget": budget,
            "thresholds": {"threshold_note": "演示起始值，非压测结论"},
        },
        "llm_route": dict(route),
        "chunks": chunks,
        "trace": [dict(t) for t in traces],
        "guardrails": guardrails,
        # write_pair() 会回填 path / sha256 / snapshot_version，此处留空占位
        "manifest": {},
        "cost": {"estimated": True, "currency": "CNY",
                 "external_request_count": 0,
                 "note": "演示离线链路，未产生外部调用费用"},
        "note": "演示产物，不是生产入库记录",
    }

    if fmt == "compact":
        ingest["parser"] = {"strategy": ingest["parser"]["strategy"],
                            "levels": levels}
        route_full = ingest["llm_route"]
        ingest["llm_route"] = {k: route_full.get(k) for k in (
            "route_id", "tier", "model", "snapshot_version", "placeholder")}
        ingest["chunks"] = [{k: c.get(k) for k in (
            "chunk_id", "page_no", "block_type", "text", "section_path")}
            for c in chunks]
        ingest["trace"] = [{k: t.get(k) for k in ("page_no", "level", "reason")}
                           for t in traces]
        # §5.4 未列 guardrails/manifest/cost，但 spec §1.1(2) 要求回放标注在 note，
        # 故 note 保留；guardrails/manifest/cost 在 compact 档移除。
        for k in ("guardrails", "cost"):
            ingest.pop(k, None)

    return analysis, ingest
