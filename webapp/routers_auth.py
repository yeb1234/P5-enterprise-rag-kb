"""认证路由：注册 / 登录 / 当前用户。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from webapp import db, security
from webapp.deps import get_current_user
from webapp.schemas import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@router.post("/register", response_model=TokenOut)
def register(body: RegisterIn):
    conn = db.get_conn()
    try:
        if conn.execute(
            "SELECT 1 FROM users WHERE username=?", (body.username,)
        ).fetchone():
            raise HTTPException(status_code=409, detail="用户名已存在")
        uid = security.new_id()
        salt, h = security.hash_password(body.password)
        conn.execute(
            "INSERT INTO users(id,username,email,password_salt,password_hash,role,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, body.username, body.email, salt, h, "user", _now()),
        )
        conn.commit()
    finally:
        conn.close()
    token = security.make_token(uid, "user")
    db.log_action(body.username, "user", "register", uid[:8], "注册新账号")
    return TokenOut(access_token=token, role="user", username=body.username)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id,username,password_salt,password_hash,role FROM users WHERE username=?",
            (body.username,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not security.verify_password(
        body.password, row["password_salt"], row["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = security.make_token(row["id"], row["role"])
    db.log_action(row["username"], row["role"], "login", row["id"][:8], "口令校验通过")
    return TokenOut(access_token=token, role=row["role"], username=row["username"])


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return UserOut(**user)
