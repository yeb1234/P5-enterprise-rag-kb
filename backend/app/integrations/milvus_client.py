"""Milvus 向量层。tenant_id 设为 partition key，检索表达式强制带权限条件。

设计红线：vector search 的 expr 必须由后端统一构造，前端传来的过滤条件只允许
收窄范围，不允许覆盖或删除租户/部门/敏感度条件 —— 见 build_permission_expr()。
"""
from __future__ import annotations

from typing import Any

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from app.core.config import settings


class MilvusVectorStore:
    def __init__(self):
        self.alias = "default"
        self.collection_name = settings.MILVUS_COLLECTION
        self._ready = False

    def _connect(self) -> None:
        if self._ready:
            return
        kwargs = {"alias": self.alias, "uri": settings.MILVUS_URI}
        if settings.MILVUS_TOKEN:
            kwargs["token"] = settings.MILVUS_TOKEN
        connections.connect(**kwargs)
        self._ready = True

    def ensure_collection(self) -> None:
        self._connect()
        if utility.has_collection(self.collection_name, using=self.alias):
            return
        fields = [
            FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64, is_partition_key=True),
            FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="dept_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="sensitivity", dtype=DataType.VARCHAR, max_length=16),
            FieldSchema(name="version_no", dtype=DataType.INT64),
            FieldSchema(name="is_deleted", dtype=DataType.BOOL),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM),
        ]
        schema = CollectionSchema(fields, description="知识库切片向量", enable_dynamic_field=False)
        collection = Collection(
            self.collection_name, schema=schema, using=self.alias,
            num_partitions=settings.MILVUS_NUM_PARTITIONS,
        )
        collection.create_index(
            "embedding",
            {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 64}},
        )
        for scalar in ("kb_id", "doc_id", "dept_id", "sensitivity"):
            collection.create_index(scalar, {"index_type": "Trie"})
        collection.load()

    def _collection(self) -> Collection:
        self._connect()
        self.ensure_collection()
        return Collection(self.collection_name, using=self.alias)

    @staticmethod
    def build_permission_expr(
        tenant_id: str,
        kb_ids: list[str] | None = None,
        dept_ids: list[str] | None = None,
        sensitivity_values: list[str] | None = None,
        extra: str | None = None,
    ) -> str:
        """唯一合法的检索表达式构造器。tenant_id 永远排第一，且不可被调用方移除。"""
        clauses = [f'tenant_id == "{tenant_id}"', "is_deleted == false"]
        if kb_ids:
            clauses.append("kb_id in [" + ", ".join(f'"{x}"' for x in kb_ids) + "]")
        if dept_ids:
            clauses.append("dept_id in [" + ", ".join(f'"{x}"' for x in dept_ids) + "]")
        if sensitivity_values:
            clauses.append("sensitivity in [" + ", ".join(f'"{v}"' for v in sensitivity_values) + "]")
        # extra 只允许来自服务层白名单（例如版本过滤），不接收裸前端字符串
        if extra:
            clauses.append(f"({extra})")
        return " and ".join(clauses)

    def upsert(self, rows: list[dict[str, Any]]) -> int:
        """幂等写入：主键是 chunk_id，重复执行只覆盖不新增。"""
        if not rows:
            return 0
        col = self._collection()
        columns = [
            [str(r["chunk_id"]) for r in rows],
            [str(r["tenant_id"]) for r in rows],
            [str(r["kb_id"]) for r in rows],
            [str(r["doc_id"]) for r in rows],
            [str(r.get("dept_id") or "") for r in rows],
            [str(r.get("sensitivity") or "internal") for r in rows],
            [int(r.get("version_no") or 1) for r in rows],
            [False] * len(rows),
            [r["embedding"] for r in rows],
        ]
        col.upsert(columns)
        col.flush()
        return len(rows)

    def search(self, vectors: list[list[float]], expr: str, top_k: int = 50) -> list[list[dict]]:
        col = self._collection()
        results = col.search(
            data=vectors,
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": max(64, top_k * 2)}},
            limit=top_k,
            expr=expr,
            output_fields=["chunk_id", "doc_id", "kb_id", "dept_id", "sensitivity", "version_no"],
        )
        out: list[list[dict]] = []
        for hits in results:
            out.append(
                [
                    {
                        "chunk_id": h.id,
                        "score": float(h.distance),
                        "doc_id": h.entity.get("doc_id"),
                        "kb_id": h.entity.get("kb_id"),
                        "dept_id": h.entity.get("dept_id"),
                        "sensitivity": h.entity.get("sensitivity"),
                    }
                    for h in hits
                ]
            )
        return out

    def delete_by_doc(self, tenant_id: str, doc_id: str) -> None:
        col = self._collection()
        col.delete(expr=f'tenant_id == "{tenant_id}" and doc_id == "{doc_id}"')
        col.flush()

    def list_chunk_ids(self, tenant_id: str, kb_id: str | None = None) -> set[str]:
        """对账用：分页遍历 Milvus 主键，与 PG 比对找孤儿向量。"""
        col = self._collection()
        expr = f'tenant_id == "{tenant_id}"'
        if kb_id:
            expr += f' and kb_id == "{kb_id}"'
        found: set[str] = set()
        iterator = col.query_iterator(expr=expr, output_fields=["chunk_id"], batch_size=1000)
        try:
            while True:
                batch = iterator.next()
                if not batch:
                    break
                found.update(str(row["chunk_id"]) for row in batch)
        finally:
            iterator.close()
        return found


_store: MilvusVectorStore | None = None


def get_vector_store() -> MilvusVectorStore:
    global _store
    if _store is None:
        _store = MilvusVectorStore()
    return _store
