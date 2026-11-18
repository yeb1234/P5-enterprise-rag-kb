"""全局配置：所有可变参数必须来自环境变量，代码里不写死密钥、地址、阈值。

学习要点：pydantic-settings 会在进程启动时校验配置，缺关键项直接启动失败，
比运行时才发现拼错的环境变量名要安全得多。
"""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # 基础
    APP_NAME: str = "enterprise-kb"
    ENV: str = "dev"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8080"

    # 安全
    JWT_SECRET_KEY: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    RATE_LIMIT_PER_MINUTE: int = 120

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "kb"
    POSTGRES_PASSWORD: str = "kb_dev_pwd"
    POSTGRES_DB: str = "kb"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_CACHE_DB: int = 0
    REDIS_BROKER_DB: int = 1
    REDIS_RESULT_DB: int = 2

    # Milvus
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_TOKEN: str = ""
    MILVUS_COLLECTION: str = "kb_chunks"
    MILVUS_NUM_PARTITIONS: int = 32

    # 对象存储
    STORAGE_BACKEND: Literal["minio", "local"] = "minio"
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "kb_minio"
    MINIO_SECRET_KEY: str = "kb_minio_pwd"
    MINIO_BUCKET: str = "kb-documents"
    MINIO_SECURE: bool = False
    LOCAL_STORAGE_DIR: str = "./.data/objects"

    # LLM（外部 DeepSeek）
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-flash"
    DEEPSEEK_TIMEOUT_SECONDS: int = 60
    DEEPSEEK_THINKING: Literal["disabled", "enabled"] = "disabled"
    LLM_FALLBACK_TO_EXTRACTIVE: bool = True

    # Embedding（独立 provider，DeepSeek 没有 embeddings 接口）
    EMBEDDING_PROVIDER: Literal["stub", "openai_compatible"] = "stub"
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIM: int = 1024
    EMBEDDING_BATCH_SIZE: int = 32

    # 上传 / 流水线
    MAX_UPLOAD_SIZE_MB: int = 2048
    UPLOAD_CHUNK_SIZE_MB: int = 8
    UPLOAD_SESSION_TTL_HOURS: int = 48
    MAX_BATCH_FILES: int = 200
    MAX_CHUNK_CONCURRENCY: int = 4
    LARGE_FILE_PAGE_THRESHOLD: int = 200
    LARGE_FILE_SIZE_MB: int = 100
    PARSE_PAGE_BATCH: int = 40
    MAX_CONCURRENT_INGEST_PER_TENANT: int = 4
    PROGRESS_THROTTLE_SECONDS: float = 0.5
    SSE_HEARTBEAT_SECONDS: int = 15
    INGEST_MAX_RETRIES: int = 3
    INGEST_SOFT_TIME_LIMIT: int = 600
    INGEST_HARD_TIME_LIMIT: int = 900

    # 分块
    CHUNK_SIZE_CHARS: int = 1200
    CHUNK_OVERLAP_CHARS: int = 150

    # 检索上下文预算：召回可以宽，送进 LLM 的上下文要窄。
    # 中文单篇 MRC 题 3 篇足够；英文 RAGTruth 常一道题跨 2~4 个段落，单独放宽。
    SEARCH_CONTEXT_DOCS: int = 3
    SEARCH_CONTEXT_DOCS_EN: int = 4
    SEARCH_CONTEXT_CITATIONS_PER_DOC: int = 2
    SEARCH_CONTEXT_MAX_CITATIONS: int = 6

    # 返回给前端的证据窗口。中文段落通常较短，400 字足够；
    # 英文 RAGTruth 的操作步骤/命令名常出现在 400 字之后，单独放宽到 600。
    SEARCH_SNIPPET_CHARS: int = 400
    SEARCH_SNIPPET_CHARS_EN: int = 600

    # ---------- 派生配置 ----------
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def redis_url(self, db: int) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{db}"

    @property
    def cache_url(self) -> str:
        return self.redis_url(self.REDIS_CACHE_DB)

    @property
    def broker_url(self) -> str:
        return self.redis_url(self.REDIS_BROKER_DB)

    @property
    def result_url(self) -> str:
        return self.redis_url(self.REDIS_RESULT_DB)

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def upload_chunk_size_bytes(self) -> int:
        return self.UPLOAD_CHUNK_SIZE_MB * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
