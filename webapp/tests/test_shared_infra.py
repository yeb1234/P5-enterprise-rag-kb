"""共享基础设施的契约测试：只测纯函数，不依赖演示脚本真的跑过。

为什么要单独测：演示脚本会写 PG / Redis，若测试依赖它的副作用，
测试之间就会互相污染（先跑演示与后跑演示结果不同）。
这里只钉住「key 怎么拼、search_path 怎么判、越权怎么识别」三条纯契约。
"""
from __future__ import annotations

import pytest

from shared_infra import (
    P5_SCHEMA,
    PROJECT_PREFIX,
    build_p5_key,
    is_foreign_schema,
    is_p5_key,
    key_owner,
    p5_search_path,
    search_path_is_valid,
)


def test_p5_key_has_project_and_user():
    key = build_p5_key("alice", "rl", "user")
    assert key == "p5kb:alice:rl:user"
    assert key.startswith(f"{PROJECT_PREFIX}:alice:")
    assert is_p5_key(key)


def test_weak_key_without_real_user_is_rejected():
    """`p5kb:rl:user` 把业务段当用户——两人会写进同一个 key，必须拒绝。"""
    assert not is_p5_key("p5kb:rl:user")
    assert not is_p5_key("p5kb:alice")     # 缺业务段


def test_neighbor_key_is_not_p5_key():
    assert not is_p5_key("p1slim:alice:rl:user")
    assert key_owner("p1slim:alice:rl:user") == "p1slim"
    assert key_owner("p5kb:alice:rl:user") == PROJECT_PREFIX
    assert key_owner("unknown:alice:rl:user") is None


@pytest.mark.parametrize("bad", ["", "a:b", "alice:wang", "alice/1", "x" * 65])
def test_invalid_user_id_raises(bad):
    with pytest.raises(ValueError):
        build_p5_key(bad, "rl", "user")


@pytest.mark.parametrize("bad_seg", ["", "rl:extra", "rl x"])
def test_invalid_segment_raises(bad_seg):
    with pytest.raises(ValueError):
        build_p5_key("alice", bad_seg)


def test_search_path_has_own_schema_then_public():
    path = p5_search_path()
    assert path == f"{P5_SCHEMA},public"
    assert search_path_is_valid(path)


@pytest.mark.parametrize("bad", ["public", f"{P5_SCHEMA}", "public,{P5_SCHEMA}", f"other,{P5_SCHEMA},public"])
def test_bad_search_path_rejected(bad):
    assert not search_path_is_valid(bad)


def test_foreign_schema_uses_whitelist():
    assert is_foreign_schema("p1_slimtrack")
    assert is_foreign_schema("p2_coreai")
    assert not is_foreign_schema(P5_SCHEMA)
    assert not is_foreign_schema("public")   # 扩展在这里，不能当越权
    assert not is_foreign_schema("")         # 空值不该被当成越权目标
