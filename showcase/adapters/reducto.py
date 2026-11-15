"""Reducto 静态 profile（海外 / 效果对照首选，只设计、未接通）。

公开价来源：docs/showcase/02-architecture.md §2.3 主理人调研口径（$0.01/页、
Standard 免费额度 $150 约 15,000 页）。本项目未开通凭证、未做独立质量实测，
不声称其复杂表格能力等同或优于任何国内服务。
"""
from __future__ import annotations

from showcase.adapters.base import PRICE_DISCLAIMER, TBC, VendorProfile

PROFILE = VendorProfile(
    id="reducto",
    region="海外 / 境外 API",
    price_hint=f"$0.01/页；Standard 免费额度 $150（约 15,000 页）｜{PRICE_DISCLAIMER}",
    note=(
        "只设计：可插拔 profile，available=False；未开通凭证、未接通 _call()，"
        "契约字段保持 __TBC__。公开资料口径，本项目未独立实测质量。"
        "境外 API 不作国内上线首选（合规与可达性）。"
    ),
    endpoint=TBC,
    auth=TBC,
    request_schema=TBC,
    response_schema=TBC,
    available=False,
)


def profile() -> dict:
    """返回 A4 `vendors[]` 用的 profile 字典。"""
    return PROFILE.as_dict()
