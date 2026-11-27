"""三路取证的 MCP 风格 HTTP 工具端点：JSON-RPC 2.0 over HTTP **子集**。

边界先说清楚（面试口径也按这个讲）：
- 这是给外部 Agent 调用的 demo 入口，**不是完整 MCP 协议实现**——没有 stdio / SSE 传输，
  没有能力协商、会话管理、resources / prompts、增量通知；
- 只实现了 Streamable-HTTP 风格的一小部分：一个 `POST /api/mcp`，跑 JSON-RPC 2.0 的
  `initialize` / `ping` / `tools/list` / `tools/call`，复用 webapp 的 Bearer 鉴权；
- 不引入 `mcp` SDK / 新依赖；四路取证全部走 `rag.retrieve` 的公开结果，
  不读 `kb_routes` 的内部状态（那层正在改 DB-backed）。

工具名 → 检索路：单路工具显式指定一路；`fusion_search` 用当前**已开启**的三路
（默认只有向量开，所以默认融合结果等价于向量检索，这是如实反映开关，不是偷偷全开）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from webapp import rag, settings
from webapp.deps import get_current_user

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "p5-kb-mcp-demo"

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}, "top_k": {"type": "integer"}},
    "required": ["question"],
}

TOOLS = [
    {
        "name": "vector_search",
        "description": "向量检索：从已入库原文的文档分块里找证据（手册/合同的具体段落），"
                       "适合回答「原文里具体怎么写的」。",
        "inputSchema": _INPUT_SCHEMA,
    },
    {
        "name": "graph_search",
        "description": "图谱检索：沿实体关系多跳找线索（依赖 Neo4j），"
                       "适合回答「谁和谁有关、顺着关系能走到哪」。",
        "inputSchema": _INPUT_SCHEMA,
    },
    {
        "name": "wiki_search",
        "description": "LLM Wiki 检索：读整理好的业务概念 / 流程 / 规则与证据链"
                       "（有场景与构建门禁），适合回答「这项业务怎么运转」。",
        "inputSchema": _INPUT_SCHEMA,
    },
    {
        "name": "fusion_search",
        "description": "多路融合：对当前已开启的检索路同时取证，"
                       "按「多路命中优先 + RRF」融合排序（默认只开向量）。",
        "inputSchema": _INPUT_SCHEMA,
    },
]

# 单路工具固定一路；fusion_search 用 None 表示「取配置里已开启的几路」。
TOOL_ROUTES: dict[str, list[str] | None] = {
    "vector_search": ["vector"],
    "graph_search": ["graph"],
    "wiki_search": ["wiki"],
    "fusion_search": None,
}


class RPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict = Field(default_factory=dict)


def _result(rpc_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id, code: int, message: str) -> dict:
    """JSON-RPC 错误走 HTTP 200：协议层的错不是 HTTP 层的错，客户端看 error 字段。"""
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _text_payload(payload: dict) -> dict:
    """MCP 风格 content：把结构化检索结果序列化成一段 text 交给 Agent。"""
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}


def _call_tool(rpc_id, name, arguments) -> dict:
    if name not in TOOL_ROUTES:
        return _error(rpc_id, -32602, f"未知工具: {name!r}")
    if not isinstance(arguments, dict):
        return _error(rpc_id, -32602, "params.arguments 必须是对象")
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return _error(rpc_id, -32602, "缺少必填参数 question")
    top_k = arguments.get("top_k", rag.TOP_K)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        return _error(rpc_id, -32602, "top_k 必须是正整数")

    routes = TOOL_ROUTES[name]
    if routes is None:
        routes = settings.routes_on()  # 当前已开启的全部路；不擅自打开关着的路
    hits, meta = rag.retrieve(question, top_k=top_k, with_meta=True, routes=routes)
    payload = {
        "tool": name,
        "routes_requested": routes,
        "hits": hits,   # 每条含 doc_title / text / snippet / score / routes / fusion_score
        "meta": {
            "retrieval_mode": meta["retrieval_mode"],
            "routes": meta["routes"],   # 每一路的 status / 候选数 / 门禁结论
            "fusion": meta["fusion"],   # multi_hit_count / multi_hit / method
            "fallback_reason": meta["fallback_reason"],
            "rerank": meta["rerank"],
            "embedding_model": meta["embedding_model"],
        },
    }
    return _result(rpc_id, _text_payload(payload))


@router.post("")
def mcp_rpc(req: RPCRequest, user: dict = Depends(get_current_user)):
    """单端点 JSON-RPC 分发：initialize / ping / tools/list / tools/call。"""
    if req.jsonrpc != "2.0":
        return _error(req.id, -32600, "Invalid Request: jsonrpc 必须是 '2.0'")

    if req.method == "initialize":
        return _result(req.id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": SERVER_NAME,
                "version": "0.1.0-demo-subset",
                "note": "MCP 风格的 HTTP JSON-RPC 子集演示端点，不是完整 MCP 协议实现",
            },
        })
    if req.method == "ping":
        return _result(req.id, {"status": "ok"})
    if req.method == "tools/list":
        return _result(req.id, {"tools": TOOLS})
    if req.method == "tools/call":
        return _call_tool(req.id, req.params.get("name"), req.params.get("arguments", {}))
    return _error(req.id, -32601, f"未知方法: {req.method}")
