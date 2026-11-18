#!/usr/bin/env python3
"""RAGTruth span 级标注复现 —— LLM 裁判版（破例，与主口径隔离）。

【破例声明】
evalbench 主口径明确「不用第二个 LLM 当裁判」（scoring.py / README）。
本评估器是 span 级标注的专项破例：span 幻觉判定本质是语义判断，
确定性规则实测 F1≈0.098 无法复现（见 evaluate_span_hallucination.py）。
本脚本仅用于 span 专项研究，**不进入 evalbench 主 run、不参与任何主指标收敛**。

方法
----
对 RAGTruth 数据集自带的 (context, output, 人工 span 标注)：
1. 把 output 按句切成候选 span（带字符偏移）；
2. 把全部候选 span + context 一次性发给裁判模型，让它逐句判
   faithful / evident_conflict / baseless_info，输出 JSON 数组；
3. 把裁判判为幻觉的 span 与人工 span 做字符级 IOU 对拍（阈值 0.5），
   报 span 级 precision / recall / F1；
4. **裁判看不到人工标签**，避免泄露导致假绿。

裁判默认模型：qwen3.8-27b（temperature=0, max_tokens=2048）。
选它的依据：网关 /models 真实 pricing 显示 prompt $0.0008 / completion $0.0027
（比 deepseek-v4.1-flash 的 $0.001/$0.004 便宜），延迟快一倍（1.1s vs 2.2s），
50 条样本 F1 0.262 vs deepseek 的 0.286（差 0.024，约 1 条 span，小样本噪声范围）。
**更便宜的 Sage-A1.5($0.000016) 实测输出不出合法 JSON，不可用。**
每条样本独立缓存（key 含裁判模型 + prompt hash + output hash），不重复烧 API。

用法：
    python evalbench/datasets/public/span_llm_judge.py --split QA --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from evalbench.core import runner  # noqa: E402
from webapp.providers import thinking_fields  # noqa: E402
# 复用 detector 的锚点正则与句首词表，refine 缩边界用（判定仍由 LLM 做）
from evaluate_span_hallucination import _NUM_RE, _PROPER_RE, _SENT_STARTERS  # noqa: E402

JUDGE_PROMPT = """You are a strict fact-checking judge for retrieval-augmented generation.

Given the EVIDENCE passages and an ANSWER split into numbered sentences, decide for EACH sentence whether it is:
- "faithful": every factual claim (names, numbers, dates, events) is supported by the evidence;
- "baseless": it introduces a specific fact/number/entity NOT present in the evidence;
- "conflict": it states something that contradicts the evidence.

Rules:
- Judge ONLY from the evidence. Do not use outside knowledge.
- Vague transition sentences with no factual claim are "faithful".
- Output ONLY a JSON array, one object per sentence, no prose, no markdown:
  [{"idx": 0, "label": "faithful|baseless|conflict", "reason": "<10 words>"}, ...]

EVIDENCE:
{context}

ANSWER SENTENCES:
{sentences}

