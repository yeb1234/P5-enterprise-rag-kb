"""检索/提示词质量改动的契约测试。

锁定四类行为，全部离线可跑、不依赖真实 DB/LLM：
- 回答语言只由问题决定（中文题不被引擎里的英文专名带跑）；
- 送进 LLM 的上下文按「文档数 × 每文档片段数 × 总量」三级预算裁剪；
- 模型整题拒答但证据确有事实时，救答只在白名单对象（书名/时间）上生效，不覆盖正常生成；
- 指标 latency_ms 按 exit 事件求和（trace 签名变更后 node 不再是 exit）。

明确不覆盖：检索质量指标、模型是否真的输出中文、正则救答的评测增益——那些要真跑评测才知道。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.agent.orchestrator import (
    build_state,
    persist_node,
    rescue_answer_with_evidence,
)
from app.agent.state import AgentState
from app.core.config import settings
from app.integrations.llm import (
    LANGUAGE_INSTRUCTIONS,
    RAG_SYSTEM_PROMPT,
    build_rag_messages,
    detect_question_language,
)
from app.models.metrics import MetricEvent
from app.services.search import query_terms, select_answer_context

# ---------- 回答语言 ----------


def test_detect_language_uses_only_question_text():
    assert detect_question_language("这本书讲的是什么") == "zh"
    assert detect_question_language("How do I reset the password?") == "en"
    # 问题里夹一个英文产品名，仍是中文题
    assert detect_question_language("DPI 切换按键在哪里") == "zh"
    # 空串兜底中文
    assert detect_question_language("") == "zh"


def test_build_rag_messages_injects_language_instruction():
    citations = [{"document_title": "手册", "page_no": 2, "snippet": "按红色按钮重置。"}]
    zh = build_rag_messages("怎么重置？", citations)
    en = build_rag_messages("How to reset?", citations)

    assert zh[0]["content"] == RAG_SYSTEM_PROMPT
    assert zh[1]["content"].count("【回答语言】") == 1
    assert LANGUAGE_INSTRUCTIONS["zh"] in zh[1]["content"]
    assert LANGUAGE_INSTRUCTIONS["en"] in en[1]["content"]
    assert LANGUAGE_INSTRUCTIONS["zh"] not in en[1]["content"]
    # 引用编号与片段都在
    assert "[1] 文档《手册》第 2 页" in zh[1]["content"]
    assert "按红色按钮重置。" in zh[1]["content"]


# ---------- 检索实词 ----------


def test_query_terms_strips_question_shell_and_stopwords():
    zh = query_terms("在哪里可以查到订单明细？")
    assert "在哪里" not in zh and "可以" not in zh
    assert "订单" in zh or "订单明" in zh

    en = query_terms("How do I reset the password?")
    assert "how" not in en and "the" not in en and "do" not in en
    assert "reset" in en and "password" in en


def test_query_terms_is_deduped_sorted_and_bounded():
    terms = query_terms("订单明细 订单明细 订单明细 收货地址 发票")
    assert len(terms) == len(set(terms))
    assert terms == sorted(terms, key=len, reverse=True)
    assert len(terms) <= 12


# ---------- 上下文预算 ----------


def _candidates() -> list[dict]:
    return [
        {"chunk_id": f"d{i}-c{j}", "doc_id": f"d{i}", "snippet": f"片段{i}-{j}"}
        for i in range(5)
        for j in range(3)
    ]


def test_select_answer_context_enforces_three_levels_of_budget():
    selected = select_answer_context(_candidates())
    docs = {c["doc_id"] for c in selected}
    # 文档数 <= 默认预算；每文档 <= 每文档片段数；总量 <= 全局上限
    assert len(docs) <= settings.SEARCH_CONTEXT_DOCS
    assert len(selected) <= settings.SEARCH_CONTEXT_MAX_CITATIONS
    for doc_id in docs:
        assert sum(c["doc_id"] == doc_id for c in selected) <= settings.SEARCH_CONTEXT_CITATIONS_PER_DOC
    # 按文档首次出现顺序，不重排
    assert [c["chunk_id"] for c in selected][:2] == ["d0-c0", "d0-c1"]


def test_select_answer_context_honors_explicit_overrides_and_skips_missing_doc():
    cands = [{"chunk_id": "x", "snippet": "无文档号"}] + _candidates()
    selected = select_answer_context(cands, doc_limit=2, per_doc=1, max_citations=2)
    assert [c["chunk_id"] for c in selected] == ["d0-c0", "d1-c0"]


def test_snippet_window_is_wider_for_english():
    from app.services.search import _snippet_chars

    assert _snippet_chars("How does it work?") > _snippet_chars("这是怎么工作的？")


# ---------- 拒答救援 ----------


def test_rescue_is_noop_when_answer_is_not_a_refusal():
    normal = "根据资料，按红色按钮重置 [1]。"
    cands = [{"snippet": "《算法导论》是经典教材"}]
    assert rescue_answer_with_evidence("推荐哪本书？", normal, cands) == normal


def test_rescue_book_title_from_evidence_only_when_query_asks_for_books():
    refusal = "现有资料无法回答。"
    cands = [{"snippet": "《算法导论》是经典教材"}]
    rescued = rescue_answer_with_evidence("有哪些书推荐？", refusal, cands)
    assert "算法导论" in rescued and "[1]" in rescued
    # 问的不是书，即使片段里有书名也不救
    assert rescue_answer_with_evidence("怎么重置？", refusal, cands) == refusal


def test_rescue_time_from_evidence_keeps_original_expression():
    refusal = "现有资料无法回答。"
    cands = [{"snippet": "上海考场 12月15日上午9:00 开始"}]
    rescued = rescue_answer_with_evidence("上海的考试时间是什么时候？", refusal, cands)
    assert "12月15日上午9:00" in rescued and "[1]" in rescued
    # 片段里没有时间值时不硬造
    assert rescue_answer_with_evidence("上海的考试时间是什么时候？", refusal,
                                       [{"snippet": "上海考场安排见通知"}]) == refusal


# ---------- 指标口径（trace 签名变更的连锁点） ----------


class _FakeDB:
    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass


def test_persist_sums_latency_from_exit_events_only():
    state = AgentState(trace_id="tr-1", tenant_id="t1", query="q")
    state.audit = [
        {"node": "guard", "event": "enter"},
        {"node": "guard", "event": "exit", "ms": 10, "blocked": False},
        {"node": "generate", "event": "exit", "ms": 20},
        {"node": "exit", "ms": 999},  # 旧格式残留，必须被忽略
    ]
    db = _FakeDB()
    user = SimpleNamespace(tenant_id="t1", id="u1")

    persist_node(state, db, user, None)  # type: ignore[arg-type]

    metrics = [x for x in db.added if isinstance(x, MetricEvent)]
    assert len(metrics) == 1
    assert metrics[0].latency_ms == 30  # 若仍按 node=="exit"，此处拿到 999 → 立即暴露回归


def test_build_state_carries_filters_and_trace_id():
    user = SimpleNamespace(id="u1", role="dept_user", department_id="d1", tenant_id="t1")
    state = build_state(user, "q", {"dept": "d1"}, "tr-9")
    assert state.filters == {"dept": "d1"}
    assert state.trace_id == "tr-9"
    assert state.user == {"id": "u1", "role": "dept_user", "dept": "d1"}
