"""Wiki 页面的目录分组。与 LLM Wiki 的定位一一对应：概念、流程、规则、经验台账。

模型构建页面时 `section` 必须落在这四类里；不在这四类里的归入「业务规则」，
因为「拿不准它属于什么」的结论，按规则对待最保守（规则会被更严格地核对证据）。
"""
from __future__ import annotations

SECTIONS: tuple[str, ...] = ("业务概念", "业务流程", "业务规则", "排障台账")
DEFAULT_SECTION = "业务规则"

MAX_TITLE = 80
MAX_BODY = 4000
MAX_STATEMENT = 300
