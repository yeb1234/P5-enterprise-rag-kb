"""Agent 编排骨架：线性 RAG 参考实现 + 可替换的 Graph 接口。

现在：LinearRAGOrchestrator 按固定节点顺序跑，节点是可注册的函数，
每步写 state.audit，方便学习「状态机怎么串」。
以后：换 LangGraph 只需实现 Orchestrator 协议、把 nodes 注册成 StateGraph 的 node，
API 层、工具注册表、权限层、审计层都不用动（见 docs/agent-extension.md）。
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any, Callable, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.agent.state import AgentState
from app.agent.tools import DEFAULT_REGISTRY, ToolRegistry
from app.core.constants import Action
from app.core.logging import get_logger
from app.integrations.llm import build_rag_messages, get_llm_client
from app.models.identity import AuditLog
from app.models.metrics import MetricEvent
from app.services.audit import record

logger = get_logger("app.agent")


def _query_hash(text: str) -> str:
    """审计只存摘要，不存提问原文；sha256 跨进程稳定，便于回放比对。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"(忽略|无视).{0,6}(之前|上面|系统).{0,6}(指令|提示)", re.I),
    re.compile(r"(打印|输出|泄露).{0,6}(系统提示|system prompt|密钥)", re.I),
]
PII_PATTERNS = [
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"\d{17}[\dXx]"),
]

NodeFn = Callable[[AgentState, Session, Any, ToolRegistry], AgentState]


@runtime_checkable
class Orchestrator(Protocol):
    def run(self, state: AgentState, db: Session, user: Any) -> AgentState: ...
    def stream(self, state: AgentState, db: Session, user: Any) -> Iterator[str]: ...


class NodeRegistry:
    def __init__(self):
        self._nodes: dict[str, NodeFn] = {}

    def register(self, name: str, fn: NodeFn) -> None:
        self._nodes[name] = fn

    def run(self, state: AgentState, db: Session, user: Any, tools: ToolRegistry,
            order: list[str]) -> AgentState:
        for name in order:
            started = time.perf_counter()
            fn = self._nodes[name]
            state.trace("enter", node=name)
            state = fn(state, db, user, tools)
            state.trace("exit", node=name,
                        ms=int((time.perf_counter() - started) * 1000),
                        blocked=state.blocked)
            if state.blocked:
                break
        return state


NODES = NodeRegistry()


def node(name: str):
    def deco(fn: NodeFn):
        NODES.register(name, fn)
        return fn
    return deco


@node("guard")
def guard_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    state.query = state.query.strip()
    if not state.query:
        state.blocked, state.block_reason = True, "问题为空"
        return state
    if any(p.search(state.query) for p in INJECTION_PATTERNS):
        state.blocked, state.block_reason = True, "疑似 Prompt 注入，已拦截"
        record(db, tenant_id=user.tenant_id, user_id=user.id, action="ask",
               resource_type="agent", result="blocked", detail={"query_hash": _query_hash(state.query)},
               meta={"trace_id": state.trace_id}, commit=True)
        return state
    # 意图路由（阶段二多 Agent 的落点：这里只做占位，不开放额外工具）
    if re.search(r"(报表|统计|多少|数量|趋势)", state.query):
        state.intent = "data_query"
    elif re.search(r"(上传|删除|审核|重试)", state.query):
        state.intent = "admin_action"
    else:
        state.intent = "knowledge_qa"
    state.plan = ["guard", "retrieve", "permission_filter", "assemble", "generate", "verify", "persist"]
    return state


@node("retrieve")
def retrieve_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    tools.invoke("kb.search", db=db, user=user, state=state, query=state.query, top_k=8)
    state.trace("retrieve", count=len(state.candidates))
    return state


@node("permission_filter")
def permission_filter_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    """第二次权限校验：召回结果进上下文前必须逐个过 decide()。"""
    from app.services.permission import PermissionService

    before = len(state.candidates)
    state.candidates = PermissionService(db, user).filter_candidates(state.candidates)
    state.citations = state.candidates
    state.trace("permission_filter", before=before, after=len(state.candidates))
    if not state.candidates:
        state.blocked = True
        state.block_reason = "没有可依据的资料，或全部片段因权限被过滤"
    return state


