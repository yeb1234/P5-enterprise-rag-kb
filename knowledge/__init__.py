"""知识治理子系统：入库新鲜度 + 事实冲突检测 + 结构化证据记录。

目录地图（自解释）：
  contracts.py   数据契约（FreshnessPolicy / DocMeta / Claim / Conflict / EvidenceRecord）
  freshness.py   时间衰减、适用性门控、过期处置、重排与可观测汇总
  claims.py      claim 级规则抽取、归一化、防误报、主题相似度
  conflicts.py   冲突检测、冲突图连通分量、按簇裁决、证据记录
  tests/         离线单测，零外部调用

快速使用：
    from knowledge import detect_and_build, extract_claims, rerank
    claims = extract_claims(text, doc_id="d1")
    record = detect_and_build(claims)
    print(record.conflict_signal, record.as_dict()["warnings"])
"""
from knowledge.contracts import (Claim, Conflict, DocMeta, EvidenceRecord,
                                 FreshnessPolicy, FreshnessResult)
from knowledge.claims import extract_claims, topic_similarity
from knowledge.conflicts import (build_evidence_record, connected_components,
                                 detect_and_build, detect_conflicts, resolve_cluster)
from knowledge.freshness import (age_days, decay_factor, evaluate, meta_from_row,
                                 parse_ts, rerank, summary)

__all__ = [
    "Claim", "Conflict", "DocMeta", "EvidenceRecord", "FreshnessPolicy", "FreshnessResult",
    "extract_claims", "topic_similarity",
    "detect_conflicts", "connected_components", "resolve_cluster",
    "build_evidence_record", "detect_and_build",
    "parse_ts", "age_days", "decay_factor", "evaluate", "rerank", "summary", "meta_from_row",
]
