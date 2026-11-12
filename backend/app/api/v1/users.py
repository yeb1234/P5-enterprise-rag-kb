"""用户 / 部门 / 显式授权管理。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.constants import Action, Role
from app.core.deps import CurrentUser, DbSession, request_meta
from app.core.errors import Forbidden, NotFound
from app.core.security import hash_password
from app.db.repository import TenantRepository
from app.models.identity import ACLGrant, Department, User
from app.schemas.auth import DepartmentCreate, DepartmentOut, UserCreate, UserOut, UserPatch
from app.schemas.common import Page
from app.services.audit import record

router = APIRouter(tags=["组织与权限"])


@router.get("/users", response_model=Page[UserOut], summary="用户列表")
def list_users(db: DbSession, user: CurrentUser, page: int = 1, page_size: int = 20):
    repo = TenantRepository(db, user.tenant_id)
    items, total = repo.paginate(repo.query(User).order_by(User.created_at.desc()), page, page_size)
    return Page(items=[UserOut.model_validate(x) for x in items], total=total, page=page, page_size=page_size)


@router.post("/users", response_model=UserOut, summary="创建用户（管理员）")
def create_user(payload: UserCreate, request: Request, db: DbSession, user: CurrentUser):
    if not user.is_admin:
        raise Forbidden("仅管理员可创建用户")
    repo = TenantRepository(db, user.tenant_id)
    if repo.query(User).filter(User.email == payload.email.lower()).first():
        raise Forbidden("邮箱已存在")
    obj = User(tenant_id=user.tenant_id, department_id=payload.department_id,
               email=payload.email.lower(), hashed_password=hash_password(payload.password),
               full_name=payload.full_name, role=payload.role)
    repo.add(obj)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.CREATE.value,
           resource_type="user", resource_id=str(obj.id), meta=request_meta(request))
    db.commit()
    return UserOut.model_validate(obj)


@router.patch("/users/{user_id}", response_model=UserOut, summary="修改用户")
def patch_user(user_id: uuid.UUID, payload: UserPatch, request: Request, db: DbSession, user: CurrentUser):
    if not user.is_admin:
        raise Forbidden("仅管理员可修改用户")
    obj = TenantRepository(db, user.tenant_id).get(User, user_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(obj, key, value)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.UPDATE.value,
           resource_type="user", resource_id=str(user_id), meta=request_meta(request))
    db.commit()
    return UserOut.model_validate(obj)


@router.get("/departments", response_model=list[DepartmentOut], summary="部门列表（含层级路径）")
def list_departments(db: DbSession, user: CurrentUser):
    rows = TenantRepository(db, user.tenant_id).query(Department).order_by(Department.path).all()
    return [DepartmentOut.model_validate(r) for r in rows]


@router.post("/departments", response_model=DepartmentOut, summary="创建部门")
def create_department(payload: DepartmentCreate, db: DbSession, user: CurrentUser):
    if not user.is_admin:
        raise Forbidden("仅管理员可创建部门")
    repo = TenantRepository(db, user.tenant_id)
    parent = repo.get(Department, payload.parent_id) if payload.parent_id else None
    level = (parent.level + 1) if parent else 1
    obj = Department(tenant_id=user.tenant_id, name=payload.name,
                     parent_id=payload.parent_id, level=level, path="/")
    repo.add(obj)
    db.flush()
    obj.path = f"{parent.path if parent else '/'}{obj.id}/"
    db.commit()
    return DepartmentOut.model_validate(obj)


class GrantCreate(BaseModel):
    subject_type: str = "user"          # user | department | role
    subject_id: str
    resource_type: str = "document"     # kb | document | chunk
    resource_id: uuid.UUID | None = None
    scope: str = "document"             # kb | document | chunk | paragraph（后两档为预留）
    action: str = "read"
    effect: str = "allow"               # allow | deny
    conditions: dict = {}


@router.get("/acl-grants", summary="显式授权列表")
def list_grants(db: DbSession, user: CurrentUser):
    rows = TenantRepository(db, user.tenant_id).query(ACLGrant).order_by(ACLGrant.created_at.desc()).all()
    return [{"id": str(r.id), "subject_type": r.subject_type, "subject_id": r.subject_id,
             "resource_type": r.resource_type, "resource_id": str(r.resource_id) if r.resource_id else None,
             "scope": r.scope, "action": r.action, "effect": r.effect,
             "conditions": r.conditions} for r in rows]


@router.post("/acl-grants", summary="新增显式授权（含片段/段落级预留）")
def create_grant(payload: GrantCreate, request: Request, db: DbSession, user: CurrentUser):
    if user.role not in {Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value, Role.DEPT_ADMIN.value}:
        raise Forbidden("需要管理员角色")
    repo = TenantRepository(db, user.tenant_id)
    obj = ACLGrant(tenant_id=user.tenant_id, subject_type=payload.subject_type,
                   subject_id=payload.subject_id, resource_type=payload.resource_type,
                   resource_id=payload.resource_id, scope=payload.scope,
                   action=payload.action, effect=payload.effect,
                   conditions=payload.conditions, granted_by=user.id)
    repo.add(obj)
    record(db, tenant_id=user.tenant_id, user_id=user.id, action=Action.GRANT.value,
           resource_type=payload.resource_type, resource_id=str(obj.id),
           params=payload.model_dump(mode="json"), meta=request_meta(request))
    db.commit()
    return {"id": str(obj.id), "scope": obj.scope}
