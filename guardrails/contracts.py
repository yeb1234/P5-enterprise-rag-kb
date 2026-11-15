"""内容安全 / PII 防护的数据契约。

设计取舍：
- 「一层」不等于「只在一处替换」。同一套规则要贯穿 **入库前 / 进模型前 /
  输出前 / 日志与导出前** 四个出口，否则原始 PII 会残留在向量库、日志、
  缓存、CSV 里，展示时打码只是自欺欺人（`docs/knowledge/02-candidates.md` C09 的既有判断）。
- 每条命中都带 `kind` / 起止位置 / 置信度 / 规则版本，便于回放与误报反馈。
- 可逆 token 默认关闭：重标识映射本身就是新的泄漏面，开启必须独立授权与审计。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "PII-RULES@1.0"

# 脱敏动作
ACTION_MASK = "mask"        # 部分遮蔽，保留格式便于人工核对
ACTION_HASH = "hash"        # 不可逆 HMAC，跨请求稳定，便于统计
ACTION_TOKEN = "token"      # 可逆占位符（默认关闭）
ACTION_REMOVE = "remove"    # 完全替换为占位符
ACTION_BLOCK = "block"      # 命中即拒绝
ACTIONS = (ACTION_MASK, ACTION_HASH, ACTION_TOKEN, ACTION_REMOVE, ACTION_BLOCK)

# 四个出口
GATE_INGEST = "ingest"      # 入库前
GATE_CONTEXT = "context"    # 进模型前
GATE_OUTPUT = "output"      # 输出前
GATE_AUDIT = "audit"        # 日志与导出前
GATES = (GATE_INGEST, GATE_CONTEXT, GATE_OUTPUT, GATE_AUDIT)


@dataclass(frozen=True)
class PIIFinding:
    kind: str
    start: int
    end: int
    value: str
    confidence: float = 1.0
    rule: str = RULE_VERSION
    note: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "start": self.start, "end": self.end,
                "confidence": self.confidence, "rule": self.rule, "note": self.note,
                "preview": _preview(self.value)}


def _preview(v: str) -> str:
    """只回显首尾各 2 位，报告本身也不该泄露完整标识符。"""
    if len(v) <= 4:
        return "*" * len(v)
    return v[:2] + "***" + v[-2:]


def masked_preview(v: str) -> str:
    return _preview(v)


@dataclass(frozen=True)
class RedactionPolicy:
    enabled: bool = True
    default_action: str = ACTION_MASK
    kind_actions: dict[str, str] = field(default_factory=dict)
    block_kinds: tuple[str, ...] = ("API_KEY",)     # 密钥命中直接拒绝
    min_confidence: float = 0.60
    reversible_token: bool = False
    hash_salt: str = "demo-salt-change-me"
    mask_keep_head: int = 3
    mask_keep_tail: int = 4

    def action_for(self, kind: str) -> str:
        if kind in self.block_kinds:
            return ACTION_BLOCK
        return self.kind_actions.get(kind, self.default_action)

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "default_action": self.default_action,
                "kind_actions": dict(self.kind_actions), "block_kinds": list(self.block_kinds),
                "min_confidence": self.min_confidence,
                "reversible_token": self.reversible_token,
                "hash_salt_configured": bool(self.hash_salt and self.hash_salt != "demo-salt-change-me"),
                "note": "规则级检测，非模型级 NER；阈值与动作是演示默认值，需真实语料校准"}


@dataclass
class RedactionResult:
    gate: str
    text: str
    findings: list[PIIFinding] = field(default_factory=list)
    actions: dict[str, int] = field(default_factory=dict)
    blocked: bool = False
    blocked_kinds: list[str] = field(default_factory=list)
    rule_version: str = RULE_VERSION
    original_len: int = 0
    redacted_len: int = 0

    @property
    def hit_count(self) -> int:
        return len(self.findings)

    def as_dict(self) -> dict:
        by_kind: dict[str, int] = {}
        for f in self.findings:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        return {
            "gate": self.gate, "text": self.text,
            "findings": [f.as_dict() for f in self.findings],
            "by_kind": by_kind, "actions": dict(self.actions),
            "blocked": self.blocked, "blocked_kinds": list(self.blocked_kinds),
            "hit_count": self.hit_count, "rule_version": self.rule_version,
            "original_len": self.original_len, "redacted_len": self.redacted_len,
        }


@dataclass
class SafetyReport:
    """一次完整链路的四个出口汇总，供前端逐项展示。"""

    results: list[RedactionResult] = field(default_factory=list)
    blocked: bool = False

    @property
    def total_hits(self) -> int:
        return sum(r.hit_count for r in self.results)

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked, "total_hits": self.total_hits,
            "gates": [r.gate for r in self.results],
            "results": [r.as_dict() for r in self.results],
            "rule_version": RULE_VERSION,
        }
