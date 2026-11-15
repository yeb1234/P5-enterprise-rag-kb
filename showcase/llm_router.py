"""LLM 路由分流与单文件快照（归属 agent-app-engineer）。

用户原话落点：「保证 LLM、对应的快照时间版本、提示词、参数等都要同步单文件
跟着这个 LLM 走（没有就空占位）；如果 RAG 后面解析难度变大会上一些 Pro 版本 LLM 兜底」。

本模块只做两件事：
  1. 从 ``ParseRouter.parse_document()`` 的**真实 dict 产物**算出可复算难度分；
  2. 用双阈值滞回状态机选出 standard / pro / fallback，产出不可变 ``RouteSnapshot``。

────────────────────────── 三态标注（写进注释，不冒充） ──────────────────────────
【已实现】难度评分、双阈值滞回 + 连续 k 次、事件直接触发 fallback、override 强制档、
          内容稳定哈希 route_id / snapshot_version、RouteSnapshot 占位规则。
【只设计】在线填真值（model / prompt / params / model_snapshot_at）——本轮一律占位；
          Pro 档真实模型与单价——未选定，不编造；真实外部 LLM 调用——本轮一次都不发。
【未做】  实时在线视觉调用作为默认路径、真实压测 / 质量基线、监控 SLO。

────────────────────────── 最重要的口径（易错，务必读） ──────────────────────────
* ``offline=True`` 是「本轮演示一律离线、不发起外部调用」的**全局前提**，它决定
  RouteSnapshot 的占位字段，但**不自动等于 event="offline"**、不会把复杂档自动打成
  fallback。依据 03-phase2-spec.md §1.1 裁决 1：「难度评分照常算出 standard / pro /
  fallback，Pro 兜底仍在复杂档真实触发；但本轮一律不发起外部 LLM 调用」。
  如果二者等价，复杂档永远选不出 pro，与「Pro 兜底真实触发」直接冲突。
* ``event`` 是调用方在**真的发生**离线不可用 / 策略不可用 / 预算耗尽 / 上游超时时
  显式传入的事件码；只有它才「忽略滞回计数、直接 fallback」。
* **绝不把「选出 Pro 档」讲成「调用了 Pro 模型」**：无论哪档，本模块都不填 model /
  prompt / params 的真值（无来源，填了就是编造）。

────────────────────────── 依赖与确定性 ──────────────────────────
* 零项目内 import：``from_parse_report`` 收的是已序列化的 dict（字段来源为
  ``parsing/contracts.py::QualitySignals.as_dict()`` 与
  ``parsing/router.py::VisionBudget.as_dict()`` / ``parse_document()``），
  因此同伴文件未落盘、``parsing/`` 可选依赖缺失时本模块都能独立运行。
* 稳定哈希思路参考 ``cache/keys.py::policy_version()``（规范化 json + sort_keys +
  sha256 截断），此处独立实现以保持零反向依赖。
* ``raw`` / ``signals`` **剥离一切观测字段**（latency_ms / created_at / run_id /
  generated_at / note / producer 与文本全文），否则同参双跑会漂移，
  违反 01-scope.md G5「双跑逐字节一致」。``decided_at`` 取传入的 ``as_of``
  固定逻辑时间，绝不用 ``datetime.utcnow()``。

────────── 两个 id 的语义（主理人集成裁决，务必区分） ──────────
* ``snapshot_version`` = **被选中的 LLM 配置快照版本**：只由配置字段决定
  （tier / model / prompt / params / 难度信号 / trigger / decided_at）。
  换模型、换提示词、换参数、换档位才该变 → 变则 ``run_id`` 变 → 旧 manifest 不复用。
  **滞回计数属可观测层，写进 route 但不改配置版本**：``reason``（人可读决策原因）
  与 ``hysteresis``（瞬时连续命中计数）不参与配置哈希。否则同参双跑
  （第 1 次 hits=1、第 2 次 hits=2）会让 ``snapshot_version`` 变 → ``run_id`` 变 →
  01-scope.md V5「同参双跑逐字节一致」不成立、manifest 被反复生成新文件。
* ``route_id`` = **决策实例 id**：含 ``reason`` / ``hysteresis`` 全字段，同参连续
  两次决策仍唯一（因为滞回状态不同），用于回放时定位「具体哪一次决策」。
* 两者都不含 ``route_id`` / ``snapshot_version`` 自身，避免循环依赖。
* ``snapshot.py`` 只通过 import 的 ``snapshot_version_for()`` 重算版本（**单一事实源，
  不做第二套哈希**）；另存一份逐字镜像常量，专供跨文件漂移的交叉一致性自测。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 裁决常量（03-phase2-spec.md §1 / 架构 §6）
# 全部为「演示起始值，非压测结论、非实测指标」；不得为了让演示好看而调阈值。
# ---------------------------------------------------------------------------

# 归一化参考值（演示起始值，非压测结论）
PAGE_REF = 20           # 页数归一化：min(1, page_count / PAGE_REF)
CELLS_REF = 100         # 表格单元格归一化：min(1, table_cells_total / CELLS_REF)

# 难度权重 Σ = 1.00（演示起始值，非实测结论、非压测结论）
# 键名即 norm_i 的语义；把「无文本层 / 需要 OCR / 低置信」设为最难信号，
# 与 parsing/ 质量门禁一致。
WEIGHTS: dict[str, float] = {
    "no_text_layer_ratio": 0.30,
    "ocr_needed": 0.25,
    "1-min_confidence": 0.20,
    "table_cells_total": 0.10,
    "page_count": 0.10,
    "1-budget_remaining_ratio": 0.05,
}

# 双阈值滞回（演示起始值，非压测结论）
THETA_ENTER = 0.60      # difficulty >= 该值 → 升档方向命中
THETA_EXIT = 0.40       # difficulty <  该值 → 降档方向命中
K_CONSECUTIVE = 2       # 升 / 降档各需连续 k 次命中，中间带 [exit, enter) 保持当前档

TIERS = ("standard", "pro", "fallback")
FALLBACK_EVENTS = ("offline", "strategy_unavailable", "budget_exhausted",
                   "upstream_timeout")

# 「单文件跟着这次 LLM 选择走」的可降级链（演示起始值；本轮不实际调用，仅留痕）
FALLBACK_CHAINS: dict[str, list[str]] = {
    "standard": ["standard", "fallback"],
    "pro": ["pro", "standard", "fallback"],
    "fallback": ["fallback"],
}

ROUTE_SCHEMA_VERSION = "showcase-route@1.0"

# ── 两个字段集：配置版本 vs 决策实例（主理人集成裁决）──────────────────────
# 都不含 route_id / snapshot_version 自身，避免循环依赖。
#
# ① 配置快照版本字段：决定「被选中的 LLM 配置」。换 model / prompt / params /
#    tier / fallback_chain / trigger / difficulty / signals / decided_at 才该变。
#    **排除 reason 与 hysteresis**：前者是人可读决策原因文案，后者是瞬时滞回计数，
#    都属可观测层，写进 route 但不改配置版本，否则同参双跑 run_id 必变（V5 失败）。
_CONFIG_HASH_FIELDS = (
    "tier", "model", "model_snapshot_at", "prompt", "prompt_version",
    "params", "fallback_chain", "trigger", "cost_hint", "placeholder",
    "difficulty", "signals", "decided_at",
)

# ② 决策实例字段：全部 15 项，含 reason / hysteresis。
#    同参连续两次决策（hits=1 → hits=2、或 standard → pro）在此集合下 route_id 唯一。
_DECISION_HASH_FIELDS = (
    "tier", "model", "model_snapshot_at", "prompt", "prompt_version",
    "params", "fallback_chain", "trigger", "cost_hint", "placeholder",
    "difficulty", "signals", "decided_at",
    "reason", "hysteresis",
)

# snapshot.py 有这两份常量的**逐字镜像**（专供跨文件漂移检测）。
# 运行时哈希只认本模块的单一事实源；镜像常量不参与计算，仅用于自测断言。
# 改动任一字段集必须同步 snapshot.py 的镜像，交叉一致性自测会捕获遗漏。

# 观测字段：从 parse report 取数时必须剥离，保证同参双跑一致（spec §1.1 裁决 4）
_OBSERVATION_FIELDS = frozenset({
    "latency_ms", "created_at", "run_id", "generated_at",
    "note", "producer", "summary", "traces", "text", "blocks", "upstream",
})


# ---------------------------------------------------------------------------
# 稳定哈希（参考 cache/keys.py::policy_version() 的规范化 json + sha256 截断）
# ---------------------------------------------------------------------------

def _stable_hash(payload: Any, *, length: int = 16) -> str:
    """任意 JSON 可序列化对象 → 内容稳定哈希。同输入逐字节一致，无挂钟时间。"""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _hash_payload(route: dict, fields: tuple[str, ...]) -> dict:
    """从 route dict 按**传入字段集**抽值；缺字段按 None 处理，保证可复算。

    ``fields`` 由调用方显式给定：``snapshot_version_for`` 传 ``_CONFIG_HASH_FIELDS``，
    ``_route_id_for`` 传 ``_DECISION_HASH_FIELDS``。两个 id 各自只认自己的字段集，
    这是「配置版本不被滞回计数改变」的落点。
    """
    return {k: route.get(k) for k in fields}


def snapshot_version_for(route: dict) -> str:
    """从 route dict **重算** ``snapshot_version`` = 被选中 LLM 的配置快照版本。

    只由 ``_CONFIG_HASH_FIELDS`` 决定：换 model / prompt / params / tier /
    fallback_chain / trigger / difficulty / signals / decided_at 才变。
    ``reason`` / ``hysteresis`` **不参与** —— 滞回计数属可观测层，写进 route
    但不改配置版本，保证同参双跑 ``snapshot_version`` 一致 → ``run_id`` 一致
    （01-scope.md V5）。``read_manifest()`` 用它复核 manifest 里的值，**不静默回放**。
    """
    return _stable_hash({
        "schema": ROUTE_SCHEMA_VERSION,
        "kind": "snapshot_version",
        **_hash_payload(route, _CONFIG_HASH_FIELDS),
    })


def _route_id_for(route: dict) -> str:
    """决策实例 id：用 ``_DECISION_HASH_FIELDS``（含 reason / hysteresis 全字段）。

    与 ``snapshot_version`` 同源、不同 kind 前缀且字段集更宽，故互不相等；
    同参连续两次决策因滞回状态不同，此 id 仍唯一。
    """
    return "rt-" + _stable_hash({
        "schema": ROUTE_SCHEMA_VERSION,
        "kind": "route_id",
        **_hash_payload(route, _DECISION_HASH_FIELDS),
    })


# ---------------------------------------------------------------------------
# 难度信号（6 项 + raw + notes；frozen dataclass）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DifficultySignals:
    """全部来自 ``ParseRouter.parse_document()`` 的**可复算**字段，零挂钟时间。

    三态：字段本身【已实现】（真实产物可算）；权重与参考值【演示起始值】。
    ``raw`` 只保留逐页可复算信号与派生量，观测字段已在取数时剥离。
    """

    page_count: int
    no_text_layer_ratio: float          # ocr_needed 命中页数 / 总页数，0..1
    table_cells_total: int              # sum(signals.table_cells)
    min_confidence: float               # min(signals.confidence)，0..1
    ocr_needed: bool                    # 任一页 ocr_needed
    budget_remaining_ratio: float       # VisionBudget.remaining / slots，0..1
    raw: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """深拷贝返回：frozen dataclass 只锁属性重绑定，不锁内部 dict 内容。"""
        return {
            "page_count": self.page_count,
            "no_text_layer_ratio": round(self.no_text_layer_ratio, 6),
            "table_cells_total": self.table_cells_total,
            "min_confidence": round(self.min_confidence, 6),
            "ocr_needed": self.ocr_needed,
            "budget_remaining_ratio": round(self.budget_remaining_ratio, 6),
            "raw": copy.deepcopy(self.raw),
            "notes": list(self.notes),
        }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def from_parse_report(rep: dict) -> DifficultySignals:
    """从 ``ParseRouter.parse_document()`` 的真实 dict 产物取 6 项难度信号。

    取数路径（实查 parsing/router.py:333-345、parsing/contracts.py:49-59）：
      * ``page_count``            ← rep["page_count"]
      * ``no_text_layer_ratio``   ← 命中 results[].signals.ocr_needed 的页数 / 总页数
      * ``table_cells_total``     ← sum(results[].signals.table_cells)
      * ``min_confidence``        ← min(results[].signals.confidence)
      * ``ocr_needed``            ← 任一页 signals.ocr_needed 为真
      * ``budget_remaining_ratio``← rep["budget"]["remaining"] / ["slots"]

    边界：空 results / slots=0 / 缺字段一律给确定性的安全默认值，绝不抛异常，
    也绝不引入挂钟时间；这样两档样本与真实在线产物都能稳定喂进来。
    """
    rep = rep or {}
    # 先剥离顶层观测字段（挂钟耗时 / 轨迹 / 说明文本），只保留可复算结构。
    # 取数仅依赖 page_count / budget / results[].signals，这些都不在剥离集里；
    # 这一步是「同参双跑一致」的显式防御，也让剥离清单不是只写在注释里的死代码。
    rep = {k: v for k, v in rep.items() if k not in _OBSERVATION_FIELDS}
    results = rep.get("results") or []
    page_count = max(0, _safe_int(rep.get("page_count"), len(results)))
    # 分母用「实际页结果数」与 page_count 的较大者，避免样本里两者不一致时比值 >1
    denom = max(1, page_count, len(results))

    pages: list[dict] = []
    ocr_hits = 0
    cells_total = 0
    confidences: list[float] = []
    any_ocr = False
    for i, result in enumerate(results, start=1):
        sig = (result or {}).get("signals") or {}
        ocr = bool(sig.get("ocr_needed", False))
        cells = max(0, _safe_int(sig.get("table_cells"), 0))
        conf = _clip01(_safe_float(sig.get("confidence"), 1.0))
        ocr_hits += 1 if ocr else 0
        any_ocr = any_ocr or ocr
        cells_total += cells
        confidences.append(conf)
        # 逐页只保留可复算字段；不写 text / blocks / latency_ms 等观测项
        pages.append({
            "page_no": _safe_int(sig.get("page_no"), i),
            "ocr_needed": ocr,
            "table_cells": cells,
            "confidence": round(conf, 6),
            "text_chars": max(0, _safe_int(sig.get("text_chars"), 0)),
            "image_ratio": round(_clip01(_safe_float(sig.get("image_ratio"), 0.0)), 6),
        })

    min_confidence = min(confidences) if confidences else 1.0
    budget = rep.get("budget") or {}
    slots = max(0, _safe_int(budget.get("slots"), 0))
    remaining = max(0, _safe_int(budget.get("remaining"), 0))
    # slots=0（无视觉预算）时按「余量充足」处理，避免除零与虚高难度
    budget_remaining_ratio = _clip01(remaining / slots) if slots > 0 else 1.0

    raw = {
        "page_count": page_count,
        "pages": pages,
        "budget": {
            "total_pages": _safe_int(budget.get("total_pages"), page_count),
            "ratio": round(_clip01(_safe_float(budget.get("ratio"), 0.0)), 6),
            "slots": slots,
            "used": max(0, _safe_int(budget.get("used"), 0)),
            "remaining": remaining,
        },
        "derived": {
            "ocr_needed_pages": ocr_hits,
            "table_cells_total": cells_total,
            "min_confidence": round(min_confidence, 6),
            "any_ocr_needed": any_ocr,
            "budget_remaining_ratio": round(budget_remaining_ratio, 6),
        },
    }
    notes = (
        "source=ParseRouter.parse_document() -> QualitySignals.as_dict() / VisionBudget.as_dict()",
        "观测字段(latency_ms/created_at/run_id/generated_at/note/producer/text/blocks)已剥离，保证同参双跑一致",
        f"PAGE_REF={PAGE_REF} CELLS_REF={CELLS_REF}（演示起始值，非压测结论）",
        f"no_text_layer_ratio=ocr_needed 命中页数/总页数，分母={denom}",
        "slots=0 时 budget_remaining_ratio 按 1.0 处理（防除零，不虚增难度）",
    )
    return DifficultySignals(
        page_count=page_count,
        no_text_layer_ratio=_clip01(ocr_hits / denom),
        table_cells_total=cells_total,
        min_confidence=min_confidence,
        ocr_needed=any_ocr,
        budget_remaining_ratio=budget_remaining_ratio,
        raw=raw,
        notes=notes,
    )


def difficulty_score(s: DifficultySignals) -> float:
    """``difficulty = Σ w_i × norm_i``，Σ w_i = 1.00。

    归一化（架构 §6.1 / spec §1，全部演示起始值、非压测结论）：
      no_text_layer_ratio → 0..1 原样；ocr_needed → 1.0/0.0；
      1-min_confidence → 1 - min_confidence；table_cells_total → min(1, cells/CELLS_REF)；
      page_count → min(1, pages/PAGE_REF)；1-budget_remaining_ratio → 1 - ratio。
    round(..., 6) 只是消除浮点尾差，保证同参双跑逐字节一致，不是调阈值。
    """
    norms = {
        "no_text_layer_ratio": _clip01(s.no_text_layer_ratio),
        "ocr_needed": 1.0 if s.ocr_needed else 0.0,
        "1-min_confidence": _clip01(1.0 - s.min_confidence),
        "table_cells_total": _clip01(s.table_cells_total / CELLS_REF),
        "page_count": _clip01(s.page_count / PAGE_REF),
        "1-budget_remaining_ratio": _clip01(1.0 - s.budget_remaining_ratio),
    }
    return round(sum(WEIGHTS[k] * norms[k] for k in WEIGHTS), 6)


# ---------------------------------------------------------------------------
# RouteSnapshot（spec §2 签名的 17 个字段，逐字遵守；frozen dataclass）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteSnapshot:
    """一次 LLM 路由选择的完整快照，**跟着这一个 LLM 选择走**。

    占位纪律（spec §1.1 裁决 1 / 架构 §6.3 / 01-scope.md V7·K2）：
    本轮演示一律离线、不发起外部 LLM 调用，故 ``model=""``、
    ``model_snapshot_at=None``、``prompt=""``、``prompt_version=""``、
    ``params={}``、``placeholder=True``、``cost_hint`` 固定说明「仅路由决策」。
    **绝不填编造的模型名、提示词或快照时间**。
    其余 tier / trigger / difficulty / signals / hysteresis / fallback_chain /
    route_id / snapshot_version / reason / decided_at 全是真实可复算值。
    """

    route_id: str
    tier: str
    model: str
    model_snapshot_at: str | None
    snapshot_version: str
    prompt: str
    prompt_version: str
    params: dict
    fallback_chain: list[str]
    trigger: str
    cost_hint: dict
    placeholder: bool
    reason: str
    difficulty: float
    signals: dict
    hysteresis: dict
    decided_at: str

    def as_dict(self) -> dict:
        """深拷贝返回，且顺序固定（前端展开 17 字段时逐字段可见）。"""
        return {
            "route_id": self.route_id,
            "tier": self.tier,
            "model": self.model,
            "model_snapshot_at": self.model_snapshot_at,
            "snapshot_version": self.snapshot_version,
            "prompt": self.prompt,
            "prompt_version": self.prompt_version,
            "params": copy.deepcopy(self.params),
            "fallback_chain": list(self.fallback_chain),
            "trigger": self.trigger,
            "cost_hint": copy.deepcopy(self.cost_hint),
            "placeholder": self.placeholder,
            "reason": self.reason,
            "difficulty": self.difficulty,
            "signals": copy.deepcopy(self.signals),
            "hysteresis": copy.deepcopy(self.hysteresis),
            "decided_at": self.decided_at,
        }


# ---------------------------------------------------------------------------
# 三档状态机（双阈值滞回 + 连续 k 次 + 事件直接触发 + override 强制档）
# ---------------------------------------------------------------------------

class TierState:
    """跨调用保持档位与连续命中计数；每次 ``decide()`` 产出一份 RouteSnapshot。

    滞回规则（架构 §6.2，全部演示起始值、非压测结论）：
      * ``difficulty >= THETA_ENTER`` → 升档方向命中；
      * ``difficulty <  THETA_EXIT``  → 降档方向命中；
      * 中间带 ``[THETA_EXIT, THETA_ENTER)`` → **保持当前档并清零连续计数**；
      * 升 / 降档各需连续 ``K_CONSECUTIVE`` 次同方向命中；
      * ``fallback`` 由 ``event`` 直接触发、忽略计数；事件解除后回 standard 重新计数。
    """

    def __init__(self, tier: str = "standard"):
        if tier not in TIERS:
            raise ValueError(f"INVALID_PARAM: unknown initial tier {tier!r}")
        self._tier = tier
        self._direction: str | None = None   # "up" | "down" | None
        self._hits = 0                       # 当前方向连续命中次数
        self._last_transition: str | None = None

    @property
    def tier(self) -> str:
        return self._tier

    def _hit(self, direction: str) -> int:
        """同方向累加、换方向重置为 1；返回本次累计命中数。"""
        if self._direction == direction:
            self._hits += 1
        else:
            self._direction = direction
            self._hits = 1
        return self._hits

    def _hold(self) -> None:
        """中间带：保持当前档，并打断「连续」语义。"""
        self._direction = None
        self._hits = 0

    def _transition(self, new_tier: str, as_of: str) -> None:
        if new_tier != self._tier:
            self._tier = new_tier
            self._last_transition = as_of   # as_of 是固定逻辑时间，不是挂钟

    def decide(self, signals: DifficultySignals, *, override: str | None,
               as_of: str, offline: bool, event: str | None = None) -> RouteSnapshot:
        """做一次路由决策（纯计算，不发任何外部请求、不落盘）。

        ``offline`` 只决定占位与说明文案，**不会自动触发 fallback**；
        只有显式 ``event``（offline / strategy_unavailable / budget_exhausted /
        upstream_timeout）才「忽略计数、直接 fallback」。详见模块 docstring。
        """
        if override is not None and override not in TIERS:
            raise ValueError(f"INVALID_PARAM: unknown llm_tier_override {override!r}")
        if event is not None and event not in FALLBACK_EVENTS:
            raise ValueError(f"INVALID_PARAM: unknown route event {event!r}")

        difficulty = difficulty_score(signals)
        prev_tier = self._tier
        trigger = "difficulty_threshold"
        notes: list[str] = []

        # ① 事件优先：直接 fallback，忽略滞回计数
        if event is not None:
            self._hold()
            self._transition("fallback", as_of)
            trigger = f"event:{event}"
            notes.append(f"事件 {event} 直接触发 fallback，忽略连续命中计数")

        # ② override 强制档：不参与计数，但留痕（强制档与自动档产生不同 run_id）
        elif override is not None:
            self._hold()
            self._transition(override, as_of)
            trigger = "override"
            notes.append(f"llm_tier_override={override} 强制档位")

        # ③ 正常滞回状态机
        else:
            # 事件解除：从 fallback 回 standard 重新计数（架构 §6.2「事件优先」条）
            if self._tier == "fallback":
                self._transition("standard", as_of)
                self._hold()
                notes.append("上一次 fallback 事件已解除，回 standard 重新计数")

            if difficulty >= THETA_ENTER:
                hits = self._hit("up")
                notes.append(
                    f"difficulty={difficulty} >= θ_enter={THETA_ENTER}，"
                    f"升档方向连续命中 {hits}/{K_CONSECUTIVE}"
                )
                if hits >= K_CONSECUTIVE:
                    self._transition("pro", as_of)
                    notes.append(f"连续命中达 {K_CONSECUTIVE}，切到 pro 档")
                else:
                    notes.append("连续命中未满，保持当前档")
            elif difficulty < THETA_EXIT:
                hits = self._hit("down")
                notes.append(
                    f"difficulty={difficulty} < θ_exit={THETA_EXIT}，"
                    f"降档方向连续命中 {hits}/{K_CONSECUTIVE}"
                )
                if hits >= K_CONSECUTIVE:
                    self._transition("standard", as_of)
                    notes.append(f"连续命中达 {K_CONSECUTIVE}，回 standard 档")
                else:
                    notes.append("连续命中未满，保持当前档")
            else:
                self._hold()
                notes.append(
                    f"difficulty={difficulty} 落在中间带 "
                    f"[{THETA_EXIT}, {THETA_ENTER})，保持当前档并清零连续计数"
                )

        tier = self._tier
        if tier != prev_tier:
            notes.append(f"档位变化 {prev_tier} -> {tier}")
        if offline:
            notes.append(
                "离线演示：本轮不发起外部 LLM 调用，model/prompt/params 为空占位；"
                "「选出 Pro 档」不等于「调用了 Pro 模型」"
            )

        hysteresis = {
            "enter": THETA_ENTER,
            "exit": THETA_EXIT,
            "consecutive_hits": self._hits,
            "state": tier,
            "last_transition": self._last_transition,
        }

        # 先组装除两个 id 外的全部字段（都是真实可复算值 / 空占位）
        route: dict[str, Any] = {
            "route_id": "",                        # 占位，下面由内容哈希回填
            "tier": tier,
            "model": "",                          # 空占位：未选定供应商，绝不编造型号
            "model_snapshot_at": None,            # 空占位：本轮无快照时间
            "snapshot_version": "",               # 占位，下面由内容哈希回填
            "prompt": "",                         # 空占位
            "prompt_version": "",                 # 空占位
            "params": {},                         # 空占位
            "fallback_chain": list(FALLBACK_CHAINS[tier]),
            "trigger": trigger,
            "cost_hint": {
                "estimated": False,
                "note": "仅路由决策，未发起外部调用",
            },
            "placeholder": True,
            "reason": "；".join(notes),
            "difficulty": difficulty,
            "signals": signals.as_dict(),
            "hysteresis": hysteresis,
            "decided_at": as_of,                  # 固定逻辑时间，非挂钟
        }
        route["snapshot_version"] = snapshot_version_for(route)
        route["route_id"] = _route_id_for(route)

        return RouteSnapshot(**route)
