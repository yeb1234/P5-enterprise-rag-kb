"""showcase 参数模型：7 类参数 + 稳定快照哈希。

唯一事实源：docs/showcase/03-phase2-spec.md §1 / §2（默认值与签名逐字遵守）。
三态：已实现（本模块，纯标准库）。

三条纪律：
1. 所有默认值是「演示起始值，非压测结论、非实测指标」，不得对外称已校准；
2. `as_of` 是**固定逻辑时间常量** `DEFAULT_AS_OF`，绝不用 datetime.utcnow()/time.time()，
   否则同参双跑的入库 JSON 不可能逐字节一致（G5 / V5）；
3. 本模块**不 import 任何同伴模块**（llm_router / snapshot / ingest_preview / guard），
   因此可以在同伴文件尚未落盘时单独 import 验证。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

DEFAULT_AS_OF = "2026-11-15T00:00:00Z"

# 与 parsing/contracts.py::STRATEGIES 逐字一致。本地定义是为了保持 params.py 零依赖，
# 不是另定一套口径；若上游策略集合变化，必须同步此常量并更新 PARAM_RANGES。
ALLOWED_STRATEGIES = ("auto", "local_only", "ocr_api", "multimodal", "off")
ALLOWED_TIERS = ("standard", "pro", "fallback")
ALLOWED_INGEST_FORMATS = ("compact", "full")

_AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class Params:
    """7 类参数（01-scope.md §4）+ 确定性逻辑时间。frozen 保证快照不可被就地改写。"""

    strategy: str = "auto"
    confidence_min: float = 0.75
    chunk_size_chars: int = 800
    chunk_overlap_chars: int = 80
    vision_escalation_ratio: float = 0.10
    vision_min_slots: int = 1
    llm_tier_override: str | None = None
    redact_enabled: bool = True
    ingest_format: str = "compact"
    as_of: str = DEFAULT_AS_OF

    def as_dict(self) -> dict:
        """字段顺序由 dataclass 定义固定；哈希另做 sort_keys 规范化。"""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def params_hash(self) -> str:
        """规范化 json → sha256[:16]。同参必同哈希，改任一参数必变（G5）。"""
        raw = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


PARAM_KEYS: tuple[str, ...] = tuple(f.name for f in fields(Params))

# A2 「取值范围」只给枚举与上下界，不给实测值；前端据此渲染输入框。
PARAM_RANGES: dict[str, dict[str, Any]] = {
    "strategy": {"type": "enum", "values": list(ALLOWED_STRATEGIES), "default": "auto"},
    "confidence_min": {"type": "float", "min": 0.0, "max": 1.0, "default": 0.75,
                       "note": "质量门禁阈值，演示起始值，非实测"},
    "chunk_size_chars": {"type": "int", "min": 1, "max": 65536, "default": 800},
    "chunk_overlap_chars": {"type": "int", "min": 0, "max": 65535, "default": 80,
                            "note": "必须严格小于 chunk_size_chars"},
    "vision_escalation_ratio": {"type": "float", "min": 0.0, "max": 1.0,
                                "default": 0.10, "note": "熔断上限，不是随机分流"},
    "vision_min_slots": {"type": "int", "min": 0, "max": 10000, "default": 1},
    "llm_tier_override": {"type": "enum|null", "values": list(ALLOWED_TIERS),
                          "default": None, "note": "null = 自动；只影响路由决策，不发起外部调用"},
    "redact_enabled": {"type": "bool", "default": True},
    "ingest_format": {"type": "enum", "values": list(ALLOWED_INGEST_FORMATS),
                      "default": "compact"},
    "as_of": {"type": "iso8601_utc", "default": DEFAULT_AS_OF,
              "note": "固定逻辑时间，格式 YYYY-MM-DDTHH:MM:SSZ；禁挂钟时间"},
}


def _invalid(msg: str) -> ValueError:
    return ValueError(f"INVALID_PARAM: {msg}")


def _number(name: str, value: Any, *, lo: float, hi: float, integer: bool) -> float | int:
    # bool 是 int 的子类，必须显式排除，避免 True 被当成 1 通过校验
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{name} 必须是{'整数' if integer else '数值'}")
    if integer:
        if isinstance(value, float) and not value.is_integer():
            raise _invalid(f"{name} 必须是整数")
        out: float | int = int(value)
    else:
        out = float(value)
    if not (lo <= out <= hi):
        raise _invalid(f"{name} 越界：{out} 不在 [{lo}, {hi}]")
    return out


def parse_params(raw: dict | None) -> Params:
    """把请求里的参数子集解析成 Params；越界/未知值一律 ValueError("INVALID_PARAM: ...")。

    A3 传入的是「当前值 + 待改子集」的合并结果；A5 传入的是 multipart 里的
    params JSON 字符串解析结果。两者共用这一处校验，避免两套口径。
    """
    if raw is None:
        return Params()
    if not isinstance(raw, dict):
        raise _invalid("期望 JSON 对象")

    unknown = [k for k in raw if k not in PARAM_KEYS]
    if unknown:
        raise _invalid(f"未知参数：{', '.join(sorted(map(str, unknown)))}")

    base = Params().as_dict()
    merged = {**base, **raw}

    strategy = merged["strategy"]
    if strategy not in ALLOWED_STRATEGIES:
        raise _invalid(f"strategy 未知值 {strategy!r}，允许 {ALLOWED_STRATEGIES}")

    confidence_min = _number("confidence_min", merged["confidence_min"],
                             lo=0.0, hi=1.0, integer=False)
    chunk_size = _number("chunk_size_chars", merged["chunk_size_chars"],
                         lo=1, hi=65536, integer=True)
    chunk_overlap = _number("chunk_overlap_chars", merged["chunk_overlap_chars"],
                            lo=0, hi=65535, integer=True)
    if int(chunk_overlap) >= int(chunk_size):
        raise _invalid("chunk_overlap_chars 必须严格小于 chunk_size_chars")
    vision_ratio = _number("vision_escalation_ratio", merged["vision_escalation_ratio"],
                           lo=0.0, hi=1.0, integer=False)
    vision_slots = _number("vision_min_slots", merged["vision_min_slots"],
                           lo=0, hi=10000, integer=True)

    tier = merged["llm_tier_override"]
    if tier is not None and tier not in ALLOWED_TIERS:
        raise _invalid(f"llm_tier_override 未知值 {tier!r}，允许 {ALLOWED_TIERS} 或 null")

    redact = merged["redact_enabled"]
    if not isinstance(redact, bool):
        raise _invalid("redact_enabled 必须是布尔值")

    ingest_format = merged["ingest_format"]
    if ingest_format not in ALLOWED_INGEST_FORMATS:
        raise _invalid(f"ingest_format 未知值 {ingest_format!r}，允许 {ALLOWED_INGEST_FORMATS}")

    as_of = merged["as_of"]
    if not isinstance(as_of, str) or not _AS_OF_RE.match(as_of):
        raise _invalid("as_of 必须是 YYYY-MM-DDTHH:MM:SSZ（ISO-8601 UTC，Z 结尾）")
    try:
        datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise _invalid(f"as_of 不是合法 UTC 时间：{exc}") from exc

    return Params(
        strategy=str(strategy),
        confidence_min=float(confidence_min),
        chunk_size_chars=int(chunk_size),
        chunk_overlap_chars=int(chunk_overlap),
        vision_escalation_ratio=float(vision_ratio),
        vision_min_slots=int(vision_slots),
        llm_tier_override=tier,
        redact_enabled=redact,
        ingest_format=str(ingest_format),
        as_of=as_of,
    )
