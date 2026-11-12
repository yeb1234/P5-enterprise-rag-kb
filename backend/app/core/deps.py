"""请求级依赖：鉴权、租户上下文、角色校验。

关键约束：tenant_id 只由依赖注入产生，业务代码不得从请求体/查询参数里读取。
后台任务同理——task 参数必须显式携带 tenant_id，并在 task 内重新构造上下文。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.constants import Role
from app.core.errors import Forbidden, Unauthorized
from app.core.security import decode_token
from app.db.repository import TenantRepository
from app.db.session import get_db
from app.models.identity import User


@dataclass(frozen=True)
class Principal:
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None
    role: str
    email: str
    department_path: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in {Role.PLATFORM_ADMIN.value, Role.TENANT_ADMIN.value}


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise Unauthorized("缺少 Bearer 令牌")
    return header[7:].strip()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Principal:
    try:
        payload = decode_token(_bearer_token(request), "access")
    except PermissionError as exc:
        code = str(exc.args[0]) if exc.args else "AUTH_INVALID"
        raise Unauthorized("登录已失效，请重新登录", code=code) from exc

    tenant_id = uuid.UUID(payload["tenant_id"])
    user = (
        TenantRepository(db, tenant_id)
        .query(User)
        .filter(User.id == uuid.UUID(payload["sub"]))
        .first()
    )
    if user is None or user.status != "active":
        raise Unauthorized("账号不可用")

    return Principal(
        id=user.id,
        tenant_id=user.tenant_id,
        department_id=user.department_id,
        role=user.role,
        email=user.email,
        department_path=getattr(user.department, "path", None) if user.department else None,
    )


CurrentUser = Annotated[Principal, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]


def get_repo(db: DbSession, user: CurrentUser) -> TenantRepository:
    return TenantRepository(db, user.tenant_id)


TenantRepo = Annotated[TenantRepository, Depends(get_repo)]


def require_roles(*roles: str) -> Callable:
    def _check(user: CurrentUser) -> Principal:
        if user.role not in roles:
            raise Forbidden(f"需要角色：{', '.join(roles)}")
        return user

    return _check


def request_meta(request: Request) -> dict:
    """审计日志的公共字段，避免每个接口重复拼装。"""
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("User-Agent"),
        "trace_id": getattr(request.state, "trace_id", None),
    }
