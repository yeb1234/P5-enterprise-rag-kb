"""身份、组织、授权与审计模型。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPkMixin


class Tenant(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    quota_bytes: Mapped[int] = mapped_column(default=50 * 1024 * 1024 * 1024, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Department(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """部门树：path 用物化路径("/祖先id/父id/自己id/")，一次 prefix 查询取整棵子树。"""
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("departments.id"), nullable=True
    )
    path: Mapped[str] = mapped_column(Text, default="/", nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    parent: Mapped["Department | None"] = relationship(remote_side="Department.id")

    __table_args__ = (UniqueConstraint("tenant_id", "name", "parent_id", name="uq_dept_name"),)


class User(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "users"
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("departments.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="dept_user", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    department: Mapped[Department | None] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_email"),)


class RefreshToken(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """刷新令牌轮换：每次刷新作废旧令牌并签发新令牌，支持单点登出。"""
    __tablename__ = "refresh_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ACLGrant(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    """显式授权表 —— 也是「逐行 / 逐段落精细权限」的落点。

    当前版本只在 PermissionService 里用到 KB / DOCUMENT 两档；
    scope=chunk / paragraph 的判定钩子已经存在（见 filter_candidates），
    后续只需补数据 + 打开开关，不需要改表结构或改调用方。
    """
    __tablename__ = "acl_grants"
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user | department | role
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)  # kb | document | chunk
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="document", nullable=False)
    action: Mapped[str] = mapped_column(String(16), default="read", nullable=False)
    effect: Mapped[str] = mapped_column(String(8), default="allow", nullable=False)  # allow | deny
    # 预留：段落级条件，例如 {"doc_id": "...", "chunk_ids": [...], "sensitivity_max": 2}
    conditions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_acl_lookup", "tenant_id", "subject_type", "subject_id", "resource_type"),
        Index("ix_acl_conditions", "conditions", postgresql_using="gin"),
    )


class AuditLog(UUIDPkMixin, TenantMixin, Base):
    """只追加、不修改、不删除。生产可按月做声明式分区 + 冷存储归档。"""
    __tablename__ = "audit_logs"
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    params_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False, index=True
    )

    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)
