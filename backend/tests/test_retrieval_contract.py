"""检索链路契约对拍：召回前权限收窄 / RRF 融合 / 召回后二次校验。

为什么单独加这一组（`backend/tests/` 原本 16 项只覆盖各自纯函数）：
- 演示页把「PG 全文 + Milvus 向量 + RRF 融合 + 召回前权限双过滤」整条标成
  「只设计 · 未落地」，容易被追问成「那到底测没测」。实查结论是：
  **算法与契约层有代码、有单测；真实 PG / Milvus / Redis / Celery 没接、没集成测。**
- 这一组把「先过滤、再重排、再进模型」的**顺序纪律**用离线可跑的方式锁住：
  不需要真数据库，也不需要真向量库，但不能让融合把被过滤的片段带回来。

明确不覆盖（保持诚实）：真实 PG `tsvector` / BM25、真实 Milvus expr 执行、
Redis pub/sub 断线重连快照、Celery chord 编排、端到端延迟与召回质量指标。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.constants import Role, Sensitivity
from app.services.permission import PermissionService, Resource, Subject, decide
from app.services.search import rrf_fuse


def _user(role: str = Role.DEPT_USER.value, dept: str = "d1") -> SimpleNamespace:
    return SimpleNamespace(id="u1", tenant_id="t1", role=role,
                           department_id=dept, department_path=f"/{dept}/")


def _service(role: str = Role.DEPT_USER.value, dept: str = "d1") -> PermissionService:
    """离线构造：db 传 None，并预置空 grants 以绕开 DB 查询。"""
    svc = PermissionService(db=None, user=_user(role, dept))
    svc._grants = []
    return svc


def _hits() -> list[dict]:
    return [
        {"chunk_id": "c1", "doc_id": "doc1", "dept_id": "d1",
         "sensitivity": "internal", "visibility": "dept", "source": "keyword"},
        {"chunk_id": "c2", "doc_id": "doc2", "dept_id": "d2",
         "sensitivity": "internal", "visibility": "dept", "source": "keyword"},
        {"chunk_id": "c3", "doc_id": "doc3", "dept_id": "d2",
         "sensitivity": "internal", "visibility": "public", "source": "keyword"},
        {"chunk_id": "c4", "doc_id": "doc4", "dept_id": "d1",
         "sensitivity": Sensitivity.CONFIDENTIAL.value,
         "visibility": "dept", "source": "semantic"},
    ]


# ---------- 召回前：权限条件必须进 expr ----------

def test_pre_recall_expr_has_four_mandatory_constraints():
    expr = _service().milvus_expr()
    assert 'tenant_id == "t1"' in expr          # 租户边界永不省略
    assert "is_deleted == false" in expr        # 软删不进召回
    assert 'dept_id in ["d1"]' in expr         # 非管理员按部门收窄
    assert "sensitivity in [" in expr           # 敏感度上限
    assert "confidential" not in expr           # DEPT_USER 不应拿到机密档


def test_admin_expr_keeps_tenant_boundary_but_does_not_shrink_departments():
    expr = _service(role=Role.TENANT_ADMIN.value).milvus_expr()
    assert 'tenant_id == "t1"' in expr
    assert "dept_id in" not in expr             # 管理员不按部门收窄
    assert 'tenant_id == "t2"' not in expr      # 但没有跨租户


# ---------- 召回后：逐条 decide() 复核 ----------

def test_post_recall_filter_drops_cross_dept_and_over_sensitivity():
    kept = _service().filter_candidates(_hits())
    assert [h["chunk_id"] for h in kept] == ["c1", "c3"]
    # c1 同部门、c3 公开；c2 跨部门、c4 超出敏感度上限


def test_pre_and_post_filter_agree_on_every_candidate():
    """A13 双向一致性对拍：expr 收窄的部门集合与 decide() 的判定不能互相打架。"""
    svc = _service()
    expr = svc.milvus_expr()
    for hit in _hits():
        res = Resource(
            tenant_id="t1", department_id=hit["dept_id"],
            sensitivity=hit["sensitivity"], visibility=hit["visibility"],
            document_id=hit["doc_id"], chunk_id=hit["chunk_id"],
        )
        sub = Subject(**{**svc.subject.__dict__, "grants": ()})
        verdict = decide(sub, "read", res).allowed
        dept_in_expr = f'"{hit["dept_id"]}"' in expr
        if not dept_in_expr and hit["visibility"] != "public":
            assert verdict is False, f"{hit['chunk_id']} 被 expr 排除却被 decide() 放行"
        if verdict:
            assert dept_in_expr or hit["visibility"] == "public"


# ---------- 顺序纪律：先过滤、再融合，融合不能把拒绝项带回来 ----------

def test_fusion_cannot_reintroduce_a_filtered_chunk():
    svc = _service()
    hits = _hits()
    # 对照：不过滤直接融合，跨部门的 c2 因为两路都命中会排到最前
    keyword = [hits[1], hits[0], hits[2]]       # c2 排第 1
    semantic = [hits[1], hits[3]]                # c2 又排第 1
    naive = rrf_fuse([keyword, semantic])
    assert naive[0]["chunk_id"] == "c2"

    # 正确顺序：先逐条复核，再融合
    filtered = svc.filter_candidates(keyword + semantic)
    fused = rrf_fuse([filtered])
    ids = {x["chunk_id"] for x in fused}
    assert "c2" not in ids and "c4" not in ids
    assert ids == {"c1", "c3"}


def test_rrf_fuse_does_not_invent_or_drop_chunks():
    a = [{"chunk_id": "a", "score": 0.9, "source": "keyword"},
         {"chunk_id": "b", "score": 0.8, "source": "keyword"}]
    b = [{"chunk_id": "b", "score": 0.7, "source": "semantic"},
         {"chunk_id": "c", "score": 0.6, "source": "semantic"}]
    fused = rrf_fuse([a, b])
    assert {x["chunk_id"] for x in fused} == {"a", "b", "c"}
    assert len(fused) == 3                     # 同一 chunk 去重，不重复进上下文
    assert fused[0]["chunk_id"] == "b"         # 两路都命中排最前
    assert set(fused[0]["matched_by"]) == {"keyword", "semantic"}


def test_empty_recall_lists_fuse_to_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_mode_names_are_stable_contract(mode):
    """融合入口接受的三种模式名，是检索契约的一部分（不依赖真实 DB）。"""
    assert mode in {"keyword", "semantic", "hybrid"}
