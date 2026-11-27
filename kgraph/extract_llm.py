"""LLM 抽取带类型的实体关系（可选增强，`rag_graph_extractor=llm` 时才调用）。

## 定位

离线抽取给的是「术语 + 共现」，回答不了「A 和 B 是什么关系」。本模块让模型读分块，
输出 `实体 / 关系类型 / 实体` 三元组，叠加到图上。

## 成本与脏数据

- **成本**：每批一次调用。同一文本只送一次（演示库里重复文档很多），并设上限 `cap`——
  超出上限的分块仍然有离线共现边，只是没有语义边。
- **脏数据必须校验**：模型抽出来的实体名若在原文里根本找不到，就是幻觉；
  关系的两个端点不在本分块的实体集合里，也丢。图是给后续检索当事实用的，
  放进去一条假的，多跳就会沿它扩散。
"""
from __future__ import annotations

import json
import re

_TRICKY = re.compile(r"[^\w\u4e00-\u9fff]")
_MAX_NAME = 32


def _parse_json(raw: str) -> dict | None:
    """容错解析：先剥代码块，再取第一个 { 到最后一个 }，最后修掉尾逗号。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    lo, hi = text.find("{"), text.rfind("}")
    if lo < 0 or hi <= lo:
        return None
    body = re.sub(r",\s*([}\]])", r"\1", text[lo:hi + 1])   # 尾逗号
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _note_failure(stats: dict, batch_no: int, reason: str) -> None:
    """逐批记失败原因。只给计数等于没说——实测时「3 批失败 2 批」根本判断不出是超时还是截断。"""
    if len(stats["failures"]) < 8:
        stats["failures"].append({"batch": batch_no, "reason": reason})


def _clean_name(value) -> str:
    name = _TRICKY.sub("", str(value or "")).strip()
    return name if 0 < len(name) <= _MAX_NAME else ""


def _clean_type(value) -> str:
    kind = _TRICKY.sub("", str(value or "")).strip()[:24]
    return kind or "related"


def extract_llm(chunks: list[dict], llm, *, batch_size: int = 8, cap: int = 80) -> dict:
    """返回 {entities, mentions, relations, stats}，可直接交给 builder.plan(llm_extra=...)。

    `llm=None` 表示当前没有可用模型（离线占位）——返回空结果且 `stats.available=False`，
    调用方据此在同步报告里写明「本次没有语义边」。
    """
    out: dict = {"entities": [], "mentions": [], "relations": [],
                 "stats": {"available": llm is not None, "batches": 0, "failed_batches": 0,
                           "chunks_total": len(chunks), "chunks_deduped": 0, "chunks_sent": 0,
                           "chunks_skipped": 0, "accepted_entities": 0,
                           "accepted_relations": 0, "dropped": 0, "failures": []}}
    if llm is None or not chunks:
        return out

    uniq: list[dict] = []
    seen: set[str] = set()
    for c in chunks:
        key = (c.get("text") or "").strip()
        if key and key not in seen:
            seen.add(key)
            uniq.append(c)
    selected, out["stats"]["chunks_skipped"] = uniq[:cap], max(0, len(uniq) - cap)
    out["stats"]["chunks_sent"] = len(selected)
    out["stats"]["chunks_deduped"] = len(chunks) - len(uniq)

    names_seen: dict[str, str] = {}
    for start in range(0, len(selected), batch_size):
        batch = selected[start:start + batch_size]
        listing = "\n\n".join(
            f"[{i}] chunk_id={c['chunk_id']}\n{(c.get('text') or '')[:500]}" for i, c in enumerate(batch))
        prompt = (
            "你在为知识库抽取实体关系。逐段阅读下面的文本，对每一段输出它提到的实体，"
            "以及实体之间**这段文本明确说出**的关系。\n"
            "规则：\n"
            "1. **每段最多 3 个实体、2 条关系**；只挑最有信息量的，宁少勿多；\n"
            "2. 实体名必须是原文里出现过的原词（可以是短语），不要自己改写或翻译；\n"
            "3. 关系类型用简短动词或短语（如：长按进入、属于、会导致），文本没说就不要编；\n"
            "4. 只输出 JSON，不要解释，不要 Markdown 代码块。格式：\n"
            '{"chunks":[{"index":0,"entities":[{"name":"复位键","kind":"部件"}],'
            '"relations":[{"src":"复位键","dst":"配网模式","type":"长按进入"}]}]}\n\n'
            f"文本：\n{listing}"
        )
        out["stats"]["batches"] += 1
        try:
            raw = llm.complete(prompt, system="你是实体关系抽取器，只输出 JSON。",
                               max_tokens=1500, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - 单批失败不该让整次同步失败
            out["stats"]["failed_batches"] += 1
            _note_failure(out["stats"], start // batch_size + 1,
                          f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if not (raw or "").strip():
            # 离线占位的 complete() 返回空串：说明根本没有可用模型，不必继续
            out["stats"]["available"] = False
            break
        data = _parse_json(raw)
        if not data:
            out["stats"]["failed_batches"] += 1
            _note_failure(out["stats"], start // batch_size + 1,
                          f"JSON 解析失败（输出长度 {len(raw)}，可能被 max_tokens 截断）")
            continue
        for item in data.get("chunks") or []:
            try:
                chunk = batch[int(item.get("index"))]
            except (TypeError, ValueError, IndexError):
                out["stats"]["dropped"] += 1
                continue
            text_low = (chunk.get("text") or "").lower()
            valid: dict[str, str] = {}
            for e in item.get("entities") or []:
                name = _clean_name(e.get("name") if isinstance(e, dict) else e)
                if name and name.lower() in text_low:      # 幻觉实体在这里被拦下
                    valid[name] = _clean_type(e.get("kind") if isinstance(e, dict) else "") or "entity"
                else:
                    out["stats"]["dropped"] += 1
            for r in item.get("relations") or []:
                src, dst = _clean_name(r.get("src")), _clean_name(r.get("dst"))
                if src in valid and dst in valid and src != dst:
                    out["relations"].append({"src": src, "dst": dst, "type": _clean_type(r.get("type")),
                                             "chunk_id": chunk["chunk_id"]})
                else:
                    out["stats"]["dropped"] += 1
            if valid:
                out["mentions"].append({"chunk_id": chunk["chunk_id"], "entities": sorted(valid)})
                for name, kind in valid.items():
                    names_seen.setdefault(name, kind)
    out["entities"] = [{"name": n, "kind": k} for n, k in sorted(names_seen.items())]
    out["stats"]["accepted_entities"] = len(out["entities"])
    out["stats"]["accepted_relations"] = len(out["relations"])
    return out
