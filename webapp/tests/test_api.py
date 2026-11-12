"""API 层集成测试（FastAPI TestClient，隔离库 + 离线提供者）：

覆盖：登录鉴权、流式问答 happy path、模型报错降级透传、badcase 采集-筛选-完成度联动。
真实 LLM 冒烟不在此处（见 live_smoke.py，容忍限流）。
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from webapp import providers, rag
from webapp.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _login(client, creds):
    r = client.post("/api/auth/login", json=creds)
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _wait_ready(client, h, doc_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        docs = client.get("/api/docs", headers=h).json()
        if any(x["id"] == doc_id and x["status"] == "ready" for x in docs):
            return True
        time.sleep(0.1)
    return False


def _parse_sse(text):
    meta, tokens, done = None, [], None
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
    return {"meta": meta, "tokens": tokens, "done": done}


def test_login_and_me(client):
    h = _login(client, {"username": "admin", "password": "admin123"})
    me = client.get("/api/auth/me", headers=h)
    assert me.status_code == 200 and me.json()["role"] == "admin"


def test_chat_stream_happy(client, monkeypatch):
    monkeypatch.setattr(rag, "ANALYZE_SIM_SECONDS", 0)
    h = _login(client, {"username": "admin", "password": "admin123"})
    d = client.post("/api/docs/seed-sample", headers=h).json()
    assert _wait_ready(client, h, d["id"])
    r = client.post("/api/chat/stream", headers=h, json={"question": "如何给设备配网？"})
    assert r.status_code == 200
    parsed = _parse_sse(r.text)
    assert parsed["done"] is not None
    assert len(parsed["tokens"]) > 0
    assert parsed["meta"]["conversation_id"]


def test_chat_source_traceability(client, monkeypatch):
    """智能问答来源追溯：meta/done 携带来源，落库后可经 /sources、/conversations、/history 取回。"""
    monkeypatch.setattr(rag, "ANALYZE_SIM_SECONDS", 0)
    # 固定一条检索命中，脱离 embedding 可用性，专注验证溯源链路
    hits = [{
        "doc_id": "D1", "doc_title": "示例说明书", "chunk_id": "C1", "idx": 0,
        "score": 0.83, "snippet": "配网步骤…", "retrieval_mode": "lexical",
        "rerank": "off", "text": "长按配网键三秒进入配网模式，指示灯快闪后松开。",
    }]
    monkeypatch.setattr(rag, "retrieve", lambda q, top_k=4: hits)
    h = _login(client, {"username": "admin", "password": "admin123"})
    r = client.post("/api/chat/stream", headers=h, json={"question": "如何给设备配网？"})
    parsed = _parse_sse(r.text)
    assert parsed["done"] is not None
    conv_id = parsed["done"]["conversation_id"]
    # 1) SSE 负载携带来源（meta 与 done 都应带）
    assert len(parsed["meta"]["sources"]) == 1
    assert len(parsed["done"]["sources"]) == 1
    assert parsed["done"]["sources"][0]["doc_title"] == "示例说明书"
    # 2) 落库后可经 /sources 取回（真值读取）
    src = client.get("/api/chat/sources", headers=h, params={"conversation_id": conv_id}).json()
    assert len(src) == 1
    assert src[0]["doc_title"] == "示例说明书" and src[0]["idx"] == 0
    # 3) 会话列表标记含来源
    convs = client.get("/api/chat/conversations", headers=h).json()
    assert any(c["conversation_id"] == conv_id and c["has_sources"] for c in convs)
    # 4) 历史回放含来源
    hist = client.get("/api/chat/history", headers=h, params={"conversation_id": conv_id}).json()
    asst = [m for m in hist["messages"] if m["role"] == "assistant"]
    assert asst and asst[0]["sources"] and asst[0]["sources"][0]["idx"] == 0


def test_chat_model_error_degradation(client, monkeypatch):
    class Boom(providers.LLMProvider):
        def stream(self, q, r):
            raise RuntimeError("model down")

    monkeypatch.setattr(providers, "get_llm", lambda: Boom())
    # 强制检索命中，确保走到模型路径（否则会走检索为空降级，绕开模型）
    monkeypatch.setattr(rag, "retrieve",
                        lambda q, top_k=4: [{"doc_title": "x", "text": "配网步骤"}])
    h = _login(client, {"username": "admin", "password": "admin123"})
    r = client.post("/api/chat/stream", headers=h, json={"question": "如何配网？"})
    parsed = _parse_sse(r.text)
    assert parsed["done"]["degrade_reason"] == "model_error"
    assert parsed["done"]["degraded"] is True
    assert parsed["done"]["answer"].strip()


def test_badcase_create_filter_completion(client):
    h = _login(client, {"username": "admin", "password": "admin123"})
    tasks = client.get("/api/eval-tasks", headers=h).json()
    tid = tasks[0]["id"]
    payload = {
        "question": "q1", "answer": "a1", "judgment": "事实错误",
        "issue": "引用缺失", "source": "chat", "eval_task_id": tid,
    }
    c = client.post("/api/badcases", headers=h, json=payload)
    assert c.status_code in (200, 201)
    bid = c.json()["id"]
    rows = client.get(f"/api/badcases?eval_task_id={tid}", headers=h).json()
    assert any(x["id"] == bid for x in rows)
    comp = client.get("/api/admin/completion", headers=h).json()
    t = next(t for t in comp["tasks"] if t["eval_task_id"] == tid)
    assert t["completed"] >= 1


def test_user_cannot_access_admin(client):
    uname = "u" + __import__("uuid").uuid4().hex[:8]
    r = client.post("/api/auth/register",
                    json={"username": uname, "password": "test1234", "email": "u@demo.local"})
    uh = {"Authorization": "Bearer " + r.json()["access_token"]}
    assert client.get("/api/admin/completion", headers=uh).status_code == 403
    assert client.get("/api/admin/users", headers=uh).status_code == 403


def test_settings_admin_gate_and_update(client, monkeypatch):
    # /api/settings 会探测本地模型；测试中屏蔽网络，避免依赖 LM Studio
    monkeypatch.setattr(providers, "probe_models", lambda *a, **k: ["m1", "m2"])
    h = _login(client, {"username": "admin", "password": "admin123"})
    r = client.get("/api/settings", headers=h)
    assert r.status_code == 200 and "config" in r.json()

    uname = "u" + __import__("uuid").uuid4().hex[:8]
    reg = client.post("/api/auth/register",
                      json={"username": uname, "password": "test1234", "email": "u@demo.local"})
    uh = {"Authorization": "Bearer " + reg.json()["access_token"]}
    assert client.get("/api/settings", headers=uh).status_code == 403

    # 更新一个不影响运行行为的字段，验证持久化
    r2 = client.put("/api/settings", headers=h, json={"patch": {"local_llm_model": "qwen-test"}})
    assert r2.status_code == 200 and r2.json()["config"]["local_llm_model"] == "qwen-test"
    # 非法枚举 / 未知键 → 400
    assert client.put("/api/settings", headers=h, json={"patch": {"llm_provider": "bogus"}}).status_code == 400
    assert client.put("/api/settings", headers=h, json={"patch": {"nope": "x"}}).status_code == 400
    # 还原
    client.put("/api/settings", headers=h,
               json={"patch": {"local_llm_model": "qwen2.5-coder-7b-instruct"}})


def test_settings_test_offline_target(client):
    h = _login(client, {"username": "admin", "password": "admin123"})
    r = client.post("/api/settings/test", headers=h, json={"target": "llm"})
    assert r.status_code == 200 and r.json()["ok"] is True
