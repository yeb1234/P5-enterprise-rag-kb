"""缓存与恢复的离线单测：含真·重启模拟（L1 清空 / 新 store 实例）。"""
from __future__ import annotations

import pytest

from cache import (CachePolicy, CacheStore, VersionController, authz_snapshot,
                   build_key, compute_doc_fingerprint, normalize_query,
                   note_shutdown, policy_version, query_hash, run_startup_checks,
                   was_graceful)

NOW = "2026-11-14T12:00:00Z"


def _key(**kw):
    base = dict(question="怎么配网", authz="authz-1", data_version="1",
                policy_ver="pol-1", now=NOW)
    base.update(kw)
    return build_key(**{k: v for k, v in base.items() if k != "now"})


def _store(schema, **policy_kw):
    """每用例一个独立 schema（见 cache/tests/conftest.py），不再用 SQLite 文件。"""
    return CacheStore(schema, CachePolicy(**policy_kw))


# ---------------------------------------------------------------- key
def test_key_changes_with_every_safety_dimension():
    k0, parts = _key()
    assert len(k0) == 35 and parts["org"] == "org"
    for field, val in (("authz", "authz-2"), ("data_version", "2"),
                       ("policy_ver", "pol-2"), ("embedding_cache_key", "external:bge-m3"),
                       ("model", "deepseek-v4.1-flash-1017"), ("top_k", 8),
                       ("freshness_on", True), ("rerank", "external")):
        assert _key(**{field: val})[0] != k0, field


def test_query_normalization_is_stable():
    assert normalize_query("  怎么  配网 \n") == "怎么 配网"
    assert query_hash("怎么配网") == query_hash(" 怎么配网  ")


def test_authz_snapshot_ignores_department_order_but_not_content():
    a = authz_snapshot(role="user", department_id="RD",
                       visible_department_ids=["RD", "MK"], grant_version=1)
    b = authz_snapshot(role="user", department_id="RD",
                       visible_department_ids=["MK", "RD"], grant_version=1)
    assert a == b
    assert a != authz_snapshot(role="user", department_id="RD",
                               visible_department_ids=["RD", "MK"], grant_version=2)


def test_doc_fingerprint_changes_on_any_document_change():
    rows = [{"id": "a", "updated_at": NOW, "chunk_count": 2, "revision": "1"}]
    f1 = compute_doc_fingerprint(rows)
    assert f1 != compute_doc_fingerprint(rows + [{"id": "b", "updated_at": NOW,
                                                  "chunk_count": 1, "revision": "1"}])
    assert f1 != compute_doc_fingerprint([{**rows[0], "chunk_count": 3}])


def test_policy_version_tracks_content():
    p = {"half_life_days": 90, "blend": 0.65}
    assert policy_version(p) == policy_version({"blend": 0.65, "half_life_days": 90})
    assert policy_version(p) != policy_version({**p, "blend": 0.5})


# ---------------------------------------------------------------- 双层缓存
def test_l1_hit_and_stats(schema):
    s = _store(schema)
    k, parts = _key()
    s.put(k, parts, {"hits": [1, 2]}, now=NOW)
    val, level = s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val == {"hits": [1, 2]} and level == "l1"
    assert s.stats.hits == 1 and s.stats.writes == 1


def test_restart_keeps_l2_entries_but_clears_l1(schema):
    """真·重启模拟：清空 L1 + 新建 store 实例（模拟进程重启），L2 仍命中。"""
    s = _store(schema)
    k, parts = _key()
    s.put(k, parts, {"answer": "A"}, now=NOW)
    s.clear_memory()
    val, level = s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val == {"answer": "A"} and level == "l2"
    s2 = _store(schema)  # 全新进程视角
    val2, level2 = s2.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val2 == {"answer": "A"} and level2 == "l2"


def test_ttl_expiry_returns_miss(schema):
    s = _store(schema, ttl_seconds=60, ttl_jitter=0)
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now=NOW, jitter=0)
    assert s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1",
                 now="2026-11-14T12:02:00Z")[0] is None
    assert s.stats.invalidations.get("ttl", 0) >= 1


def test_version_or_authz_change_is_a_miss(schema):
    """懒失效：不删条目，只靠 key/版本复核让旧数据不可命中。"""
    s = _store(schema)
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now=NOW)
    assert s.get(k, data_version="2", authz="authz-1", policy_ver="pol-1", now=NOW)[0] is None
    assert "data_version_changed" in s.stats.invalidations
    s.put(k, parts, {"x": 2}, now=NOW)
    assert s.get(k, data_version="1", authz="authz-2", policy_ver="pol-1", now=NOW)[0] is None
    assert "authz_snapshot_changed" in s.stats.invalidations


