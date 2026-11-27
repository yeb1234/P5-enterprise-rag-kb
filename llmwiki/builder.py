"""构建 LLM Wiki：模型把分块整理成页面，**每条结论必须带证据链**。

## Wiki 仓库长什么样（两样东西）

1. **页面（Markdown）**：模型整理出来的业务概念 / 流程 / 规则，给人读、也给智能体读；
2. **claims（证据链）**：页面里的每一条结论，各自出自哪个分块、原话是什么、
   当时那段正文的指纹是多少。

## 为什么证据链要校验，而不是「模型给了就存」

Wiki 是给后续检索当事实用的。模型写页面时会「顺手归纳」，归纳本身是我们要的，
但证据必须是原文里真实存在的：
- 证据指向的 chunk_id 不在本批素材里 → 丢；
- 给了原话（quote）但原文里找不到这句 → 丢这条证据（空白差异忽略）；
- 一条结论的证据全被丢光 → 丢这条结论；
- 一页没有任何有效结论 → 整页丢弃。

这些数字都记在 `stats` 里：丢了多少、为什么丢，构建报告能直接给人看。
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from llmwiki import contracts
from llmwiki.gate import text_fingerprint

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """比对原话时忽略所有空白：模型会重新排版，但不该改动字。"""
    return _WS.sub("", text or "")


def _parse_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    lo, hi = text.find("{"), text.rfind("}")
    if lo < 0 or hi <= lo:
        return None
    body = re.sub(r",\s*([}\]])", r"\1", text[lo:hi + 1])      # 尾逗号
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _note_failure(stats: dict, index: int, reason: str) -> None:
    """记下每批失败的原因（最多 8 条）。只给一个「失败了几批」的计数，排障时等于没说——
    实测第一版就是这样，看到「6 批失败 5 批」却不知道是超时还是被截断。"""
    if len(stats["failures"]) < 8:
        stats["failures"].append({"batch": index + 1, "reason": reason})


def _dedupe(chunks: list[dict]) -> tuple[list[dict], int]:
    """同一份文本只送一次。演示库里重复文档很多，不合并就是白花模型调用。"""
    out, seen = [], set()
    for c in chunks:
        key = (c.get("text") or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out, len(chunks) - len(out)


def _validate_batch(data: dict, batch: list[dict], scenario: str,
                    stats: dict) -> list[dict]:
    """校验一批模型输出，返回通过的页面。每个丢弃分支都计数。"""
    by_id = {c["chunk_id"]: c for c in batch}
    pages: list[dict] = []
    for raw in data.get("pages") or []:
        if not isinstance(raw, dict):
            stats["pages_dropped"] += 1
            continue
        title = str(raw.get("title") or "").strip()[:contracts.MAX_TITLE]
        body = str(raw.get("markdown") or "").strip()[:contracts.MAX_BODY]
        section = str(raw.get("section") or "").strip()
        if section not in contracts.SECTIONS:
            stats["sections_normalized"] += 1
            section = contracts.DEFAULT_SECTION
        if not title or not body:
            stats["pages_dropped"] += 1
            continue

        claims: list[dict] = []
        for rc in raw.get("claims") or []:
            if not isinstance(rc, dict):
                stats["claims_dropped"] += 1
                continue
            statement = str(rc.get("statement") or "").strip()[:contracts.MAX_STATEMENT]
            ev_out: list[dict] = []
            for re_ in rc.get("evidence") or []:
                cid = str((re_ or {}).get("chunk_id") or "").strip() if isinstance(re_, dict) else ""
                chunk = by_id.get(cid)
                if not chunk:                       # 证据指向素材之外：编的
                    stats["evidence_dropped"] += 1
                    continue
                quote = str(re_.get("quote") or "").strip()[:200]
                if quote and _norm(quote) not in _norm(chunk.get("text") or ""):
                    stats["evidence_dropped"] += 1   # 原话在原文里找不到
                    continue
                ev_out.append({"chunk_id": cid, "doc_id": chunk.get("doc_id"),
                               "idx": chunk.get("idx"), "quote": quote})
            if not statement or not ev_out:
                stats["claims_dropped"] += 1
                continue
            src = "\n".join(by_id[e["chunk_id"]].get("text") or "" for e in ev_out)
            claims.append({"statement": statement, "evidence": ev_out,
                           "source_hash": text_fingerprint(src)})
        if not claims:                              # 没有一条有证据的结论：整页不要
            stats["pages_dropped"] += 1
            continue
        page_id = hashlib.sha1(f"{scenario}|{section}|{title}".encode("utf-8")).hexdigest()[:16]
        pages.append({"page_id": page_id, "title": title, "section": section, "body_md": body,
                      "summary": body.replace("\n", " ")[:120], "scenario": scenario,
                      "claims": claims})
    return pages


def build(scenario: str, chunks: list[dict], llm, *, instruction: str = "",
          batch_size: int = 6, max_batches: int | None = None,
          on_batch=None) -> dict:
    """返回 {available, pages, stats}。`llm=None` 或离线占位时 `available=False`。

    `instruction` 是管理员写的整理要求（对应视频里「人要准备的提示词」），
    它决定模型按什么口径整理，所以它属于「业务场景已确定」的一部分。

    `on_batch(i, n)`：每开始一批时回调一次，供调用方报进度（构建整体要跑几分钟，是后台任务）。

    ## 每批为什么只给 6 段、并给输出设上限

    实测踩过：一批 12 段、`max_tokens=2500` 时，6 批里 5 批失败——要么输出被截断成半截 JSON
    解析不了，要么单次调用超过 90 秒超时。生成速度大约 60 token/s，让模型「随便写」它就会
    写到上限。所以提示词里明确限制每批页数、每页字数、每页结论条数，把单次输出压到千余 token。
    """
    stats = {"batches": 0, "failed_batches": 0, "batches_skipped": 0, "chunks_total": len(chunks),
             "chunks_deduped": 0, "chunks_sent": 0, "pages_raw": 0, "pages_kept": 0,
             "pages_dropped": 0, "claims_kept": 0, "claims_dropped": 0, "evidence_dropped": 0,
             "sections_normalized": 0, "failures": [], "elapsed_ms": 0}
    # 计数口径：batches = 实际送给模型的批数；failed_batches = 送了但没拿到可用结果的批数；
    # batches_skipped = 因为提前中止或 max_batches 截断而没送的批数。
    out = {"available": False, "pages": [], "stats": stats}
    started = time.perf_counter()
    if llm is None or not scenario.strip() or not chunks:
        return out

    uniq, stats["chunks_deduped"] = _dedupe(chunks)
    batches = [uniq[i:i + batch_size] for i in range(0, len(uniq), batch_size)]
    if max_batches is not None:
        stats["batches_skipped"] = max(0, len(batches) - max_batches)
        batches = batches[:max_batches]
    pages_by_id: dict[str, dict] = {}
    for i, batch in enumerate(batches):
        stats["batches"] += 1
        stats["chunks_sent"] += len(batch)
        if on_batch is not None:
            on_batch(i + 1, len(batches))
        listing = "\n\n".join(
            f"[chunk_id={c['chunk_id']}] ({(c.get('title') or '')[:40]})\n{(c.get('text') or '')[:700]}"
            for c in batch)
        prompt = (
            f"你是企业知识库的「知识整理员」。业务场景：{scenario}。\n"
            f"管理员给的整理要求：{instruction.strip() or '（无，按通用口径整理）'}\n\n"
            f"页面目录分组只能取这四种之一：{' / '.join(contracts.SECTIONS)}。\n\n"
            "下面是知识库里的一批原文分块（已脱敏）。请把它们整理成 LLM Wiki 页面：\n"
            "1. **本批最多 2 页**；每页 Markdown **不超过 350 字**，只写与本业务场景有关的内容；\n"
            "2. 与场景无关的分块直接忽略；这一批没有值得整理的内容时输出 {\"pages\":[]}；\n"
            "3. 每页最多 4 条 claim，每条 statement 不超过 60 字，并给出证据：出自哪个分块"
            "（chunk_id），附上该分块里**连续的原话**作 quote（不超过 40 字）；\n"
            "4. 不要写原文里没有的规则；拿不准的结论不要写；\n"
            "5. 只输出 JSON，不要解释。格式：\n"
            '{"pages":[{"title":"…","section":"业务流程","markdown":"…","claims":'
            '[{"statement":"…","evidence":[{"chunk_id":"…","quote":"…"}]}]}]}\n\n'
            f"原文分块：\n{listing}"
        )
        try:
            raw = llm.complete(prompt, system="你是知识整理员，只输出 JSON。",
                               max_tokens=1500, temperature=0.0)
        except Exception as exc:  # noqa: BLE001 - 单批失败不该让整次构建失败
            stats["failed_batches"] += 1
            _note_failure(stats, i, f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if not (raw or "").strip():
            # 离线占位的 complete() 返回空串：根本没有可用模型，后面的批没有继续的意义。
            # 没送的批如实记为 skipped，不记成 failed。
            stats["failed_batches"] += 1
            _note_failure(stats, i, "模型返回空（离线占位或不可用）")
            stats["batches_skipped"] += len(batches) - i - 1
            break
        data = _parse_json(raw)
        if not data:
            stats["failed_batches"] += 1
            _note_failure(stats, i, "输出不是完整 JSON（多半是超出 max_tokens 被截断）")
            continue
        stats["pages_raw"] += len(data.get("pages") or [])
        for page in _validate_batch(data, batch, scenario, stats):
            # 同一主题的页面出现在不同批次里：合并结论，不重复建页
            cur = pages_by_id.get(page["page_id"])
            if cur is None:
                pages_by_id[page["page_id"]] = page
            else:
                cur["claims"].extend(page["claims"])

    out["pages"] = list(pages_by_id.values())
    # available 的含义是「模型真的在干活」：至少有一批拿到了可用结果。
    # 注意它不等于「有页面」——模型正常工作但产出全被证据校验拦下时，available 为真、页面为空。
    out["available"] = (stats["batches"] - stats["failed_batches"]) > 0
    stats["pages_kept"] = len(out["pages"])
    stats["claims_kept"] = sum(len(p["claims"]) for p in out["pages"])
    stats["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return out
