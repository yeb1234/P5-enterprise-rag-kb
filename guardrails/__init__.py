"""内容安全 / PII 防护：`guardrails/` 独立纯函数包。

四个出口共用同一套规则：入库前 `ingest` / 进模型前 `context` /
输出前 `output` / 日志与导出前 `audit`。

定位：规则级检测（正则 + 校验位 + 上下文门控），**不是**模型级 NER，
也不是合规审计。演示切片用它保证「展示打码了、日志和导出没打码」不会发生。
"""
from guardrails.contracts import (ACTIONS, GATES, RULE_VERSION, PIIFinding,
                                  RedactionPolicy, RedactionResult, SafetyReport)
from guardrails.detectors import rule_catalog, scan
from guardrails.pipeline import (Guardrails, default, export_rows, gather,
                                 get_policy, run_demo, set_policy, status)
from guardrails.redact import StreamRedactor, redact_many, redact_text

__all__ = [
    "ACTIONS", "GATES", "RULE_VERSION", "PIIFinding", "RedactionPolicy",
    "RedactionResult", "SafetyReport", "rule_catalog", "scan", "Guardrails",
    "default", "export_rows", "gather", "get_policy", "run_demo", "set_policy",
    "status", "StreamRedactor", "redact_many", "redact_text",
]
