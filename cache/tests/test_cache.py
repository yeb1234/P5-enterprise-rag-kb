"""缓存与恢复的离线单测：含真·重启模拟（L1 清空 / 新 store 实例）。"""
from __future__ import annotations

import sqlite3

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


def _store(tmp_path, **policy_kw):
    return CacheStore(tmp_path / "cache.db", CachePolicy(**policy_kw))


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
def test_l1_hit_and_stats(tmp_path):
    s = _store(tmp_path)
    k, parts = _key()
    s.put(k, parts, {"hits": [1, 2]}, now=NOW)
    val, level = s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val == {"hits": [1, 2]} and level == "l1"
    assert s.stats.hits == 1 and s.stats.writes == 1


def test_restart_keeps_l2_entries_but_clears_l1(tmp_path):
    """真·重启模拟：清空 L1 + 新建 store 实例（模拟进程重启），L2 仍命中。"""
    s = _store(tmp_path)
    k, parts = _key()
    s.put(k, parts, {"answer": "A"}, now=NOW)
    s.clear_memory()
    val, level = s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val == {"answer": "A"} and level == "l2"
    s2 = _store(tmp_path)  # 全新进程视角
    val2, level2 = s2.get(k, data_version="1", authz="authz-1", policy_ver="pol-1", now=NOW)
    assert val2 == {"answer": "A"} and level2 == "l2"


def test_ttl_expiry_returns_miss(tmp_path):
    s = _store(tmp_path, ttl_seconds=60, ttl_jitter=0)
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now=NOW, jitter=0)
    assert s.get(k, data_version="1", authz="authz-1", policy_ver="pol-1",
                 now="2026-11-14T12:02:00Z")[0] is None
    assert s.stats.invalidations.get("ttl", 0) >= 1


def test_version_or_authz_change_is_a_miss(tmp_path):
    """懒失效：不删条目，只靠 key/版本复核让旧数据不可命中。"""
    s = _store(tmp_path)
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now=NOW)
    assert s.get(k, data_version="2", authz="authz-1", policy_ver="pol-1", now=NOW)[0] is None
    assert "data_version_changed" in s.stats.invalidations
    s.put(k, parts, {"x": 2}, now=NOW)
    assert s.get(k, data_version="1", authz="authz-2", policy_ver="pol-1", now=NOW)[0] is None
    assert "authz_snapshot_changed" in s.stats.invalidations


def test_negative_cache_has_shorter_ttl(tmp_path):
    s = _store(tmp_path, ttl_seconds=900, negative_ttl_seconds=60, ttl_jitter=0)
    k, parts = _key()
    neg = s.put(k, parts, [], is_negative=True, now=NOW, jitter=0)
    assert neg.is_negative and neg.expires_at == "2026-11-14T12:01:00Z"


def test_l1_capacity_evicts_lru(tmp_path):
    s = _store(tmp_path, l1_capacity=2, l2_capacity=100)
    for i in range(3):
        k, parts = _key(question=f"q{i}")
        s.put(k, parts, {"i": i}, now=NOW)
    assert len(s._l1) == 2 and s.stats.evictions >= 1


def test_l2_capacity_eviction(tmp_path):
    s = _store(tmp_path, l1_capacity=100, l2_capacity=2)
    for i in range(4):
        k, parts = _key(question=f"q{i}")
        s.put(k, parts, {"i": i}, now=NOW)
    n = s.enforce_capacity()
    assert n == 2 and s.count() == 2


def test_runtime_state_survives_new_store(tmp_path):
    s = _store(tmp_path)
    s.state_set("knowledge_policy", '{"blend":0.5}', now=NOW)
    assert _store(tmp_path).state_get("knowledge_policy") == '{"blend":0.5}'


def test_version_controller_bump_is_lazy(tmp_path):
    s = _store(tmp_path)
    vc = VersionController(s)
    r = vc.bump_data_version(reason="doc_update", now=NOW)
    assert r.old_version == "0" and r.new_version == "1" and r.strategy == "lazy"
    assert vc.data_version() == "1"
    r2 = vc.eager_purge("drill")
    assert r2.strategy == "eager"


# ---------------------------------------------------------------- 启动恢复
def _business_tables(path):
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, filename TEXT, status TEXT, updated_at TEXT,
            chunk_count INTEGER DEFAULT 0, checksum TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            chunk_id TEXT, model TEXT, dim INTEGER, vec TEXT, updated_at TEXT);
    """)
    return con


def test_startup_recovers_orphan_tasks_idempotently(tmp_path):
    path = tmp_path / "cache.db"
    con = _business_tables(path)
    con.execute("INSERT INTO documents VALUES ('d1','a.md','analyzing',?,0,'')", (NOW,))
    con.commit(); con.close()
    s = CacheStore(path)
    calls = []
    r1 = run_startup_checks(s, now=NOW, requeue=lambda d, f: calls.append((d, f)))
    assert r1.healthy and r1.requeued == 1 and calls == [("d1", "a.md")]
    # 真实重跑会把状态改成 ready/error；模拟之
    con = sqlite3.connect(str(path))
    con.execute("UPDATE documents SET status='ready' WHERE id='d1'")
    con.commit(); con.close()
    r2 = run_startup_checks(s, now=NOW, requeue=lambda d, f: calls.append((d, f)))
    assert r2.orphan_task_ids == [] and r2.requeued == 0 and len(calls) == 1
    assert r2.boot_count == 2


def test_startup_purges_ghost_vectors_and_expired_cache(tmp_path):
    path = tmp_path / "cache.db"
    con = _business_tables(path)
    con.execute("INSERT INTO chunk_embeddings VALUES ('ghost','m',3,'[]',?)", (NOW,))
    con.commit(); con.close()
    s = CacheStore(path, CachePolicy(ttl_seconds=10, ttl_jitter=0))
    k, parts = _key()
    s.put(k, parts, {"x": 1}, now="2026-11-14T11:00:00Z", jitter=0)
    r = run_startup_checks(s, now=NOW)
    assert r.ghost_vectors == 1 and r.expired_cache_purged == 1 and r.healthy
    assert s.state_get("doc_fingerprint")


def test_startup_bumps_data_version_when_docs_changed_between_boots(tmp_path):
    path = tmp_path / "cache.db"
    con = _business_tables(path)
    con.execute("INSERT INTO documents VALUES ('d1','a.md','ready',?,1,'c1')", (NOW,))
    con.commit(); con.close()
    s = CacheStore(path)
    run_startup_checks(s, now=NOW)
    assert VersionController(s).data_version() == "0"
    con = sqlite3.connect(str(path))
    con.execute("INSERT INTO documents VALUES ('d2','b.md','ready',?,1,'c2')", (NOW,))
    con.commit(); con.close()
    run_startup_checks(s, now=NOW)
    assert VersionController(s).data_version() == "1"


def test_graceful_shutdown_marker(tmp_path):
    s = _store(tmp_path)
    assert was_graceful(s) is False
    note_shutdown(s, now=NOW)
    assert was_graceful(s) is True
    r = run_startup_checks(s, now=NOW)
    assert r.last_shutdown_at == NOW


def test_startup_report_categories_are_complete(tmp_path):
    r = run_startup_checks(_store(tmp_path), now=NOW)
    cats = {c.category for c in r.checks}
    assert {"discardable", "rebuildable", "must_persist"} <= cats
    d = r.as_dict()
    assert d["healthy"] is True and d["checks"] and d["notes"]
