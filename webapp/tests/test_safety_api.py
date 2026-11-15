"""内容安全四出口的 API 级离线回归（隔离库 + 强制离线，零外部调用）。

与 `guardrails/tests/` 的分工：那边测纯规则，这里证明**真的接进了链路**——
上传后的 chunks、SSE 下发与落库、badcase 采集/导出、审计日志都不留原始标识符。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from guardrails import get_policy, set_policy
from webapp import db, rag
from webapp.main import app

ID_OK = "110101199003071938"
PHONE = "13812345678"


@pytest.fixture(autouse=True)
def _restore_policy():
    before = get_policy()
    yield
    set_policy(before)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(rag, "ANALYZE_SIM_SECONDS", 0)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _sse_blocks(text):
    meta, tokens, done = {}, [], {}
    for block in text.split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        ev, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if not data:
            continue
        obj = json.loads(data)
        if ev == "meta":
            meta = obj
        elif ev == "done":
            done = obj
        else:
            tokens.append(obj)
    return meta, tokens, done


def _upload(client, auth, content: bytes, name="safety-staff.txt"):
    r = client.post("/api/docs/upload", headers=auth,
                    files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    for _ in range(60):
        d = [x for x in client.get("/api/docs", headers=auth).json() if x["id"] == doc_id][0]
        if d["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert d["status"] == "ready", d
    return doc_id


# ---------- 只读状态与演示接口 ----------

def test_status_requires_login_and_exposes_ledger(client, auth):
    assert client.get("/api/safety/status").status_code in (401, 403)
    r = client.get("/api/safety/status", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert set(d["gates"]) == {"ingest", "context", "output", "audit"}
    assert d["vault"]["enabled"] is False
    assert "event_rows" in d["ledger"]
    assert "合成" in d["samples_note"]
    assert d["integration"]["ingest"].startswith("已接入")


def test_check_runs_same_rules_on_all_four_gates(client, auth):
    r = client.post("/api/safety/check", headers=auth,
                    json={"text": f"身份证 {ID_OK}，手机 {PHONE}"})
    assert r.status_code == 200
    d = r.json()
    assert [x["gate"] for x in d["report"]["results"]] == ["ingest", "context", "output", "audit"]
    assert len({x["text"] for x in d["report"]["results"]}) == 1
    assert ID_OK not in json.dumps(d, ensure_ascii=False)
    assert d["report"]["total_hits"] == 8      # 每个出口 2 条 × 4


def test_demo_negative_controls_and_stream_leak(client, auth):
    d = client.post("/api/safety/demo", headers=auth).json()
    assert len(d["negative_controls"]) == 4
    assert d["stream"]["leaked"] is False
    assert d["stream"]["hits"] >= 1
    assert len(d["chain"]) == 4


def test_policy_write_requires_admin_and_validates(client, auth):
    assert client.put("/api/safety/policy", json={"enabled": False}).status_code in (401, 403)
    assert client.put("/api/safety/policy", headers=auth,
                      json={"default_action": "nonsense"}).status_code == 400
    assert client.put("/api/safety/policy", headers=auth,
                      json={"min_confidence": 1.7}).status_code == 400
    r = client.put("/api/safety/policy", headers=auth, json={"min_confidence": 0.8})
    assert r.status_code == 200
    assert r.json()["policy"]["min_confidence"] == 0.8
    assert r.json()["effective"].startswith("本进程即时生效")


def test_policy_disabled_is_warned_and_really_disables(client, auth):
    r = client.put("/api/safety/policy", headers=auth, json={"enabled": False}).json()
    assert "放行原文" in r["warning"]
    d = client.post("/api/safety/check", headers=auth,
                    json={"text": f"手机 {PHONE}"}).json()
    assert d["report"]["total_hits"] == 0
    assert PHONE in d["report"]["results"][0]["text"]


# ---------- 真实链路：入库前 ----------

def test_ingest_gate_redacts_text_before_chunking(client, auth):
    doc_id = _upload(client, auth,
                     f"员工登记：身份证 {ID_OK}，手机 {PHONE}，邮箱 a@example.com。\n"
                     "配网说明：长按电源键三秒。".encode())
    conn = db.get_conn()
    try:
        chunks = [r[0] for r in conn.execute(
            "SELECT text FROM chunks WHERE doc_id=?", (doc_id,))]
    finally:
        conn.close()
    assert chunks
    joined = "\n".join(chunks)
    assert ID_OK not in joined and PHONE not in joined
    assert "***" in joined


# ---------- 真实链路：进模型前 / 输出前 / 落库 ----------

def test_chat_stream_redacts_every_persisted_surface(client, auth):
    _upload(client, auth,
            f"员工登记：身份证 {ID_OK}，手机 {PHONE}。\n配网说明：长按电源键三秒。".encode())
    question = f"请核对员工身份证 {ID_OK} 与手机 {PHONE} 的登记流程"
    r = client.post("/api/chat/stream", headers=auth, json={"question": question})
    assert r.status_code == 200
    meta, tokens, done = _sse_blocks(r.text)
    assert len(tokens) > 1 and done
    # SSE「一个原始 token 一个包」的续传契约不能被脱敏打乱
    assert all(t["index"] == i for i, t in enumerate(tokens))
    assert ID_OK not in done["answer"] and PHONE not in done["answer"]
    assert done["safety"]["gate"] == "output"
    assert "tail" in done

    conv = meta["conversation_id"]
    conn = db.get_conn()
    try:
        logs = conn.execute("SELECT role, content FROM chat_logs WHERE conversation_id=?",
                            (conv,)).fetchall()
        srcs = conn.execute("SELECT snippet, chunk_text FROM chat_sources WHERE conversation_id=?",
                            (conv,)).fetchall()
    finally:
        conn.close()
    assert logs and len(logs) == 2
    assert all(ID_OK not in (x[1] or "") and PHONE not in (x[1] or "") for x in logs)
    assert all(ID_OK not in (a or "") + (b or "") and PHONE not in (a or "") + (b or "")
               for a, b in srcs)


def test_resume_from_index_contract_still_holds(client, auth):
    _upload(client, auth, "配网说明：长按电源键三秒，指示灯变蓝后完成绑定。".encode())
    r = client.post("/api/chat/stream", headers=auth,
                    json={"question": "如何给设备配网？", "resume_from": 12})
    assert r.status_code == 200
    meta, tokens, done = _sse_blocks(r.text)
    assert meta.get("resume_from") == 12
    assert (not tokens) or (tokens[0]["index"] == 12 and
                            all(t["index"] == 12 + i for i, t in enumerate(tokens)))


# ---------- 真实链路：日志与导出前 ----------

def test_badcase_create_list_and_export_are_redacted(client, auth):
    tid = client.get("/api/eval-tasks", headers=auth).json()[0]["id"]
    payload = {"eval_task_id": tid,
               "question": f"身份证 {ID_OK} 怎么核验？",
               "answer": "已按规则打码。",
               "issue_desc": f"手机号 {PHONE} 未打码",
               "source": "chat"}
    r = client.post("/api/badcases", headers=auth, json=payload)
    assert r.status_code == 201
    assert ID_OK not in json.dumps(r.json(), ensure_ascii=False)
    assert PHONE not in json.dumps(r.json(), ensure_ascii=False)

    listing = client.get("/api/badcases", headers=auth)
    assert ID_OK not in listing.text and PHONE not in listing.text
    for fmt in ("csv", "json"):
        exp = client.get(f"/api/badcases/export?format={fmt}", headers=auth)
        assert exp.status_code == 200
        assert ID_OK not in exp.text and PHONE not in exp.text


def test_audit_log_never_stores_raw_identifier(client, auth):
    # target / detail 是审计日志里最容易带上用户输入的两个字段
    db.log_action("admin", "admin", "safety-unit", f"身份证 {ID_OK}",
                  f"手机 {PHONE} 已处理")
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT target, detail FROM audit_logs WHERE action='safety-unit'").fetchall()
    finally:
        conn.close()
    assert rows
    assert all(ID_OK not in (a or "") + (b or "") and PHONE not in (a or "") + (b or "")
               for a, b in rows)


def test_safety_events_ledger_records_real_hits_by_gate(client, auth):
    _upload(client, auth,
            f"员工登记：身份证 {ID_OK}，手机 {PHONE}。\n配网说明：长按电源键三秒。".encode())
    question = f"请核对员工身份证 {ID_OK} 的登记流程"
    assert client.post("/api/chat/stream", headers=auth,
                       json={"question": question}).status_code == 200
    d = client.get("/api/safety/status", headers=auth).json()["ledger"]
    assert d["event_rows"] > 0
    assert "ingest" in d["by_gate"] and "context" in d["by_gate"]
    assert all({"gate", "kind", "hits", "last_at"} <= set(x) for x in d["recent"])
    # 台账只存统计，绝不存原文
    assert ID_OK not in json.dumps(d, ensure_ascii=False)
