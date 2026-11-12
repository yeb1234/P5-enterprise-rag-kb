"""受控工具注册表 —— Agent 内部编排的能力边界。

设计文档里「对外能力受控封装」在这里落地：
  · 工具先注册才能调用，参数走 Pydantic/白名单校验，禁止自由 SQL；
  · 每次调用先过 PermissionService，再记审计；
  · 后续接 LangGraph 时，节点只需持有同一个 ToolRegistry，不用重写鉴权。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.constants import Action
from app.core.errors import Forbidden
from app.core.logging import get_logger
from app.services.audit import record

logger = get_logger("app.agent.tools")


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    required_action: str = Action.READ.value
    allowed_roles: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict]:
        return [{"name": s.name, "description": s.description, "action": s.required_action}
                for s in self._tools.values()]

    def invoke(self, name: str, *, db, user, state, **kwargs) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise Forbidden(f"未注册的工具：{name}")
        if spec.allowed_roles and user.role not in spec.allowed_roles:
            raise Forbidden(f"角色 {user.role} 无权调用 {name}")

        from app.services.permission import PermissionService, Resource

        decision = PermissionService(db, user).check(
            spec.required_action,
            Resource(tenant_id=str(user.tenant_id), visibility="tenant"),
            raise_on_deny=False,
        )
        if not decision.allowed:
            record(db, tenant_id=user.tenant_id, user_id=user.id, action=name,
                   resource_type="tool", result="denied", detail={"reason": decision.reason},
                   commit=True)
            raise Forbidden(f"工具 {name} 被权限拦截：{decision.reason}")

        state.tool_calls.append({"name": name, "args": _safe_args(kwargs)})
        result = spec.handler(db=db, user=user, state=state, **kwargs)
        state.trace("tool", name=name, result=str(result)[:200])
        return result


def _safe_args(kwargs: dict) -> dict:
    """审计里不记原文/密钥，只记结构，避免日志本身变成泄密面。"""
    return {k: (f"<{type(v).__name__} len={len(v)}>" if isinstance(v, (str, bytes, list)) else str(v))
            for k, v in kwargs.items()}


def build_default_registry() -> ToolRegistry:
    """当前只开放检索类工具；数据查询/管理类按阶段二再挂，避免骨架期就把口子开大。"""
    from app.schemas.search import SearchFilters, SearchRequest
    from app.services.search import hybrid_search

    registry = ToolRegistry()

    def kb_search(*, db, user, state, query: str, top_k: int = 8, **kwargs):
        req = SearchRequest(query=query, mode="hybrid", top_k=top_k,
                            filters=SearchFilters(**state.filters))
        response = hybrid_search(db, user, req)
        state.candidates = [c.model_dump() for h in response.hits for c in h.citations]
        state.citations = state.candidates
        return {"total": response.total, "took_ms": response.took_ms}

    registry.register(ToolSpec(
        name="kb.search", description="混合检索知识库片段", handler=kb_search,
        required_action=Action.SEARCH.value,
    ))
    return registry


DEFAULT_REGISTRY = build_default_registry()
