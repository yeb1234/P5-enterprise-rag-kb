"""内容安全 / PII 规则层离线单测（零外部调用）。

重点不是「正则能匹配」，而是三件容易被面试官追问的事：
1. 校验位与上下文门控真的能挡住误报；
2. 流式逐 token 输出时，跨 chunk 的标识符不会被切成两半发出去；
3. 四个出口拿到的是同一套规则、同一份结果。
"""
from __future__ import annotations

import pytest

from guardrails import (ACTIONS, GATES, RULE_VERSION, Guardrails, RedactionPolicy,
                        StreamRedactor, default, export_rows, get_policy,
                        redact_text, rule_catalog, run_demo, scan, set_policy,
                        status)
from guardrails.contracts import (ACTION_BLOCK, ACTION_HASH, ACTION_MASK,
                                  ACTION_REMOVE, ACTION_TOKEN)
from guardrails.detectors import _luhn, _valid_id18, _valid_id15, _valid_ipv4
from guardrails.pipeline import STREAM_SAMPLE

ID_OK = "110101199003071938"     # ISO 7064 MOD 11-2 校验位合法
ID_BAD = "11010119900307193X"    # 校验位非法
CARD_OK = "6222021234567894"     # Luhn 合法
PHONE = "13812345678"


@pytest.fixture(autouse=True)
def _restore_policy():
    """任何测试改了全局策略都要还原，避免污染后续用例。"""
    before = get_policy()
    yield
    set_policy(before)


# ---------- 1. 校验位：误报防线的地基 ----------

def test_valid_id18_accepts_real_checksum_and_rejects_fake():
    assert _valid_id18(ID_OK) is True
    assert _valid_id18(ID_BAD) is False
    assert _valid_id18("11010119900307193") is False        # 少一位
    assert _valid_id18("110101199013071938") is False       # 月份 13 非法
    assert _valid_id15("110101900307193") is True


def test_luhn_and_ipv4():
    assert _luhn(CARD_OK) is True
    assert _luhn("6222021234567890") is False
    assert _valid_ipv4("192.168.1.1") is True
    assert _valid_ipv4("999.1.1.1") is False


def test_scan_finds_core_identifier_kinds():
    text = (f"身份证 {ID_OK}，手机 {PHONE}，邮箱 zhangsan@example.com，"
            f"工号：EMP-20260118，姓名：张三，卡号 {CARD_OK}。")
    kinds = {f.kind for f in scan(text)}
    assert {"ID_CARD_CN", "PHONE_CN", "EMAIL", "EMPLOYEE_ID", "NAME_CN",
            "BANK_CARD"} <= kinds


def test_negative_controls_do_not_false_positive():
    """四条最容易误报的负向对照，一条都不能命中。"""
    cases = [
        f"订单号 11010119900307123456 不是身份证。",
        f"流水号 1138123456789 不是手机号。",
        f"证件号 {ID_BAD} 校验位不合法，不应误报。",
        "长按复位键进入配网模式，指示灯变蓝后即可绑定账号。",
    ]
    for t in cases:
        assert scan(t) == [], t


def test_overlap_resolution_keeps_id_card_not_bank_card():
    """18 位纯数字同时符合身份证与银行卡形状时，只报置信度更高的身份证。"""
    found = scan(f"身份证 {ID_OK} 请登记。")
    assert [f.kind for f in found] == ["ID_CARD_CN"]


def test_context_gated_rules_need_field_names():
    """中文姓名 / 工号 / 护照必须靠字段名词定位，不做「看见汉字就当姓名」。"""
    assert scan("张三今天来上班了。") == []
    assert any(f.kind == "NAME_CN" for f in scan("姓名：张三"))
    assert any(f.kind == "EMPLOYEE_ID" for f in scan("工号：EMP-20260118"))
    assert any(f.kind == "PASSPORT" for f in scan("护照号：E12345678"))
    assert scan("20260118") == []


def test_finding_preview_never_exposes_full_value():
    f = scan(f"手机 {PHONE}")[0]
    d = f.as_dict()
    assert PHONE not in str(d)
    assert d["preview"] == "13***78"
    assert d["rule"] == RULE_VERSION


# ---------- 2. 脱敏动作与策略 ----------

def test_redact_mask_and_hash():
    r = redact_text(f"手机 {PHONE}", gate="output")
    assert PHONE not in r.text and "138****5678" in r.text
    assert r.hit_count == 1 and r.actions == {ACTION_MASK: 1}
    assert r.original_len > 0 and r.redacted_len == len(r.text)

    p = RedactionPolicy(default_action=ACTION_HASH, hash_salt="unit-test-salt")
    h = redact_text(f"手机 {PHONE}", gate="audit", policy=p)
    assert PHONE not in h.text and "[H:" in h.text
    # 带盐 HMAC 跨请求稳定，便于统计去重
    h2 = redact_text(f"手机 {PHONE}", gate="audit", policy=p)
    assert h.text == h2.text


