"""供应商 profile 契约（静态描述，只设计，不调用）。

唯一事实源：docs/showcase/03-phase2-spec.md §1.1(6)、02-architecture.md §3.1/§3.3。

硬约束：
- 本包**不发起任何外部请求**，不读 .env 的密钥值，不修改
  `parsing/strategies/ocr_api.py::_call()`（保持 `__TBC__`）。
- 所有 profile `available=False`；未填字段一律 `__TBC__`，不编造 endpoint / 单价 / 契约。
- 价格只写 2026-11-15 联网调研到的**公开价**，必须带
  「公开价、非账单、以控制台实时价为准」声明（PRICE_DISCLAIMER）。
- `to_parse_result()` 只是响应 → ParseResult 的映射骨架，真实接通属 Phase 2 之后单独评审。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TBC = "__TBC__"
PRICE_DISCLAIMER = "公开价、非账单、以控制台实时价为准"


@dataclass(frozen=True)
class VendorProfile:
    """02-architecture.md §3.1 要求的契约字段。

    id / region / price_hint / endpoint / auth / request_schema / response_schema /
    to_parse_result()；额外提供 available / note / as_dict() 供 A4 展示。
    """

    id: str
    region: str
    price_hint: str
    note: str
    endpoint: str = TBC
    auth: str = TBC
    request_schema: str = TBC
    response_schema: str = TBC
    available: bool = False

    def contract(self) -> dict[str, str]:
        """保持 `__TBC__`，与 parsing/strategies/ocr_api.py::contract() 口径一致。"""
        return {
            "endpoint": self.endpoint,
            "auth": self.auth,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
        }

    def as_dict(self) -> dict[str, Any]:
        """A4 `vendors[]` 与 `vendor_profiles()` 的返回形状（spec §2）。"""
        return {
            "id": self.id,
            "region": self.region,
            "price_hint": self.price_hint,
            "available": self.available,
            "contract": self.contract(),
            "note": self.note,
        }

    def to_parse_result(self, response: dict) -> dict:
        """响应 → `parsing.contracts.ParseResult` 的映射骨架（只设计）。

        刻意不实现：本轮没有凭证、没有真实响应样本，任何字段映射都会是猜测。
        `available=False` 时调用即抛 NotImplementedError，避免「静默当成已接通」。
        """
        if not self.available:
            raise NotImplementedError(
                f"{self.id} 未接通（available=False）：to_parse_result 只设计，"
                "不发起外部请求，也不编造响应字段"
            )
        raise NotImplementedError(
            f"{self.id} 的响应映射骨架待单独评审（Phase 2 之后），本轮未实现"
        )
