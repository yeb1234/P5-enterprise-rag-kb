"""事实断言（claim）抽取：规则级、纯标准库、可复算。

为什么不直接让大模型读两段判断矛盾：
- 成本高、延迟大，且模型输出不能自动裁决谁胜出（只能当告警线索）；
- 数字、日期、版本、状态这类结构化事实，用字段解析 + 归一化比模型更可靠；
- 规则级的每一步都能在前端展示，面试官追问「怎么防误报」时有具体答案。

防误报（来自 2026-11-14 调研）：
- 年份 1900–2099 且无单位 → 不作为数值 claim（否则两个文档的日期会被当冲突）；
- 裸小整数 1–9 且无单位 → 标 low_confidence，不单独触发冲突；
- 单位、否定词、适用范围必须原样保留，归一化只用于「比对」不改原文；
- 每条 claim 都保留支持片段 `text` 与 `chunk_id`，引用能回溯。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from knowledge.contracts import Claim

# 赋值句式：主语 + 关系词 + 值
_ASSIGN = re.compile(
    r"^\s*(?:根据|按照|依据|本文档|本制度|本政策)?\s*"
    r"([\w\u4e00-\u9fff（）()·\-/]{2,32}?)\s*"
    r"(?:的)?\s*(是|为|＝|=|：|:|规定为|设置为|调整为|不超过|最多为|至少为|is|equals?)\s*"
    r"(.+?)\s*[。.;；]?\s*$"
)
_TABLE_ROW = re.compile(r"^\s*\|?\s*([^|]{2,32}?)\s*\|\s*([^|]{1,60}?)\s*\|?\s*$")
# 否定/方向词不对称检测：谓语必须在句末，避免把普通句子都抽成 claim。
# 场景：一份文档写「固定工位需要」，另一份写「固定工位不再需要」。
_STATE_STMT = re.compile(
    r"^\s*([\w\u4e00-\u9fff（）()·\-/]{2,30}?)\s*"
    r"(不再需要|不再要求|不再适用|已废止|已取消|已停用|已作废|"
    r"需要|必须|要求|支持|允许|启用)\s*[。.;；]?\s*$"
)
_STATE_NEG = {"不再需要", "不再要求", "不再适用", "已废止", "已取消", "已停用", "已作废"}
_NEG_WORDS = ("不再", "不支持", "不允许", "禁止", "已废止", "已取消", "停用", "作废",
              "no longer", "deprecated", "disabled", "not supported", "must not")
_POS_WORDS = ("支持", "允许", "启用", "生效", "有效", "required", "enabled", "supported")
_NUMBER = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(天|日|秒|毫秒|ms|分钟|小时|次|个|人|台|%|％|元|万元|MB|GB|QPS|qps)?")
_VERSION = re.compile(r"\b[vV]?(\d+(?:\.\d+){1,3})\b")
_DATE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")

ATTR_PATTERNS = [
    (re.compile(r"天数|期限|有效期|天|日(?!志)|duration", re.I), "duration_days"),
    (re.compile(r"超时|timeout|秒|毫秒|ms|latency", re.I), "timeout"),
    (re.compile(r"版本|version|修订|revision", re.I), "version"),
    (re.compile(r"限额|上限|次数|并发|配额|QPS|limit|quota", re.I), "limit"),
    (re.compile(r"金额|价格|报价|费用|元|价格|price|cost", re.I), "amount"),
    (re.compile(r"状态|是否|支持|启用|开关|status|enabled", re.I), "status"),
]

_UNIT_ALIAS = {"日": "天", "％": "%", "毫秒": "ms", "qps": "QPS"}


def _attr_of(subject: str) -> str:
    for pat, name in ATTR_PATTERNS:
        if pat.search(subject):
            return name
    return "value"


def _norm_value(raw: str) -> tuple[str, str, float]:
    """返回 (归一化值, 单位, 置信度)。归一化只用于比对，不改原文。"""
    s = raw.strip().strip("。.;；，,、\"'")
    s = s.replace("％", "%").replace("＝", "=")

    m = _VERSION.search(s)
    if m and ("." in m.group(1)):
        return m.group(1), "version", 1.0

    d = _DATE.search(s)
    if d:
        return f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}", "date", 0.9

    n = _NUMBER.search(s)
    if n:
        num, unit = float(n.group(1)), (n.group(2) or "")
        unit = _UNIT_ALIAS.get(unit, unit)
        # 年份单独出现（无单位）不是数值事实，容易被误当冲突
        if not unit and 1900 <= num <= 2099 and float(num).is_integer():
            return s, "", 0.2
        conf = 0.5 if (not unit and 1 <= num <= 9 and float(num).is_integer()) else 1.0
        text = f"{num:g}"
        return text, unit, conf

    low = s.lower()
    if any(w in low for w in ("true", "yes", "是", "启用", "支持", "生效", "有效")):
        return "true", "bool", 0.9
    if any(w in low for w in ("false", "no", "否", "停用", "不支持", "禁止", "作废")):
        return "false", "bool", 0.9
    return s, "", 0.8


def _polarity(sentence: str) -> int:
    low = sentence.lower()
    if any(w in low for w in _NEG_WORDS):
        return -1
    return 1


def _new_id(prefix: str = "c") -> str:
    return prefix + uuid.uuid4().hex[:10]


def extract_claims(text: str, *, doc_id: str, chunk_id: str = "",
                   scope: dict[str, str] | None = None,
                   valid_from: str | None = None, authority: int = 1) -> list[Claim]:
    """从一段文本抽 claim。逐句扫描，规则不命中的句子直接跳过（不硬凑）。"""
    scope = dict(scope or {})
    out: list[Claim] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("|---"):
            continue
        subject = value = None
        state_polarity: int | None = None
        m = _ASSIGN.match(line)
        if m:
            subject, value = m.group(1).strip(), m.group(3).strip()
        else:
            t = _TABLE_ROW.match(line)
            if t:
                subject, value = t.group(1).strip(), t.group(2).strip()
            else:
                s = _STATE_STMT.match(line)
                if s:
                    subject = s.group(1).strip()
                    state_polarity = -1 if s.group(2) in _STATE_NEG else 1
                    value = "false" if state_polarity < 0 else "true"
        if not subject or not value or len(value) > 80:
            continue
        if state_polarity is not None:
            norm, unit, conf, polarity = value, "bool", 0.9, state_polarity
        else:
            norm, unit, conf = _norm_value(value)
            polarity = _polarity(line)
        out.append(Claim(
            claim_id=_new_id(), doc_id=doc_id,
            subject=subject, attribute=_attr_of(subject),
            value=value, value_norm=norm, polarity=polarity, unit=unit,
            scope=scope, valid_from=valid_from, authority=authority,
            text=raw_line.strip(), chunk_id=chunk_id, confidence=conf,
        ))
    return out


def topic_similarity(a: str, b: str) -> float:
    """轻量主题相似度：中文相邻二元组 + 英文整词的 Jaccard。

    用于跨 key 的否定不对称检测门控：只有主题确实接近才比对，
    避免「退款期限 10 天」和「接口超时 30 秒」被误判为冲突。
    """
    def grams(s: str) -> set[str]:
        s = (s or "").lower()
        toks = set(re.findall(r"[a-z0-9]+", s))
        cjk = re.findall(r"[\u4e00-\u9fff]+", s)
        for seg in cjk:
            toks.update(seg[i:i + 2] for i in range(len(seg) - 1))
            if len(seg) == 1:
                toks.add(seg)
        return toks
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)
