"""四出口统一入口：入库前 / 进模型前 / 输出前 / 日志与导出前共用同一套规则。

为什么要一个 `Guardrails` 而不是在每个路由里各写一次正则：
- 规则版本、置信度阈值、动作策略必须单点配置，否则「输出脱敏了、日志没脱敏」；
- 前端要能一次拿到四个出口的命中汇总（`SafetyReport`），评审时可逐项指给人看；
- 缓存与 `chat_sources` 也走 `protect()`，保证「缓存里不能存未脱敏内容」。
"""
from __future__ import annotations

import os
import threading
from typing import Iterable

from guardrails.contracts import (ACTION_MASK, GATE_AUDIT, GATE_CONTEXT, GATE_INGEST,
                                  GATE_OUTPUT, PIIFinding, RedactionPolicy,
                                  RedactionResult, SafetyReport)
from guardrails.detectors import rule_catalog, scan
from guardrails.redact import (StreamRedactor, VAULT, redact_text)

def _env_policy() -> RedactionPolicy:
    """纯包自治配置：`GUARDRAILS_*`，不与 webapp / backend 的既有键冲突。

    `GUARDRAILS_HASH_SALT` 生产必须换成密钥管理系统的值——默认盐只是演示占位，
    用默认盐做 hash 脱敏等于没脱（彩虹表可反推）。
    """
    def _truthy(name: str, default: str) -> bool:
        return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off", ""}

    try:
        min_conf = float(os.environ.get("GUARDRAILS_MIN_CONFIDENCE", "0.60"))
    except ValueError:
        min_conf = 0.60
    return RedactionPolicy(
        enabled=_truthy("GUARDRAILS_ENABLED", "1"),
        default_action=os.environ.get("GUARDRAILS_ACTION", ACTION_MASK),
        min_confidence=min_conf,
        reversible_token=_truthy("GUARDRAILS_REVERSIBLE_TOKEN", "0"),
        hash_salt=os.environ.get("GUARDRAILS_HASH_SALT", "demo-salt-change-me"),
    )


_lock = threading.RLock()
_policy = _env_policy()

# 内置演示样本：覆盖用户关心的「ID / 用户信息」，以及会误报的负向对照。
# 身份证 / 银行卡号的校验位都是按 ISO 7064 MOD 11-2 与 Luhn 实际算出来的，
# 不能用随手编的号码，否则演示会把「校验位非法」误报成另一类标识符。
DEMO_SAMPLES: tuple[dict[str, str], ...] = (
    {"label": "身份证（合法校验位）", "text": "员工身份证号是 110101199003071938，请登记。"},
    {"label": "手机号 + 邮箱", "text": "联系手机 13812345678，邮箱 zhangsan@example.com。"},
    {"label": "API 密钥（应被拦截）", "text": "请使用 sk-abcdefghijklmnop1234567890 调用接口。"},
    {"label": "工号 / 员工 ID", "text": "工号：EMP-20260118，姓名：张三。"},
    {"label": "银行卡（Luhn 合法）", "text": "报销卡号 6222021234567894，请勿外传。"},
    {"label": "负向对照：校验位非法的身份证", "text": "证件号 11010119900307193X 校验位不合法，不应误报。"},
    {"label": "负向对照：18 位订单号", "text": "订单号 11010119900307123456 不是身份证。"},
    {"label": "负向对照：13 位长号中的手机号子串", "text": "流水号 1138123456789 不是手机号。"},
    {"label": "负向对照：普通技术文档", "text": "长按复位键进入配网模式，指示灯变蓝后即可绑定账号。"},
)

# 长文本 + 逐字符流式下发：验证「身份证跨 chunk 边界时不会先发出前几位」
STREAM_SAMPLE = ("员工身份证号是 110101199003071938，请登记。" + "后续操作说明请参照手册第三章。")


def get_policy() -> RedactionPolicy:
    with _lock:
        return _policy


def set_policy(p: RedactionPolicy) -> RedactionPolicy:
    global _policy
    with _lock:
        _policy = p
        return p


class Guardrails:
    def __init__(self, policy: RedactionPolicy | None = None):
        self.policy = policy or get_policy()

    def scan(self, text: str) -> list[PIIFinding]:
        return scan(text)

    def protect(self, text: str, gate: str) -> RedactionResult:
        return redact_text(text, gate=gate, policy=self.policy)

    def ingest(self, text: str) -> RedactionResult:
        return self.protect(text, GATE_INGEST)

    def context(self, text: str) -> RedactionResult:
        return self.protect(text, GATE_CONTEXT)

    def output(self, text: str) -> RedactionResult:
        return self.protect(text, GATE_OUTPUT)

    def audit(self, text: str) -> RedactionResult:
        return self.protect(text, GATE_AUDIT)

    def sources(self, items: list[dict]) -> tuple[list[dict], SafetyReport]:
        """来源卡片 / chat_sources：脱敏 snippet、chunk_text、doc_title。"""
        out: list[dict] = []
        results: list[RedactionResult] = []
        for item in items:
            d = dict(item)
            for field in ("text", "snippet", "doc_title"):
                if isinstance(d.get(field), str) and d[field]:
                    r = self.protect(d[field], GATE_CONTEXT)
                    d[field] = r.text
                    if r.hit_count:
                        results.append(r)
            out.append(d)
        return out, gather(results)

    def stream(self) -> StreamRedactor:
        return StreamRedactor(self.policy)


