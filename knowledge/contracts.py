"""知识治理子系统的数据契约。

设计取舍（与项目其它包一致：先定契约，再做策略）：
- 所有判定都落到可序列化、可复算的字段上，前端能逐条展示「为什么」；
- 新鲜度不是「越新越对」：适用性（valid_from / expires_at / scope）与
  来源权威度（authority）都是一等字段，避免把「未来生效的政策」或
  「旧合同仍适用于某群体」误判；
- 冲突检测产出的是**证据记录（EvidenceRecord）**，交给生成层时带明确的
  conflict_signal，绝不把一堆互斥片段无序塞进上下文赌模型选对。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 过期处置
ACTION_DEMOTE = "demote"     # 降权但保留，回答里标注
ACTION_EXCLUDE = "exclude"   # 排除出候选，仍在报告中留痕
ACTION_WARN = "warn"         # 不改分数，仅告警
EXPIRED_ACTIONS = (ACTION_DEMOTE, ACTION_EXCLUDE, ACTION_WARN)

# 冲突类型（沿用公开分类，不一刀切）
KIND_NUMERIC = "numeric"             # 同一字段出现互斥数值
KIND_NEGATION = "negation"           # 一方含否定/废止方向词
KIND_FRESHNESS = "freshness"         # 值不同且生效期不同，可能是版本更替
KIND_AUTHORITY = "authority"         # 值不同且来源权威度不同
KIND_UNRESOLVED = "unresolved"       # 权威性相当、适用范围相同，无法裁决

# 裁决结果
RESOLVE_AUTHORITY = "trust_higher_authority"
RESOLVE_RECENCY = "trust_newer_with_review"   # 新版本优先，但强制需复核
RESOLVE_ESCALATE = "present_both_and_escalate"
RESOLVE_SKIP = "proceed_normally"


@dataclass(frozen=True)
class FreshnessPolicy:
    """新鲜度策略。阈值是演示默认经验起始值，需用真实语料校准。"""

    enabled: bool = True
    half_life_days: float = 90.0          # 时间衰减半衰期
    stale_after_days: float = 365.0       # 超过即标 stale
    expired_action: str = ACTION_DEMOTE   # 已过 expires_at 时怎么处置
    demote_floor: float = 0.10            # demote 的最低衰减系数，避免完全归零
    blend: float = 0.65                   # 有效分 = 相关分 * (1-blend + blend*decay)
    stale_alert_ratio: float = 0.10       # stale 占比告警线（公开经验值，非本项目实测）

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "half_life_days": self.half_life_days,
            "stale_after_days": self.stale_after_days, "expired_action": self.expired_action,
            "demote_floor": self.demote_floor, "blend": self.blend,
            "stale_alert_ratio": self.stale_alert_ratio,
            "note": "演示默认经验起始值，非压测结论，需用真实语料校准",
        }


@dataclass(frozen=True)
class DocMeta:
    """文档的时效与适用性元数据。时间统一用 ISO-8601 UTC 字符串。"""

    doc_id: str
    title: str = ""
    revision: str = "1"
    valid_from: str | None = None        # 生效时间；未来生效的文档不得进上下文
    expires_at: str | None = None        # 到期时间
    last_verified_at: str | None = None  # 最近人工复核时间（比入库时间更可信）
    indexed_at: str | None = None
    authority: int = 1                   # 来源权威等级，由业务负责人定义，不是全局常量
    scope: dict[str, str] = field(default_factory=dict)  # region / plan / product_version 等
    checksum: str = ""

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "title": self.title, "revision": self.revision,
            "valid_from": self.valid_from, "expires_at": self.expires_at,
            "last_verified_at": self.last_verified_at, "indexed_at": self.indexed_at,
            "authority": self.authority, "scope": dict(self.scope),
            "checksum": self.checksum,
        }


@dataclass(frozen=True)
class FreshnessResult:
    doc_id: str
    age_days: float
    decay: float
    base_score: float
    effective_score: float
    is_stale: bool = False
    is_expired: bool = False
    not_yet_effective: bool = False
    excluded: bool = False
    action: str = ACTION_WARN
    reason: str = ""
    original_rank: int = 0

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "age_days": round(self.age_days, 2),
            "decay": round(self.decay, 4), "base_score": round(self.base_score, 4),
            "effective_score": round(self.effective_score, 4),
            "is_stale": self.is_stale, "is_expired": self.is_expired,
            "not_yet_effective": self.not_yet_effective, "excluded": self.excluded,
            "action": self.action, "reason": self.reason,
            "original_rank": self.original_rank,
        }


@dataclass(frozen=True)
class Claim:
    """一条可比对的事实断言。key 决定「哪些断言在说同一件事」。"""

    claim_id: str
    doc_id: str
    subject: str
    attribute: str
    value: str
    value_norm: str
    polarity: int = 1                 # 1 肯定 / -1 否定、废止、不再适用
    unit: str = ""
    scope: dict[str, str] = field(default_factory=dict)
    valid_from: str | None = None
    authority: int = 1
    text: str = ""                    # 支持片段原文，引用必须能回溯
    chunk_id: str = ""
    confidence: float = 1.0

    @property
    def key(self) -> str:
        scope_key = ",".join(f"{k}={v}" for k, v in sorted(self.scope.items()))
        return f"{self.subject}|{self.attribute}|{scope_key}"

    def as_dict(self) -> dict:
        d = {
            "claim_id": self.claim_id, "doc_id": self.doc_id,
            "subject": self.subject, "attribute": self.attribute,
            "value": self.value, "value_norm": self.value_norm,
            "polarity": self.polarity, "unit": self.unit, "scope": dict(self.scope),
            "valid_from": self.valid_from, "authority": self.authority,
            "text": self.text, "chunk_id": self.chunk_id,
            "confidence": self.confidence, "key": self.key,
        }
        return d


@dataclass(frozen=True)
class Conflict:
    """一个冲突簇。簇内所有 claim 都保留，裁决只决定「谁进主上下文」。"""

    key: str
    claim_ids: tuple[str, ...]
    values: tuple[str, ...]
    kind: str
    severity: str = "warn"             # info | warn | block
    resolution: str = RESOLVE_ESCALATE
    winner_id: str | None = None
    needs_review: bool = True
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key, "claim_ids": list(self.claim_ids),
            "values": list(self.values), "kind": self.kind,
            "severity": self.severity, "resolution": self.resolution,
            "winner_id": self.winner_id, "needs_review": self.needs_review,
            "reason": self.reason,
        }


@dataclass
class EvidenceRecord:
    """给生成层的结构化证据。检索 → 冲突检测 → 生成 之间的正式契约。"""

    as_of: str
    claims: list[Claim] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    selected_claim_ids: list[str] = field(default_factory=list)
    excluded_claim_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolution_log: list[dict] = field(default_factory=list)

    @property
    def conflict_signal(self) -> bool:
        return bool(self.conflicts)

    def as_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "claims": [c.as_dict() for c in self.claims],
            "conflicts": [c.as_dict() for c in self.conflicts],
            "selected_claim_ids": list(self.selected_claim_ids),
            "excluded_claim_ids": list(self.excluded_claim_ids),
            "warnings": list(self.warnings),
            "resolution_log": list(self.resolution_log),
            "conflict_signal": self.conflict_signal,
            "note": "存在冲突时生成层必须显式披露，不得自行挑选一个值",
        }
