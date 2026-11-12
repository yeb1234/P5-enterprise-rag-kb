"""评估执行器：上下文组装、真实 API 调用、缓存复用、限流降级、结果落盘。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from webapp.envfile import load_env  # noqa: E402

load_env()
from webapp import providers  # noqa: E402
from evalbench.core import gitmeta, scoring  # noqa: E402

CACHE_PATH = ROOT / "cache" / "responses.jsonl"


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def load_samples() -> list[dict]:
    path = ROOT / "datasets" / "samples_v1.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_prompt(version: str) -> tuple[str, str]:
    path = ROOT / "prompts" / f"{version}.md"
    text = path.read_text(encoding="utf-8")
    return text, _sha(text)


def load_params(pid: str) -> dict:
    return json.loads((ROOT / "configs" / f"params_{pid}.json").read_text(encoding="utf-8"))


def load_models() -> dict:
    return json.loads((ROOT / "configs" / "models.json").read_text(encoding="utf-8"))


def build_context(sample: dict, params: dict) -> list[dict]:
    """按 RGB 的 passage_num / noise_rate 组装上下文；负面拒绝类强制 noise_rate=1。"""
    pos = [p for p in sample["passages"] if p["kind"] == "positive"]
    noisy = [p for p in sample["passages"] if p["kind"] == "noisy"]
    n = int(params["passage_num"])
    if sample["task_category"] == "negative_rejection":
        return noisy[:n]
    n_noisy = min(len(noisy), int(round(n * float(params["noise_rate"]))))
    n_pos = min(len(pos), n - n_noisy)
    chosen = pos[:n_pos] + noisy[:n_noisy]
    for p in pos[n_pos:] + noisy[n_noisy:]:
        if len(chosen) >= n:
            break
        chosen.append(p)
    return chosen


def render_prompt(template: str, sample: dict, context: list[dict]) -> str:
    passages = "\n".join(f"[{p['doc_id']}] {p['text']}" for p in context)
    return template.replace("{passages}", passages).replace("{question}", sample["question"])


def _parse_answer(data) -> tuple[str, bool, bool]:
    choices = data.get("choices") or [] if isinstance(data, dict) else []
    msg = (choices[0].get("message") or {}) if choices else {}
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    if content:
        # 复用项目里的流式思考标签过滤逻辑（这里按单块喂入）
        content = "".join(providers._filter_inline_think(iter([content]))).strip()
    reasoning_only = bool(not content and reasoning)
    return (content or reasoning), reasoning_only, bool(choices)


def call_live(model_cfg: dict, prompt: str, params: dict) -> dict:
    base = os.environ.get("DISCOVERY_BASE_URL", "").rstrip("/")
    key = os.environ.get("DISCOVERY_API_KEY", "")
    adaptations = model_cfg.get("adaptations", [])
    max_tokens = int(params["max_tokens"])
    if "require_min_max_tokens_512" in adaptations:
        max_tokens = max(max_tokens, 512)
    timeout = int(model_cfg.get("timeout_s", 90))
    payload = {
        "model": model_cfg["id"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": float(params["temperature"]),
        "top_p": float(params["top_p"]),
    }
    url = base + "/chat/completions"
    last_error = ""
    for attempt in range(4):
        t0 = time.time()
        try:
            status, data = providers._post_json(url, payload, key, timeout=timeout)
        except Exception as e:  # 网络/超时
            if attempt < 3:
                time.sleep(providers.backoff_seconds(attempt))
                last_error = f"{type(e).__name__}: {str(e)[:120]}"
                continue
            return {"status": "transport_error", "answer": "", "latency_ms": int((time.time() - t0) * 1000),
                    "degraded": "transport_error", "detail": last_error or str(e)[:120]}
        ms = int((time.time() - t0) * 1000)
        if status == 200:
            answer, reasoning_only, has_choice = _parse_answer(data)
            return {
                "status": "ok", "answer": answer, "latency_ms": ms,
                "degraded": "empty_model_output" if not answer else None,
                "reasoning_only": reasoning_only,
                "detail": None if has_choice else "response_without_choices",
            }
        raw = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        if status == 429:
            kind = providers.classify_429(raw)
            if kind == "quota":
                return {"status": "quota_exceeded", "answer": "", "latency_ms": ms,
                        "degraded": "quota_exceeded", "detail": raw[:160]}
            if attempt < 3:
                time.sleep(providers.backoff_seconds(attempt))
                continue
            return {"status": "rate_limited", "answer": "", "latency_ms": ms,
                    "degraded": "rate_limit_exceeded", "detail": raw[:160]}
        if status >= 500 and attempt < 3:
            time.sleep(providers.backoff_seconds(attempt))
            last_error = f"HTTP {status}: {raw[:120]}"
            continue
        return {"status": "http_error", "answer": "", "latency_ms": ms,
                "degraded": f"http_{status}", "detail": raw[:160]}
    return {"status": "rate_limited", "answer": "", "latency_ms": 0,
            "degraded": "retry_exhausted", "detail": last_error}


def call_offline(sample: dict, context: list[dict]) -> dict:
    """确定性占位：只用于验证评估链路，comparable=false，绝不写入质量结论。"""
    if sample["expected_behavior"] == "abstain":
        answer = "资料不足，无法回答。"
    else:
        answer = "；".join(p["text"] for p in context)
    return {"status": "ok", "answer": answer, "latency_ms": 0,
            "degraded": "offline_placeholder", "reasoning_only": False, "detail": None}


def load_cache() -> dict:
    out = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                out[rec["key"]] = rec["value"]
            except Exception:
                continue
    return out


def append_cache(key: str, value: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")


def run_stage(*, label: str, models: list[str], prompts: list[str], params: list[str],
              sample_ids: list[str] | None = None, offline: bool = False,
              repeats: int = 1, verbose: bool = True) -> tuple[str, dict]:
    samples = load_samples()
    if sample_ids:
        samples = [s for s in samples if s["id"] in sample_ids]
    model_index = {m["id"]: m for m in load_models()["selected"]}
    prompt_assets = {v: load_prompt(v) for v in prompts}
    param_assets = {p: load_params(p) for p in params}
    cache = {} if offline else load_cache()

    git = gitmeta.snapshot()
    snapshot = {
        "run_id": None,
        "label": label,
        "mode": "offline" if offline else "live",
        "comparable": not offline,
        "git": git,
        "dataset": {"file": "samples_v1.jsonl", "hash": _sha([s["id"] for s in samples]),
                    "n_samples": len(samples), "origin": "self_built_v1 (见 datasets/SOURCES.md)"},
        "metrics_version": scoring.load_metrics()["version"],
        "models": {m: model_index[m] for m in models},
        "prompts": {v: {"sha256_16": prompt_assets[v][1]} for v in prompts},
        "params": {p: {"sha256_16": _sha(param_assets[p]), **param_assets[p]} for p in params},
        "repeats": repeats,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    run_id = "RUN-" + _sha(snapshot | {"ts": snapshot["created_at"], "nonce": time.time_ns()})[:12]
    snapshot["run_id"] = run_id

    records: list[dict] = []
    combos = list(product(models, prompts, params, samples, range(repeats)))
    for i, (model, pv, pid, sample, rep) in enumerate(combos, 1):
        tpl, phash = prompt_assets[pv]
        pcfg = param_assets[pid]
        context = build_context(sample, pcfg)
        prompt = render_prompt(tpl, sample, context)
        key = _sha([model, phash, _sha(pcfg), sample["id"], _sha(context), rep, "live" if not offline else "offline"])

        if key in cache:
            call = dict(cache[key])
            call["from_cache"] = True
        else:
            call = (call_offline(sample, context) if offline
                    else call_live(model_index[model], prompt, pcfg))
            call["from_cache"] = False
            if call["status"] == "ok" and not offline:
                append_cache(key, {k: v for k, v in call.items() if k != "from_cache"})

        record = {
            "run_id": run_id, "model": model, "prompt_version": pv, "params_id": pid,
            "sample_id": sample["id"], "task_category": sample["task_category"],
            "repeat": rep, "cache_key": key,
            "status": call["status"], "degraded": call.get("degraded"),
            "reasoning_only": call.get("reasoning_only", False),
            "from_cache": call.get("from_cache", False),
            "detail": call.get("detail"),
            "answer": call.get("answer", ""),
            "scores": scoring.score_sample(sample, call.get("answer", ""),
                                           {"latency_ms": call.get("latency_ms", 0)}),
        }
        records.append(record)
        if verbose:
            tag = "CACHE" if record["from_cache"] else ("OK" if record["status"] == "ok" else record["status"].upper())
            print(f"  [{i:>3}/{len(combos)}] {model:<28} {pv:<16} {pid} {sample['id']} r{rep} -> {tag}",
                  flush=True)

    summary = scoring.aggregate(records)
    summary.update({
        "run_id": run_id, "label": label, "mode": snapshot["mode"],
        "comparable": snapshot["comparable"], "git": git,
        "n_records": len(records),
        "status_counts": {s: sum(1 for r in records if r["status"] == s)
                          for s in sorted({r["status"] for r in records})},
    })

    out_dir = ROOT / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_id, summary
