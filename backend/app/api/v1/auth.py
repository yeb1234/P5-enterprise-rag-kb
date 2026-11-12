"""登录 / 刷新令牌轮换 / 登出 / 当前用户 / 开发期数据初始化。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import Action, Role
from app.core.deps import CurrentUser, DbSession, Principal, request_meta
from app.core.errors import Conflict, Forbidden, Unauthorized
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    sha256_token,
    verify_password,
)
from app.db.session import get_db
from app.models.identity import Department, RefreshToken, Tenant, User
from app.schemas.auth import LoginRequest, MeOut, TokenPair, UserOut
from app.services.audit import record

router = APIRouter(prefix="/auth", tags=["认证"])

_COOKIE = "kb_refresh"


def _issue_tokens(db: Session, user: User, response: Response) -> tuple[TokenPair, str]:
    access = create_access_token(user)
    refresh, digest, expiry = create_refresh_token(user)
    db.add(RefreshToken(
        tenant_id=user.tenant_id, user_id=user.id, token_hash=digest, expires_at=expiry
    ))
    db.flush()
    response.set_cookie(
        _COOKIE, refresh, httponly=True, secure=settings.is_production,
        samesite="lax", max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, path="/api/v1/auth",
    )
    return TokenPair(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    ), digest


@router.post("/login", response_model=TokenPair, summary="账号密码登录")
def login(payload: LoginRequest, response: Response, request: Request, db: DbSession):
    tenant = db.query(Tenant).filter(Tenant.slug == (payload.tenant_slug or "demo")).first()
    if tenant is None:
        tenant = db.query(Tenant).filter(Tenant.status == "active").first()
    if tenant is None:
        raise Unauthorized("租户不存在，请先调用 /auth/bootstrap 初始化")
    user = db.query(User).filter(
        User.tenant_id == tenant.id, User.email == payload.email.lower(), User.status == "active"
    ).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        record(db, tenant_id=tenant.id, action="login", resource_type="user",
               result="failed", detail={"email": payload.email}, meta=request_meta(request), commit=True)
        raise Unauthorized("账号或密码错误")
    user.last_login_at = datetime.now(timezone.utc)
    tokens, _ = _issue_tokens(db, user, response)
    record(db, tenant_id=tenant.id, user_id=user.id, action="login", resource_type="user",
           result="success", meta=request_meta(request))
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair, summary="刷新令牌（轮换）")
def refresh(request: Request, response: Response, db: DbSession):
    raw = request.cookies.get(_COOKIE)
    if not raw:
        raise Unauthorized("缺少刷新令牌")
    try:
        payload = decode_token(raw, "refresh")
    except PermissionError as exc:
        raise Unauthorized("刷新令牌已失效", code=str(exc.args[0])) from exc
    digest = sha256_token(raw)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == digest).first()
    if row is None or row.revoked_at is not None or row.expires_at < datetime.now(timezone.utc):
        raise Unauthorized("刷新令牌已失效，请重新登录")
    user = db.query(User).filter(User.id == row.user_id, User.status == "active").first()
    if user is None:
        raise Unauthorized("账号不可用")
    row.revoked_at = datetime.now(timezone.utc)
    tokens, new_digest = _issue_tokens(db, user, response)
    row.replaced_by = new_digest
    db.commit()
    return tokens


@router.post("/logout", summary="登出并吊销刷新令牌")
def logout(request: Request, response: Response, db: DbSession):
    raw = request.cookies.get(_COOKIE)
    if raw:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == sha256_token(raw)).first()
        if row:
            row.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(_COOKIE, path="/api/v1/auth")
    return {"ok": True}


@router.get("/me", response_model=MeOut, summary="当前登录用户与权限")
def me(user: CurrentUser, db: DbSession):
    from app.services.permission import PermissionService

    perms = PermissionService(db, user)
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    dept = db.query(Department).filter(Department.id == user.department_id).first() if user.department_id else None
    return MeOut(
        **UserOut(id=user.id, tenant_id=user.tenant_id, department_id=user.department_id,
                  email=user.email, full_name="", role=user.role, status="active").model_dump(),
        tenant_name=tenant.name if tenant else None,
        department_name=dept.name if dept else None,
        permissions=perms.allowed_sensitivities(),
    )


@router.post("/bootstrap", summary="开发期初始化演示数据（仅空库且非生产可用）")
def bootstrap(db: DbSession):
    """一键造出「两个部门 + 三类角色」，方便直接验证隔离、权限、看板。"""
    if settings.is_production:
        raise Forbidden("生产环境禁止调用")
    if db.query(Tenant).count() > 0:
        raise Conflict("已有租户，禁止重复初始化")

    tenant = Tenant(name="演示企业", slug="demo", plan="standard")
    db.add(tenant)
    db.flush()
    hq = Department(tenant_id=tenant.id, name="总部", path=f"/{uuid.uuid4()}/", level=1)
    db.add(hq)
    db.flush()
    hq.path = f"/{hq.id}/"
    rd = Department(tenant_id=tenant.id, name="研发中心", parent_id=hq.id,
                    path=f"/{hq.id}/{uuid.uuid4()}/", level=2)
    mk = Department(tenant_id=tenant.id, name="市场部", parent_id=hq.id,
                    path=f"/{hq.id}/{uuid.uuid4()}/", level=2)
    db.add_all([rd, mk])
    db.flush()
    rd.path, mk.path = f"/{hq.id}/{rd.id}/", f"/{hq.id}/{mk.id}/"

    users = [
        User(tenant_id=tenant.id, department_id=hq.id, email="admin@demo.local",
             hashed_password=hash_password("Admin@12345"), full_name="平台管理员",
             role=Role.TENANT_ADMIN.value),
        User(tenant_id=tenant.id, department_id=rd.id, email="dev@demo.local",
             hashed_password=hash_password("Dev@12345"), full_name="研发同事",
             role=Role.DEPT_USER.value),
        User(tenant_id=tenant.id, department_id=mk.id, email="viewer@demo.local",
             hashed_password=hash_password("View@12345"), full_name="市场访客",
             role=Role.VIEWER.value),
    ]
    db.add_all(users)
    db.commit()
    return {
        "ok": True,
        "tenant": "demo",
        "accounts": [
            {"email": "admin@demo.local", "password": "Admin@12345", "role": "tenant_admin"},
            {"email": "dev@demo.local", "password": "Dev@12345", "role": "dept_user"},
            {"email": "viewer@demo.local", "password": "View@12345", "role": "viewer"},
        ],
        "note": "演示口令仅用于本地学习，生产环境禁止保留",
    }