JSON ARRAY:"""

# 分句先把小数点（数字.数字）换成占位符，切完再还原。
# 纯正则的负向后顾在此场景会把整句吞掉（实测），改用占位更稳。
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")
_SENT_RE = re.compile(r"[^.!?]+[.!?]?\s*")
# 占位符必须是单字符，保证 masked 与原文偏移一一对应（IOU 用原文坐标）
_DECIMAL_PLACEHOLDER = "⋅"


def split_sentences(output: str) -> list[tuple[int, int, str]]:
    """返回 (start, end, text)，偏移基于原文。"""
    masked = _DECIMAL_RE.sub(r"\1" + _DECIMAL_PLACEHOLDER + r"\2", output)
    out = []
    for m in _SENT_RE.finditer(masked):
        text = m.group(0).replace(_DECIMAL_PLACEHOLDER, ".")
        if not text.strip():
            continue
        out.append((m.start(), m.end(), text))
    # 还原后的偏移与原文一致（占位符是每 1 字符替换 1 字符）
    return out
# v2：关闭 thinking + 2048 预算后启用；与 v1 缓存隔离，避免旧判定混入
_CACHE = HERE / "span_judge_cache_v2.jsonl"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def sentences(output: str) -> list[dict]:
    out = []
    for start, end, text in split_sentences(output):
        t = text.strip()
        if len(t) < 15:
            continue
        out.append({"idx": len(out), "start": start, "end": end, "text": t})
    return out


def load_cache() -> dict:
    if not _CACHE.exists():
        return {}
    out = {}
    for line in _CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                r = json.loads(line)
                out[r["key"]] = r["value"]
            except Exception:
                pass
    return out


def parse_judgments(content: str, n: int) -> list[dict] | None:
    """从裁判输出里抠 JSON 数组；失败返回 None（不静默当全 faithful）。"""
    content = content.strip()
    m = re.search(r"\[[\s\S]*\]", content)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(arr, list):
        return None
    by_idx = {x.get("idx"): x.get("label") for x in arr if isinstance(x, dict)}
    out = []
    for i in range(n):
        label = by_idx.get(i)
        if label not in ("faithful", "baseless", "conflict"):
            return None
        out.append({"idx": i, "label": label})
    return out


def refine_span(sent: dict, context: str) -> dict:
    """裁判判 baseless 的整句 → 缩到句内含证据外锚点的最小子句。

    动机（实测）：人工 span 是短语级（~75 字符），裁判整句级（~205 字符），
    IOU 天然偏低。用规则在句内定位"证据外锚点所在子句"，把粒度拉近人工。
    规则只用于**缩边界**，是否幻觉仍由 LLM 裁判决定——不违反语义判定原则。
    """
    text = sent["text"]
    ctx = context.lower()
    # 按逗号/分号切子句，记录相对偏移
    parts = []
    pos = 0
    for piece in re.split(r"([,;])", text):
        if piece in (",", ";"):
            pos += 1
            continue
        parts.append((pos, pos + len(piece), piece))
        pos += len(piece)
    best = None
    for s, e, piece in parts:
        # 该子句里的数字/专名锚点，有多少不在 context
        hits_out = []
        for x in anchors_local(piece):
            if x in ctx:
                continue
            core = re.sub(r"[^\d.]", "", x).rstrip(".")
            if core and core in re.sub(r"[^\d.]", "", ctx):
                continue
            hits_out.append(x)
        if hits_out and (best is None or len(piece) < len(best[2])):
            best = (s, e, piece)
    if best is None:
        return sent  # 找不到细粒度锚点，退回整句
    s, e, piece = best
    return {"start": sent["start"] + s + (1 if s > 0 else 0),
            "end": sent["start"] + e, "text": piece.strip()}


def anchors_local(text: str) -> set[str]:
    """refine 用的轻量锚点：数字量 + 多词/单词大写专名（复用主提取口径）。"""
    out = set()
    for m in _NUM_RE.finditer(text):
        out.add(re.sub(r"\s+", " ", m.group(0).lower()).strip())
    for m in _PROPER_RE.finditer(text):
        w = m.group(0)
        if w.lower() in _SENT_STARTERS:
            continue
        out.add(w.lower())
    return out


def iou(a: dict, b: dict) -> float:
    s, e = max(a["start"], b["start"]), min(a["end"], b["end"])
    inter = max(0, e - s)
    union = (a["end"] - a["start"]) + (b["end"] - b["start"]) - inter
    return inter / union if union else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="QA", choices=("QA", "Summary", "Data2txt", "ALL"))
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--iou-threshold", type=float, default=0.5)
    ap.add_argument("--model", default="qwen3.8-27b", help="默认用便宜的 qwen3.8-27b；deepseek-v4.1-flash-1017 作高成本对照")
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--refine", action="store_true", help="裁判判 baseless 后，用规则把整句缩到短语级 span（粒度对齐人工标注）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = pq.read_table(HERE / "raw" / "ragtruth_processed_test.parquet").to_pylist()
    if args.split != "ALL":
        rows = [r for r in rows if r["task_type"] == args.split]
    with_span = [r for r in rows if r["hallucination_labels"] and r["hallucination_labels"] != "[]"]
    no_span = [r for r in rows if not (r["hallucination_labels"] and r["hallucination_labels"] != "[]")]
    # 正负各取一半，避免全负样本让 precision 虚高
    half = args.limit // 2
    selected = with_span[:half] + no_span[:args.limit - half]

    # 支持任意网关模型：selected 里有的用其配置，没有的用默认超时（便宜 Judge 候选常不在 selected）
    model_index = {m["id"]: m for m in runner.load_models()["selected"]}
    model_cfg = model_index.get(args.model, {"id": args.model, "timeout_s": 90})
    # 2048 预算 + 关闭 thinking：deepseek 是推理模型，默认思考过程会吃掉
    # 1024 预算导致 JSON 输不完（实测 8 条里 6 条 judge_failed）。
    # thinking_fields 与 backend/llm.py 同口径，网关实测 reasoning_tokens 55→0。
    params = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2048}
    extra = thinking_fields(disabled=True)
    cache = load_cache()

    records = []
    for r in selected:
        gold = json.loads(r["hallucination_labels"]) if r["hallucination_labels"] else []
        sents = sentences(r["output"])
        if not sents:
            continue
        sent_text = "\n".join(f"[{s['idx']}] {s['text']}" for s in sents)
        prompt = JUDGE_PROMPT.replace("{context}", r["context"][:6000]).replace("{sentences}", sent_text)
        key = sha(f"{args.model}|{JUDGE_PROMPT}|{r['output']}|{r['context']}")
        if key in cache:
            judged = cache[key]
            from_cache = True
        else:
            call = runner.call_live(model_cfg, prompt, params, extra_fields=extra)
            judged = parse_judgments(call.get("answer", ""), len(sents)) if call["status"] == "ok" else None
            from_cache = False
            if judged is not None:
                with _CACHE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "value": judged}, ensure_ascii=False) + "\n")
                cache[key] = judged
            time.sleep(args.sleep)

        if judged is None:
            records.append({"id": r["id"], "status": "judge_failed", "n_gold": len(gold), "n_pred": 0, "n_hit": 0})
            print(f"[{r['id']}] judge_failed", flush=True)
            continue

        pred = [s for s, j in zip(sents, judged) if j["label"] != "faithful"]
        if args.refine:
            # LLM 判定"这句幻觉"→ 规则缩到句内含证据外锚点的子句，粒度对齐人工 span
            pred = [refine_span(p, r["context"]) for p in pred]
        used, hits = set(), 0
        for p in pred:
            best, bi = 0.0, -1
            for i, g in enumerate(gold):
                if i in used:
                    continue
                v = iou(p, g)
                if v > best:
                    best, bi = v, i
            if best >= args.iou_threshold:
                hits += 1
                used.add(bi)
        rec = {
            "id": r["id"], "task_type": r["task_type"], "status": "ok", "from_cache": from_cache,
            "n_gold": len(gold), "n_pred": len(pred), "n_hit": hits,
            "gold_labels": [g.get("label_type") for g in gold],
            "pred": [{"start": p["start"], "end": p["end"], "text": p["text"][:100],
                      "label": j["label"]} for p, j in zip(sents, judged) if j["label"] != "faithful"],
        }
        records.append(rec)
        print(f"[{r['id']}] gold={len(gold)} pred={len(pred)} hit={hits} cache={from_cache}", flush=True)

    ok = [r for r in records if r["status"] == "ok"]
    tp, npred, ngold = (sum(r[k] for r in ok) for k in ("n_hit", "n_pred", "n_gold"))
    precision = tp / npred if npred else None
    recall = tp / ngold if ngold else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    summary = {
        "split": args.split, "n_selected": len(selected), "n_judged_ok": len(ok),
        "iou_threshold": args.iou_threshold, "judge_model": args.model,
        "gold_spans": ngold, "pred_spans": npred, "hits": tp,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "exception_note": "LLM-as-judge 破例，仅 span 专项；不进 evalbench 主口径、不参与收敛。裁判未见人工标签。",
        "caveats": ["句子级 span 与人工短语级 span 的 IOU 天然偏低", "裁判模型自身可能漏判/误判", "样本量小"],
    }
    out = Path(args.out) if args.out else HERE / f"span_judge_{args.split.lower()}.json"
    out.write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("落盘:", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
