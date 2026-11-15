"""PII 检测器：格式 → 校验位 → 上下文 → 置信度的四级判定链。

为什么不能只写正则（面试最常被追问的误报问题）：
- `\\d{17}[\\dXx]` 会把 18 位订单号 / 流水号误判成身份证。所以必须校验
  出生日期段 + ISO 7064 MOD 11-2 校验位；不通过的一律降级为低置信度并跳过。
- `1[3-9]\\d{9}` 会命中 13 位长号中间的子串。所以必须加前后数字边界断言。
- 16–19 位纯数字可能是银行卡，也可能是订单号。所以要过 Luhn 校验。
- 中文姓名、护照号、工号几乎没有稳定的文本特征，必须靠上下文关键词门控，
  且只给中等置信度；不做「看见两个汉字就当姓名」。

规则版本 `PII-RULES@1.0` 会随每条命中回传，规则变更能回放比对。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from guardrails.contracts import PIIFinding, RULE_VERSION

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _valid_id18(s: str) -> bool:
    if len(s) != 18 or not s[:17].isdigit():
        return False
    try:
        datetime.strptime(s[6:14], "%Y%m%d")
    except ValueError:
        return False
    total = sum(int(c) * w for c, w in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == s[17].upper()


def _valid_id15(s: str) -> bool:
    if len(s) != 15 or not s.isdigit():
        return False
    try:
        datetime.strptime("19" + s[6:12], "%Y%m%d")
    except ValueError:
        return False
    return True


def _luhn(s: str) -> bool:
    total, alt = 0, False
    for ch in reversed(s):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _valid_ipv4(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


@dataclass(frozen=True)
class Rule:
    kind: str
    label: str
    pattern: re.Pattern
    confidence: float
    group: int = 0
    validator: Callable[[str], bool] | None = None
    require_context: tuple[str, ...] = ()
    note: str = ""


RULES: tuple[Rule, ...] = (
    Rule("API_KEY", "API 密钥 / Token",
         # 真实供应商密钥常带分段连字符或下划线（sk-proj-… / sk-or-v1-… /
         # sk-<TENANT>-…）。字符集只写 [A-Za-z0-9] 会在第一个连字符处截断，
         # 导致整条密钥漏检并被原文回显；故把 - 与 _ 纳入。
         # 首字符仍限定字母数字，总长 ≥16，避免把普通短标识误判成密钥。
         re.compile(r"(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{15,}|AKIA[0-9A-Z]{16}"
                    r"|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"),
         1.0, note="密钥命中默认 block，不进任何日志；含连字符/下划线分段形态"),
    Rule("ID_CARD_CN", "中国身份证（18 位）",
         re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), 1.0, validator=_valid_id18,
         note="含出生日期合法性 + MOD 11-2 校验位，避免把订单号误判"),
    Rule("ID_CARD_CN_15", "中国身份证（15 位旧版）",
         re.compile(r"(?<!\d)\d{15}(?!\d)"), 0.65, validator=_valid_id15,
         require_context=("身份证", "证件", "ID", "身份"),
         note="旧版无校验位，必须靠上下文关键词门控"),
    Rule("PHONE_CN", "中国大陆手机号",
         re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), 0.95,
         note="前后数字边界断言，避免命中长号子串"),
    Rule("BANK_CARD", "银行卡号",
         re.compile(r"(?<!\d)\d{16,19}(?![\dXx])"), 0.85, validator=_luhn,
         note="Luhn 校验；末尾紧跟 X/x 说明可能是身份证校验位，交给身份证规则，不误报成银行卡"),
    Rule("EMAIL", "邮箱地址",
         re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), 0.95),
    Rule("IPV4", "IPv4 地址",
         re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), 0.8,
         validator=_valid_ipv4, note="逐段 0–255 校验"),
    Rule("MAC", "MAC 地址",
         re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}"), 0.9),
    Rule("PASSPORT", "护照 / 证件号",
         re.compile(r"(?:护照|通行证|passport|Passport)\s*(?:号|号码|No\.?|ID)?\s*[:：]?\s*([A-Za-z0-9]{6,12})"),
         0.75, group=1, note="上下文关键词门控，只取捕获组"),
    Rule("EMPLOYEE_ID", "工号 / 员工编号",
         re.compile(r"(?:工号|员工编号|员工ID|EMP|STAFF|staff_id)\s*[:：#\-]?\s*([A-Za-z0-9-]{4,16})"),
         0.8, group=1, note="用户特别关注的 ID 类信息，靠字段名定位"),
    Rule("NAME_CN", "中文姓名",
         re.compile(r"(?:姓名|联系人|客户姓名|收件人)\s*[:：]\s*([\u4e00-\u9fff·]{2,6})"),
         0.7, group=1, note="仅识别带字段名的姓名，不对任意中文串做姓名猜测"),
)

CONTEXT_WINDOW = 40


def _has_context(text: str, start: int, keywords: tuple[str, ...]) -> bool:
    left = text[max(0, start - CONTEXT_WINDOW):start].lower()
    right = text[start:start + CONTEXT_WINDOW].lower()
    return any(k.lower() in left or k.lower() in right for k in keywords)


def scan(text: str) -> list[PIIFinding]:
    """扫描文本，返回去重后的命中列表（按位置排序）。"""
    if not text:
        return []
    found: list[PIIFinding] = []
    for rule in RULES:
        for m in rule.pattern.finditer(text):
            raw = m.group(rule.group)
            if not raw:
                continue
            start, end = m.start(rule.group), m.end(rule.group)
            if rule.require_context and not _has_context(text, start, rule.require_context):
                continue
            if rule.validator and not rule.validator(raw):
                continue
            found.append(PIIFinding(
                kind=rule.kind, start=start, end=end, value=raw,
                confidence=rule.confidence, rule=RULE_VERSION, note=rule.note,
            ))
    # 重叠消解：长命中优先、同长度高置信度优先；避免同一串数字被报成多种类型
    found.sort(key=lambda f: (f.start, -(f.end - f.start), -f.confidence))
    kept: list[PIIFinding] = []
    for f in found:
        if any(f.start < k.end and k.start < f.end for k in kept):
            continue
        kept.append(f)
    kept.sort(key=lambda f: f.start)
    return kept


def rule_catalog() -> list[dict]:
    return [{"kind": r.kind, "label": r.label, "confidence": r.confidence,
             "note": r.note, "validator": r.validator.__name__ if r.validator else "",
             "context_gated": bool(r.require_context)} for r in RULES]
