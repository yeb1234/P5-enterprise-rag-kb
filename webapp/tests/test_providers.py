"""适配器层单测：离线实现、默认选择、429 退避重试、始终 429 抛错。

这些测试不触网，用 fake http.client 验证重试契约；真实端点冒烟在 live_smoke 里。
"""
import json
import types

import pytest

from webapp import providers


def test_offline_provider_stream_tokens():
    p = providers.OfflineLLMProvider()
    toks = list(p.stream("如何配网？", [{"doc_title": "x", "text": "配网步骤"}]))
    assert toks and all(isinstance(t, str) for t in toks)


def test_get_llm_default_is_offline(monkeypatch):
    from webapp import settings
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "llm_provider": "offline"})
    p = providers.get_llm()
    assert isinstance(p, providers.OfflineLLMProvider)


def test_discovery_provider_uses_env(monkeypatch):
    """Discovery 网关：base/key 只读 .env，模型名可运行时切换。"""
    from webapp import settings
    monkeypatch.setenv("DISCOVERY_BASE_URL", "https://discovery.example/v1")
    monkeypatch.setenv("DISCOVERY_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "get_all",
                        lambda: {**settings.DEFAULTS, "llm_provider": "discovery",
                                 "discovery_model": "deepseek-v4.1-flash-1017"})
    p = providers.get_llm()
    assert isinstance(p, providers.OpenAILikeLLMProvider)
    assert p.base_url == "https://discovery.example/v1"
    assert p.api_key == "sk-test"
    assert p.model == "deepseek-v4.1-flash-1017"
    assert providers.get_model_label() == "deepseek-v4.1-flash-1017"


def _fake_http(status_seq, content="你好"):
    class FakeFP:
        def __init__(self, c):
            payload = json.dumps({"choices": [{"delta": {"content": c}}]})
            self._it = iter([f"data: {payload}\n".encode(), b"data: [DONE]\n"])

        def readline(self):
            try:
                return next(self._it)
            except StopIteration:
                return b""

    class FakeResp:
        def __init__(self, status):
            self.status = status
            self.fp = FakeFP(content) if status == 200 else None

        def read(self):
            return b""

        def close(self):
            pass

    class FakeConn:
        def __init__(self, host, timeout=None):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            s = status_seq.pop(0) if status_seq else 200
            return FakeResp(s)

        def read(self):
            return b""

        def close(self):
            pass

    return types.SimpleNamespace(HTTPSConnection=FakeConn)


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(providers.http, "client", _fake_http([429, 429, 200]))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "deepseek-flash")
    out = "".join(p.stream("hi", []))
    assert "你好" in out


def test_filter_inline_think_single_chunk():
    out = "".join(providers._filter_inline_think(iter([
        "<mm:think>The user asks about wifi.</mm:think>配网分三步：复位、连热点、输密码。"])))
    assert out == "配网分三步：复位、连热点、输密码。"


def test_filter_inline_think_split_across_chunks():
    """标记被切在两个 chunk 之间也要能正确剥掉。"""
    chunks = ["<mm:th", "ink>思考中…</mm:th", "ink>正式回答。"]
    assert "".join(providers._filter_inline_think(iter(chunks))) == "正式回答。"


def test_filter_inline_think_passthrough():
    text = "普通回答，没有思考标签，逐字输出。"
    assert "".join(providers._filter_inline_think(iter(list(text)))) == text


def test_classify_429_splits_quota_and_rate():
    quota = json.dumps({"error": {"message": "Quota exceeded.", "type": "quota_exceeded",
                                  "code": "quota_exceeded"}})
    rate = json.dumps({"error": {"message": "Rate limit exceeded. Please retry later.",
                                "type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}})
    anthropic_rate = json.dumps({"type": "error", "error": {"type": "rate_limit_error",
                                                            "message": "slow down"}})
    assert providers.classify_429(quota) == "quota"
    assert providers.classify_429(rate) == "rate"
    assert providers.classify_429(anthropic_rate) == "rate"


def test_backoff_uses_retry_after_and_jitter():
    assert providers.backoff_seconds(0, 5) == 5.0          # 听服务端
    assert providers.backoff_seconds(1, 999) == 30.0        # 上限 30s
    for attempt, base in ((0, 1), (1, 2), (2, 4), (5, 8)):
        v = providers.backoff_seconds(attempt)
        assert base <= v <= base + 0.5, (attempt, v)        # 指数 + 抖动


def _fake_http_429(payload: dict, headers: dict | None = None):
    class FakeResp:
        status = 429

        def read(self):
            return json.dumps(payload).encode()

        def getheader(self, name):
            return (headers or {}).get(name)

        def close(self):
            pass

    class FakeConn:
        def __init__(self, host, timeout=None):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    return types.SimpleNamespace(HTTPSConnection=FakeConn)


def test_quota_429_fails_fast_without_retry(monkeypatch):
    """quota_exceeded 不该重试：重试只烧时间，额度也不会回来。"""
    sleeps = []
    monkeypatch.setattr(providers.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(providers.http, "client", _fake_http_429(
        {"error": {"type": "quota_exceeded", "code": "quota_exceeded"}}))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "m")
    with pytest.raises(RuntimeError) as ei:
        list(p.stream("hi", []))
    assert "quota_exceeded" in str(ei.value)
    assert sleeps == [], "额度耗尽不应触发退避重试"


def test_rate_429_retries_then_raises(monkeypatch):
    sleeps = []
    monkeypatch.setattr(providers.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(providers.http, "client", _fake_http_429(
        {"error": {"type": "rate_limit_exceeded", "code": "rate_limit_exceeded"}}))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "m")
    with pytest.raises(RuntimeError):
        list(p.stream("hi", []))
    assert len(sleeps) == 3, sleeps          # 三次退避
    assert all(0 <= s <= 30 for s in sleeps)


def test_stream_tolerates_empty_choices(monkeypatch):
    """网关常发 choices: [] 的纯 usage 块，不能让 IndexError 把正常回答判成 model_error。"""
    class FakeFP:
        def __init__(self):
            self._it = iter([
                b'data: {"choices":[],"usage":{"total_tokens":10}}\n',
                ('data: ' + json.dumps({"choices": [{"delta": {"content": "你好"}}]}) + "\n").encode(),
                b'data: {"choices":[],"usage":{"total_tokens":20}}\n',
                b"data: [DONE]\n",
            ])

        def readline(self):
            try:
                return next(self._it)
            except StopIteration:
                return b""

    class FakeResp:
        status = 200
        fp = FakeFP()

        def read(self):
            return b""

        def close(self):
            pass

    class FakeConn:
        def __init__(self, host, timeout=None):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(providers.http, "client", types.SimpleNamespace(HTTPSConnection=FakeConn))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "m")
    assert "".join(p.stream("hi", [])) == "你好"


def test_always_429_raises(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(providers.http, "client", _fake_http([429, 429, 429]))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "deepseek-flash")
    with pytest.raises(RuntimeError):
        "".join(p.stream("hi", []))