def test_all_five_actions():
    p = RedactionPolicy(reversible_token=True)
    cases = {
        ACTION_MASK: RedactionPolicy(default_action=ACTION_MASK),
        ACTION_HASH: RedactionPolicy(default_action=ACTION_HASH),
        ACTION_TOKEN: RedactionPolicy(default_action=ACTION_TOKEN, reversible_token=True),
        ACTION_REMOVE: RedactionPolicy(default_action=ACTION_REMOVE),
        ACTION_BLOCK: RedactionPolicy(default_action=ACTION_BLOCK),
    }
    for act, pol in cases.items():
        r = redact_text(f"手机 {PHONE}", gate="output", policy=pol)
        assert PHONE not in r.text, act
        assert r.actions.get(act, 0) == 1, (act, r.actions)
    assert p.reversible_token is True
    assert set(ACTIONS) == set(cases)


def test_block_only_applies_to_api_key_by_default():
    text = "请使用 sk-abcdefghijklmnop1234567890 调用接口。"
    r = redact_text(text, gate="ingest")
    assert r.blocked is True and r.blocked_kinds == ["API_KEY"]
    assert "sk-abcdefghijklmnop1234567890" not in r.text
    # 普通 PII 默认不 block，只打码
    assert redact_text(f"手机 {PHONE}", gate="ingest").blocked is False


def test_policy_disabled_is_explicit_passthrough():
    p = RedactionPolicy(enabled=False)
    r = redact_text(f"手机 {PHONE}", gate="audit", policy=p)
    assert r.text == f"手机 {PHONE}" and r.hit_count == 0


def test_min_confidence_filters_low_confidence_findings():
    hi = RedactionPolicy(min_confidence=0.95)
    lo = RedactionPolicy(min_confidence=0.60)
    text = "姓名：张三，工号：EMP-20260118"
    assert redact_text(text, gate="output", policy=hi).hit_count == 0
    assert redact_text(text, gate="output", policy=lo).hit_count == 2


def test_export_rows_does_not_mutate_input():
    rows = [{"question": f"手机 {PHONE}", "answer": "无", "count": 3}]
    out = export_rows(rows)
    assert PHONE not in out[0]["question"]
    assert rows[0]["question"] == f"手机 {PHONE}"      # 原对象未被就地修改
    assert out[0]["count"] == 3


# ---------- 3. 流式输出闸：最容易被忽略的泄漏面 ----------

def test_stream_does_not_leak_identifier_split_across_chunks():
    """长文本 + 逐字符下发：旧实现会把身份证前 10 位先发出去，撤不回来。"""
    sr = StreamRedactor()
    out = "".join(sr.push(ch) for ch in STREAM_SAMPLE) + sr.flush()
    assert ID_OK not in out
    assert "***" in out
    assert sr.hits >= 1 and sr.held > 0


@pytest.mark.parametrize("size", [1, 2, 5, 9, 17, 31, 64])
def test_stream_chunk_sizes_never_leak(size):
    text = f"员工身份证号是 {ID_OK}，请登记。" + "后续操作说明请参照手册第三章。" * 6
    sr = StreamRedactor()
    parts = [sr.push(text[i:i + size]) for i in range(0, len(text), size)]
    out = "".join(parts) + sr.flush()
    assert ID_OK not in out


def test_stream_plain_text_roundtrips_exactly():
    """没有 PII 时输出必须与原文逐字相同，否则会悄悄丢字（SSE 契约）。"""
    text = "长按电源键三秒开机，进入设置界面选择无线网络并完成配网。" * 8
    sr = StreamRedactor()
    out = "".join(sr.push(ch) for ch in text) + sr.flush()
    assert out == text


def test_stream_summary_has_counts():
    sr = StreamRedactor()
    "".join(sr.push(ch) for ch in STREAM_SAMPLE) + sr.flush()
    s = sr.summary()
    assert s["gate"] == "output" and s["hits"] >= 1
    assert "ID_CARD_CN" in s["by_kind"] and s["held_boundaries"] > 0


# ---------- 4. 四出口一致性与演示契约 ----------

def test_four_gates_produce_identical_results():
    g = Guardrails()
    text = f"客户姓名：张三，工号：EMP-20260118，手机 {PHONE}。"
    results = [g.ingest(text), g.context(text), g.output(text), g.audit(text)]
    assert [r.gate for r in results] == list(GATES)
    assert len({r.text for r in results}) == 1
    expected = results[0].hit_count
    assert expected >= 3                       # 姓名 / 工号 / 手机
    assert all(r.hit_count == expected for r in results)


def test_run_demo_contract():
    d = run_demo()
    assert d["rule_version"] == RULE_VERSION
    assert len(d["negative_controls"]) == 4
    assert d["stream"]["leaked"] is False
    assert len(d["chain"]) == 4
    assert {c["gate"] for c in d["chain"]} == set(GATES)
    assert len({c["text"] for c in d["chain"]}) == 1
    assert d["total_hits"] > 0


def test_status_structure_and_vault_default_off():
    s = status()
    assert set(s["gates"]) == set(GATES)
    assert len(s["rules"]) == len(rule_catalog()) >= 10
    assert s["vault"]["enabled"] is False
    assert s["policy"]["reversible_token"] is False
    assert "ingest" in s["integration"] and "未做" in s["integration"]["model_level_ner"]


def test_set_policy_takes_effect_immediately():
    p = RedactionPolicy(default_action=ACTION_REMOVE, min_confidence=0.9)
    set_policy(p)
    assert get_policy() is p
    assert default().policy is p
    r = default().output(f"手机 {PHONE}")
    assert PHONE not in r.text and "[已移除:" in r.text
