"""RAG 流式生成降级单测（不触网）：

- 检索为空 → degraded=retrieval_empty，诚实拒答；
- 真实模型抛错 → 优雅降级为本地拼接（degraded=model_error），SSE 不中断；
- resume_from 续传索引连续。
"""
import pytest

from webapp import rag, providers


def test_retrieval_empty_degrades():
    items = list(rag.stream_answer("量子纠缠如何应用？", []))
    assert items
    assert items[0]["degraded"] is True
    assert items[0]["degrade_reason"] == "retrieval_empty"
    assert "没有找到" in "".join(i["token"] for i in items)


def test_model_error_degradation(monkeypatch):
    class Boom(providers.LLMProvider):
        def stream(self, q, r):
            raise RuntimeError("model down")

    monkeypatch.setattr(providers, "get_llm", lambda: Boom())
    items = list(rag.stream_answer("如何配网？", [{"doc_title": "x", "text": "配网步骤"}]))
    assert items
    assert all(i["degraded"] for i in items)
    assert any(i["degrade_reason"] == "model_error" for i in items)
    # 降级回退到本地拼接回答，内容非空（不丢弃本次问答）
    assert "".join(i["token"] for i in items).strip()


def test_quota_exceeded_maps_to_own_reason(monkeypatch):
    """额度耗尽与普通报错分开：提示不同，且不暗示「等会儿重试就好」。"""

    class Quota(providers.LLMProvider):
        def stream(self, q, r):
            raise RuntimeError("quota_exceeded: 网关额度或墨点已用尽")

    monkeypatch.setattr(providers, "get_llm", lambda: Quota())
    state: dict = {}
    items = list(rag.stream_answer("如何配网？", [{"doc_title": "x", "text": "配网步骤"}], state=state))
    assert all(i["degrade_reason"] == "quota_exceeded" for i in items)
    assert state["degrade_reason"] == "quota_exceeded"
    assert "额度已用尽" in "".join(i["token"] for i in items)


def test_stream_interrupted_does_not_append_fallback(monkeypatch):
    """已吐出正文后才中断：不把降级文案拼到真答案后面。"""

    class Half(providers.LLMProvider):
        def stream(self, q, r):
            yield "已经生成的"
            yield "部分回答"
            raise RuntimeError("conn reset")

    monkeypatch.setattr(providers, "get_llm", lambda: Half())
    state: dict = {}
    items = list(rag.stream_answer("如何配网？", [{"doc_title": "x", "text": "配网步骤"}], state=state))
    text = "".join(i["token"] for i in items)
    assert text == "已经生成的部分回答"
    assert "降级" not in text
    assert state["degrade_reason"] == "stream_interrupted"


def test_resume_from_index_continuity():
    items = list(rag.stream_answer("q", [{"doc_title": "x", "text": "y"}], resume_from=2))
    assert items[0]["index"] == 2
    assert [i["index"] for i in items] == list(range(2, 2 + len(items)))


def test_empty_model_output_degrades(monkeypatch):
    """模型正常收尾但零正文（如推理模型只吐 reasoning_content）不能给空白回答。"""

    class Silent(providers.LLMProvider):
        def stream(self, q, r):
            return iter(())  # 一个 token 都没有

    monkeypatch.setattr(providers, "get_llm", lambda: Silent())
    state: dict = {}
    items = list(rag.stream_answer("如何配网？", [{"doc_title": "x", "text": "配网步骤"}], state=state))
    assert items, "空输出必须兜底，不能返回空流"
    assert all(i["degrade_reason"] == "empty_model_output" for i in items)
    assert "".join(i["token"] for i in items).strip()
    assert state["degraded"] is True and state["degrade_reason"] == "empty_model_output"


def test_lexical_terms_chinese_bigram():
    # 中文无空格分词：整句不能当一个 token，否则子串永远匹配不到
    terms = rag._lexical_terms("如何给设备配网？")
    assert "配网" in terms          # 相邻二元组命中
    assert "设备" in terms
    assert "量子纠缠" not in terms  # 长串不整体匹配
