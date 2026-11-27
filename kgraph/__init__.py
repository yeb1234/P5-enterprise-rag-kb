"""kgraph —— 图谱检索（GraphRAG 路由）的纯函数包。

## 为什么叫 kgraph 而不是 graphrag
微软有一个同名的 PyPI 包 `graphrag`。本地目录若也叫 `graphrag/`，在项目根运行时会把
它遮蔽掉，将来谁 `pip install graphrag` 就撞车。改名只为一件事：不占别人的包名。

## 这个包在「三路取证」里的位置
同一个问题，三路各取所需（理念见 docs/kb-routes/01-design.md）：
- 向量检索：从原文里找证据；
- **图谱检索（本包）：沿关系找线索**，尤其是跨分块的多跳关系；
- LLM Wiki：读整理好的业务知识。

## 设计取舍
- **图里只放关系，不放正文**。PG 的 `chunks` 仍是唯一真值；图里的 `Chunk` 节点只有
  `chunk_id`，检索结果必须回到 PG 复核（文档是否仍 ready、分块是否还在）才算数。
  所以图数据库挂了或数据脏了，最坏结果是「少一路证据」，不是「答错」。
- **不依赖 webapp/**。`webapp/` 负责读 PG、取 LLM、写审计；本包只做纯计算与图查询，
  测试用假 store 即可覆盖，不需要真 Neo4j。
"""
from kgraph.contracts import GRAPH_NAMESPACE, GraphCandidate

__all__ = ["GRAPH_NAMESPACE", "GraphCandidate"]
