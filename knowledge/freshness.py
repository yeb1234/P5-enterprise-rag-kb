"""入库新鲜度：时间衰减 + 适用性门控 + 过期处置 + 重排。

为什么不是一个 `ORDER BY created_at DESC`：
- 最新 != 适用。未来生效的政策、仍适用于特定群体的旧合同、不同地区的制度，
  都要靠 `valid_from` / `expires_at` / `scope` 判断，而不是只看时间戳。
- 相关性不能被新鲜度完全压过。`blend` 控制新鲜度在有效分里的权重，
  默认 0.65，既让旧内容降权，也不会让一篇高度相关的老文档彻底消失。
- 半衰期衰减是可复算的纯函数：`decay = 0.5 ** (age_days / half_life_days)`，
  每一个数值都能在前端轨迹里解释清楚。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from knowledge.contracts import (ACTION_DEMOTE, ACTION_EXCLUDE, ACTION_WARN,
                                 DocMeta, FreshnessPolicy, FreshnessResult)


def parse_ts(value: str | None) -> datetime | None:
    """解析 ISO-8601（含尾部 Z）。无效输入返回 None，绝不抛给上传链路。"""
    if not value:
        return None
    try:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def age_days(meta: DocMeta, now: datetime | None = None) -> float:
    """文档年龄。优先用人工复核时间（比入库时间更能代表「还有效」），
    其次索引时间，最后生效时间；都没有就视为 0 天（新入库）。"""
    ref = utcnow(now)
    for raw in (meta.last_verified_at, meta.indexed_at, meta.valid_from):
        dt = parse_ts(raw)
        if dt:
            return max(0.0, (ref - dt).total_seconds() / 86400.0)
    return 0.0


def decay_factor(age: float, half_life_days: float, floor: float = 0.10) -> float:
    """时间衰减：每过一个半衰期折半，但不低于 floor（避免完全归零）。"""
    if half_life_days <= 0:
        return 1.0
    return max(floor, min(1.0, 0.5 ** (max(0.0, age) / half_life_days)))


def evaluate(meta: DocMeta, base_score: float, *, policy: FreshnessPolicy,
             now: datetime | None = None, original_rank: int = 0) -> FreshnessResult:
    """对单个候选算新鲜度与有效分。所有分支都留下 reason，便于前端展示。"""
    ref = utcnow(now)
    age = age_days(meta, ref)
    decay = decay_factor(age, policy.half_life_days, policy.demote_floor)
    effective = float(base_score) * (1.0 - policy.blend + policy.blend * decay)

    vf, exp = parse_ts(meta.valid_from), parse_ts(meta.expires_at)
    if vf and vf > ref:
        return FreshnessResult(
            doc_id=meta.doc_id, age_days=age, decay=decay,
            base_score=float(base_score), effective_score=0.0,
            not_yet_effective=True, excluded=True, action=ACTION_EXCLUDE,
            reason="valid_from 在未来，尚未生效，本轮排除（不是判它过期）",
            original_rank=original_rank,
        )

    is_expired = bool(exp and exp < ref)
    is_stale = age > policy.stale_after_days
    action, reason = ACTION_WARN, "新鲜度正常"
    excluded = False

    if is_expired:
        if policy.expired_action == ACTION_EXCLUDE:
            effective, excluded, action = 0.0, True, ACTION_EXCLUDE
            reason = "expires_at 已过，按策略排除出候选（报告仍留痕）"
        elif policy.expired_action == ACTION_DEMOTE:
            effective *= policy.demote_floor
            action = ACTION_DEMOTE
            reason = "expires_at 已过，按策略降权并在回答中标注"
        else:
            action = ACTION_WARN
            reason = "expires_at 已过，按策略只告警不改分"
    elif is_stale:
        action = ACTION_DEMOTE
        reason = f"年龄 {age:.1f} 天超过 stale_after_days={policy.stale_after_days}，标 stale 并降权"

    if not policy.enabled:
        effective, action = float(base_score), ACTION_WARN
        reason = "新鲜度开关关闭，仅计算不干预排序"

    return FreshnessResult(
        doc_id=meta.doc_id, age_days=age, decay=decay,
        base_score=float(base_score), effective_score=effective,
        is_stale=is_stale, is_expired=is_expired, excluded=excluded,
        action=action, reason=reason, original_rank=original_rank,
    )


def rerank(candidates: list[dict], meta_map: dict[str, DocMeta], *,
           policy: FreshnessPolicy, now: datetime | None = None) -> tuple[list[dict], list[FreshnessResult]]:
    """在检索结果上叠加新鲜度。保留 original_rank / base_score 供前端做开关前后对照。

    candidates 是 rag.retrieve() 的 hit（含 chunk_id / doc_id / score / text）。
    缺 meta 的文档按「新入库、无限制」处理，不因缺少元数据被误杀。
    """
    results: list[FreshnessResult] = []
    enriched: list[dict] = []
    for rank, c in enumerate(candidates, 1):
        doc_id = str(c.get("doc_id") or c.get("document_id") or "")
        meta = meta_map.get(doc_id) or DocMeta(doc_id=doc_id)
        base = float(c.get("score") or 0.0)
        r = evaluate(meta, base, policy=policy, now=now, original_rank=rank)
        results.append(r)
        item = dict(c)
        item["freshness"] = r.as_dict()
        item["effective_score"] = round(r.effective_score, 6)
        enriched.append(item)

    kept = [c for c, r in zip(enriched, results) if not r.excluded]
    kept.sort(key=lambda x: x["effective_score"], reverse=True)
    return kept, results


def summary(results: list[FreshnessResult], *, policy: FreshnessPolicy) -> dict:
    """可观测汇总：年龄分布、stale 占比、过期数、是否触发告警。"""
    if not results:
        return {"count": 0, "stale_ratio": 0.0, "alert": False,
                "buckets": {}, "note": "无候选"}
    buckets = {"0-30d": 0, "31-90d": 0, "91-365d": 0, ">365d": 0}
    for r in results:
        a = r.age_days
        if a <= 30:
            buckets["0-30d"] += 1
        elif a <= 90:
            buckets["31-90d"] += 1
        elif a <= 365:
            buckets["91-365d"] += 1
        else:
            buckets[">365d"] += 1
    stale = sum(1 for r in results if r.is_stale)
    ratio = stale / len(results)
    return {
        "count": len(results),
        "stale": stale, "expired": sum(1 for r in results if r.is_expired),
        "not_yet_effective": sum(1 for r in results if r.not_yet_effective),
        "excluded": sum(1 for r in results if r.excluded),
        "stale_ratio": round(ratio, 4),
        "buckets": buckets,
        "alert": ratio > policy.stale_alert_ratio,
        "alert_threshold": policy.stale_alert_ratio,
        "note": "告警阈值是公开经验值，非本项目实测；需用真实语料校准",
    }


def meta_from_row(row: dict[str, Any]) -> DocMeta:
    """把 SQLite 行 / API 请求体转成 DocMeta，容忍缺字段与 JSON 字符串 scope。"""
    import json
    scope = row.get("scope") or {}
    if isinstance(scope, str):
        try:
            scope = json.loads(scope)
        except (ValueError, TypeError):
            scope = {}
    return DocMeta(
        doc_id=str(row.get("doc_id") or row.get("id") or ""),
        title=str(row.get("title") or row.get("filename") or ""),
        revision=str(row.get("revision") or "1"),
        valid_from=row.get("valid_from"), expires_at=row.get("expires_at"),
        last_verified_at=row.get("last_verified_at"),
        indexed_at=row.get("indexed_at") or row.get("created_at"),
        authority=int(row.get("authority") or 1),
        scope={str(k): str(v) for k, v in dict(scope).items()},
        checksum=str(row.get("checksum") or ""),
    )
