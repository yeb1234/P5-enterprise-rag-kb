"""缓存 key 构造：每一段都对应一类会出事故的失效场景。

设计要点：
- **先按权限过滤，再写缓存**（项目硬约束）。缓存里永远不存越权内容，
  命中后仍要做一次轻量 `decide()` / 授权快照比对，不把 TTL 当权限控制。
- 授权快照 `authz_snapshot` 让转岗 / 角色变更 / 授权版本递增后旧 key 自然不命中。
- 数据版本 `data_version` 让文档增删改后旧答案立即失效，不必等 TTL。
- 模型名、embedding cache_key、策略版本都在 key 里，避免串模型与串配置。
- 返回 `(key, parts)`：parts 供前端逐段展示「这个 key 为什么这样拼」。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cache.contracts import CACHE_SCHEMA_VERSION, ORG_NAMESPACE


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_query(question: str) -> str:
    """归一化提问：去首尾空白、折叠连续空格、统一大小写。中文不做分词改写。"""
    return re.sub(r"\s+", " ", (question or "").strip()).lower()


def query_hash(question: str) -> str:
    return _sha(normalize_query(question))[:20]


def authz_snapshot(*, role: str = "", department_id: str = "",
                   visible_department_ids: list[str] | None = None,
                   grant_version: int = 0, org_namespace: str = ORG_NAMESPACE) -> str:
    """授权快照：身份 / 角色 / 部门 / 可见部门集合 / 授权版本 / 组织命名空间。

    转岗、角色变更、ACLGrant 变更（grant_version 递增）都会改变这个值，
    从而让旧缓存 key 不可命中——这是「先过滤后写」之外的第二道防线。
    """
    payload = "|".join([
        org_namespace, role or "-", department_id or "-",
        ",".join(sorted(visible_department_ids or [])),
        str(grant_version),
    ])
    return _sha(payload)[:16]


def compute_doc_fingerprint(rows: list[dict[str, Any]]) -> str:
    """从文档表算集合指纹，用于对账（不替代 data_version 计数器）。

    取 count + 最新 updated_at + chunk_count 之和 + checksum 摘要，
    任何一篇文档增删改都会改变它。
    """
    if not rows:
        return "empty"
    ids = sorted(str(r.get("id") or "") for r in rows)
    stamps = sorted(str(r.get("updated_at") or "") for r in rows)
    chunks = sum(int(r.get("chunk_count") or 0) for r in rows)
    checks = sorted(str(r.get("checksum") or r.get("revision") or "") for r in rows)
    return _sha("|".join([
        str(len(rows)), stamps[-1] if stamps else "", str(chunks),
        _sha(",".join(ids)), _sha(",".join(checks)),
    ]))[:20]


def policy_version(policy: dict[str, Any]) -> str:
    """策略版本 = 策略内容的稳定哈希；改半衰期 / 过期处置都会变。"""
    return _sha(json.dumps(policy, sort_keys=True, ensure_ascii=False))[:12]


def build_key(*, question: str, kind: str = "retrieval",
             embedding_cache_key: str = "offline-lexical",
             model: str = "", top_k: int = 4, retrieval_mode: str = "",
             rerank: str = "off", freshness_on: bool = False,
             authz: str = "", data_version: str = "", policy_ver: str = "",
             org_namespace: str = ORG_NAMESPACE) -> tuple[str, dict[str, str]]:
    """构造缓存 key。返回短 key 与可读的各段拆解。"""
    parts = {
        "schema": CACHE_SCHEMA_VERSION,
        "org": org_namespace,
        "kind": kind,
        "query": query_hash(question),
        "embedding": embedding_cache_key,
        "model": model or "-",
        "top_k": str(top_k),
        "retrieval_mode": retrieval_mode or "-",
        "rerank": rerank or "off",
        "freshness": "on" if freshness_on else "off",
        "authz": authz or "anonymous",
        "data_version": data_version or "0",
        "policy_version": policy_ver or "0",
    }
    raw = "|".join(f"{k}={parts[k]}" for k in sorted(parts))
    return "kb:" + _sha(raw)[:32], parts


def key_matches(entry: dict[str, Any], *, data_version: str, authz: str,
                policy_ver: str) -> bool:
    """命中后的轻量复核：版本或授权变了就当未命中，绝不返回旧数据。"""
    return (entry.get("data_version") == data_version
            and entry.get("authz_snapshot") == authz
            and entry.get("policy_version") == policy_ver)
