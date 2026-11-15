"""PII 四出口接线：同一套规则、同一份报告，供左右两区共用。

设计取舍（与 `guardrails/README.md` 一致）：
- 展示层打码不算防护，所以入库前 / 进模型前 / 输出前 / 日志导出前
  走的是**同一个** `Guardrails` 实例与同一份 `RedactionPolicy`。
- 对同一段文本依次跑四出口是为了**证明规则单点**（四次命中数应一致）；
  最终文本只取第一次 `ingest` 的结果，避免重复打码把掩码再打一次。
- `report` 必须含 `blocked`，`api.py` 用它决定是否返回 `PII_BLOCKED`。
"""
from __future__ import annotations

from guardrails import Guardrails, RULE_VERSION, gather

GATES = ("ingest", "context", "output", "audit")


def protect_text(text: str, enabled: bool) -> tuple[str, dict]:
    """返回 (脱敏后文本, 四出口报告)。

    ``enabled=False`` 时不改动文本，但仍给结构完整的全零报告，
    让右侧 JSON 的字段集合保持稳定（关闭开关不会让字段凭空消失）。
    """
    if not enabled:
        return text, {
            "chain": [],
            "report": {"blocked": False, "total_hits": 0, "gates": list(GATES),
                       "results": [], "rule_version": RULE_VERSION},
            "rule_version": RULE_VERSION,
        }

    g = Guardrails()
    results = [g.ingest(text), g.context(text), g.output(text), g.audit(text)]
    report = gather(results).as_dict()
    return results[0].text, {
        "chain": [r.as_dict() for r in results],
        "report": report,
        "rule_version": RULE_VERSION,
    }
