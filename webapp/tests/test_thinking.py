"""低思考 / 快速档单测：证明配置、三条请求路径与展示标签共用同一口径。

约束：
- 全部离线，用 fake HTTP 捕获请求体，不发任何真实外部调用；
- 不改动 get_model_label() 的返回值（既有断言依赖它）；
- 不改 effective_embedding() 的 cache_key（向量缓存命名空间与推理档位正交）。
"""
import json
import types

import pytest

from webapp import providers, settings


# --------------------------------------------------------------------------
# fake HTTP：捕获 _stream_raw 发出的请求体
# --------------------------------------------------------------------------
def _capture_stream(captured: list):
    class FakeFP:
        def __init__(self):
            payload = json.dumps({"choices": [{"delta": {"content": "你好"}}]})
            self._it = iter([f"data: {payload}\n".encode(), b"data: [DONE]\n"])

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
        def request(self, method, path, body=None, headers=None):
            captured.append({"method": method, "path": path, "body": body,
                             "headers": headers or {}})

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    return lambda url, timeout: (FakeConn(), types.SimpleNamespace(path="/v1"))


# --------------------------------------------------------------------------
# 配置层
# --------------------------------------------------------------------------
def test_default_is_low_thinking():
    assert settings.DEFAULTS["llm_thinking"] == "disabled"
    assert settings.thinking_disabled(settings.DEFAULTS) is True
    assert settings.ENUMS["llm_thinking"] == {"disabled", "enabled"}


def test_thinking_fields_exact_payload():
    """与 backend/app/integrations/llm.py:52 的 thinking 字段逐字一致。"""
    assert providers.thinking_fields(disabled=True) == {"thinking": {"type": "disabled"}}
    assert providers.thinking_fields(disabled=False) == {}


def test_settings_rejects_invalid_thinking_value():
    with pytest.raises(ValueError):
        settings.set_many({"llm_thinking": "turbo"})
    with pytest.raises(ValueError):
        settings.set_many({"not_a_key": "x"})


# --------------------------------------------------------------------------
# 装配层
# --------------------------------------------------------------------------
def _cfg(thinking="disabled"):
    return {**settings.DEFAULTS, "llm_provider": "discovery",
            "discovery_model": "deepseek-v4.1-flash-1017",
            "llm_thinking": thinking}


def test_get_llm_propagates_thinking_mode(monkeypatch):
    monkeypatch.setattr(settings, "get_all", lambda: _cfg("disabled"))
    low = providers.get_llm()
    assert isinstance(low, providers.OpenAILikeLLMProvider)
    assert low.thinking_disabled is True
    assert low._extras() == {"thinking": {"type": "disabled"}}

    monkeypatch.setattr(settings, "get_all", lambda: _cfg("enabled"))
    full = providers.get_llm()
    assert full.thinking_disabled is False
    assert full._extras() == {}


def test_model_label_and_thinking_mode_label(monkeypatch):
    """档位单独给一个 label，不污染 get_model_label()。"""
    monkeypatch.setattr(settings, "get_all", lambda: _cfg("disabled"))
    assert providers.get_model_label() == "deepseek-v4.1-flash-1017"
    assert providers.get_thinking_mode() == "低思考 · 快速"
    monkeypatch.setattr(settings, "get_all", lambda: _cfg("enabled"))
    assert providers.get_thinking_mode() == "完整推理"
    assert providers.get_model_label() == "deepseek-v4.1-flash-1017"


def test_thinking_does_not_change_embedding_cache_key():
    low = settings.effective_embedding(_cfg("disabled"))
    full = settings.effective_embedding(_cfg("enabled"))
    assert low["cache_key"] == full["cache_key"]


# --------------------------------------------------------------------------
# 三条请求路径
# --------------------------------------------------------------------------
def test_stream_payload_has_thinking_by_default(monkeypatch):
    captured = []
    monkeypatch.setattr(providers, "_conn_for", _capture_stream(captured))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k",
                                        "deepseek-v4.1-flash-1017")
    assert "你好" in "".join(p.stream("hi", []))
    body = json.loads(captured[0]["body"])
    assert body["model"] == "deepseek-v4.1-flash-1017"
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is True


def test_stream_payload_without_thinking_when_full_reasoning(monkeypatch):
    captured = []
    monkeypatch.setattr(providers, "_conn_for", _capture_stream(captured))
    p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "m",
                                        thinking_disabled=False)
    "".join(p.stream("hi", []))
    assert "thinking" not in json.loads(captured[0]["body"])


def test_complete_payload_has_thinking():
    seen = {}

    def fake_post(url, payload, api_key="", timeout=60):
        seen.update(payload)
        return 200, {"choices": [{"message": {"content": "ok"}}]}

    original = providers._post_json
    providers._post_json = fake_post
    try:
        p = providers.OpenAILikeLLMProvider("https://x.test/v1", "k", "m")
        assert p.complete("hi") == "ok"
        assert seen["thinking"] == {"type": "disabled"}
    finally:
        providers._post_json = original


def test_test_llm_payload_and_honest_evidence(monkeypatch):
    seen = {}

    def fake_post(url, payload, api_key="", timeout=60):
        seen.update(payload)
        return 200, {"choices": [{"message": {"content": "你好"}}],
                     "usage": {"completion_tokens_details": {"reasoning_tokens": 0}}}

    monkeypatch.setattr(providers, "_post_json", fake_post)
    r = providers.test_llm("https://x.test/v1", "deepseek-v4.1-flash-1017", "k")
    assert seen["thinking"] == {"type": "disabled"}
    assert r["thinking_field_sent"] == "thinking=disabled"
    assert r["low_thinking_evidence"] == "no_reasoning_tokens"
    # 字段未上报时标 not_reported，不假装确认
    monkeypatch.setattr(providers, "_post_json",
                        lambda url, payload, api_key="", timeout=60:
                        (200, {"choices": [{"message": {"content": "你好"}}], "usage": {}}))
    r2 = providers.test_llm("https://x.test/v1", "m", "k")
    assert r2["low_thinking_evidence"] == "not_reported"


def test_settings_effective_exposes_thinking():
    from webapp.routers_settings import _effective
    eff_low = _effective(_cfg("disabled"))
    eff_full = _effective(_cfg("enabled"))
    assert eff_low["thinking"] == "低思考 · 快速"
    assert eff_full["thinking"] == "完整推理"
    assert eff_low["llm"] == eff_full["llm"]
