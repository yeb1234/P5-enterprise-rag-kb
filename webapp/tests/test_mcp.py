"""MCP 风格 HTTP 工具端点测试（JSON-RPC 2.0 over HTTP 子集）。

共享测试库的两条隔离约定（与 test_kb_routes.py 同款）：
- 用例前后都把三路开关复位成默认，避免污染别的用例；
- 造数带本文件专属锚词（NONCE），问句也带锚词，只断言「自己的分块命中」，
  不假设结果总数（别的用例留下的文档也会在候选池里）。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from webapp import db, rag, settings
from webapp.main import app

ADMIN = {"username": "admin", "password": "admin123"}
RPC_URL = "/api/mcp"
NONCE = "曜岚"          # 本文件专属锚词，不撞其它用例的「耀石 / 配网」等语料
DEFAULT_ROUTES = {"rag_route_vector": "on", "rag_route_graph": "off",
                  "rag_route_wiki": "off", "wiki_scenario": ""}
_my_docs: list[str] = []


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate():
    settings.set_many(DEFAULT_ROUTES)
    _my_docs.clear()
    yield
    settings.set_many(DEFAULT_ROUTES)
    with db.get_conn() as conn:
        for doc_id in _my_docs:
            conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                         "(SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.commit()


def _login(client) -> dict:
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _rpc(client, headers: dict | None, method: str, params: dict | None = None):
    return client.post(RPC_URL, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
    })


def _payload(resp) -> dict:
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body, body
    return json.loads(body["result"]["content"][0]["text"])


def _seed_doc(client, headers, monkeypatch) -> str:
    """上传一份带锚词的文档并等它 ready；只清本用例自己造的文档。"""
    monkeypatch.setattr(rag, "ANALYZE_SIM_SECONDS", 0)
    content = f"{NONCE}量子洗碗机排障记录：先查电源，再查进水阀。\n"
    r = client.post("/api/docs/upload", headers=headers,
                    files={"file": ("mcp-fixture.txt", content.encode("utf-8"), "text/plain")})
    assert r.status_code == 200
    doc_id = r.json()["id"]
    _my_docs.append(doc_id)
    deadline = time.time() + 10
    while time.time() < deadline:
        doc = next((d for d in client.get("/api/docs", headers=headers).json()
                    if d["id"] == doc_id), None)
        if doc and doc["status"] in ("ready", "error"):
            assert doc["status"] == "ready", doc.get("error_msg")
            return doc_id
        time.sleep(0.05)
    pytest.fail("seeded 文档未就绪")


def test_tools_list_returns_four_tools(client):
    h = _login(client)
    resp = _rpc(client, h, "tools/list")
    assert resp.status_code == 200
    assert resp.json()["jsonrpc"] == "2.0" and resp.json()["id"] == 1
    tools = resp.json()["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "vector_search", "graph_search", "wiki_search", "fusion_search"]
    schema = {"type": "object",
              "properties": {"question": {"type": "string"}, "top_k": {"type": "integer"}},
              "required": ["question"]}
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"] == schema


def test_initialize_and_ping(client):
    h = _login(client)
    init = _rpc(client, h, "initialize").json()["result"]
    assert init["protocolVersion"]
    assert "demo" in init["serverInfo"]["version"]
    assert "不是完整 MCP" in init["serverInfo"]["note"]
    assert _rpc(client, h, "ping").json()["result"]["status"] == "ok"


def test_vector_search_hits_seeded_doc(client, monkeypatch):
    h = _login(client)
    _seed_doc(client, h, monkeypatch)
    question = f"{NONCE}量子洗碗机怎么排障"
    data = _payload(_rpc(client, h, "tools/call", {
        "name": "vector_search", "arguments": {"question": question, "top_k": 3}}))
    assert data["tool"] == "vector_search"
    assert data["routes_requested"] == ["vector"]
    hit = next(h for h in data["hits"] if NONCE in h["text"])
    assert all(k in hit for k in ("doc_title", "text", "snippet", "score", "routes"))
    assert hit["routes"] == ["vector"]
    assert data["meta"]["routes"]["vector"]["status"] == "ok"

    # fusion_search 只跑「当前已开启」的路：默认同样是向量，命中同一份锚词文档
    fused = _payload(_rpc(client, h, "tools/call", {
        "name": "fusion_search", "arguments": {"question": question}}))
    assert fused["routes_requested"] == ["vector"]
    assert any(NONCE in h["text"] for h in fused["hits"])


def test_wiki_search_reports_gate_blocked(client):
    h = _login(client)
    data = _payload(_rpc(client, h, "tools/call", {
        "name": "wiki_search", "arguments": {"question": "订单排障流程怎么走"}}))
    assert data["routes_requested"] == ["wiki"]
    assert data["meta"]["routes"]["wiki"]["status"] == "gate_blocked"
    assert data["hits"] == []


def test_requires_auth(client):
    resp = _rpc(client, None, "tools/list")
    assert resp.status_code == 401


def test_unknown_method_and_tool_return_rpc_error(client):
    h = _login(client)
    unknown_method = _rpc(client, h, "no/such/method").json()
    assert unknown_method["error"]["code"] == -32601

    unknown_tool = _rpc(client, h, "tools/call", {
        "name": "nope", "arguments": {"question": "x"}}).json()
    assert unknown_tool["error"]["code"] == -32602

    missing_question = _rpc(client, h, "tools/call", {
        "name": "vector_search", "arguments": {}}).json()
    assert missing_question["error"]["code"] == -32602


def test_retrieve_filters_illegal_routes(client, monkeypatch):
    h = _login(client)  # 走一遍登录确保 TestClient / schema 就绪
    _seed_doc(client, h, monkeypatch)
    question = f"{NONCE}量子洗碗机怎么排障"

    hits, meta = rag.retrieve(question, routes=["bogus"], with_meta=True)
    assert hits == [] and meta["retrieval_mode"] == "off"
    assert "note" in meta

    hits, meta = rag.retrieve(question, routes=["bogus", "vector"], with_meta=True)
    assert set(meta["routes"]) == {"vector"}
    assert any(NONCE in hit["text"] for hit in hits)