def gather(results: Iterable[RedactionResult]) -> SafetyReport:
    rep = SafetyReport()
    for r in results:
        rep.results.append(r)
        if r.blocked:
            rep.blocked = True
    return rep


def default() -> Guardrails:
    return Guardrails(get_policy())


def status() -> dict:
    p = get_policy()
    return {
        "policy": p.as_dict(),
        "rules": rule_catalog(),
        "vault": {"enabled": p.reversible_token, "entries": VAULT.size,
                  "note": "可逆 token 默认关闭；开启后映射本身是新的泄漏面，生产需加密存储 + 独立授权 + 保留期"},
        "gates": [GATE_INGEST, GATE_CONTEXT, GATE_OUTPUT, GATE_AUDIT],
        "samples": [dict(s) for s in DEMO_SAMPLES],
        "integration": {
            "ingest": "已接入 webapp/rag.py::analyze_document（分块前脱敏，异常 fail-closed 阻断入库）",
            "context": "已接入 webapp/routers_chat.py（问题与来源片段进模型前）",
            "output": "已接入 webapp/routers_chat.py（SSE 流式闸 + done.tail 补齐尾部）",
            "audit": "已接入 webapp/db.py::log_action 与 routers_badcase.py（采集/列表/导出）",
            "backend_skeleton": "规则级（orchestrator.py 两条正则 + verify_node 输出替换），未接本包",
            "model_level_ner": "未做（A/B 档）",
            "compliance_audit": "未做（留存策略 / 法务审批 / SIEM 对接）",
        },
    }


def run_demo() -> dict:
    """跑内置样本，返回逐项命中、四出口链路与流式跨界验证（前端一键演示）。"""
    g = default()
    rows = []
    for s in DEMO_SAMPLES:
        r = g.output(s["text"])
        rows.append({
            "label": s["label"], "original": s["text"],
            "redacted": r.text, "hits": [f.as_dict() for f in r.findings],
            "hit_count": r.hit_count, "blocked": r.blocked,
            "actions": r.actions, "by_kind": r.as_dict()["by_kind"],
        })

    # 同一段文本依次穿过四个出口，证明「输出打了码、日志/导出没打码」不会发生
    chain_text = "客户姓名：张三，工号：EMP-20260118，手机 13812345678，邮箱 zhangsan@example.com。"
    chain = []
    for gate_name, fn in (("ingest", g.ingest), ("context", g.context),
                          ("output", g.output), ("audit", g.audit)):
        r = fn(chain_text)
        chain.append({"gate": gate_name, "text": r.text, "hit_count": r.hit_count,
                      "by_kind": r.as_dict()["by_kind"], "actions": r.actions})

    # 逐字符模拟 SSE：长文本让标识符跨多个 chunk，检查是否泄漏原文
    sr = g.stream()
    stream_out = "".join(sr.push(ch) for ch in STREAM_SAMPLE) + sr.flush()
    stream = {
        "chunks": len(STREAM_SAMPLE),
        "held_boundaries": sr.held,
        "hits": sr.hits,
        "leaked": "110101199003071938" in stream_out,
        "output": stream_out[:160],
        "note": "逐字符下发；leaked=false 说明跨 chunk 的标识符没有被切成两半发出去",
    }

    return {
        "rule_version": "PII-RULES@1.0",
        "policy": get_policy().as_dict(),
        "rows": rows,
        "chain": chain,
        "stream": stream,
        "total_hits": sum(x["hit_count"] for x in rows),
        "negative_controls": [x["label"] for x in rows
                              if x["label"].startswith("负向对照") and x["hit_count"] == 0],
        "note": "合成样本，仅验证规则连通性与防误报，不是质量基线；真实语料需单独评测",
    }


def export_rows(rows: list[dict], fields: Iterable[str] = ("question", "answer", "issue_desc", "source")) -> list[dict]:
    """CSV / JSON 导出前的统一脱敏（不修改原行对象）。"""
    g = default()
    out = []
    for row in rows:
        d = dict(row)
        for f in fields:
            if isinstance(d.get(f), str):
                d[f] = g.audit(d[f]).text
        out.append(d)
    return out
