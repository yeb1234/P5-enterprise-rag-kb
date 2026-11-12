"""Agent 状态契约：编排层与业务层之间只传这个对象。

预留给后续 LangGraph / 多 Agent 的关键点：
  · 状态是可序列化的，节点可中断、可回放、可回放失败节点；
  · 每个节点把 (节点名, 耗时, 结果摘要) 追加到 audit 链，天然可观测；
  · 工具调用必须经过 ToolRegistry（权限 + 参数白名单 + 审计），节点不得裸调外部能力。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    trace_id: str = ""
    tenant_id: str = ""
    user: dict[str, Any] = Field(default_factory=dict)
    query: str = ""
    intent: str = "knowledge_qa"    # knowledge_qa | data_query | admin_action | chitchat
    plan: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict] = Field(default_factory=list)
    context: str = ""
    answer: str = ""
    citations: list[dict] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    tool_calls: list[dict] = Field(default_factory=list)
    audit: list[dict] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    def trace(self, node: str, **extra) -> None:
        self.audit.append({"node": node, **extra})
