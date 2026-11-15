"""供应商 profile 注册表（静态，只设计）。

`vendor_profiles()` 是 spec §2 钉死的公开符号，`showcase/api.py` 的 A4 只 import 它。
全部 `available=False`、未填字段 `__TBC__`、**不发起任何外部请求**。
"""
from __future__ import annotations

from showcase.adapters.aliyun_docmind import PROFILE as ALIYUN_DOCMIND
from showcase.adapters.base import VendorProfile
from showcase.adapters.local_free import PROFILE as LOCAL_FREE
from showcase.adapters.reducto import PROFILE as REDUCTO

VENDOR_REGISTRY: tuple[VendorProfile, ...] = (
    REDUCTO,
    ALIYUN_DOCMIND,
    LOCAL_FREE,
)


def vendor_profiles() -> list[dict]:
    """每个 profile: {"id","region","price_hint","available":False,
    "contract":{...__TBC__...},"note"}；含 reducto / aliyun_docmind / local_free。

    价格一律带「公开价、非账单、以控制台实时价为准」声明，见 base.PRICE_DISCLAIMER。
    """
    return [p.as_dict() for p in VENDOR_REGISTRY]


__all__ = ["VENDOR_REGISTRY", "VendorProfile", "vendor_profiles"]
