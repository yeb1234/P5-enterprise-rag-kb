"""事实冲突检测：claim 分组 → 冲突图连通分量 → 按簇裁决 → 结构化证据。

核心纪律（来自 2026-11-14 调研，也是中文面试题「两份文档打架信谁」的正确答法）：
1. **不让向量检索独自裁决**。相似度分数不含权威度、生效期、适用性信息；
   位置偏差、语气强弱、词法重合都会让模型挑错来源。
2. **冲突是一种正式状态**，要显式传给生成层，不能把互斥片段无序塞进上下文。
3. **按簇独立裁决**（cluster-aware recency）：冲突图里每个连通分量单独处理，
   避免「整批只留最新一份」误删其它独立簇的有效信息。
4. **权威度由业务注册表定义**，不是开发在代码里写死一个全局常量；
   这里只实现「读 DocMeta.authority 并解释」，注册表本身属于治理流程。
5. **新版本优先只是先验，不是自动采信**：`trust_newer_with_review` 必须带
   `needs_review=True`；权威性相当时诚实并列两个版本 + 转人工，不发明 tie-break。
"""
from __future__ import annotations

from collections import defaultdict

from knowledge.claims import topic_similarity
from knowledge.contracts import (Claim, Conflict, EvidenceRecord,
                                 KIND_AUTHORITY, KIND_FRESHNESS, KIND_NEGATION,
                                 KIND_NUMERIC, KIND_UNRESOLVED,
                                 RESOLVE_AUTHORITY, RESOLVE_ESCALATE,
                                 RESOLVE_RECENCY)
from knowledge.freshness import parse_ts, utcnow


def _classify(group: list[Claim]) -> str:
    authorities = {c.authority for c in group}
    valid_froms = {c.valid_from for c in group if c.valid_from}
    if len(authorities) > 1:
        return KIND_AUTHORITY
    if len(valid_froms) > 1:
        return KIND_FRESHNESS
    if any(c.polarity < 0 for c in group) and any(c.polarity > 0 for c in group):
        return KIND_NEGATION
    if any(c.unit in ("version", "date") or c.value_norm[:1].isdigit() for c in group):
        return KIND_NUMERIC
    return KIND_UNRESOLVED


def detect_conflicts(claims: list[Claim], *, topic_sim_min: float = 0.30,
                     min_confidence: float = 0.5) -> list[Conflict]:
    """按 `Claim.key` 分组检测。只有归一化值互斥（或肯定/否定相反）才报冲突。

    - 低置信度 claim（年份、裸小整数）不单独触发冲突，避免误报；
    - 主题门控用于跨 key 的否定不对称检测，阈值默认 0.30（经验起始值）；
    - 同一 key 但值完全相同 → 视为互相印证，不报冲突。
    """
    groups: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        if c.confidence >= min_confidence:
            groups[c.key].append(c)

    out: list[Conflict] = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        norms = {c.value_norm for c in group}
        pols = {c.polarity for c in group}
        conflicting = len(norms) > 1 or (len(pols) > 1 and topic_similarity(
            group[0].subject + group[0].text, group[-1].subject + group[-1].text) >= topic_sim_min)
        if not conflicting:
            continue
        out.append(Conflict(
            key=key, claim_ids=tuple(c.claim_id for c in group),
            values=tuple(sorted({c.value for c in group})),
            kind=_classify(group),
            severity="warn",
            resolution=RESOLVE_ESCALATE,
            winner_id=None, needs_review=True,
            reason="检测到互斥取值，待按簇裁决",
        ))
    return out


def connected_components(conflicts: list[Conflict]) -> list[list[Conflict]]:
    """冲突图连通分量（DFS）。共享任一 claim 的冲突并入同一簇。"""
    if not conflicts:
        return []
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for cf in conflicts:
        ids = list(cf.claim_ids)
        for cid in ids[1:]:
            union(ids[0], cid)

    buckets: dict[str, list[Conflict]] = defaultdict(list)
    for cf in conflicts:
        buckets[find(cf.claim_ids[0])].append(cf)
    return list(buckets.values())


