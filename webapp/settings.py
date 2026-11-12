"""运行时配置：LLM / Embedding / Rerank 提供者选择与本地端点。

设计取舍：
- 生产对应配置中心 / 环境注入；演示切片落 SQLite（app_settings），**切换即时生效、无需重启**。
- .env 只作为「首次默认值」来源；之后以库表为准，可在「模型设置」页改。
- 只允许白名单键与枚举值，避免任意键污染；未知键直接拒绝。
"""
from __future__ import annotations

import os
import sqlite3
import time

from webapp import db
from webapp.envfile import load_env

load_env()

DEFAULTS: dict[str, str] = {
    # LLM：offline（离线占位）| sensenova（外部 DeepSeek 代理）| discovery（Intern-AI 多模型网关）| local（LM Studio）
    "llm_provider": os.environ.get("WEBAPP_LLM_PROVIDER", "sensenova"),
    "local_llm_base_url": os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1234/v1"),
    "local_llm_model": os.environ.get("LOCAL_LLM_MODEL", "qwen2.5-coder-7b-instruct"),
    "sensenova_model": os.environ.get("SENSENOVA_MODEL", "deepseek-flash"),
    "discovery_model": os.environ.get("DISCOVERY_MODEL", "deepseek-v4.1-flash-1017"),
    # Embedding：offline（词法）| local（LM Studio Bge M3）| external（硅基流动 BAAI/bge-m3 等 OpenAI 兼容）
    "embedding_provider": os.environ.get("WEBAPP_EMBEDDING_PROVIDER", "local"),
    "local_embedding_base_url": os.environ.get("LOCAL_EMBED_BASE_URL", "http://127.0.0.1:1234/v1"),
    "local_embedding_model": os.environ.get("LOCAL_EMBED_MODEL", "text-embedding-bge-m3"),
    "external_embedding_base_url": os.environ.get("EXTERNAL_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"),
    "external_embedding_model": os.environ.get("EXTERNAL_EMBED_MODEL", "BAAI/bge-m3"),
    # Rerank：off | llm（用当前 LLM 打分重排）| external（Cohere 风格 /rerank，如硅基流动/TEI）
    # 注：LM Studio 不暴露 /v1/rerank，故没有 "local" 这一档。
    "rerank_provider": os.environ.get("WEBAPP_RERANK_PROVIDER", "off"),
    "external_rerank_base_url": os.environ.get("EXTERNAL_RERANK_BASE_URL",
                                               "https://api.siliconflow.cn/v1"),
    "external_rerank_model": os.environ.get("EXTERNAL_RERANK_MODEL",
                                            "BAAI/bge-reranker-v2-m3"),
}

ALLOWED = set(DEFAULTS)
ENUMS: dict[str, set[str]] = {
    "llm_provider": {"offline", "sensenova", "discovery", "local"},
    "embedding_provider": {"offline", "local", "external"},
    "rerank_provider": {"off", "llm", "external"},
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get_all() -> dict[str, str]:
    out = dict(DEFAULTS)
    conn = db.get_conn()
    try:
        for r in conn.execute("SELECT key, value FROM app_settings"):
            if r["key"] in ALLOWED:
                out[r["key"]] = r["value"]
    except sqlite3.OperationalError:
        pass  # 表尚未初始化（独立脚本/首次启动前）：回落到默认值
    finally:
        conn.close()
    return out


def get(key: str) -> str:
    return get_all().get(key, DEFAULTS.get(key, ""))


def effective_embedding(cfg: dict | None = None) -> dict:
    """当前生效的 embedding 配置，作为「端点 / 模型 / 缓存命名空间」的唯一事实源。

    为什么要有这个函数：向量按 (chunk_id, model) 缓存在 chunk_embeddings 表，
    如果切换模型后仍沿用同一个 model 字符串，就会复用旧模型的向量——维度不一致时
    _cosine 会静默返回 0，检索看似正常实则全错。因此 cache_key 必须带上
    provider + 模型名，让「本地 Bge M3」与「硅基流动 BAAI/bge-m3」的缓存天然隔离。
    """
    cfg = cfg or get_all()
    kind = cfg.get("embedding_provider", "offline")
    if kind == "external":
        base = cfg.get("external_embedding_base_url", "")
        model = cfg.get("external_embedding_model", "")
        # 硅基流动的嵌入与重排是同一账号同一把 key；允许用 WEBAPP_EMBEDDING_API_KEY 单独指定。
        # 注意：不能叫 EMBEDDING_API_KEY —— 那是 backend/ 骨架的同名配置，命名空间要隔离。
        api_key = (os.environ.get("WEBAPP_EMBEDDING_API_KEY")
                   or os.environ.get("RERANK_API_KEY", ""))
        return {
            "kind": "external", "base_url": base, "model": model, "api_key": api_key,
            "name": f"external:{model}", "cache_key": f"external:{model}",
        }
    if kind == "local":
        base = cfg.get("local_embedding_base_url", "")
        model = cfg.get("local_embedding_model", "")
        return {
            "kind": "local", "base_url": base, "model": model, "api_key": "",
            "name": f"local:{model}", "cache_key": f"local:{model}",
        }
    return {
        "kind": "offline", "base_url": "", "model": "", "api_key": "",
        "name": "offline-lexical", "cache_key": "offline-lexical",
    }


def set_many(patch: dict) -> dict[str, str]:
    now = _now()
    conn = db.get_conn()
    try:
        for k, v in patch.items():
            if k not in ALLOWED:
                raise ValueError(f"未知配置项: {k}")
            sv = str(v)
            if k in ENUMS and sv not in ENUMS[k]:
                raise ValueError(f"{k} 取值非法: {sv}")
            conn.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, sv, now),
            )
        conn.commit()
    finally:
        conn.close()
    return get_all()
