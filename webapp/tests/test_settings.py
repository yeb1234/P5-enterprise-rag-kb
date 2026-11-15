"""设置与提供者选择单测（不触网）：默认值、持久化/校验、LLM 选择、检索回退、rerank 跳过。"""
import pytest

from webapp import providers, rag, settings


def test_defaults_present_and_offline():
    cfg = settings.get_all()
    for k in settings.DEFAULTS:
        assert k in cfg
    # conftest 强制离线，保证测试不依赖本地 LM Studio
    assert cfg["llm_provider"] == "offline"
    assert cfg["embedding_provider"] == "offline"


def test_set_many_persists_and_validates():
    settings.set_many({"local_llm_model": "qwen-test"})
    assert settings.get("local_llm_model") == "qwen-test"
    with pytest.raises(ValueError):
        settings.set_many({"llm_provider": "bogus"})
    with pytest.raises(ValueError):
        settings.set_many({"nope": "x"})
    settings.set_many({"local_llm_model": settings.DEFAULTS["local_llm_model"]})


def test_get_llm_selection(monkeypatch):
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "llm_provider": "offline"})
    assert isinstance(providers.get_llm(), providers.OfflineLLMProvider)

    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "llm_provider": "local", "local_llm_model": "m1"})
    p = providers.get_llm()
    assert isinstance(p, providers.OpenAILikeLLMProvider) and p.model == "m1"

    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "llm_provider": "sensenova", "sensenova_model": "deepseek-flash"})
    p = providers.get_llm()
    assert isinstance(p, providers.OpenAILikeLLMProvider) and p.model == "deepseek-flash"


def _cands():
    return [
        {"chunk_id": "c1", "doc_id": "d1", "idx": 0, "text": "设备配网步骤：长按复位键", "title": "配网"},
        {"chunk_id": "c2", "doc_id": "d2", "idx": 0, "text": "账号绑定流程", "title": "账号"},
    ]


def test_embedding_fallback_to_lexical(monkeypatch):
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "embedding_provider": "local"})
    monkeypatch.setattr(rag, "_load_ready_chunks", _cands)
    monkeypatch.setattr(rag, "_semantic_rank",
                        lambda q, c, cfg: (_ for _ in ()).throw(RuntimeError("down")))
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert hits
    assert meta["retrieval_mode"] == "lexical(fallback)"
    assert meta["fallback_reason"].startswith("embedding_unavailable")


def test_rerank_unavailable_skipped(monkeypatch):
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "embedding_provider": "offline", "rerank_provider": "external"})
    monkeypatch.setattr(rag, "_load_ready_chunks", _cands)
    monkeypatch.setattr(providers, "rerank", lambda *a, **k: None)
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert hits and meta["rerank"] == "external_unavailable(skipped)"


def _cands2():
    return [
        {"chunk_id": "c1", "doc_id": "d1", "idx": 0, "text": "设备配网步骤：长按复位键", "title": "配网"},
        {"chunk_id": "c2", "doc_id": "d2", "idx": 0, "text": "配网失败请检查 Wi-Fi 频段", "title": "配网2"},
    ]


class _FakeLLM(providers.LLMProvider):
    def __init__(self, text):
        self.text = text
        self.model_label = "fake"

    def stream(self, q, r):
        yield self.text

    def complete(self, prompt, system=None, max_tokens=256, temperature=0.0):
        return self.text


def test_rerank_with_llm_offline_none():
    assert providers.rerank_with_llm("q", ["a", "b"], providers.OfflineLLMProvider()) is None


def test_rerank_with_llm_parse():
    fake = _FakeLLM('好的：[{"index":0,"score":9},{"index":1,"score":3}]')
    assert providers.rerank_with_llm("q", ["a", "b"], fake) == [9.0, 3.0]


def test_rerank_llm_applied_and_reorders(monkeypatch):
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "embedding_provider": "offline", "rerank_provider": "llm"})
    monkeypatch.setattr(rag, "_load_ready_chunks", _cands2)
    fake = _FakeLLM('[{"index":0,"score":2},{"index":1,"score":9}]')
    monkeypatch.setattr(providers, "get_llm", lambda cfg=None: fake)
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert len(hits) == 2
    assert hits[0]["chunk_id"] == "c2"          # LLM 打分更高的排前面
    assert meta["rerank"] == "applied:llm"


