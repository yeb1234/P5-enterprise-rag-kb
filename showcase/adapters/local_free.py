"""免费自搭路径 profile（指向 parsing/strategies/local.py，只描述、不调用）。

注意：这里的 `available=False` 是 **vendor_profiles() 契约要求**（A4 `vendors`
全 False，表示「站点不通过 vendor 适配器发起调用」），不是说本机解析不可用。
真实可用的 P0 解析能力由 `parsing.ParseRouter` 直接提供，A5 走的是这条路径；
本 profile 只是把它登记成一条「免费路径」，便于讲解「A 免费自搭 / B 收费适配器」两条路径。
"""
from __future__ import annotations

from showcase.adapters.base import TBC, VendorProfile

PROFILE = VendorProfile(
    id="local_free",
    region="本机 / 离线",
    price_hint="0（标准库 + 可选 pypdf / Pillow，无外部计费，非账单口径）",
    note=(
        "已实现（解析资产）：parsing/strategies/local.py::LocalStrategy.parse，"
        "由 parsing.ParseRouter 在 PARSING_OFFLINE=1 下直接调用，外部请求计数 0。"
        "本 profile 只登记路径、不由站点调用；复杂档视觉结果为离线回放，非实时在线识别。"
    ),
    endpoint="parsing/strategies/local.py::LocalStrategy.parse",
    auth="不需要（同进程调用，无凭证）",
    request_schema="payload: bytes + mime + page_no + context（见 parsing/strategies/base.py）",
    response_schema="parsing.contracts.ParseResult",
    available=False,
)


def profile() -> dict:
    """返回 A4 `vendors[]` 用的 profile 字典。"""
    return PROFILE.as_dict()
