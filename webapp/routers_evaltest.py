"""评估测试子页 API：配置、运行记录、指标、日志、成本/预算、限流、插件、权限矩阵。

权限口径（与既有「采集与查看权限分离」一致）：
- 所有 GET 对登录用户开放；用量/预算/日志按 scope 过滤（普通用户强制 user_id 条件，不查而非隐藏）；
- 所有 PUT / POST（配置、预算、限流规则、触发运行）仅 admin，接口层 403；
- 配置/预算/运行全部写审计台账与追踪日志。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from webapp import db, observability as obs
from webapp.deps import get_current_user, require_admin

ROOT = Path(__file__).resolve().parent.parent
router = APIRouter(prefix="/api/evaltest", tags=["evaltest"])


class ConfigPatch(BaseModel):
    patch: dict


class BudgetPatch(BaseModel):
    limit_cny: float
    policy: str = "warn"


class RatePatch(BaseModel):
    rules: list[dict]


def _scope(user: dict) -> tuple[bool, str]:
    return user["role"] == "admin", user["id"]


def _options() -> dict:
    p = ROOT / "evalbench"
    def names(pattern: str, suffix: str) -> list[str]:
        return sorted(x.stem for x in (p / pattern).glob("*." + suffix)) if (p / pattern).exists() else []
    return {
        "prompts": names("prompts", "md")[:12] or ["v1_baseline", "v2_grounded", "v3_structured"],
        "params": [x.stem.replace("params_", "") for x in
                   sorted((p / "configs").glob("params_*.json"))] if (p / "configs").exists() else ["A", "B", "C"],
        "samples": ["all", "S01,S02,S03", "S04,S05,S06", "S07,S08,S09", "S10,S11,S12"],
    }


@router.get("/config")
def get_config(user: dict = Depends(get_current_user)):
    is_admin, _ = _scope(user)
    c = obs.get_config()
    return {**c, "options": _options(), "is_admin": is_admin,
            "note": "仅 admin 可修改；离线档位 comparable=false，real_api 档位受契约门禁阻断"}


@router.put("/config")
def put_config(body: ConfigPatch, _: dict = Depends(require_admin)):
    try:
        return {**obs.set_config(body.patch), "options": _options(), "is_admin": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/run")
def start_run(user: dict = Depends(require_admin)):
    c = obs.get_config()["config"]
    return obs.run_offline_eval(actor=user["username"], project=c.get("project", "default"),
                                tenant=c.get("tenant", "default"), user_id=user["id"])


@router.get("/runs")
def get_runs(limit: int = Query(100, ge=1, le=500), _: dict = Depends(get_current_user)):
    return {"runs": obs.list_runs(limit), "source_note": "evalbench/runs 快照 + 本页离线运行，均只读真实留存"}


@router.get("/runs/{run_id}")
def get_run(run_id: str, _: dict = Depends(get_current_user)):
    base = obs.EVALBENCH_RUNS / run_id
    sp, rp, cp = base / "summary.json", base / "records.jsonl", base / "config_snapshot.json"
    if not sp.exists():
        raise HTTPException(status_code=404, detail="run 不存在")
    summary = json.loads(sp.read_text(encoding="utf-8"))
    cfg = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
    records = [json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines() if x.strip()] if rp.exists() else []
    return {"run_id": run_id, "summary": summary, "config_snapshot": cfg,
            "records_preview": records[:20], "n_records": len(records)}


@router.get("/metrics")
def get_metrics(run_id: str = "", model: str = "", _: dict = Depends(get_current_user)):
    return obs.run_metrics(run_id=run_id, model=model)


@router.get("/logs")
def get_logs(run_id: str = "", level: str = "", limit: int = Query(200, ge=1, le=1000),
             user: dict = Depends(get_current_user)):
    is_admin, uid = _scope(user)
    return {"logs": obs.list_logs(run_id=run_id, level=level, limit=limit,
                                  user_id=user["username"], is_admin=is_admin),
            "scope": "全局（admin）" if is_admin else "仅本人 / 系统事件"}


@router.get("/usage")
def get_usage(dim: str = Query("project"), user: dict = Depends(get_current_user)):
    is_admin, uid = _scope(user)
    return obs.usage_summary(dim, user_id=uid, is_admin=is_admin)


@router.get("/budget")
def get_budget(user: dict = Depends(get_current_user)):
    is_admin, _ = _scope(user)
    b = obs.check_budget(actor=user["username"], role=user["role"])
    if not is_admin:
        # 普通用户只看「是否受限」，不暴露全局配置细节
        b.pop("prices", None)
        b["scope"] = "只读；预算配置仅 admin 可改"
    else:
        b["scope"] = "全局（admin）"
    return b


@router.put("/budget")
def put_budget(body: BudgetPatch, user: dict = Depends(require_admin)):
    if body.limit_cny < 0:
        raise HTTPException(status_code=400, detail="预算上限不能为负")
    if body.policy not in ("warn", "block"):
        raise HTTPException(status_code=400, detail="policy 只能是 warn 或 block")
    now = obs._now()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO budgets(scope,key,limit_cny,policy,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(scope,key) DO UPDATE SET limit_cny=excluded.limit_cny,"
            " policy=excluded.policy, updated_at=excluded.updated_at",
            ("global", "default", body.limit_cny, body.policy, now),
        )
        conn.commit()
    finally:
        conn.close()
    obs.log_event("INFO", "budget_updated",
                  f"limit={body.limit_cny} policy={body.policy}", actor=user["username"])
    db.log_action(user["username"], "admin", "budget", "global/default",
                  f"limit={body.limit_cny}, policy={body.policy}")
    return obs.check_budget(actor=user["username"], role="admin")


@router.get("/ratelimit")
def get_ratelimit(_: dict = Depends(get_current_user)):
    return {
        "rules": obs.rate_rules(), "hits": obs.rate_hits(50),
        "feedback": {"status_code": 429, "headers": ["Retry-After", "X-RateLimit-Dimension",
                                                     "X-RateLimit-Limit", "X-RateLimit-Window"],
                     "body": {"detail": "请求过于频繁", "dimension": "user|tenant|endpoint|model",
                              "limit": 0, "window_s": 0, "retry_after": 0},
                     "policy": "rate_limit_exceeded 退避重试；quota_exceeded 不重试"},
        "boundary": "进程内滑动窗口，单实例有效；生产换网关层 Redis 令牌桶",
    }


@router.put("/ratelimit")
def put_ratelimit(body: RatePatch, user: dict = Depends(require_admin)):
    allowed = set(obs.RATE_DIMENSIONS)
    for r in body.rules:
        if r.get("dimension") not in allowed:
            raise HTTPException(status_code=400, detail="未知限流维度: " + str(r.get("dimension")))
        try:
            lim, win = int(r["limit_count"]), int(r["window_s"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="limit_count / window_s 必须是整数")
        if lim < 1 or win < 1:
            raise HTTPException(status_code=400, detail="限额与窗口必须为正整数")
    conn = db.get_conn()
    try:
        for r in body.rules:
            conn.execute(
                "INSERT INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT(dimension) DO UPDATE SET"
                " limit_count=excluded.limit_count, window_s=excluded.window_s,"
                " enabled=excluded.enabled, updated_at=excluded.updated_at",
                (r["dimension"], int(r["limit_count"]), int(r["window_s"]),
                 1 if r.get("enabled", 1) else 0, obs._now()),
            )
        conn.commit()
    finally:
        conn.close()
    obs.log_event("WARN", "rate_rules_updated", json.dumps(body.rules, ensure_ascii=False),
                  actor=user["username"])
    db.log_action(user["username"], "admin", "rate_rules", "evaltest",
                  json.dumps(body.rules, ensure_ascii=False))
    return get_ratelimit(user)


@router.post("/ratelimit/probe")
def probe_ratelimit(user: dict = Depends(require_admin)):
    """验收用（仅 admin）：在独立命名空间模拟一次超限，返回统一 429 结构，不改全局规则。"""
    hit = obs.rate_check(path="/api/evaltest/ratelimit/probe#probe",
                         user_id="__probe__" + user["id"], tenant="__probe__",
                         model="__probe__")
    fake = {"dimension": "user", "key": user["username"], "path": "/api/evaltest/ratelimit/probe",
            "limit": 3, "window_s": 60, "retry_after": 60, "current": 4}
    return {"hit": bool(hit), "example_429": {
        "status_code": 429, "headers": {"Retry-After": str(fake["retry_after"]),
                                        "X-RateLimit-Dimension": fake["dimension"],
                                        "X-RateLimit-Limit": str(fake["limit"]),
                                        "X-RateLimit-Window": str(fake["window_s"])},
        "body": {"detail": "请求过于频繁，请稍后重试", **fake},
    }}


@router.get("/plugins")
def get_plugins(_: dict = Depends(get_current_user)):
    return {"plugins": obs.PLUGINS, "total": len(obs.PLUGINS),
            "note": "「已落地」指本仓库有可运行代码；「演示级」指能力可跑但为替身；「架构预留 / 未做」不算交付"}


@router.get("/permissions")
def get_permissions(_: dict = Depends(get_current_user)):
    """权限矩阵：多部门 / 多租户 / 组织隔离 / 角色层级 / 资源归属 / 可见范围 / 越权边界。"""
    return {
        "layers": [
            {"layer": "组织隔离", "rule": "tenant_id 只由依赖注入产生，禁止来自请求体；所有查询经 TenantRepository 下推到 SQL",
             "demo": "单租户（default）；users/documents/chat_logs 带 owner_id / user_id / created_by",
             "production": "backend/app/db/repository.py · TenantRepository", "status": "架构预留"},
            {"layer": "部门", "rule": "department_id 挂在资源与用户上；跨部门默认不可见，显式授权才放行",
             "demo": "未建 department 表，仅用量台账有 project / tenant 维度", "production": "待建 org 树", "status": "未做"},
            {"layer": "角色层级", "rule": "owner > admin > editor > viewer；权限只继承不放宽，判定收敛到 decide() 纯函数",
             "demo": "仅 admin / user 两级；admin 三个页面 + 写接口 403", "production": "backend/app/services/permission.py", "status": "演示级"},
            {"layer": "资源归属", "rule": "文档/会话/badcase 归属 owner；删除与导出必须校验归属或 admin",
             "demo": "documents.owner_id、chat_logs.user_id、badcases.created_by 已落库并参与过滤", "production": "同上", "status": "已落地"},
            {"layer": "数据可见范围", "rule": "先按权限过滤，再重排、再进模型；统计同理（不查而非隐藏）",
             "demo": "/api/overview 普通用户强制带 owner_id / user_id / created_by 条件", "production": "Milvus expr + filter_candidates 双过滤", "status": "演示级"},
            {"layer": "越权边界", "rule": "前端隐藏 ≠ 后端放行；所有 admin 接口独立 require_admin，返回 403",
             "demo": "admin/audit/settings/evaltest 写接口均有后端门禁；selftest C24/C31/C32 覆盖", "production": "行级 ACL + 审计留存", "status": "演示级"},
        ],
        "matrix": [
            {"resource": "文档列表 / 删除", "user": "仅本人 owner_id", "admin": "全局", "anon": "401"},
            {"resource": "智能问答 / 历史", "user": "仅本人会话", "admin": "全局会话", "anon": "401"},
            {"resource": "badcase 采集", "user": "可写", "admin": "可写", "anon": "401"},
            {"resource": "badcase 查看 / 导出 / 完成度", "user": "403", "admin": "可读写", "anon": "401"},
            {"resource": "审计日志 / 成员 / 模型设置", "user": "403", "admin": "可读写", "anon": "401"},
            {"resource": "评估测试（只读）", "user": "本人范围用量/日志", "admin": "全局", "anon": "401"},
            {"resource": "评估配置 / 预算 / 限流 / 触发运行", "user": "403", "admin": "可读写", "anon": "401"},
        ],
        "note": "本切片是单租户演示；多租户行级隔离、部门树、逐段落 ACL 属架构预留，不得对外称已实现",
    }