def resolve_cluster(cluster: list[Conflict], claims_by_id: dict[str, Claim],
                    *, now=None) -> list[Conflict]:
    """对单个连通分量裁决，返回已填好 resolution / winner 的 Conflict 列表。

    裁决顺序：业务权威度 → 生效期（需复核）→ 无法裁决（并列 + 转人工）。
    每个簇独立处理；被淘汰的 claim 不删除，只在 EvidenceRecord 里标 excluded。
    """
    ref = utcnow(now)
    involved: dict[str, Claim] = {}
    for cf in cluster:
        for cid in cf.claim_ids:
            c = claims_by_id.get(cid)
            if c:
                involved[cid] = c
    if not involved:
        return cluster

    max_authority = max(c.authority for c in involved.values())
    top = [c for c in involved.values() if c.authority == max_authority]
    if len({c.authority for c in involved.values()}) > 1:
        winner = sorted(top, key=lambda c: (parse_ts(c.valid_from) or ref, c.claim_id))[-1]
        reason = (f"来源权威度不同（最高 authority={max_authority}），按业务注册表取高权威来源；"
                  "权威等级由业务负责人定义，不由代码硬编码")
        resolution, needs_review = RESOLVE_AUTHORITY, False
    else:
        dated = [c for c in involved.values() if parse_ts(c.valid_from)]
        if len({c.valid_from for c in dated}) > 1:
            winner = max(dated, key=lambda c: (parse_ts(c.valid_from) or ref, c.claim_id))
            reason = ("权威性相当但生效期不同，按较新版本进入主上下文；"
                      "新版本优先只是先验，标 needs_review 待人工确认，不自动采信")
            resolution, needs_review = RESOLVE_RECENCY, True
        else:
            winner = None
            reason = ("权威性相当、适用范围相同且无可裁决的生效期差异；"
                      "并列呈现两个版本并转知识负责人审核，不发明 tie-breaking 规则")
            resolution, needs_review = RESOLVE_ESCALATE, True

    winner_id = winner.claim_id if winner else None
    resolved = []
    for cf in cluster:
        resolved.append(Conflict(
            key=cf.key, claim_ids=cf.claim_ids, values=cf.values, kind=cf.kind,
            severity="block" if resolution == RESOLVE_ESCALATE else "warn",
            resolution=resolution, winner_id=winner_id,
            needs_review=needs_review, reason=reason,
        ))
    return resolved


def build_evidence_record(claims: list[Claim], conflicts: list[Conflict], *,
                          as_of: str = "") -> EvidenceRecord:
    """生成层的正式输入：claim、冲突、选中/排除清单、告警与裁决日志。"""
    by_id = {c.claim_id: c for c in claims}
    resolved: list[Conflict] = []
    for cluster in connected_components(conflicts):
        resolved.extend(resolve_cluster(cluster, by_id))

    conflicted_ids = {cid for cf in resolved for cid in cf.claim_ids}
    winner_ids = {cf.winner_id for cf in resolved if cf.winner_id}
    selected = [c.claim_id for c in claims
                if c.claim_id not in conflicted_ids or c.claim_id in winner_ids]
    excluded = [cid for cid in conflicted_ids if cid not in winner_ids]

    warnings: list[str] = []
    if resolved:
        warnings.append(f"检测到 {len(resolved)} 组事实冲突，回答必须显式披露，不得自行挑选一个值")
    if any(cf.resolution == RESOLVE_ESCALATE for cf in resolved):
        warnings.append("存在无法裁决的冲突：并列呈现来源与原文，转知识负责人审核")
    if any(cf.needs_review for cf in resolved):
        warnings.append("存在 needs_review 裁决：新版本优先只是先验，需人工确认")

    log = [{"key": cf.key, "resolution": cf.resolution, "winner_id": cf.winner_id,
            "needs_review": cf.needs_review, "reason": cf.reason,
            "claim_ids": list(cf.claim_ids)} for cf in resolved]

    return EvidenceRecord(
        as_of=as_of, claims=list(claims), conflicts=resolved,
        selected_claim_ids=selected, excluded_claim_ids=sorted(excluded),
        warnings=warnings, resolution_log=log,
    )


def detect_and_build(claims: list[Claim], *, as_of: str = "",
                     topic_sim_min: float = 0.30) -> EvidenceRecord:
    """一步到位：检测冲突 → 按簇裁决 → 产出证据记录。"""
    return build_evidence_record(claims, detect_conflicts(claims, topic_sim_min=topic_sim_min),
                                 as_of=as_of)
