"""内容安全 API：四出口状态、命中台账、规则演示、策略调整。

设计取舍：
- 状态与演示接口任意登录用户可读（评审人要能现场点开看）；
- 策略写入仅 admin，且会在审计日志留痕；
- `/check` 与 `/demo` 只跑内存样本，**不写台账**，避免演示把统计刷脏；
- 台账 `safety_events` 只存 gate / kind / 数量 / 动作 / 规则版本，不存原文。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from guardrails import (ACTIONS, RULE_VERSION, RedactionPolicy, default, gather,
                        get_policy, run_demo, set_policy, status)
from webapp import db
from webapp.deps import get_current_user, require_admin

router = APIRouter(prefix="/api/safety", tags=["safety"])


class CheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PolicyPatch(BaseModel):
    enabled: bool | None = None
    default_action: str | None = None
    min_confidence: float | None = None
    reversible_token: bool | None = None
    hash_salt: str | None = None
    mask_keep_head: int | None = None
    mask_keep_tail: int | None = None


def _ledger(limit: int = 60) -> dict:
    """按出口 / 类别汇总真实命中，证明四个出口都真的在跑。"""
    conn = db.get_conn()
    try:
        by_gate = conn.execute(
            "SELECT gate, SUM(hit_count) AS n, COUNT(*) AS rows FROM safety_events "
            "GROUP BY gate"
        ).fetchall()
        rows = conn.execute(
            "SELECT gate, kind, SUM(hit_count) AS n, MAX(ts) AS last_at "
            "FROM safety_events GROUP BY gate, kind ORDER BY last_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM safety_events").fetchone()["c"]
    finally:
        conn.close()
    return {
        "event_rows": total,
        "by_gate": {r["gate"]: {"hits": r["n"], "rows": r["rows"]} for r in by_gate},
        "recent": [{"gate": r["gate"], "kind": r["kind"], "hits": r["n"],
                    "last_at": r["last_at"]} for r in rows],
        "note": "只记数量与类别，不记原文、不记 preview、不记可逆 token 映射",
    }


@router.get("/status")
def get_status(_: dict = Depends(get_current_user)):
    s = status()
    s["ledger"] = _ledger()
    s["samples_note"] = (
        "内置样本里的证件号 / 手机号 / 卡号均为按校验位算法合成的教学用号码，"
        "不是任何真实个人的信息；校验位合法才用来做正向用例。"
    )
    return s


@router.get("/events")
def get_events(limit: int = 50, _: dict = Depends(get_current_user)):
    return _ledger(min(max(limit, 1), 200))


@router.post("/check")
def check(body: CheckIn, _: dict = Depends(get_current_user)):
    """同一段文本依次穿过四个出口，直观看到「每一层都在拦」。不写台账。"""
    g = default()
    results = [g.ingest(body.text), g.context(body.text),
               g.output(body.text), g.audit(body.text)]
    return {
        "rule_version": RULE_VERSION,
        "policy": get_policy().as_dict(),
        "report": gather(results).as_dict(),
        "note": "演示接口，只跑内存、不落台账；输入不会被保存",
    }


@router.post("/demo")
def demo(_: dict = Depends(get_current_user)):
    """一键样本集：正向命中 + 4 条防误报负向对照 + 四出口链路 + 流式跨界。"""
    return run_demo()


@router.put("/policy")
def put_policy(body: PolicyPatch, user: dict = Depends(require_admin)):
    p = get_policy()
    d = {
        "enabled": p.enabled,
        "default_action": p.default_action,
        "kind_actions": dict(p.kind_actions),
        "block_kinds": tuple(p.block_kinds),
        "min_confidence": p.min_confidence,
        "reversible_token": p.reversible_token,
        "hash_salt": p.hash_salt,
        "mask_keep_head": p.mask_keep_head,
        "mask_keep_tail": p.mask_keep_tail,
    }
    for k, v in body.model_dump(exclude_none=True).items():
        d[k] = v
    if d["default_action"] not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"动作必须是 {list(ACTIONS)} 之一")
    if not 0.0 <= d["min_confidence"] <= 1.0:
        raise HTTPException(status_code=400, detail="min_confidence 必须在 [0,1]")
    if d["mask_keep_head"] < 0 or d["mask_keep_tail"] < 0:
        raise HTTPException(status_code=400, detail="保留位数不能为负")
    new = RedactionPolicy(**d)
    set_policy(new)
    db.log_action(user["username"], user["role"], "safety", "policy",
                  f"更新内容安全策略 enabled={new.enabled}, action={new.default_action}, "
                  f"min_confidence={new.min_confidence}, reversible_token={new.reversible_token}")
    return {
        "policy": new.as_dict(),
        "effective": "本进程即时生效（不重启）",
        "warning": (
            "关闭 enabled 会让四个出口全部放行原文，仅限对照演示；"
            "生产环境应由配置管理系统锁定，不提供运行时关闭入口。"
            if not new.enabled else
            "可逆 token 开启后映射本身就是新的泄漏面，生产需加密存储 + 独立授权 + 保留期。"
            if new.reversible_token else ""
        ),
    }