def test_cosine_bounds():
    assert rag._cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert rag._cosine([1, 0], [0, 1]) == pytest.approx(0.0)


# ---- 外部 embedding（硅基流动 BAAI/bge-m3）----
_EXT = {
    **settings.DEFAULTS,
    "embedding_provider": "external",
    "external_embedding_base_url": "https://api.siliconflow.cn/v1",
    "external_embedding_model": "BAAI/bge-m3",
}


def test_external_embedding_effective_config(monkeypatch):
    # 这两把 key 的优先级是「独立嵌入 key > 共用的 RERANK_API_KEY」，
    # 不清掉会带走 .env 里的真实值——那样的断言在本机能过、换台机器就挂，
    # 而且会把真 key 打进出错信息里。
    monkeypatch.delenv("WEBAPP_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EXTERNAL_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("EXTERNAL_EMBED_MODEL", raising=False)
    monkeypatch.setenv("RERANK_API_KEY", "sk-test")
    eff = settings.effective_embedding(_EXT)
    assert eff["kind"] == "external"
    assert eff["base_url"] == "https://api.siliconflow.cn/v1"
    assert eff["model"] == "BAAI/bge-m3"
    assert eff["api_key"] == "sk-test"
    assert eff["name"] == "external:BAAI/bge-m3"


def test_external_embedding_prefers_dedicated_key(monkeypatch):
    """独立嵌入 key 存在时必须优先于共用的 RERANK_API_KEY。"""
    monkeypatch.setenv("RERANK_API_KEY", "sk-shared")
    monkeypatch.setenv("WEBAPP_EMBEDDING_API_KEY", "sk-dedicated")
    eff = settings.effective_embedding(_EXT)
    assert eff["api_key"] == "sk-dedicated"


def test_embedding_cache_key_isolated_across_providers():
    """切模型后必须换缓存命名空间，否则会复用旧模型向量、余弦静默失真。"""
    local = settings.effective_embedding({**settings.DEFAULTS, "embedding_provider": "local"})
    external = settings.effective_embedding(_EXT)
    offline = settings.effective_embedding({**settings.DEFAULTS, "embedding_provider": "offline"})
    keys = {local["cache_key"], external["cache_key"], offline["cache_key"]}
    assert len(keys) == 3


def test_get_embedding_external_uses_openai_like_with_key(monkeypatch):
    monkeypatch.delenv("WEBAPP_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EXTERNAL_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("EXTERNAL_EMBED_MODEL", raising=False)
    monkeypatch.setenv("RERANK_API_KEY", "sk-test")
    p = providers.get_embedding(_EXT)
    assert isinstance(p, providers.OpenAILikeEmbeddingProvider)
    assert p.model == "BAAI/bge-m3"
    assert p.api_key == "sk-test"
    assert p.name == "external:BAAI/bge-m3"


def test_retrieve_external_embedding_uses_semantic_rank(monkeypatch):
    """external 档也要走向量排序（旧代码只认 local，会静默退化成词法）。"""
    monkeypatch.setattr(settings, "get_all", lambda: _EXT)
    monkeypatch.setattr(rag, "_load_ready_chunks", _cands)
    calls = {}

    def fake_rank(q, c, cfg):
        # 契约：返回 (排序结果, 后端说明)。后端说明会进 mode，方便排查走了哪条路径。
        calls["hit"] = True
        for x in c:
            x["score"] = 0.9
        return c, "external_embedding(external:BAAI/bge-m3)"

    monkeypatch.setattr(rag, "_semantic_rank", fake_rank)
    hits, meta = rag.retrieve("如何配网", with_meta=True)
    assert calls.get("hit") is True
    assert hits
    assert meta["retrieval_mode"].startswith("hybrid: lexical_gate + external_embedding")
    assert meta["embedding_model"] == "external:BAAI/bge-m3"
