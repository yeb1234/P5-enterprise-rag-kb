"""密码哈希与 JWT。刷新令牌只把 sha256 摘要落库，泄漏数据库也拿不到原文。"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _encode(subject: str, token_type: str, expires: timedelta, extra: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "jti": str(uuid.uuid4()),
        **extra,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user) -> str:
    return _encode(
        str(user.id),
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        {
            "tenant_id": str(user.tenant_id),
            "department_id": str(user.department_id) if user.department_id else None,
            "role": user.role,
        },
    )


def create_refresh_token(user) -> tuple[str, str, datetime]:
    """返回 (明文令牌, sha256 摘要, 过期时间)。明文只通过 httpOnly Cookie 交给浏览器。"""
    expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    token = _encode(str(user.id), "refresh", expires, {"tenant_id": str(user.tenant_id)})
    digest = sha256_token(token)
    expiry = datetime.now(timezone.utc) + expires
    return token, digest, expiry


def sha256_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """统一解码入口：验签、验过期、验类型，任何异常都对外收敛成 AUTH_INVALID。"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise PermissionError("TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise PermissionError("AUTH_INVALID") from exc
    if payload.get("typ") != expected_type:
        raise PermissionError("AUTH_INVALID")
    return payload