def test_negative_cache_has_shorter_ttl(schema):
    s = _store(schema, ttl_seconds=900, negative_ttl_seconds=60, ttl_jitter=0)
    k, parts = _key()
    neg = s.put(k, parts, [], is_negative=True, now=NOW, jitter=0)
    assert neg.is_negative and neg.expires_at == "2026-11-14T12:01:00Z"


def test_l1_capacity_evicts_lru(schema):
    s = _store(schema, l1_capacity=2, l2_capacity=100)
    for i in range(3):
        k, parts = _key(question=f"q{i}")
        s.put(k, parts, {"i": i}, now=NOW)
    assert len(s._l1) == 2 and s.stats.evictions >= 1


def test_l2_capacity_eviction(schema):
    s = _store(schema, l1_capacity=100, l2_capacity=2)
    for i in range(4):
        k, parts = _key(question=f"q{i}")
        s.put(k, parts, {"i": i}, now=NOW)
    n = s.enforce_capacity()
    assert n == 2 and s.count() == 2


def test_runtime_state_survives_new_store(schema):
    s = _store(schema)
    s.state_set("knowledge_policy", '{"blend":0.5}', now=NOW)
    assert _store(schema).state_get("knowledge_policy") == '{"blend":0.5}'


def test_version_controller_bump_is_lazy(schema):
    s = _store(schema)
    vc = VersionController(s)
    r = vc.bump_data_version(reason="doc_update", now=NOW)
    assert r.old_version == "0" and r.new_version == "1" and r.strategy == "lazy"
    assert vc.data_version() == "1"
    r2 = vc.eager_purge("drill")
    assert r2.strategy == "eager"


# ---------------------------------------------------------------- 启动恢复
def test_startup_recovers_orphan_tasks_idempotently(schema, conn):
    """孤儿任务（status=analyzing）重启后被重投，且只投一次。

    改造前这里会给一个临时 SQLite 文件 CREATE TABLE documents；现在业务表由
    conftest 在独立 schema 里用 `scripts/pg_init.sql` 建好，测试直接插真实行。
    """
    conn.execute(
        "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,"
        "error_msg,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d1", "u1", "a.md", "a.md", 10, "analyzing", 0, "", NOW, NOW),
    )
    conn.commit()
    s = CacheStore(schema)
    calls = []
    r1 = run_startup_checks(s, now=NOW, requeue=lambda d, f: calls.append((d, f)))
    assert r1.healthy and r1.requeued == 1 and calls == [("d1", "a.md")]

    conn.execute("UPDATE documents SET status='ready' WHERE id='d1'")
    conn.commit()
    r2 = run_startup_checks(s, now=NOW, requeue=lambda d, f: calls.append((d, f)))
    assert r2.orphan_task_ids == [] and r2.requeued == 0 and len(calls) == 1
    assert r2.boot_count == 2


def test_startup_purges_ghost_vectors_and_expired_cache(schema, conn):
    """幽灵向量（无对应 chunks 的 embedding）被对账清掉；过期缓存也被清。"""
    conn.execute(
        "INSERT INTO chunk_embeddings(chunk_id,model,dim,vec,updated_at)"
        " VALUES (?,?,?,?,?)", ("ghost", "m", 3, "[]", NOW),
    )
    conn.commit()
    s = CacheStore(schema, CachePolicy(ttl_seconds=10, ttl_jitter=0))
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now="2026-11-14T11:00:00Z", jitter=0)
    r = run_startup_checks(s, now=NOW)
    assert r.ghost_vectors == 1 and r.expired_cache_purged == 1 and r.healthy
    assert s.state_get("doc_fingerprint")


def test_startup_bumps_data_version_when_docs_changed_between_boots(schema, conn):
    """两次启动之间文档指纹变化 → data_version 递增（懒失效的入口）。"""
    conn.execute(
        "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,"
        "error_msg,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d1", "u1", "a.md", "a.md", 10, "ready", 1, "", NOW, NOW),
    )
    conn.commit()
    s = CacheStore(schema)
    run_startup_checks(s, now=NOW)
    assert VersionController(s).data_version() == "0"

    conn.execute(
        "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,"
        "error_msg,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("d2", "u1", "b.md", "b.md", 10, "ready", 1, "", NOW, NOW),
    )
    conn.commit()
    run_startup_checks(s, now=NOW)
    assert VersionController(s).data_version() == "1"


def test_graceful_shutdown_marker(schema):
    s = _store(schema)
    assert was_graceful(s) is False
    note_shutdown(s, now=NOW)
    assert was_graceful(s) is True
    r = run_startup_checks(s, now=NOW)
    assert r.last_shutdown_at == NOW


def test_startup_report_categories_are_complete(schema):
    r = run_startup_checks(_store(schema), now=NOW)
    cats = {c.category for c in r.checks}
    assert {"discardable", "rebuildable", "must_persist"} <= cats
    d = r.as_dict()
    assert d["healthy"] is True and d["checks"] and d["notes"]
