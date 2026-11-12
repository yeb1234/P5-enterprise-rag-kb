"""枚举与常量：把「状态机」集中定义，避免各处散落魔法字符串。"""
from enum import Enum


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    DEPT_ADMIN = "dept_admin"
    DEPT_USER = "dept_user"
    VIEWER = "viewer"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    DEPT = "dept"
    CONFIDENTIAL = "confidential"


# 敏感度偏序：数值越大越敏感，权限判定只比数值，不写 if/else 长链
SENSITIVITY_LEVEL = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.INTERNAL.value: 1,
    Sensitivity.DEPT.value: 2,
    Sensitivity.CONFIDENTIAL.value: 3,
}


class DocStatus(str, Enum):
    DRAFT = "draft"            # 在线编辑中，未提交
    PENDING = "pending"        # 已上传，等待入队
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    INDEXED = "indexed"        # 可检索
    FAILED = "failed"
    ARCHIVED = "archived"
    DELETED = "deleted"


class UploadStatus(str, Enum):
    INITIALIZED = "initialized"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"      # 分片齐了，等待合并
    ASSEMBLING = "assembling"
    COMPLETED = "completed"    # 合并完成、文档已创建、任务已入队
    INSTANT = "instant"        # 秒传命中
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BatchStatus(str, Enum):
    COLLECTING = "collecting"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"        # 部分成功
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestStage(str, Enum):
    QUEUED = "queued"
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"
    FINALIZE = "finalize"
    DONE = "done"


# 阶段权重：文件越大，解析/向量化占比越高；这里给一套可解释的默认值
STAGE_WEIGHTS = {
    IngestStage.QUEUED.value: 0.00,
    IngestStage.PARSE.value: 0.30,
    IngestStage.CHUNK.value: 0.10,
    IngestStage.EMBED.value: 0.40,
    IngestStage.INDEX.value: 0.15,
    IngestStage.FINALIZE.value: 0.05,
    IngestStage.DONE.value: 1.00,
}

# 上传阶段（前端分片传输）占整个文档进度的 20%，后端处理占 80%
UPLOAD_PHASE_WEIGHT = 0.20
INGEST_PHASE_WEIGHT = 0.80


class Action(str, Enum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    SEARCH = "search"
    EXPORT = "export"
    MANAGE = "manage"
    GRANT = "grant"


class ResourceType(str, Enum):
    KB = "kb"
    DOCUMENT = "document"
    CHUNK = "chunk"
    CATEGORY = "category"
    BATCH = "batch"
    TASK = "task"
    TENANT = "tenant"


class ACLScope(str, Enum):
    """权限粒度。当前实现到 KB / DOCUMENT；CHUNK / PARAGRAPH 是架构预留。"""
    KB = "kb"
    DOCUMENT = "document"
    CHUNK = "chunk"
    PARAGRAPH = "paragraph"


class Visibility(str, Enum):
    DEPT = "dept"        # 仅本部门
    TENANT = "tenant"    # 全租户可见（仍受敏感度约束）
    PUBLIC = "public"    # 公开（内网登录用户可见）


ROLE_SENSITIVITY_CEILING = {
    Role.PLATFORM_ADMIN.value: 3,
    Role.TENANT_ADMIN.value: 3,
    Role.DEPT_ADMIN.value: 2,
    Role.DEPT_USER.value: 1,
    Role.VIEWER.value: 1,
}

WRITE_ROLES = {
    Role.PLATFORM_ADMIN.value,
    Role.TENANT_ADMIN.value,
    Role.DEPT_ADMIN.value,
    Role.DEPT_USER.value,
}

ALLOWED_MIME_PREFIXES = (
    "application/pdf",
    "text/",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/json",
    "image/png",
    "image/jpeg",
    # 浏览器对部分文件给不出 MIME（空 type），由扩展名推断 + 首片魔数校验兜底
    "application/octet-stream",
)

ERROR_CODES = {
    "AUTH_REQUIRED": 401,
    "AUTH_INVALID": 401,
    "TOKEN_EXPIRED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "VALIDATION_ERROR": 422,
    "RATE_LIMITED": 429,
    "QUOTA_EXCEEDED": 413,
    "PAYLOAD_TOO_LARGE": 413,
    "STORAGE_ERROR": 502,
    "UPSTREAM_ERROR": 502,
    "TASK_ERROR": 500,
    "INTERNAL_ERROR": 500,
}
