"""阿里云文档解析（大模型版）静态 profile（国内上线首选，只设计、未接通）。

公开价来源：docs/showcase/02-architecture.md §2.3 主理人调研口径
（基础 0.02 元/页、增强 0.04 元/页；免费额度 `__TBC__`）。
本项目未开通凭证、未部署、未做独立实测，不声称「已上线」或「已接入」。
"""
from __future__ import annotations

from showcase.adapters.base import PRICE_DISCLAIMER, TBC, VendorProfile

PROFILE = VendorProfile(
    id="aliyun_docmind",
    region="中国大陆 / 同账号计费",
    price_hint=(
        f"基础 0.02 元/页、增强 0.04 元/页；免费额度 {TBC}｜{PRICE_DISCLAIMER}"
    ),
    note=(
        "只设计：可插拔 profile，available=False；未开通凭证、未接通 _call()，"
        "契约字段与免费额度保持 __TBC__。复杂表格能力未独立实测，"
        "不声称等同 Reducto。注册 / 实名 / 备案 / 支付归属由用户本人完成，"
        "本项目只给步骤，不承诺已上线。"
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
