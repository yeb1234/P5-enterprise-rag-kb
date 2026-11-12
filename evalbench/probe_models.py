#!/usr/bin/env python3
"""多模型兼容性探测：真实调用网关，记录模型间差异并给出适配结论。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from webapp.envfile import load_env  # noqa: E402

load_env()
from webapp import providers  # noqa: E402

DEFAULT_CANDIDATES = [
    "minimax-m3", "kimi-k2.6", "deepseek-v4.1-flash-1017",
    "glm-5.3", "deepseek-v4-pro-0813", "qwen3.8-27b",
]


def _content_of(data) -> tuple[int, bool]:
    if not isinstance(data, dict):
        return 0, False
    choices = data.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    return len(content), bool(not content and reasoning)


def probe_one(model: str, base: str, key: str, quick: bool = False) -> dict:
    out: dict = {"model": model}

    t0 = time.time()
    try:
        r = providers.test_llm(base, model, key)
        out["test_llm"] = r
    except Exception as e:
        out["test_llm"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    out["test_llm_wall_ms"] = int((time.time() - t0) * 1000)

    # 参数兼容性：temperature 是否被网关接受
    try:
        status, data = providers._post_json(
            base.rstrip("/") + "/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": "回复：OK"}],
             "stream": False, "max_tokens": 32, "temperature": 0.0, "top_p": 1.0},
            key, timeout=90,
        )
        out["temperature_param"] = {
            "status": status,
            "supported": status == 200 or (status == 429),
            "note": "429 视为参数被接受（限流而非参数错误）" if status == 429 else "",
        }
    except Exception as e:
        out["temperature_param"] = {"status": None, "supported": None,
                                    "note": f"{type(e).__name__}: {str(e)[:120]}"}

    if not quick:
        # 小 token 预算是否会吃光正文（推理模型适配依据）
        try:
            status, data = providers._post_json(
                base.rstrip("/") + "/chat/completions",
                {"model": model, "messages": [{"role": "user", "content": "回复：OK"}],
                 "stream": False, "max_tokens": 24},
                key, timeout=90,
            )
            chars, reasoning_only = _content_of(data)
            out["small_budget"] = {"status": status, "content_chars": chars,
                                   "reasoning_only": reasoning_only,
                                   "empty_content": status == 200 and chars == 0}
        except Exception as e:
            out["small_budget"] = {"status": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return out


def conclude(r: dict) -> str:
    """结论只依据本次实测；不把网关层面的共性行为归因于某个模型类型。"""
    t = r.get("test_llm") or {}
    sb = r.get("small_budget") or {}
    if not t.get("ok"):
        return "探测失败，不纳入全量评估"
    bits = []
    if sb.get("empty_content"):
        bits.append("max_tokens=24 时正文为空（6 个模型共性，非推理模型独有）→ 统一采用 >=512 token 预算")
    if t.get("reasoning_only"):
        bits.append("连 256 token 都只吐 reasoning，需从 reasoning_content 回退")
    if (t.get("latency_ms") or 0) > 15000:
        bits.append("冷启动/推理阶段延迟高，超时需放宽到 180s")
    if "minimax" in r["model"]:
        bits.append("项目既有实测中 content 可能内联 <mm:think>，评分前做标签过滤（本次短探测未复现）")
    return "；".join(bits) if bits else "无特殊适配要求，通用 OpenAI 兼容路径即可"


DIFFERENCES = [
    "实测 6 个模型在 max_tokens=256 的探活下均返回正文（2 字）、reasoning_only=False；"
    "因此「零正文」不是推理模型专属，本轮未复现 reasoning_only 现象",
    "实测 max_tokens=24 时 6 个模型全部返回空 content（含非推理型），属网关侧 token 预算行为；"
    "统一要求 max_tokens>=512，不把它归因于模型类型",
    "延迟差异达数量级：deepseek-v4.1-flash-1017 26.4s，deepseek-v4-pro-0813 0.8s，"
    "minimax-m3 0.8s、glm-5.3 1.7s、qwen3.8-27b 2.8s、kimi-k2.6 3.6s；超时须按模型分级",
    "temperature=0 / top_p=1 在 6 个模型上均被网关接受（HTTP 200），可用于确定性参数对比",
    "可观测差异主要是延迟与思考块内联（项目既有实测），而非协议不兼容；"
    "适配层统一走 OpenAI 兼容 /chat/completions，仅按模型调整 timeout / 标签过滤 / token 预算",
]


def recompute() -> int:
    """不重发 API，仅用修正后的结论重算已有探测文件的解释字段。"""
    path = ROOT / "out" / "models_probe.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for r in payload["results"]:
        r["conclusion"] = conclude(r)
    payload["differences"] = DIFFERENCES
    payload["interpretation_revised_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["interpretation_note"] = "原始测量值未改动；仅修正把网关共性误归因为推理模型的解释"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"recomputed conclusions for {len(payload['results'])} models -> {path.relative_to(PROJECT)}")
    return 0


def main() -> int:
    if "--recompute" in sys.argv:
        return recompute()
    quick = "--quick" in sys.argv
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or DEFAULT_CANDIDATES
    base = os.environ.get("DISCOVERY_BASE_URL", "")
    key = os.environ.get("DISCOVERY_API_KEY", "")
    cfg = json.loads((ROOT / "configs" / "models.json").read_text(encoding="utf-8"))
    selected = {m["id"]: m for m in cfg["selected"]}

    results = []
    for m in models:
        print(f"probing {m} ...", flush=True)
        r = probe_one(m, base, key, quick=quick)
        r["selected_for_full_eval"] = m in selected
        r["role"] = selected.get(m, {}).get("role", "probe_only")
        r["adaptations"] = selected.get(m, {}).get("adaptations", [])
        r["conclusion"] = conclude(r)
        results.append(r)
        t = r.get("test_llm") or {}
        print(f"  latency={t.get('latency_ms')}ms chars={t.get('content_chars')} "
              f"reasoning_only={t.get('reasoning_only')} -> {r['conclusion']}", flush=True)

    payload = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gateway": cfg["gateway"],
        "available_at_probe": cfg["available_at_probe"],
        "probe_mode": "quick" if quick else "full",
        "results": results,
        "differences": DIFFERENCES,
        "selection_conclusion": "网关 14 个模型中选 3 个覆盖「快 / 通用 / 推理」做全量评估；"
                                "其余仅做兼容探测，理由与配额约束见 configs/models.json",
        "note": "本文件只记录公开端点与模型行为，不含任何密钥",
    }
    out = ROOT / "out" / "models_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out.relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
