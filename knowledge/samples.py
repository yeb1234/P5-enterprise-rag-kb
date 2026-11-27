"""内置演示场景：一组可控样本，用于把新鲜度与冲突检测讲清楚。

为什么用内置样本而不是真实上传的文档：
- 演示要**稳定可复现**，不能依赖「库里恰好有冲突数据」；
- 样本里刻意放了正向对照（真冲突）与负向对照（不同 scope、同值、无关主题），
  能直接展示「检测器不会乱报」；
- 全部是合成内容，不写业务库、不写向量、不触发任何外部调用。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from knowledge.contracts import DocMeta, FreshnessPolicy
from knowledge.claims import extract_claims
from knowledge.conflicts import detect_and_build
from knowledge.freshness import rerank, summary

SCENARIO_ID = "DEMO-FRESHNESS-CONFLICT-001"


def _iso(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(now: datetime, days: int) -> str:
    return (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_scenario(*, policy: FreshnessPolicy | None = None,
                   now: datetime | None = None) -> dict:
    """构建并返回完整演示结果，前端一次请求即可拿到全部可展示字段。"""
    ref = now or datetime.now(timezone.utc)
    p = policy or FreshnessPolicy()

    # --- 文档元数据：覆盖新旧、地区 scope、权威度、未来生效、已过期 ---
    metas = {
        "D1": DocMeta("D1", "员工手册 v1（2025 版）", revision="1",
                      valid_from=_iso(ref, 700), last_verified_at=_iso(ref, 500),
                      indexed_at=_iso(ref, 700), authority=1,
                      scope={"region": "CN"}, checksum="d1"),
        "D2": DocMeta("D2", "员工手册 v2（2026 修订）", revision="2",
                      valid_from=_iso(ref, 20), last_verified_at=_iso(ref, 5),
                      indexed_at=_iso(ref, 20), authority=1,
                      scope={"region": "CN"}, checksum="d2"),
        "D3": DocMeta("D3", "海外分公司手册（GB）", revision="1",
                      valid_from=_iso(ref, 60), indexed_at=_iso(ref, 60),
                      authority=1, scope={"region": "GB"}, checksum="d3"),
        "D4": DocMeta("D4", "合规公告·财务口径", revision="1",
                      valid_from=_iso(ref, 10), indexed_at=_iso(ref, 10),
                      authority=3, scope={"region": "CN"}, checksum="d4"),
        "D5": DocMeta("D5", "2027 新制度（尚未生效）", revision="draft",
                      valid_from=_future(ref, 30), indexed_at=_iso(ref, 1),
                      authority=1, scope={"region": "CN"}, checksum="d5"),
        "D6": DocMeta("D6", "双十一促销政策（已到期）", revision="1",
                      valid_from=_iso(ref, 400), expires_at=_iso(ref, 30),
                      indexed_at=_iso(ref, 400), authority=1,
                      scope={"region": "CN"}, checksum="d6"),
    }

    # --- 正文：真冲突 / 负向对照都齐 ---
    texts = {
        "D1": "婚假天数为 10 天。\n接口超时为 30 秒。",
        "D2": "婚假天数为 15 天。\n接口超时为 30 秒。",
        "D3": "婚假天数为 20 天。\n接口超时为 30 秒。",   # scope=GB，不应与 CN 冲突
        "D4": "报销上限为 5000 元。\n接口超时为 30 秒。",  # 高权威；婚假不写，留给 D1/D2 演示版本更替
        "D5": "婚假天数为 30 天。\n接口超时为 30 秒。",    # 未生效，排除
        "D6": "报销上限为 8000 元。\n接口超时为 30 秒。",  # 已过期
    }

    # --- 候选命中：原分刻意让「旧文档」排第一，展示新鲜度重排效果 ---
    hits = [
        {"doc_id": "D1", "chunk_id": "D1-c1", "score": 0.95},
        {"doc_id": "D2", "chunk_id": "D2-c1", "score": 0.88},
        {"doc_id": "D3", "chunk_id": "D3-c1", "score": 0.80},
        {"doc_id": "D4", "chunk_id": "D4-c1", "score": 0.76},
        {"doc_id": "D5", "chunk_id": "D5-c1", "score": 0.99},
        {"doc_id": "D6", "chunk_id": "D6-c1", "score": 0.70},
    ]
    for h in hits:
        h["text"] = texts.get(h["doc_id"], "")

    ordered, freshness = rerank(hits, metas, policy=p, now=ref)
    _, freshness_off = rerank(hits, metas,
                              policy=FreshnessPolicy(enabled=False,
                                                     expired_action=p.expired_action,
                                                     half_life_days=p.half_life_days,
                                                     stale_after_days=p.stale_after_days,
                                                     blend=p.blend), now=ref)
    off_order = [c["doc_id"] for c, r in zip(hits, freshness_off) if not r.excluded]

    # --- claim 与冲突：只对「当前适用」的文档抽 claim ---
    applicable = [d for d, r in zip(hits, freshness) if not r.excluded]
    claims = []
    for h in applicable:
        m = metas[h["doc_id"]]
        claims.extend(extract_claims(
            h["text"], doc_id=m.doc_id, chunk_id=h["chunk_id"],
            scope=m.scope, valid_from=m.valid_from, authority=m.authority,
        ))
    evidence = detect_and_build(claims, as_of=ref.strftime("%Y-%m-%dT%H:%M:%SZ"))
    # demote 策略下过期文档仍参与 claim 抽取（只是降权），冲突检测正好把它抓出来：
    # 这条要显式告诉演示者，避免误以为「降权 = 已隔离」。
    if any(r.is_expired and not r.excluded for r in freshness):
        evidence.warnings.append(
            "存在已过 expires_at 但按 demote 策略仅降权的文档；它仍参与 claim 抽取，"
            "本次已被冲突检测标记，不会静默进入主上下文"
        )

    return {
        "scenario_id": SCENARIO_ID,
        "as_of": ref.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": p.as_dict(),
        "documents": [metas[k].as_dict() | {"text": texts[k]} for k in metas],
        "freshness": {
            "results": [r.as_dict() | {"title": metas[r.doc_id].title}
                        for r in freshness],
            "summary": summary(freshness, policy=p),
            "order_with": [h["doc_id"] for h in ordered],
            "order_without": off_order,
            "note": "order_without 仅关闭时间衰减；适用性门控（未生效/已过期）仍生效",
        },
        "evidence": evidence.as_dict(),
        "controls": {
            "true_conflicts": [c.as_dict() for c in evidence.conflicts],
            "negative_controls": [
                "D3 的 region=GB 与 CN 文档 scope 不同，不报冲突",
                "各文档「接口超时为 30 秒」取值相同，不报冲突",
                "D5 未来生效、D6 已过期，不参与 claim 抽取",
            ],
            "note": "合成样本，仅用于演示；不是质量基线，未做真实语料评测",
        },
    }
