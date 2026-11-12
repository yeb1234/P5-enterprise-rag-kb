"""请求/响应数据契约（pydantic，随 FastAPI 一并可用）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str | None = None
    password: str = Field(min_length=6, max_length=64)


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None = None
    role: str
    created_at: str


class ChatIn(BaseModel):
    question: str
    conversation_id: str | None = None
    resume_from: int = 0  # 断线恢复：从第几个 token 继续


class BadcaseCreate(BaseModel):
    eval_task_id: str
    question: str
    answer: str | None = None
    verdict: str | None = None       # 正确|错误|部分正确|不确定
    issue_desc: str | None = None
    source: str = "chat"             # chat|manual|eval


class BadcaseUpdate(BaseModel):
    verdict: str | None = None
    issue_desc: str | None = None
    status: str | None = None        # pending|in_review|resolved


class BadcaseFilter(BaseModel):
    eval_task_id: str | None = None
    status: str | None = None
    source: str | None = None
    start: str | None = None         # 时间范围起 YYYY-MM-DD 或 ISO
    end: str | None = None
    q: str | None = None             # 关键字检索（问题/回答/说明）
    limit: int = 200
