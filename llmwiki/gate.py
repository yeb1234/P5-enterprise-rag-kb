"""Wiki 门禁：三个条件都稳定，才允许这一路参与检索。

每个判断都落到可复算的字段上（`checks` 里逐项写明 ok / reason / 依据），
前端据此显示「Wiki 没启动，是因为缺哪一条」，而不是只有一个灰掉的开关。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from knowledge.contracts import DocMeta, FreshnessPolicy
from knowledge.freshness import evaluate as freshness_evaluate

CHECKS = ("scenario", "sources", "freshness")


def source_fingerprint(docs: list[dict]) -> str:
    """信源指纹：对每篇 ready 文档的「id + 版本 + 更新时间 + 正文哈希」整体取 SHA-256。

    `content_hash` 由调用方从分块正文算出（见 webapp/kb_routes.py 的 SQL）——
    `documents` 表没有 checksum 列，而 Wiki 是从分块正文整理出来的，
    所以「正文变没变」只能看分块。任何一篇文档增删改，指纹都会变。
    """
    lines = sorted(
        f"{d.get('doc_id')}|{d.get('revision') or ''}|{d.get('updated_at') or ''}|{d.get('content_hash') or ''}"
        for d in docs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def text_fingerprint(text: str) -> str:
    """一段正文的内容指纹，前 16 位。构建时记在每条证据上，检索时重算比对。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _meta(d: dict) -> DocMeta:
    return DocMeta(
        doc_id=str(d.get("doc_id") or ""),
        valid_from=d.get("valid_from"), expires_at=d.get("expires_at"),
        last_verified_at=d.get("last_verified_at"),
        indexed_at=d.get("created_at"),
    )


def evaluate_gate(cfg: dict, docs: list[dict], build_meta: dict | None, *,
                   policy: FreshnessPolicy | None = None,
                   now: datetime | None = None) -> dict:
    """返回 {allowed, checks, reasons, fingerprint}。纯函数：同样的输入必然同样的输出。

    cfg 需要 wiki_scenario / wiki_stale_ratio_max 两个配置项。
    build_meta 是上次成功构建时记录的 {scenario, source_fingerprint, built_at, pages, claims}，
    没构建过传 None。
    """
    ref = now or datetime.now(timezone.utc)
    policy = policy or FreshnessPolicy()
    fp = source_fingerprint(docs)
    built = build_meta or {}
    reasons: list[str] = []

    # ① 业务场景已确定，且与构建时一致
    scenario = str(cfg.get("wiki_scenario") or "").strip()
    built_scenario = built.get("scenario")
    scenario_ok = bool(scenario) and (build_meta is None or built_scenario == scenario)
    if not scenario:
        reasons.append("未设置业务场景：没有整理口径，Wiki 无从构建")
        scenario_reason = "等待管理员设置业务场景"
    elif not scenario_ok:
        reasons.append(f"业务场景在构建后变更（{built_scenario!r} → {scenario!r}），需重建")
        scenario_reason = "场景在构建后变更，需重建"
    else:
        scenario_reason = "场景已确定且与构建时一致"

    # ② 信源与构建时一致
    built_fp = built.get("source_fingerprint")
    sources_ok = bool(docs) and bool(built_fp) and built_fp == fp
    if not docs:
        reasons.append("没有可用的 ready 文档")
        sources_reason = "知识库里没有已就绪的文档"
    elif not built_fp:
        reasons.append("尚未构建 Wiki")
        sources_reason = "尚未构建"
    elif not sources_ok:
        reasons.append("信源在构建后发生变化，页面可能已过期")
        sources_reason = "信源已变化，需重建"
    else:
        sources_reason = "信源与构建时一致"

    # ③ 新鲜度：过期 / 未生效一律不放行；陈旧占比有阈值
    try:
        stale_max = float(cfg.get("wiki_stale_ratio_max", 0.1))
    except (TypeError, ValueError):
        stale_max = 0.1
    stale = expired = pending = 0
    for d in docs:
        r = freshness_evaluate(_meta(d), 1.0, policy=policy, now=ref)
        stale += int(r.is_stale and not r.excluded)
        expired += int(r.is_expired)
        pending += int(r.not_yet_effective)
    stale_ratio = round(stale / (len(docs) or 1), 4)
    fresh_ok = bool(docs) and expired == 0 and pending == 0 and stale_ratio <= stale_max
    if expired:
        reasons.append(f"{expired} 篇文档已过期（expires_at 已过）")
    if pending:
        reasons.append(f"{pending} 篇文档尚未生效（valid_from 在未来）")
    if stale_ratio > stale_max:
        reasons.append(f"陈旧文档占比 {stale_ratio:.0%} 超过阈值 {stale_max:.0%}")

    checks = {
        "scenario": {"ok": scenario_ok, "value": scenario, "built_with": built_scenario,
                     "reason": scenario_reason},
        "sources": {"ok": sources_ok, "docs": len(docs), "fingerprint": fp[:16],
                    "built_fingerprint": (built_fp or "")[:16], "reason": sources_reason},
        "freshness": {"ok": fresh_ok, "stale": stale, "expired": expired,
                      "not_yet_effective": pending, "stale_ratio": stale_ratio,
                      "stale_ratio_max": stale_max, "stale_after_days": policy.stale_after_days,
                      "reason": "新鲜度稳定" if fresh_ok else "存在过期 / 未生效 / 陈旧占比超阈值"},
    }
    return {
        "allowed": all(checks[c]["ok"] for c in CHECKS),
        "checks": checks,
        "reasons": reasons,
        "fingerprint": fp,
        "evaluated_at": ref.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "阈值是演示起始值，需用真实语料校准",
    }


__all__ = ["CHECKS", "evaluate_gate", "source_fingerprint", "text_fingerprint"]
