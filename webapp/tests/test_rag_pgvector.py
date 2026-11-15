"""pgvector 检索路径的用例。

为什么单独开一个文件：`rag.retrieve()` 在 embedding_provider=offline 时根本不会走向量分支，
其余单测全默认 offline，所以新加的 SQL 路径平时一行都跑不到——
**没被跑到的分支等于没写**，这里用真表 + 定长假向量把它钉住。
"""

from __future__ import annotations

import json

import pytest

from webapp import db, providers, rag, settings

DIM = 1024
MODEL = "stub-1024"
CACHE_KEY = f"external:{MODEL}"


def _vec(first: float, second: float = 0.0) -> list[float]:
    v = [0.0] * DIM
    v[0], v[1] = first, second
    return v


class _StubEmbedding:
    """固定返回同一条查询向量，避免测试依赖外部 API。"""

    def __init__(self, vec: list[float]):
        self._vec = vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vec) for _ in texts]


@pytest.fixture()
def seeded(monkeypatch):
    """往测试 schema 里写三条已知向量 + 两个 chunk。"""
    rows = [("pgv-a", _vec(1.0, 0.0)), ("pgv-b", _vec(0.9, 0.1)), ("pgv-c", _vec(0.0, 1.0))]
    conn = db.get_conn()
    try:
        for cid, v in rows:
            conn.execute(
                "INSERT INTO chunk_embeddings(chunk_id, model, dim, vec, embedding, updated_at) "
                "VALUES (?,?,?,?,?::vector, now()) "
                "ON CONFLICT(chunk_id, model) DO UPDATE SET vec=excluded.vec, embedding=excluded.embedding",
                (cid, CACHE_KEY, DIM, json.dumps(v), json.dumps(v)),
            )
        conn.commit()
    finally:
        conn.close()

    cfg = {
        "embedding_provider": "external",
        "external_embedding_base_url": "http://stub.invalid/v1",
        "external_embedding_model": MODEL,
        "rerank_provider": "off",
    }
    monkeypatch.setattr(settings, "get_all", lambda: cfg)
    monkeypatch.setattr(providers, "get_embedding", lambda _cfg: _StubEmbedding(_vec(1.0, 0.0)))
    cands = [{"chunk_id": cid, "doc_id": "d", "idx": i, "text": f"t{i}", "title": "t"}
             for i, (cid, _) in enumerate(rows)]
    yield cfg, cands

    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM chunk_embeddings WHERE model = ?", (CACHE_KEY,))
        conn.commit()
    finally:
        conn.close()


def test_pgvector_rank_orders_like_python(seeded):
    """两条路径在同一批向量上必须给出同样的先后（分数只有浮点级差异）。"""
    cfg, cands = seeded
    pg = rag._semantic_rank_pg("任意问题", [dict(c) for c in cands], cfg)
    py = rag._semantic_rank_py("任意问题", [dict(c) for c in cands], cfg)
    assert [c["chunk_id"] for c in pg] == [c["chunk_id"] for c in py]
    assert pg[0]["chunk_id"] == "pgv-a"
    assert pg[0]["score"] > pg[1]["score"] > pg[2]["score"]


def test_pgvector_scores_are_cosine_similarity(seeded):
    """`1 - (a <=> b)` 必须等于余弦相似度，否则分数口径会与旧实现不一致。"""
    cfg, cands = seeded
    ranked = rag._semantic_rank_pg("任意问题", [dict(c) for c in cands], cfg)
    score = {c["chunk_id"]: c["_emb_score"] for c in ranked}
    assert score["pgv-a"] == pytest.approx(1.0, abs=1e-9)
    assert score["pgv-c"] == pytest.approx(0.0, abs=1e-9)


def test_backend_switch_and_fallback_note(seeded, monkeypatch):
    """`WEBAPP_VECTOR_BACKEND=python` 走旧路径；pgvector 出错时降级必须在 mode 里留痕。"""
    cfg, cands = seeded
    monkeypatch.setenv("WEBAPP_VECTOR_BACKEND", "python")
    assert rag.vector_backend() == "python"
    ranked, note = rag._semantic_rank("任意问题", [dict(c) for c in cands], cfg)
    assert note.startswith("external_embedding(")

    monkeypatch.setenv("WEBAPP_VECTOR_BACKEND", "pgvector")
    monkeypatch.setattr(rag, "_semantic_rank_pg",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no extension")))
    ranked, note = rag._semantic_rank("任意问题", [dict(c) for c in cands], cfg)
    assert note.startswith("pgvector_fallback_python:RuntimeError")
    assert ranked  # 降级后仍然有语义排序结果，不是退化成纯词法


def test_unknown_backend_falls_back_to_pgvector(monkeypatch):
    """拼错的开关值不能被当成 python（那会静默变慢且无人察觉）。"""
    monkeypatch.setenv("WEBAPP_VECTOR_BACKEND", "pgvec")
    assert rag.vector_backend() == "pgvector"
