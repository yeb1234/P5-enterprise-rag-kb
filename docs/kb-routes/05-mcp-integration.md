# P5 三路取证：MCP 风格 HTTP 工具端点与三种传输的边界

编制：2026-11-27 ｜ 状态：**HTTP JSON-RPC 子集已实现；SSE / stdio / 完整 MCP 只设计；演示切片，不是上线交付版**

> **理念来源**：B 站视频《企业知识库怎么选？RAG、GraphRAG、LLM Wiki，用订单排障拆开三路取证》
> （BV1rTyZzfEF9）里「用 MCP 把 Wiki 接进 Agent 的三种传输」。本文只写「视频里的传输取舍 →
> 本项目怎么落地 → 哪些只是设计」，不把视频的结论当成我们的实测结论。
> 三路检索本身的落地见 `docs/kb-routes/01-design.md`。

---

## 1. 三种传输：各自适合什么，代价是什么

MCP 把「工具定义 / 调用」与「消息怎么送达」分开。传输选哪种，取决于 Agent 跑在哪、谁来管进程和连接。

| 传输 | 怎么送 | 适用场景 | 取舍 |
|---|---|---|---|
| **stdio** | Agent 把 MCP Server 当**本地子进程**启动，JSON-RPC 走 stdin/stdout | IDE 插件、桌面客户端、单人本机工具；进程随客户端起停 | 实现最轻、没有网络暴露面；但只能单机、生命周期绑定客户端，**多用户 Web 服务用不了**，日志/鉴权都得另做 |
| **SSE**（HTTP + Server-Sent Events，2024-11-05 风格） | 一条 GET SSE 长连接做服务端→客户端推送，客户端另开 POST 发请求 | 需要长任务进度、服务端主动通知（构建/同步几秒到几分钟的场景） | 能把「推」送出去；但长连接怕代理超时、多副本要黏连、断线恢复要自己设计，双向通信得维护两条通道 |
| **Streamable HTTP**（2025-03-26 之后的推荐方向） | **单端点 POST** 承载请求/响应，可选 GET SSE 承载流与恢复；可无状态，也可带 `Mcp-Session-Id` | 云端 / Web 多用户 Agent、要经过常规网关和负载均衡的场景 | 网关友好、易横向扩展；代价是协议面最大：版本协商、会话、可恢复流、安全边界都要实现完整 |

**本项目的选择**：它是 FastAPI 的多用户 Web 演示切片，Agent 在浏览器/服务端一侧，不是本地子进程——
方向上是 Streamable HTTP 这一类**单端点 HTTP**；但**只实现了它的一小部分**，见下一节。

---

## 2. 已实现：HTTP JSON-RPC 子集

新增 `POST /api/mcp`，一个端点跑 JSON-RPC 2.0 的四个方法：

| 方法 | 返回 | 说明 |
|---|---|---|
| `initialize` | `protocolVersion`、`capabilities.tools`、`serverInfo` | `serverInfo.version` 明写 `demo`，`note` 明写「不是完整 MCP 协议实现」 |
| `ping` | `{"status":"ok"}` | 连通性探测 |
| `tools/list` | 4 个工具定义（name / description / inputSchema） | 工具元信息 |
| `tools/call` | MCP 风格 `content: [{type:"text", text: "<JSON 字符串>"}]` | 把检索结果序列化成文本交给 Agent |

- **鉴权**：`Depends(get_current_user)`，Bearer Token，未登录 401（沿用 webapp 现有鉴权，不另造一套）；
- **错误契约**：协议层错误走 **HTTP 200 + `error` 字段**，不抛 500——
  `-32600` 非法请求、`-32601` 未知 method、`-32602` 未知工具 / 缺 `question` / `top_k` 非法；
- **零新依赖**：没装 `mcp` SDK，手写 JSON-RPC 分发；四路结果全部来自 `rag.retrieve` 的公开返回值，
  不读 `kb_routes` 的内部状态（那层正在改 DB-backed）；
- **参数安全**：`rag.retrieve(..., routes=...)` 对路由名做白名单过滤，`routes=["bogus"]` 会被过滤成全关。

### curl 示例（按实际端口替换，示例用 README 的 8765）

```bash
TOKEN=<登录后 /api/auth/login 返回的 access_token>

# 1) initialize：协议版本与 demo 身份
curl -s http://127.0.0.1:8765/api/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# 2) tools/list：Agent 据此知道有哪些工具、入参是什么
curl -s http://127.0.0.1:8765/api/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 3) tools/call：调用向量这一路取原文证据
curl -s http://127.0.0.1:8765/api/mcp \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"vector_search","arguments":{"question":"设备配网怎么做？","top_k":4}}}'
```

`tools/call` 的 `result.content[0].text` 再 `json.loads` 一次才是业务 payload，
这是 MCP 的 content 约定（文本块里装结构化结果），不是多套了一层无用的编码。

---

## 3. 四个工具各喂给 Agent 什么证据

| 工具 | 实际跑哪几路 | 喂给 Agent 的证据 | 退化时 Agent 看到什么 |
|---|---|---|---|
| `vector_search` | `["vector"]` | 原文分块：`doc_title` / `text` / `snippet` / `score`，回答「原文里怎么写」 | 词法无命中 → `hits: []`，`meta.retrieval_mode=lexical` |
| `graph_search` | `["graph"]` | 图谱多跳命中的分块，回答「谁和谁有关、顺着关系走到哪」 | Neo4j 不可用 → `meta.routes.graph.status=unavailable` + reason，**不 500** |
| `wiki_search` | `["wiki"]` | Wiki 页面结论对应的证据分块，回答「业务怎么运转」 | 场景没填 / 没构建 → `meta.routes.wiki.status=gate_blocked` + reason |
| `fusion_search` | **当前已开启**的三路（`settings.routes_on()`） | 多路召回后融合排序的分块 | 全关 → `hits: []`，`meta.retrieval_mode=off` |

**「多路命中排前」在返回里怎么看**：每条 hit 的 `routes` 字段列出命中它的路，
例如 `["vector","graph"]` 就是双路命中；`fusion_score` 是 RRF 分；
`meta.fusion.multi_hit_count` 给出本次双路及以上命中的条数，`meta.fusion.method` 写明规则
（`multi_hit_first + rrf`）。排序先按命中路数多者优先，再按 RRF 名次分——
不同量纲的分数（余弦 / 跳数衰减 / 页面匹配）不相加。

**注意**：`fusion_search` 不擅自打开关着的路，默认只有向量开时，它的结果等价于 `vector_search`；
要让三路都参与，得先把开关打开并让依赖就绪（图谱 Neo4j、Wiki 场景+构建）。这是如实反映配置，不是 bug。

---

## 4. 三态表

| 状态 | 项 |
|---|---|
| **已实现** | `POST /api/mcp` 的 HTTP JSON-RPC 子集（`initialize` / `ping` / `tools/list` / `tools/call`）；4 个取证工具；Bearer 鉴权与 401；JSON-RPC 错误契约（`-32600/-32601/-32602`）；路由名白名单过滤；7 项新增单测 |
| **只设计** | SSE 传输（长任务进度推送）；stdio 传输（本地 Agent 子进程）；完整 MCP 协议兼容（tools/resources/prompts、能力协商、会话与 `Mcp-Session-Id`、可恢复流、notifications） |
| **未做** | Agent 侧自动路由「该问哪一路」（现在由调用方显式指定工具）；把 Wiki 每条结论的证据链渲染给最终用户的 UI（后台 claim 已落库，前端展示未接） |
