"""知识治理 API：新鲜度策略、演示场景、自定义冲突检测。

设计取舍：
- `knowledge/` 是纯函数包，这里只做 HTTP 适配，不写业务逻辑；
- 策略存进程内（演示切片单进程），生产应落配置中心并带版本号；
- 演示场景是合成样本，不写业务库、不写向量、不触发外部调用；
- 写接口仅 admin，与既有「写接口仅 admin」约定一致；
- 不改动既有检索/生成链路的默认行为：真实问答是否叠加新鲜度由
  `webapp/rag.py` 里的显式开关决定（默认关闭，避免打断 33 项 selftest）。
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from knowledge import (FreshnessPolicy, detect_and_build, extract_claims,
                       meta_from_row)
from knowledge.samples import SCENARIO_ID, build_scenario
from webapp import db
from webapp.deps import get_current_user, require_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_lock = threading.Lock()
_POLICY = FreshnessPolicy()


class PolicyPatch(BaseModel):
    enabled: bool | None = None
    half_life_days: float | None = None
    stale_after_days: float | None = None
    expired_action: str | None = None
    demote_floor: float | None = None
    blend: float | None = None
    stale_alert_ratio: float | None = None


class DetectPayload(BaseModel):
    texts: list[dict[str, Any]]


def _current() -> FreshnessPolicy:
    with _lock:
        return _POLICY


def _apply(patch: PolicyPatch) -> FreshnessPolicy:
    global _POLICY
    with _lock:
        d = _POLICY.__dict__.copy()
        for k, v in patch.model_dump(exclude_none=True).items():
            d[k] = v
        # 显式校验，避免非法值污染所有后续判定
        if d["half_life_days"] <= 0:
            raise ValueError("half_life_days 必须 > 0")
        if not 0 <= d["blend"] <= 1:
            raise ValueError("blend 必须在 [0,1]")
        if not 0 <= d["demote_floor"] <= 1:
            raise ValueError("demote_floor 必须在 [0,1]")
        if d["stale_after_days"] < 0:
            raise ValueError("stale_after_days 必须 >= 0")
        if not 0 <= d["stale_alert_ratio"] <= 1:
            raise ValueError("stale_alert_ratio 必须在 [0,1]")
        if d["expired_action"] not in ("demote", "exclude", "warn"):
            raise ValueError("expired_action 必须是 demote / exclude / warn")
        _POLICY = FreshnessPolicy(**d)
        return _POLICY


@router.get("/status")
def status(_: dict = Depends(get_current_user)):
    p = _current()
    return {
        "scenario_id": SCENARIO_ID,
        "policy": p.as_dict(),
        "fields": {
            "valid_from": "生效时间；未来生效的文档排除出上下文",
            "expires_at": "到期时间；按 expired_action 处置",
            "last_verified_at": "人工复核时间，优先于入库时间计算年龄",
            "revision": "文档版本号",
            "authority": "来源权威等级，由业务注册表定义",
            "scope_json": "适用性维度（region / plan / product_version）",
        },
        "integration": {
            "algorithm_package": "已实现（knowledge/）",
            "demo_scenario": "已实现（合成样本，不写业务库）",
            "retrieval_chain": "可选叠加，默认关闭，见 webapp/rag.py",
            "generation_chain": "只设计（EvidenceRecord 已就绪，未注入 prompt）",
            "frontend": "已实现（知识治理视图）",
        },
        "note": "阈值是演示默认经验起始值，非压测结论，需用真实语料校准",
    }


@router.put("/policy")
def put_policy(body: PolicyPatch, user: dict = Depends(require_admin)):
    try:
        p = _apply(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.log_action(user["username"], user["role"], "knowledge", "policy",
                  f"policy={p.as_dict()}")
    return {"policy": p.as_dict(), "effective": "本进程即时生效；生产需落配置中心并多进程同步"}


@router.get("/scenario")
def scenario(_: dict = Depends(get_current_user)):
    """一次返回全部可展示字段：文档原文、新鲜度报告、排序对照、证据记录、负向对照。"""
    return build_scenario(policy=_current())


@router.post("/detect")
def detect(body: DetectPayload, user: dict = Depends(require_admin)):
    """对自定义文本跑 claim 抽取 + 冲突检测（不写库、不触发外部调用）。"""
    claims = []
    for item in body.texts:
        meta = meta_from_row(item)
        scope = meta.scope or {}
        claims.extend(extract_claims(
            str(item.get("text") or ""), doc_id=meta.doc_id,
            chunk_id=str(item.get("chunk_id") or ""),
            scope=scope, valid_from=meta.valid_from, authority=meta.authority,
        ))
    rec = detect_and_build(claims)
    db.log_action(user["username"], user["role"], "knowledge", "detect",
                  f"claims={len(claims)} conflicts={len(rec.conflicts)}")
    return {"scenario_id": "custom", "evidence": rec.as_dict()}
