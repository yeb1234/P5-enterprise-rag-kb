"""权限判定唯一入口。

架构原则：
1. 判定逻辑收敛到一个纯函数 decide()，不依赖 DB，可单测、可回放、可审计；
2. 所有接口、检索、后台任务、Agent 工具都调用同一个 check()；
3. 「先按权限过滤，再重排、再进模型」是红线：Milvus expr 与召回后二次过滤各做一次；
4. ACLScope.CHUNK / PARAGRAPH 的求值钩子已存在，当前默认放行，
   后续只要补 acl_grants 数据 + 打开 FINE_GRAINED_ACL_ENABLED，不用改调用方。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.constants import (
    ROLE_SENSITIVITY_CEILING,
    SENSITIVITY_LEVEL,
    WRITE_ROLES,
    Action,
    ACLScope,
    Role,
    Visibility,
)
from app.core.errors import Forbidden
from app.models.identity import ACLGrant

FINE_GRAINED_ACL_ENABLED = False  # 逐行/逐段落精细权限总开关（架构预留）


@dataclass(frozen=True)
class Subject:
    user_id: str
    tenant_id: str
    role: str
    department_id: str | None = None
    department_path: str | None = None
    grants: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Resource:
    tenant_id: str
    kb_id: str | None = None
    department_id: str | None = None
    owner_id: str | None = None
    sensitivity: str = "internal"
    visibility: str = Visibility.DEPT.value
    document_id: str | None = None
    chunk_id: str | None = None
    acl: dict[str, Any] = field(default_factory=dict)
    scope: str = ACLScope.DOCUMENT.value


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    matched_grant: str | None = None


def _grant_applies(grant: dict, sub: Subject, res: Resource, action: str) -> bool:
    if grant.get("tenant_id") and grant["tenant_id"] != res.tenant_id:
        return False
    if grant.get("action") not in (action, Action.MANAGE.value, "*"):
        return False
    if grant.get("resource_type") == "kb" and grant.get("resource_id") not in (None, res.kb_id):
        return False
    if grant.get("resource_type") == "document" and grant.get("resource_id") not in (None, res.document_id):
        return False
    if grant.get("resource_type") == "chunk" and grant.get("resource_id") not in (None, res.chunk_id):
        return False
    subject_type, subject_id = grant.get("subject_type"), str(grant.get("subject_id"))
    return {
        "user": subject_id == sub.user_id,
        "department": subject_id == sub.department_id,
        "role": subject_id == sub.role,
        "all": True,
    }.get(subject_type, False)


def evaluate_fine_grained(sub: Subject, res: Resource) -> Decision:
    """逐行 / 逐段落权限钩子。

    res.acl 形如：
      {"allowed_users": ["..."], "allowed_departments": ["..."],
       "allowed_roles": ["tenant_admin"], "denied_users": ["..."]}
    当前默认放行；打开开关后，命中 deny 直接丢弃片段（不进 LLM 上下文）。
    """
    if not FINE_GRAINED_ACL_ENABLED or res.scope not in (ACLScope.CHUNK.value, ACLScope.PARAGRAPH.value):
        return Decision(True, "fine-grained:disabled")
    acl = res.acl or {}
    if sub.user_id in set(acl.get("denied_users", [])):
        return Decision(False, "fine-grained:explicit-deny")
    allow_sets = (
        set(acl.get("allowed_users", [])),
        set(acl.get("allowed_departments", [])),
        set(acl.get("allowed_roles", [])),
    )
    candidates = (sub.user_id, sub.department_id, sub.role)
    if any(c and c in s for c, s in zip(candidates, allow_sets)):
        return Decision(True, "fine-grained:allow")
    if not any(allow_sets):
        return Decision(True, "fine-grained:no-restriction")
    return Decision(False, "fine-grained:not-in-allowlist")


def decide(sub: Subject, action: str, res: Resource) -> Decision:
    """纯函数：给定主体与资源，返回是否允许及可解释的原因。"""
    # 0. 租户边界：platform_admin 是唯一可跨租户的角色
    if sub.tenant_id != res.tenant_id and sub.role != Role.PLATFORM_ADMIN.value:
        return Decision(False, "tenant-mismatch")

    # 1. 显式 deny 优先级最高
    for g in sub.grants:
        if g.get("effect") == "deny" and _grant_applies(g, sub, res, action):
            return Decision(False, "explicit-deny", str(g.get("id")))

    # 2. 精细粒度（片段/段落）钩子
    fine = evaluate_fine_grained(sub, res)
    if not fine.allowed:
        return fine

    # 3. 平台管理员
    if sub.role == Role.PLATFORM_ADMIN.value:
        return Decision(True, "platform-admin")

    # 4. 写操作：viewer 一律拒绝
    if action in {Action.CREATE.value, Action.UPDATE.value, Action.DELETE.value,
                  Action.UPLOAD.value, Action.MANAGE.value, Action.GRANT.value}:
        if sub.role not in WRITE_ROLES:
            return Decision(False, "read-only-role")
        if action in {Action.MANAGE.value, Action.GRANT.value} and sub.role not in {
            Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value, Role.DEPT_ADMIN.value
        }:
            return Decision(False, "admin-required")

    # 5. 敏感度上限
    level = SENSITIVITY_LEVEL.get(res.sensitivity, 3)
    ceiling = ROLE_SENSITIVITY_CEILING.get(sub.role, 0)
    if level > ceiling:
        for g in sub.grants:
            if g.get("effect") == "allow" and _grant_applies(g, sub, res, action):
                return Decision(True, "sensitivity-grant", str(g.get("id")))
        return Decision(False, "sensitivity-exceeded")

    # 6. 租户管理员
    if sub.role == Role.TENANT_ADMIN.value:
        return Decision(True, "tenant-admin")

    # 7. 显式 allow
    for g in sub.grants:
        if g.get("effect") == "allow" and _grant_applies(g, sub, res, action):
            return Decision(True, "explicit-allow", str(g.get("id")))

    # 8. 可见范围
    if res.visibility == Visibility.PUBLIC.value:
        return Decision(True, "public")
    if res.visibility == Visibility.TENANT.value:
        return Decision(True, "tenant-visible")
    if sub.department_id and res.department_id == sub.department_id:
        return Decision(True, "same-department")
    # 部门管理员管本部门子树（物化路径前缀）
    if sub.role == Role.DEPT_ADMIN.value and sub.department_path and res.department_id:
        if res.department_id in sub.department_path or res.department_id == sub.department_id:
            return Decision(True, "department-subtree")
    if res.owner_id and res.owner_id == sub.user_id:
        return Decision(True, "owner")
    return Decision(False, "department-mismatch")


class PermissionService:
    """DB 包装层：加载显式授权、生成 SQL 过滤条件、构造 Milvus 表达式。"""

    def __init__(self, db: Session, user: Any):
        self.db = db
        self.user = user
        self._grants: list[dict] | None = None
        self.subject = Subject(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            role=user.role,
            department_id=str(user.department_id) if user.department_id else None,
            department_path=getattr(user, "department_path", None),
            grants=(),
        )

    def _load_grants(self) -> tuple[dict, ...]:
        if self._grants is None:
            rows = (
                self.db.query(ACLGrant)
                .filter(ACLGrant.tenant_id == self.user.tenant_id)
                .all()
            )
            self._grants = [
                {
                    "id": str(r.id), "tenant_id": str(r.tenant_id),
                    "subject_type": r.subject_type, "subject_id": r.subject_id,
                    "resource_type": r.resource_type,
                    "resource_id": str(r.resource_id) if r.resource_id else None,
                    "scope": r.scope, "action": r.action, "effect": r.effect,
                }
                for r in rows
            ]
        return tuple(self._grants)

    def check(self, action: str, resource: Resource, *, raise_on_deny: bool = True) -> Decision:
        sub = Subject(**{**self.subject.__dict__, "grants": self._load_grants()})
        result = decide(sub, action, resource)
        if not result.allowed and raise_on_deny:
            raise Forbidden(f"权限不足：{result.reason}")
        return result

    def resource_from_document(self, doc, kb_visibility: str = "dept") -> Resource:
        return Resource(
            tenant_id=str(doc.tenant_id),
            kb_id=str(doc.kb_id),
            department_id=str(doc.department_id) if doc.department_id else None,
            owner_id=str(doc.owner_id) if doc.owner_id else None,
            sensitivity=doc.sensitivity,
            visibility=kb_visibility,
            document_id=str(doc.id),
        )

    def visible_document_clause(self, model):
        """列表/检索统一使用的 SQL 条件：部门 + 可见性 + 敏感度，永不省略 tenant。"""
        user = self.user
        base = [model.tenant_id == user.tenant_id, model.is_deleted.is_(False)]
        if user.role in {Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value}:
            return and_(*base)
        sensitivity_allowed = self.allowed_sensitivities()
        dept_clause = or_(
            model.department_id == user.department_id,
            model.department_id.is_(None),
        )
        return and_(*base, dept_clause, model.sensitivity.in_(sensitivity_allowed))

    def allowed_sensitivities(self) -> list[str]:
        ceiling = ROLE_SENSITIVITY_CEILING.get(self.user.role, 0)
        return [name for name, level in SENSITIVITY_LEVEL.items() if level <= ceiling]

    def allowed_dept_ids(self) -> list[str]:
        return [str(self.user.department_id)] if self.user.department_id else []

    def milvus_expr(self, kb_ids: list[str] | None = None, extra: str | None = None) -> str:
        from app.integrations.milvus_client import MilvusVectorStore

        is_admin = self.user.role in {Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value}
        return MilvusVectorStore.build_permission_expr(
            tenant_id=str(self.user.tenant_id),
            kb_ids=kb_ids,
            # 非管理员才按部门收窄；管理员仍需 tenant_id（构造器内强制）
            dept_ids=None if is_admin else (self.allowed_dept_ids() or [""]),
            sensitivity_values=self.allowed_sensitivities(),
            extra=extra,
        )

    def filter_candidates(self, hits: Iterable[dict]) -> list[dict]:
        """召回后二次校验：从 Milvus/PG 回来的片段逐个过 decide()，
        命中但无权限的片段直接丢弃，绝不进入 LLM 上下文。"""
        grants = self._load_grants()
        kept = []
        for hit in hits:
            res = Resource(
                tenant_id=str(self.user.tenant_id),
                kb_id=str(hit.get("kb_id") or ""),
                department_id=str(hit.get("dept_id") or "") or None,
                sensitivity=hit.get("sensitivity") or "internal",
                visibility=hit.get("visibility") or "dept",
                document_id=str(hit.get("doc_id") or ""),
                chunk_id=str(hit.get("chunk_id") or ""),
                acl=hit.get("acl") or {},
                scope=ACLScope.CHUNK.value,
            )
            sub = Subject(**{**self.subject.__dict__, "grants": grants})
            if decide(sub, Action.READ.value, res).allowed:
                kept.append(hit)
        return kept
