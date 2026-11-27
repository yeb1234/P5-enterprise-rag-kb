"""图检索的契约：命名空间、候选、跳数打分表。"""
from __future__ import annotations

from dataclasses import dataclass

# 图里的命名空间。P5 的每个节点都带 `kb` 属性，所有 Cypher 必须带 `{kb: $kb}` 过滤——
# Neo4j 社区版只有一个业务库，和别的项目共用实例时只能靠属性隔离。
# 与 shared_infra.py 的项目前缀保持同一口径（p5kb）。
GRAPH_NAMESPACE = "p5kb"

# 跳数 → 分数。一跳（分块直接提到问题里的实体）最可信，之后逐跳衰减。
# 这是演示起始值，不是调参结论；写死成表是为了让「为什么这条排在那条后面」可解释。
HOP_SCORE: dict[int, float] = {1: 1.0, 2: 0.6, 3: 0.4}


@dataclass(frozen=True)
class GraphCandidate:
    """图谱路由给出的一个候选分块。

    `score` 只在图谱路由内部有意义（用于排序和融合时的名次），不与向量分数同量纲；
    融合用的是名次（RRF），不是把这个分数和余弦直接相加。
    """

    chunk_id: str
    score: float
    hop: int                    # 从命中实体出发走了几跳
    path: tuple[str, ...]       # 经过的实体名，一跳时为空
    matched: tuple[str, ...]    # 问题命中的起始实体

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "score": self.score, "hop": self.hop,
            "path": list(self.path), "matched": list(self.matched),
        }
