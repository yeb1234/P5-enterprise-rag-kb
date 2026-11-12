"""集中导入所有模型，保证 Base.metadata 完整（Alembic autogenerate 依赖这一点）。"""
from app.models.identity import ACLGrant, AuditLog, Department, RefreshToken, Tenant, User
from app.models.knowledge import (
    Category,
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeBase,
    Tag,
    document_tags,
)
from app.models.metrics import DailyStat, MetricEvent
from app.models.pipeline import BatchUpload, IngestJob, UploadChunk, UploadSession

__all__ = [
    "ACLGrant", "AuditLog", "Department", "RefreshToken", "Tenant", "User",
    "Category", "Chunk", "Document", "DocumentVersion", "KnowledgeBase", "Tag", "document_tags",
    "DailyStat", "MetricEvent",
    "BatchUpload", "IngestJob", "UploadChunk", "UploadSession",
]
