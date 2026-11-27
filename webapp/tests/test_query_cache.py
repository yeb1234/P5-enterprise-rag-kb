"""query 向量缓存单测（不触网、不依赖真实端点）。

要守住的四件事：
1. 同一句话第二次取向量不再回源（省掉整段同步调用）；
2. 换模型命名空间（local: / external:）必须天然不命中，否则会拿旧模型的向量去排序；
3. 取出来的是副本 —— 调用方改坏它不能污染缓存里那份；
4. TTL / 容量是回收语义，过期或挤爆就 miss，不能当成「权限或新鲜度」用。
"""
import pytest

from webapp import providers, settings

CFG_LOCAL = {
    "embedding_provider": "local",
    "local_embedding_base_url": "http://127.0.0.1:9/v1",
    "local_embedding_model": "bge-m3-test",
}
CFG_EXTERNAL = {
    "embedding_provider": "external",
    "external_embedding_base_url": "http://127.0.0.1:9/v1",
    "external_embedding_model": "bge-m3-test",
}


class _Stub:
    """按「第几次调用」返回不同向量，用来区分回源与命中。"""

    def __init__(self):
        self.calls = 0
        self.texts: list[str] = []

    def embed(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        base = float(self.calls)
        return [[base, base + 0.5, base + 1.0] for _ in texts]


@pytest.fixture
def stub(monkeypatch):
    s = _Stub()
    monkeypatch.setattr(providers, "get_embedding", lambda cfg=None: s)
    providers.QUERY_VEC_CACHE.clear()
    yield s
    providers.QUERY_VEC_CACHE.clear()


def test_second_call_hits_cache(stub):
    v1 = providers.embed_query("宝宝夜里总醒怎么办", CFG_LOCAL)
    v2 = providers.embed_query("宝宝夜里总醒怎么办", CFG_LOCAL)
    assert stub.calls == 1, "第二次必须命中缓存，不能再打端点"
    assert v1 == v2
    assert providers.last_query_embedding_cached() is True


def test_different_text_or_model_misses(stub):
    providers.embed_query("问题 A", CFG_LOCAL)
    providers.embed_query("问题 B", CFG_LOCAL)
    assert stub.calls == 2, "不同文本必须各自回源"
    # 换命名空间：同名模型但 provider 不同 → cache_key 不同 → 不允许命中
    providers.embed_query("问题 A", CFG_EXTERNAL)
    assert stub.calls == 3, "换 embedding 实现必须重新取，不能复用旧命名空间的向量"
    assert settings.effective_embedding(CFG_LOCAL)["cache_key"] != \
        settings.effective_embedding(CFG_EXTERNAL)["cache_key"]


def test_returned_vector_is_copy(stub):
    v1 = providers.embed_query("同一句话", CFG_LOCAL)
    v1[0] = 999.0
    v2 = providers.embed_query("同一句话", CFG_LOCAL)
    assert v2[0] != 999.0, "调用方改返回值不能污染缓存"


def test_ttl_expiry_and_lru_eviction():
    c = providers._QueryVecCache(maxsize=2, ttl=0.0)
    key = ("m", "h")
    c.put(key, [1.0])
    assert c.get(key) is None, "ttl=0 应当立刻过期"
    assert c.stats()["expired"] == 1

    c = providers._QueryVecCache(maxsize=2, ttl=3600.0)
    c.put(("m", "a"), [1.0])
    c.put(("m", "b"), [2.0])
    c.get(("m", "a"))            # a 变热，b 成为最旧
    c.put(("m", "c"), [3.0])     # 挤掉 b
    assert c.get(("m", "b")) is None
    assert c.get(("m", "a")) == [1.0]
    assert c.stats()["evicted"] == 1


def test_stats_hit_rate():
    c = providers._QueryVecCache(maxsize=4, ttl=3600.0)
    c.put(("m", "x"), [1.0])
    c.get(("m", "x"))
    c.get(("m", "y"))
    s = c.stats()
    assert (s["hits"], s["misses"], s["hit_rate"]) == (1, 1, 0.5)
