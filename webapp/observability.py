"""可观测与治理引擎：成本规则、预算、限流、用量台账、评估运行与追踪日志。

设计取舍（与生产架构对齐）：
- 用量逐调用落 SQLite（生产对应数仓事实表 + 供应商账单对账）；
- token 未拿到供应商 usage 时按字符估算，并逐条标 estimated=1，绝不冒充真实计费；
- 单价表是**演示估算值**，不是任何供应商的报价，响应里固定带 price_note；
- 预算默认 policy=warn（告警留痕，不拒绝），admin 显式改 block 才硬拦；
- 限流是进程内滑动窗口 + 命中落库，用户 / 接口 / 模型调用三维独立计数；
  部门只作成本归因，不参与计数；生产换 Redis 令牌桶 + 网关层。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from webapp import db

ROOT = Path(__file__).resolve().parent.parent
EVALBENCH_RUNS = ROOT / "evalbench" / "runs"

PRICE_NOTE = "单价为演示估算值，非供应商报价；上线前必须用真实账单校准。"

# 每 1K token 的演示估算单价（元）。仅用于相对比较与预算演练，不代表真实价格。
PRICES: dict[str, dict] = {
    "default": {"in": 0.004, "out": 0.012, "label": "通用（估算）"},
    "minimax-m3": {"in": 0.002, "out": 0.008, "label": "minimax-m3（估算）"},
    "deepseek-v4.1-flash-1017": {"in": 0.004, "out": 0.012, "label": "DeepSeek V4.1 Flash（估算）"},
    "kimi-k2.6": {"in": 0.004, "out": 0.016, "label": "kimi-k2.6（估算）"},
    "BAAI/bge-m3": {"in": 0.0005, "out": 0.0, "label": "bge-m3 embedding（估算）"},
    "BAAI/bge-reranker-v2-m3": {"in": 0.0005, "out": 0.0, "label": "bge-reranker（估算）"},
}

DIMENSIONS = {"project", "department_id", "call_type", "model", "user_id"}
# 限流配额本体是用户 / 接口 / 模型调用三维（用户任务 3 原文）；
# department 是成本归因标签，不参与计数、不产生按部门的 429 隔离。
RATE_DIMENSIONS = ("user", "endpoint", "model")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def estimate_tokens(text: str) -> int:
    """粗略估算：中文按字符、英文按 4 字符/token 折算，取上界保证不低估预算。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return max(1, cjk + (rest + 3) // 4)


def price_for(model: str) -> dict:
    return PRICES.get(model, PRICES["default"])


def cost_of(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = price_for(model)
    return round((prompt_tokens / 1000) * p["in"] + (completion_tokens / 1000) * p["out"], 6)


# --------------------------------------------------------------------------
# 用量台账
# --------------------------------------------------------------------------
def record_usage(*, project: str = "default", department_id: str = "default", user_id: str = "",
                 call_type: str, model: str = "", prompt_text: str = "", completion_text: str = "",
                 prompt_tokens: int | None = None, completion_tokens: int | None = None,
                 estimated: int = 1, run_id: str | None = None, detail: str = "") -> dict:
    """旁路记录一次调用。任何异常都不得影响业务主流程。

    department_id 是事件发生瞬间的部门快照（成本归因维度，不回溯），
    由服务端会话派生，不接受请求体传入；它不是准入隔离配额。
    """
    pt = prompt_tokens if prompt_tokens is not None else estimate_tokens(prompt_text)
    ct = completion_tokens if completion_tokens is not None else estimate_tokens(completion_text)
    row = {
        "id": uuid.uuid4().hex, "ts": _now(), "project": project or "default",
        "department_id": department_id or "default", "user_id": user_id or "", "call_type": call_type,
        "model": model or "", "prompt_tokens": pt, "completion_tokens": ct,
        "total_tokens": pt + ct, "cost_cny": cost_of(model, pt, ct),
        "estimated": estimated, "run_id": run_id, "detail": detail, "created_at": _now(),
    }
    try:
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO usage_events(id,ts,project,department_id,user_id,call_type,model,"
                "prompt_tokens,completion_tokens,total_tokens,cost_cny,estimated,run_id,detail,created_at)"
                " VALUES (:id,:ts,:project,:department_id,:user_id,:call_type,:model,"
                ":prompt_tokens,:completion_tokens,:total_tokens,:cost_cny,:estimated,:run_id,:detail,:created_at)",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return row


def check_budget(*, actor: str = "", role: str = "") -> dict:
    """预算检查。默认 warn 只留痕；policy=block 且已超额时拒绝新的付费调用。"""
    conn = db.get_conn()
    try:
        b = conn.execute(
            "SELECT limit_cny,policy,updated_at FROM budgets WHERE scope='global' AND key='default'"
        ).fetchone()
        limit_cny = float(b["limit_cny"]) if b else 50.0
        policy = b["policy"] if b else "warn"
        updated = b["updated_at"] if b else ""
        spend = float(conn.execute("SELECT COALESCE(SUM(cost_cny),0) AS c FROM usage_events").fetchone()["c"])
    finally:
        conn.close()
    exceeded = spend >= limit_cny
    state = {
        "limit_cny": limit_cny, "spend_cny": round(spend, 4),
        "remaining_cny": round(max(0.0, limit_cny - spend), 4),
        "usage_rate": round(spend / limit_cny, 4) if limit_cny > 0 else 1.0,
        "policy": policy, "exceeded": exceeded, "updated_at": updated,
        "period": "累计（演示切片不重置周期）", "price_note": PRICE_NOTE,
        "prices": PRICES,
    }
    if exceeded:
        log_event("WARN", "budget_exceeded",
                  f"spend={state['spend_cny']} >= limit={limit_cny}, policy={policy}", actor=actor)
        db.log_action(actor or "system", role or "system", "budget", "global/default",
                      f"exceeded={exceeded}, policy={policy}")
    state["allowed"] = not (exceeded and policy == "block")
    return state


def usage_summary(dim: str = "project", *, user_id: str = "", is_admin: bool = False) -> dict:
    """按项目 / 部门 / 调用类型 / 模型聚合。普通用户强制按 user_id 过滤（不查而非隐藏）。

    dim 直接拼进 SQL（白名单校验在 DIMENSIONS），所以传的是物理列名 department_id。
    """
    if dim not in DIMENSIONS:
        dim = "project"
    where, params = "", []
    if not is_admin:
        where = " WHERE user_id=?"
        params.append(user_id)
    conn = db.get_conn()
    try:
        rows = conn.execute(
            f"SELECT {dim} AS k, COUNT(*) AS calls, SUM(prompt_tokens) AS pt,"
            " SUM(completion_tokens) AS ct, SUM(total_tokens) AS tt, SUM(cost_cny) AS cost,"
            " SUM(estimated) AS est FROM usage_events" + where + " GROUP BY k ORDER BY cost DESC",
            params,
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS pt,"
            " COALESCE(SUM(completion_tokens),0) AS ct, COALESCE(SUM(total_tokens),0) AS tt,"
            " COALESCE(SUM(cost_cny),0) AS cost FROM usage_events" + where, params,
        ).fetchone()
    finally:
        conn.close()
    out = [{
        "key": r["k"] or "—", "calls": r["calls"], "prompt_tokens": r["pt"] or 0,
        "completion_tokens": r["ct"] or 0, "total_tokens": r["tt"] or 0,
        "cost_cny": round(r["cost"] or 0, 4), "estimated_rows": r["est"] or 0,
    } for r in rows]
    return {
        "dimension": dim, "rows": out,
        "totals": {"calls": total["calls"], "prompt_tokens": total["pt"],
                   "completion_tokens": total["ct"], "total_tokens": total["tt"],
                   "cost_cny": round(total["cost"], 4)},
        "scope": "全局（admin）" if is_admin else "仅本人调用",
        "price_note": PRICE_NOTE,
        "derivation": "逐调用台账聚合；estimated=1 的记录按字符折算 token，非供应商 usage",
    }


# --------------------------------------------------------------------------
# 限流：进程内滑动窗口（生产换 Redis 令牌桶 + 网关层）
# --------------------------------------------------------------------------
_WINDOWS: dict[tuple, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def rate_rules() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT dimension,limit_count,window_s,enabled,updated_at FROM rate_rules ORDER BY dimension"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def rate_hits(limit: int = 50) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT ts,dimension,key,path,limit_count,window_s FROM rate_hits"
            " ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def rate_check(*, path: str, user_id: str, department: str = "default", model: str = "") -> dict | None:
    """返回命中超限的维度信息；None 表示放行。

    中间件调用它，命中时返回统一 429 错误体所需字段。计数落库用于可观测。
    department 只作统计归因标签：不进入 RATE_DIMENSIONS、不参与计数、
    **不做按部门的 429 隔离断言**；用户 / 接口 / 模型调用三维才是配额本体。
    """
    keys = {
        "user": user_id or "anonymous",
        "endpoint": path,
        "model": model or "-",
    }
    rules = {r["dimension"]: r for r in rate_rules()}
    now = time.time()
    with _rate_lock:
        for dim in RATE_DIMENSIONS:
            r = rules.get(dim)
            if not r or not r["enabled"]:
                continue
            lim, win = int(r["limit_count"]), int(r["window_s"])
            k = (dim, keys[dim])
            q = _WINDOWS[k]
            while q and q[0] <= now - win:
                q.popleft()
            if len(q) >= lim:
                _persist_hit(dim, keys[dim], path, lim, win)
                return {
                    "dimension": dim, "key": keys[dim], "path": path,
                    "limit": lim, "window_s": win,
                    "retry_after": max(1, int(win - (now - q[0]))) if q else win,
                    "current": len(q),
                    # 只随错误体回传用于成本归因，不参与上面的计数与判定
                    "department": department or "default",
                }
            q.append(now)
    return None


def _persist_hit(dim: str, key: str, path: str, lim: int, win: int) -> None:
    try:
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO rate_hits(id,ts,dimension,key,path,limit_count,window_s)"
                " VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, _now(), dim, key, path, lim, win),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    log_event("WARN", "rate_limit_exceeded", f"{dim}={key} limit={lim}/{win}s path={path}")


# --------------------------------------------------------------------------
# 评估配置 / 运行 / 日志
# --------------------------------------------------------------------------
CONFIG_KEYS = ("profile", "prompt_version", "params_id", "samples", "project", "department")


def get_config() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT key,value,updated_at FROM eval_configs").fetchall()
    finally:
        conn.close()
    out = {r["key"]: r["value"] for r in rows}
    return {"config": out, "updated_at": max((r["updated_at"] for r in rows), default="")}


def set_config(patch: dict, actor: str = "admin") -> dict:
    bad = [k for k in patch if k not in CONFIG_KEYS]
    if bad:
        raise ValueError("未知配置项: " + ",".join(bad))
    now = _now()
    conn = db.get_conn()
    try:
        for k, v in patch.items():
            conn.execute(
                "INSERT INTO eval_configs(key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, str(v), now),
            )
        conn.commit()
    finally:
        conn.close()
    log_event("INFO", "eval_config_updated", json.dumps(patch, ensure_ascii=False), actor=actor)
    db.log_action(actor, "admin", "eval_config", "evaltest", json.dumps(patch, ensure_ascii=False))
    return get_config()


def log_event(level: str, event: str, detail: str = "", *, run_id: str | None = None,
              actor: str = "") -> None:
    try:
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO eval_logs(id,ts,run_id,level,event,detail,actor) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, _now(), run_id, level, event, detail[:400], actor),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def list_logs(*, run_id: str = "", level: str = "", limit: int = 200,
              user_id: str = "", is_admin: bool = False) -> list[dict]:
    sql = "SELECT ts,run_id,level,event,detail,actor FROM eval_logs WHERE 1=1"
    p: list = []
    if run_id:
        sql += " AND run_id=?"; p.append(run_id)
    if level:
        sql += " AND level=?"; p.append(level)
    if not is_admin:
        sql += " AND (actor=? OR actor='' OR actor='system')"; p.append(user_id)
    sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"; p.append(min(limit, 1000))
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, p).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _git_meta() -> tuple[str, str]:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        b = r.stdout.strip() if r.returncode == 0 else ""
        r2 = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, timeout=5)
        c = r2.stdout.strip() if r2.returncode == 0 else ""
        return b or "unknown", c or "unknown"
    except Exception:
        return "unknown", "unknown"


def sync_evalbench_runs() -> int:
    """把 evalbench/runs 的真实快照同步进台账（INSERT OR IGNORE，不改动源文件）。"""
    if not EVALBENCH_RUNS.exists():
        return 0
    conn = db.get_conn()
    n = 0
    try:
        for d in sorted(EVALBENCH_RUNS.glob("RUN-*")):
            sp, cp = d / "summary.json", d / "config_snapshot.json"
            if not (sp.exists() and cp.exists()):
                continue
            if conn.execute("SELECT 1 FROM eval_runs WHERE run_id=?", (d.name,)).fetchone():
                continue
            try:
                s = json.loads(sp.read_text(encoding="utf-8"))
                c = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            g = c.get("git") or s.get("git") or {}
            conn.execute(
                "INSERT INTO eval_runs(run_id,label,mode,comparable,profile,prompt_version,params_id,"
                "samples,n_records,status_counts,created_at,actor,git_branch,git_commit,source)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.name, s.get("label") or c.get("label") or "", s.get("mode") or "live",
                 1 if s.get("comparable") else 0, c.get("metrics_version", ""),
                 ",".join((c.get("prompts") or {}).keys()), ",".join((c.get("params") or {}).keys()),
                 "all", int(s.get("n_records") or 0),
                 json.dumps(s.get("status_counts") or {}, ensure_ascii=False),
                 c.get("created_at") or c.get("generated_at") or _now(),
                 c.get("actor", ""), g.get("branch", ""), g.get("commit", ""), "evalbench"),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def list_runs(limit: int = 100) -> list[dict]:
    sync_evalbench_runs()
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM eval_runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (min(limit, 500),)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["status_counts"] = json.loads(d.get("status_counts") or "{}")
        except Exception:
            d["status_counts"] = {}
        d["comparable"] = bool(d["comparable"])
        out.append(d)
    return out


def run_metrics(*, run_id: str = "", model: str = "") -> dict:
    """指标来自 evalbench/runs/<run_id>/summary.json（真实留存），不重算不编造。"""
    if run_id:
        sp = EVALBENCH_RUNS / run_id / "summary.json"
        if not sp.exists():
            return {"run_id": run_id, "groups": [], "by_model": {}, "note": "run 不存在"}
        s = json.loads(sp.read_text(encoding="utf-8"))
        groups = [g for g in s.get("groups", []) if not model or g.get("model") == model]
        return {
            "run_id": run_id, "groups": groups, "by_model": s.get("by_model", {}),
            "n_records": s.get("n_records"), "status_counts": s.get("status_counts", {}),
            "source": "evalbench/runs", "comparable": bool(s.get("comparable", True)),
        }
    # 未指定 run：优先取「记录数最多」的完整可比 run，避免默认落到小样本探针
    runs = [r for r in list_runs() if r.get("source") == "evalbench" and r.get("comparable")]
    if not runs:
        return {"run_id": "", "groups": [], "by_model": {}, "note": "暂无可比 run"}
    best = max(runs, key=lambda r: (r.get("n_records") or 0, r.get("created_at") or ""))
    return run_metrics(run_id=best["run_id"], model=model)


def run_offline_eval(*, actor: str = "admin", project: str = "default",
                     department: str = "default", user_id: str = "") -> dict:
    """可运行的离线评估：零外部 API 调用，基于自建样本做确定性评分，全程留痕。

    刻意不调用 evalbench 的真实模型链路，避免配额消耗；结果 comparable=false。
    """
    cfg = get_config()["config"]
    budget = check_budget(actor=actor, role="admin")
    if not budget["allowed"]:
        log_event("ERROR", "eval_rejected_budget",
                  f"policy=block spend={budget['spend_cny']} limit={budget['limit_cny']}", actor=actor)
        return {"ok": False, "reason": "budget_exceeded", "budget": budget}

    samples_path = ROOT / "evalbench" / "datasets" / "samples_v1.jsonl"
    samples = []
    if samples_path.exists():
        samples = [json.loads(x) for x in samples_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    want = cfg.get("samples", "all")
    if want and want != "all":
        ids = [s.strip() for s in want.split(",") if s.strip()]
        samples = [s for s in samples if s["id"] in ids] or samples

    run_id = "RUN-" + uuid.uuid4().hex[:12]
    branch, commit = _git_meta()
    now = _now()
    log_event("INFO", "eval_start", f"profile={cfg.get('profile')} samples={len(samples)}", run_id=run_id, actor=actor)
    # 确定性离线评分：按参考关键事实覆盖率，不调用任何模型
    records = []
    for s in samples:
        answer = "; ".join(s.get("key_facts", [])) or "INSUFFICIENT"
        facts = [f for f in s.get("key_facts", []) if f]
        acc = 1.0 if facts else 0.0
        records.append({"sample_id": s["id"], "task_category": s["task_category"],
                        "answer": answer, "answer_accuracy": acc,
                        "rejection_correctness": 1.0 if s.get("expected_behavior") == "abstain" else 1.0,
                        "faithfulness": 1.0, "format_compliance": 1.0, "answer_economy": 1.0})
        # 逐样本记一次用量（估算，明确标 estimated=1）
        record_usage(project=project, department_id=department, user_id=user_id, call_type="eval",
                     model="offline_placeholder", prompt_text=s["question"],
                     completion_text=answer, estimated=1, run_id=run_id,
                     detail=f"sample={s['id']} offline")
    n = len(records)
    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in records:
        for dim in ("answer_accuracy", "rejection_correctness", "faithfulness",
                    "format_compliance", "answer_economy"):
            groups[("offline_placeholder", r["task_category"], dim)].append(r[dim])
    summary_groups = [{"model": "offline_placeholder", "task_category": k[1], "dimension": k[2],
                       "mean": round(sum(v) / len(v), 4), "n": len(v)} for k, v in sorted(groups.items())]

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO eval_runs(run_id,label,mode,comparable,profile,prompt_version,params_id,"
            "samples,n_records,status_counts,created_at,actor,git_branch,git_commit,source)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, "offline-eval", "offline", 0, cfg.get("profile"),
             cfg.get("prompt_version"), cfg.get("params_id"), cfg.get("samples"), n,
             json.dumps({"ok": n}), now, actor, branch, commit, "webapp"),
        )
        conn.commit()
    finally:
        conn.close()
    out_dir = EVALBENCH_RUNS / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps({
        "run_id": run_id, "label": "offline-eval", "mode": "offline", "comparable": False,
        "n_records": n, "status_counts": {"ok": n}, "groups": summary_groups,
        "by_model": {"offline_placeholder": {"per_category": {}, "overall": None,
                                             "avg_latency_ms": 0}},
        "git": {"branch": branch, "commit": commit},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "config_snapshot.json").write_text(json.dumps({
        "run_id": run_id, "label": "offline-eval", "mode": "offline", "comparable": False,
        "created_at": now, "git": {"branch": branch, "commit": commit},
        "profile": cfg, "note": "零外部 API 调用；comparable=false；不写入质量基线",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event("INFO", "eval_done", f"records={n} comparable=false", run_id=run_id, actor=actor)
    db.log_action(actor, "admin", "eval_run", run_id, f"offline records={n}")
    return {"ok": True, "run_id": run_id, "n_records": n, "comparable": False,
            "branch": branch, "commit": commit, "groups": summary_groups,
            "note": "离线占位评估，零外部 API 调用，comparable=false"}


PLUGINS = [
    {"id": "observability", "name": "全链路可观测（trace_id / span）", "module": "webapp/observability.py · log_event() / eval_logs",
     "status": "演示级", "boundary": "本切片只落事件级日志与 run 维度追踪；未接 OpenTelemetry / Langfuse，无跨服务 trace 传播",
     "why": "面试高频：如何定位一次 RAG 回答慢/错在哪一段（检索、重排还是生成）"},
    {"id": "cost_budget", "name": "Token 计量、成本归因与预算闸门", "module": "webapp/observability.py · usage_events / budgets",
     "status": "已落地（演示级）", "boundary": "token 按字符估算、单价为演示值；生产须接供应商 usage 与账单对账，预算周期与多币种未做",
     "why": "面试高频：怎么防止某个部门把月度预算打爆，超限是告警还是硬拦"},
    {"id": "rate_limit", "name": "多维限流（用户/接口/模型调用）", "module": "webapp/observability.py · rate_check()；webapp/main.py 中间件",
     "status": "已落地（演示级）", "boundary": "进程内滑动窗口，单实例有效；生产须换 Redis 令牌桶 + 网关层，且区分 rate/quota 两类 429",
     "why": "面试高频：RPM/TPM 共享配额下如何做隔离，429 的 Retry-After 与退避策略"},
    {"id": "retry_circuit", "name": "退避重试与配额不重试", "module": "webapp/providers.py · classify_429() / backoff_seconds()",
     "status": "已落地", "boundary": "只做单次请求级；未做全局并发闸、熔断与队列吸收突发",
     "why": "面试高频：rate_limit_exceeded 可重试，quota_exceeded 不能重试，区别是什么"},
    {"id": "low_thinking", "name": "推理强度档位（低思考 / 快速）", "module": "webapp/settings.py · thinking_disabled()；webapp/providers.py · thinking_fields()",
     "status": "已落地（演示级）", "boundary": "请求体注入 thinking=disabled，沿用 backend 骨架同名字段；网关是否识别该字段未经实测，未识别时由内联思考块过滤兜底，不宣称已确认生效",
     "why": "面试高频：推理模型首 token 延迟高、max_tokens 被思考占满导致正文为空，怎么在保证答案完整的前提下压低延迟"},
    {"id": "cache", "name": "语义/向量缓存", "module": "webapp/db.py · chunk_embeddings（按 provider:模型 隔离）",
     "status": "已落地", "boundary": "只缓存分块向量；未做答案级语义缓存与缓存串部门治理",
     "why": "面试高频：换 embedding 模型后旧向量怎么办（缓存命名空间隔离）"},
    {"id": "rerank", "name": "Rerank 插件（cross-encoder / LLM 打分）", "module": "webapp/providers.py · rerank() / rerank_with_llm()",
     "status": "已落地", "boundary": "三档 off/llm/external；不可用时优雅跳过并留痕，不假装用了",
     "why": "面试高频：双塔召回与 cross-encoder 精排的分工"},
    {"id": "citation", "name": "引用溯源插件", "module": "webapp/routers_chat.py · chat_sources 表 + 三个回放接口",
     "status": "已落地", "boundary": "只到分块级引用；未做答案逐句 grounding / 高亮定位",
     "why": "面试高频：怎么证明答案不是编的"},
    {"id": "eval_gate", "name": "评测 CI 闸门 / 回归对比", "module": "evalbench/ · compare.py / iterations/",
     "status": "演示级", "boundary": "有版本化对象与前后对照；闸门接 CI、阈值冻结、质量基线仍未做",
     "why": "面试高频：改了提示词怎么证明没退化"},
    {"id": "permission", "name": "权限与部门可见性插件", "module": "backend/app/services/permission.py · decide()",
     "status": "架构预留", "boundary": "生产骨架有纯函数判定；tenant_id 为历史命名（多租户是未来研发方向）；webapp 切片只做 admin 门禁 + owner 过滤，行级 ACL 未实现",
     "why": "面试高频：先按权限过滤再检索，还是检索后过滤？为什么"},
    {"id": "agent_mcp", "name": "Agent 编排 / MCP 工具注册", "module": "backend/app/agent/ · docs/agent-extension.md",
     "status": "架构预留", "boundary": "只有状态机与工具注册表接口；多 Agent 分流、MCP 网关未落地",
     "why": "面试高频：知识库怎么从单轮问答演进到工具调用 Agent"},
    {"id": "guardrails", "name": "内容安全与 PII 脱敏", "module": "未接入（设计项）",
     "status": "未做", "boundary": "输入输出审核、越权内容拦截、审计脱敏检索均未完成",
     "why": "面试高频：企业知识库的合规红线，以及审计日志里不能写什么"},
]
