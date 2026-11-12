"""鉴权依赖：Bearer 解析、当前用户、admin 门禁。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from webapp import db, security

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="未登录或令牌缺失")
    payload = security.verify_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id,username,email,role,created_at FROM users WHERE id=?",
            (payload["sub"],),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="用户不存在")
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
