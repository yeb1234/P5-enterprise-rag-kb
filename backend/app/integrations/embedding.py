"""Embedding Provider —— 必须与 LLM 解耦。

重要事实：DeepSeek 的 OpenAI 兼容 API 只提供 chat/completions，没有 /embeddings。
所以语义检索需要单独接一个外部 embedding 服务（任意 OpenAI 兼容端点，例如 bge-m3）。
骨架默认用 stub：基于「特征哈希 + 字符二元组」的确定性伪向量，
能跑通分片→向量化→Milvus 检索→RRF 全链路，但只反映字面重合度，
不具备语义能力，禁止拿它做效果评估。换真实服务只需改 .env，代码零改动。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

import httpx

from app.core.config import settings


@runtime_checkable
class EmbeddingProvider(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokens(text: str) -> list[str]:
    """中文按字 + 相邻二元组，英文数字按词；让 stub 有基本的字面相似度语义。"""
    cleaned = re.sub(r"\s+", " ", text.lower()).strip()
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", cleaned)
    bigrams = [words[i] + words[i + 1] for i in range(len(words) - 1)]
    return words + bigrams


class StubEmbeddingProvider:
    """确定性特征哈希（feature hashing），同一段文本永远得到同一个向量。"""

    def __init__(self, dim: int | None = None):
        self.dim = dim or settings.EMBEDDING_DIM
        self.model_name = "stub-hashing"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in _tokens(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class OpenAICompatibleEmbedding:
    """任意遵循 OpenAI /embeddings 协议的外部服务。不部署本地模型。"""

    def __init__(self):
        self.dim = settings.EMBEDDING_DIM
        self.model_name = settings.EMBEDDING_MODEL
        self.base_url = (settings.EMBEDDING_BASE_URL or "").rstrip("/")
        if not self.base_url:
            raise ValueError("EMBEDDING_PROVIDER=openai_compatible 时必须配置 EMBEDDING_BASE_URL")

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model_name, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        vectors = [item["embedding"] for item in sorted(data, key=lambda x: x.get("index", 0))]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"embedding 维度 {len(vectors[0])} 与 EMBEDDING_DIM={self.dim} 不一致；"
                "更换模型需重建 Milvus collection 并全量重算向量"
            )
        return vectors


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = (
            OpenAICompatibleEmbedding()
            if settings.EMBEDDING_PROVIDER == "openai_compatible"
            else StubEmbeddingProvider()
        )
    return _provider