@node("assemble")
def assemble_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    state.context = "\n\n".join(
        f"[{i + 1}] {c.get('snippet', '')}" for i, c in enumerate(state.candidates[:12])
    )
    state.metrics["retrieved_n"] = len(state.candidates)
    return state


@node("generate")
def generate_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    llm = get_llm_client()
    messages = build_rag_messages(state.query, state.candidates[:12])
    started = time.perf_counter()
    state.answer = llm.chat(messages)
    state.metrics["ttft_ms"] = int((time.perf_counter() - started) * 1000)
    state.metrics["model"] = llm.model_name
    return state


@node("verify")
def verify_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    """输出闸：PII 复述扫描、无依据标记。骨架用规则，阶段二接模型级检测。"""
    for pattern in PII_PATTERNS:
        if pattern.search(state.answer):
            state.answer = pattern.sub("[已脱敏]", state.answer)
            state.trace("verify", action="pii_masked")
    if not re.search(r"\[\d+\]", state.answer) and state.candidates:
        state.answer += "\n\n（提示：本回答未标注引用编号，请核对原文。）"
    return state


@node("persist")
def persist_node(state: AgentState, db: Session, user: Any, tools: ToolRegistry) -> AgentState:
    """指标 + 审计落库，构成数据飞轮的原始数据源。"""
    db.add(MetricEvent(
        tenant_id=user.tenant_id, user_id=user.id, endpoint="/search/answer",
        trace_id=state.trace_id, status="ok" if not state.blocked else "blocked",
        latency_ms=sum(int(a.get("ms", 0)) for a in state.audit if a.get("node") == "exit"),
        ttft_ms=int(state.metrics.get("ttft_ms", 0)),
        retrieved_n=len(state.candidates),
        tokens_in=len(state.context) // 2, tokens_out=len(state.answer) // 2,
        cache_hit=False,
    ))
    record(db, tenant_id=user.tenant_id, user_id=user.id, action="ask",
           resource_type="agent", result="blocked" if state.blocked else "success",
           detail={"query_hash": _query_hash(state.query), "intent": state.intent,
                   "retrieved": len(state.candidates), "nodes": state.plan},
           meta={"trace_id": state.trace_id})
    db.commit()
    return state


DEFAULT_ORDER = ["guard", "retrieve", "permission_filter", "assemble", "generate", "verify", "persist"]


class LinearRAGOrchestrator:
    """当前参考实现：节点顺序固定、状态可序列化、每节点可插校验分支。"""

    def __init__(self, registry: ToolRegistry | None = None, nodes: NodeRegistry | None = None):
        self.tools = registry or DEFAULT_REGISTRY
        self.nodes = nodes or NODES

    def run(self, state: AgentState, db: Session, user: Any) -> AgentState:
        state = self.nodes.run(state, db, user, self.tools, DEFAULT_ORDER)
        if state.blocked and not state.answer:
            state.answer = f"无法回答：{state.block_reason}"
        return state

    def stream(self, state: AgentState, db: Session, user: Any) -> Iterator[str]:
        """SSE 流式：先做检索/过滤/装配，再逐 token 推给前端，最后补 persist。"""
        for name in DEFAULT_ORDER[:4]:
            fn = self.nodes._nodes[name]
            state = fn(state, db, user, self.tools)
            if state.blocked:
                yield state.block_reason
                return
        llm = get_llm_client()
        messages = build_rag_messages(state.query, state.candidates[:12])
        started = time.perf_counter()
        for delta in llm.chat_stream(messages):
            state.answer += delta
            yield delta
        state.metrics["ttft_ms"] = int((time.perf_counter() - started) * 1000)
        state.metrics["model"] = llm.model_name
        for name in DEFAULT_ORDER[5:]:
            state = self.nodes._nodes[name](state, db, user, self.tools)


def build_state(user: Any, query: str, filters: dict | None = None, trace_id: str | None = None) -> AgentState:
    return AgentState(
        trace_id=trace_id or str(uuid.uuid4()),
        tenant_id=str(user.tenant_id),
        user={"id": str(user.id), "role": user.role, "dept": str(user.department_id)},
        query=query, filters=filters or {},
    )


def get_orchestrator() -> LinearRAGOrchestrator:
    return LinearRAGOrchestrator()
