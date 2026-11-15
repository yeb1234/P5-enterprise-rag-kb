"""运行时状态 API：缓存策略、key 拆解、检索缓存演示、模拟重启与失效演练。

设计取舍：
- 检索结果缓存放在**API 演示层**，不塞进 `rag.retrieve()` 主链路（默认关闭、
  不打断 33 项端到端自测）；真实链路接入是下一步，文档里如实标注。
- 演示严格 **先按权限过滤再写缓存**：普通用户只保留 owner_id 匹配的命中，
  admin 保留全部；缓存 value 里不含被过滤掉的片段。
- 所有写接口仅 admin。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cache import (CachePolicy, VersionController, authz_snapshot, build_key,
                   policy_version)
from webapp import db, providers, rag, recovery, settings
from webapp.deps import get_current_user, require_admin

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class CachePolicyPatch(BaseModel):
    enabled: bool | None = None
    l1_capacity: int | None = None
    l2_capacity: int | None = None
    ttl_seconds: int | None = None
    negative_ttl_seconds: int | None = None
    ttl_jitter: float | None = None


def _versions() -> tuple[str, str, str]:
    vc = VersionController(recovery.store())
    return vc.data_version(), vc.grant_version(), vc.policy_version()


def _example_key(question: str = "怎么配网") -> tuple[str, dict]:
    dv, gv, pv = _versions()
    authz = authz_snapshot(role="admin", department_id="",
                           visible_department_ids=[], grant_version=int(gv or 0))
    cfg = settings.get_all()
    return build_key(
        question=question, embedding_cache_key=settings.effective_embedding(cfg)["cache_key"],
        model=providers.get_model_label(), top_k=4, retrieval_mode="hybrid",
        rerank=cfg.get("rerank_provider", "off"), freshness_on=rag._freshness_enabled(),
        authz=authz, data_version=dv, policy_ver=pv)


@router.get("/status")
def status(_: dict = Depends(get_current_user)):
    s = recovery.store()
    key, parts = _example_key()
    return {
        "cache_policy": s.policy.as_dict(),
        "versions": {"data_version": _versions()[0], "grant_version": _versions()[1],
                     "policy_version": _versions()[2]},
        "stats": s.stats.as_dict(),
        "l2_entries": s.count(),
        "key_example": {"key": key, "parts": parts,
                        "note": "每段对应一类失效场景；缺 authz 会串权限、缺 data_version 会返回旧答案"},
        "recovery_report": recovery.report(),
        "integration": {
            "vector_cache": "已实现（chunk_embeddings，按 provider:模型 隔离）",
            "retrieval_cache": "演示级（本 API 的 cache-demo），默认不接主链路",
            "answer_cache": "未做（依赖 prompt + 生成模型，误命中风险高）",
            "semantic_cache": "未做",
            "startup_recovery": "已实现（lifespan 调用）",
            "policy_persistence": "已实现（runtime_state）",
        },
        "note": "阈值是演示默认经验值，非压测结论；L1 重启即清空、L2 持久化",
    }


@router.put("/cache-policy")
def put_cache_policy(body: CachePolicyPatch, user: dict = Depends(require_admin)):
    s = recovery.store()
    p = s.policy
    d = p.__dict__.copy()
    for k, v in body.model_dump(exclude_none=True).items():
        d[k] = v
    if d["l1_capacity"] <= 0 or d["l2_capacity"] <= 0:
        raise HTTPException(status_code=400, detail="容量必须 > 0")
    if d["ttl_seconds"] <= 0 or d["negative_ttl_seconds"] <= 0:
        raise HTTPException(status_code=400, detail="TTL 必须 > 0")
    if not 0 <= d["ttl_jitter"] < 1:
        raise HTTPException(status_code=400, detail="ttl_jitter 必须在 [0,1)")
    s.policy = CachePolicy(**d)
    recovery.persist_policy("cache_policy", s.policy.as_dict())
    db.log_action(user["username"], user["role"], "runtime", "cache_policy",
                  f"policy={s.policy.as_dict()}")
    return {"cache_policy": s.policy.as_dict(), "effective": "本进程即时生效"}


@router.get("/cache-demo")
def cache_demo(question: str = "配网", user: dict = Depends(get_current_user)):
    """一次跑完：构造 key → 查缓存 → miss 则检索 → 先按权限过滤 → 写缓存 →
    清 L1 后验证 L2 命中 → 递增 data_version 后验证旧 key 不可命中。
    """
    s = recovery.store()
    dv, gv, pv = _versions()
    authz = authz_snapshot(role=user["role"], department_id="",
                           visible_department_ids=[], grant_version=int(gv or 0))
    cfg = settings.get_all()
    key, parts = build_key(
        question=question, embedding_cache_key=settings.effective_embedding(cfg)["cache_key"],
        model=providers.get_model_label(), top_k=4, retrieval_mode="hybrid",
        rerank=cfg.get("rerank_provider", "off"), freshness_on=rag._freshness_enabled(),
        authz=authz, data_version=dv, policy_ver=pv)

    val, level = s.get(key, data_version=dv, authz=authz, policy_ver=pv)
    if val is None:
        hits = rag.retrieve(question, top_k=4)
        visible = _filter_for_user(hits, user)
        # 写缓存前已完成权限过滤；缓存 value 只含可见命中，且不带全文以降低泄漏面
        value = {"hits": [{"doc_id": h.get("doc_id"), "chunk_id": h.get("chunk_id"),
                           "score": h.get("score")} for h in visible],
                 "filtered_out": len(hits) - len(visible)}
        s.put(key, parts, value, is_negative=not value["hits"])
        level = "miss"
    else:
        value = val

    # 模拟重启：清 L1 后同一 key 应从 L2 命中
    s.clear_memory()
    after_restart, lvl2 = s.get(key, data_version=dv, authz=authz, policy_ver=pv)
    # 版本变化后用「新版本重新构造 key」查：应为未命中（懒失效，不改全局版本、不删条目）
    changed_dv = dv + "+changed"
    changed_key, _ = build_key(
        question=question, embedding_cache_key=settings.effective_embedding(cfg)["cache_key"],
        model=providers.get_model_label(), top_k=4, retrieval_mode="hybrid",
        rerank=cfg.get("rerank_provider", "off"), freshness_on=rag._freshness_enabled(),
        authz=authz, data_version=changed_dv, policy_ver=pv)
    changed_val, _ = s.get(changed_key, data_version=changed_dv, authz=authz, policy_ver=pv)
    return {
        "key": key, "parts": parts,
        "first_lookup": level,
        "permission_filter": {
            "before": value.get("filtered_out", 0) + len(value.get("hits", [])),
            "after": len(value.get("hits", [])),
            "note": "先过滤后写：缓存 value 只含当前用户可见的命中标识，不含正文"},
        "after_l1_cleared": {"level": lvl2, "hit": after_restart is not None,
                             "note": "L1 清空后仍从 L2 命中 = 重启后缓存可恢复"},
        "after_version_change": {"changed_data_version": changed_dv, "hit": changed_val is not None,
                                 "note": "data_version 变了 → 新 key 未命中；旧条目等 TTL 到期回收（懒失效）"},
        "stats": s.stats.as_dict(),
    }


def _filter_for_user(hits: list[dict], user: dict) -> list[dict]:
    """演示切片的权限过滤：admin 全见，普通用户只保留 owner_id 匹配的文档。"""
    if user.get("role") == "admin" or not hits:
        return hits
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, owner_id FROM documents").fetchall()
    finally:
        conn.close()
    mine = {r["id"] for r in rows if r["owner_id"] == user["id"]}
    return [h for h in hits if h.get("doc_id") in mine]


@router.post("/simulate-restart")
def simulate_restart(_: dict = Depends(require_admin)):
    return recovery.simulate_restart()


@router.post("/invalidate")
def invalidate(reason: str = "explicit", _: dict = Depends(require_admin)):
    s = recovery.store()
    n = s.invalidate_all(reason)
    return {"purged": n, "reason": reason,
            "note": "主动清空 L1+L2；日常文档/授权变更走懒失效，不做批量删除"}


@router.get("/recovery")
def get_recovery(_: dict = Depends(get_current_user)):
    return recovery.report() or {"note": "尚未启动自检"}
