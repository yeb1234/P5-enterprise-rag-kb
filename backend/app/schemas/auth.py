from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_slug: str | None = None  # 多租户入口；单租户部署可省略


class UserOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None
    email: str
    full_name: str
    role: str
    status: str

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "dept_user"
    department_id: uuid.UUID | None = None


class UserPatch(BaseModel):
    full_name: str | None = None
    role: str | None = None
    department_id: uuid.UUID | None = None
    status: str | None = None


class DepartmentCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    path: str
    level: int

    model_config = {"from_attributes": True}


class MeOut(UserOut):
    tenant_name: str | None = None
    department_name: str | None = None
    permissions: list[str] = []

    model_config = {"from_attributes": True}
