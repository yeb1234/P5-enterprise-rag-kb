"""对象存储抽象：原始文件、上传分片、版本快照都走这里。

分片合并用 MinIO compose_object（服务端拼接，不把 2GB 文件读进内存）；
本地文件系统实现只用于「没有 Docker 时先跑通代码」的学习场景。
"""
from __future__ import annotations

import io
import os
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

from minio import Minio
from minio.commonconfig import ComposeSource

from app.core.config import settings


@runtime_checkable
class ObjectStore(Protocol):
    def ensure_bucket(self) -> None: ...
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def stream(self, key: str, block_size: int = 1024 * 1024): ...  # 大文件校验用，不整文件读进内存
    def compose(self, source_keys: list[str], dest_key: str, content_type: str) -> None: ...
    def remove(self, keys: list[str]) -> None: ...
    def exists(self, key: str) -> bool: ...
    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str: ...


class MinioStore:
    def __init__(self):
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket = settings.MINIO_BUCKET

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(self.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)

    def get(self, key: str) -> bytes:
        resp = self.client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stream(self, key: str, block_size: int = 1024 * 1024):
        resp = self.client.get_object(self.bucket, key)
        try:
            while True:
                block = resp.read(block_size)
                if not block:
                    break
                yield block
        finally:
            resp.close()
            resp.release_conn()

    def compose(self, source_keys: list[str], dest_key: str, content_type: str) -> None:
        """服务端顺序拼接。除最后一片外每片需 >= 5MiB，因此分片配置固定 8MiB。"""
        sources = [ComposeSource(self.bucket, k) for k in source_keys]
        self.client.compose_object(self.bucket, dest_key, sources)
        # 合并成功后删临时分片，失败则保留以便人工排查 / 重试
        self.remove(source_keys)

    def remove(self, keys: list[str]) -> None:
        from minio.deleteobjects import DeleteObject

        errors = list(self.client.remove_objects(self.bucket, (DeleteObject(k) for k in keys)))
        if errors:
            raise RuntimeError(f"删除对象失败: {errors[:3]}")

    def exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception:
            return False

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        from datetime import timedelta

        return self.client.presigned_get_object(self.bucket, key, expires=timedelta(seconds=expires_seconds))


class LocalStore:
    """学习调试用：目录结构与对象 key 一一对应，方便直接打开文件看内容。"""

    def __init__(self):
        self.root = Path(settings.LOCAL_STORAGE_DIR).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root)):
            raise ValueError("非法对象路径")
        return p

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def stream(self, key: str, block_size: int = 1024 * 1024):
        with self._path(key).open("rb") as src:
            while True:
                block = src.read(block_size)
                if not block:
                    break
                yield block

    def compose(self, source_keys: list[str], dest_key: str, content_type: str) -> None:
        dest = self._path(dest_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            for key in source_keys:
                with self._path(key).open("rb") as src:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
        self.remove(source_keys)

    def remove(self, keys: list[str]) -> None:
        for key in keys:
            p = self._path(key)
            if p.exists():
                p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        # 本地模式没有签名概念，由 /documents/{id}/download 走后端鉴权后流式返回
        return f"/api/v1/documents/download-local?key={key}"


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = MinioStore() if settings.STORAGE_BACKEND == "minio" else LocalStore()
        try:
            _store.ensure_bucket()
        except Exception:  # infra 未就绪时允许 API 先启动，健康检查会显示 degraded
            import logging

            logging.getLogger("app").warning("对象存储初始化失败，上传接口将返回 502")
    return _store


def object_key(tenant_id: str, *parts: str) -> str:
    """对象路径强制带 tenant 前缀，禁止业务层自己拼路径。"""
    return "/".join([tenant_id, *parts])
