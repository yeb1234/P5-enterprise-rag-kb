# Agent 内部编排扩展点

设计原则：**编排层可以换，鉴权层、工具层、审计层不换。**
当前是线性 RAG 参考实现；以后接 LangGraph / 多 Agent / MCP 网关时，API 层几乎零改动。

---

## 1. 现有四个抽象

| 文件 | 抽象 | 作用 |
|---|---|---|
| `app/agent/state.py` | `AgentState` | 可序列化状态：query / intent / plan / candidates / context / answer / citations / tool_calls / audit / metrics |
| `app/agent/orchestrator.py` | `Orchestrator` 协议 + `NodeRegistry` | 节点是可注册函数，顺序可配，每节点写 enter/exit 审计与耗时 |
| `app/agent/tools.py` | `ToolRegistry` / `ToolSpec` | 工具先注册才能调，调用前过权限、调用后写审计 |
| `app/services/search.py` | `hybrid_search` / `build_answer_context` | 检索能力本身不依赖编排框架，可被任意节点复用 |

节点链：`guard → retrieve → permission_filter → assemble → generate → verify → persist`

- `guard`：空问题/Prompt 注入规则拦截、query 归一化、意图占位；
- `retrieve`：通过 `ToolRegistry.invoke("kb.search")` 走受控检索；
- `permission_filter`：**第二次**逐片段权限校验，无权限片段不进上下文；
- `assemble`：带编号上下文（引用契约贯穿到生成）；
- `generate`：外部 DeepSeek，`chat()` / `chat_stream()` 两种；无密钥走抽取式降级；
- `verify`：PII 复述扫描、无引用标记提示（规则级，阶段二接模型级检测）；
- `persist`：`metric_events` + `audit_logs` 落库，构成数据飞轮原始数据源。

`AgentState.trace()` 让每个节点自己追加审计，`LinearRAGOrchestrator.run()` 与 `.stream()`
共用同一批节点，差别只在生成节点是否流式。

---

## 2. 换成 LangGraph 要改什么

```python
# 伪代码：只改编排层，其他层不动
from langgraph.graph import StateGraph, END
from app.agent.orchestrator import guard_node, retrieve_node, permission_filter_node, ...

builder = StateGraph(AgentState)          # TypedDict 版 state，或直接包一层
for name, fn in NODES.items():
    builder.add_node(name, lambda s, fn=fn: fn(s, db, user, DEFAULT_REGISTRY))
builder.set_entry_point("guard")
builder.add_edge("guard", "retrieve")
...
builder.add_conditional_edges("guard", route_by_intent, {...})   # 多 Agent 分流
graph = builder.compile()
```

需要做的只有：把 `AgentState` 换成 TypedDict/channel 结构、把 `Orchestrator` 协议的实现换成
compiled graph、在 FastAPI 的 `/search/answer` 里把 `get_orchestrator()` 指向新实现。
**权限、工具、检索、审计、SSE 契约全部复用**——这正是把框架放在最外层的意义。

---

## 3. 多 Agent 分流的落点

设计文档里的三 Agent 规则（知识问答 / 数据查询 / 管理）在本骨架里留了意图占位：

```python
# guard_node 里
if re.search(r"(报表|统计|多少|数量|趋势)", query): state.intent = "data_query"
elif re.search(r"(上传|删除|审核|重试)", query): state.intent = "admin_action"
else: state.intent = "knowledge_qa"
```

要真正分流，按顺序判定：**租户/部门权限可用的工具集 → 意图分类 → 知识库可见范围**，
任一环节不满足就降级到知识问答 Agent 或直接拒答，不允许跨部门兜底。
每个 Agent 拿到的 `ToolRegistry` 是**白名单子集**：

| Agent | 允许工具 | 权限要求 |
|---|---|---|
| 知识问答 | `kb.search` | dept_user 起 |
| 数据查询 | `db.query_template`（模板化，禁止自由 SQL） | dept_user + 模板授权 |
| 管理 | `doc.ingest`、`task.status` | tenant_admin |

当前 `build_default_registry()` 只注册了 `kb.search`。骨架期不开放数据/管理类工具，
是为了不把口子开大——等 RBAC 与审计报表补齐再挂。

---

## 4. 工具网关的安全约束

`ToolRegistry.invoke()` 强制做四件事：

1. 未注册工具直接拒绝；
2. `allowed_roles` 校验；
3. `PermissionService.check()`（用同一个 `decide()` 纯函数），被拦截时写 `result=denied` 审计；
4. 参数只记结构不记原文（`_safe_args`），避免日志本身成为泄密面。

以后接 MCP 时，把 MCP tool 列表映射成 `ToolSpec` 注册进来即可，参数白名单用 Pydantic 模型，
**绝不开放自由 SQL / 任意 HTTP 调用**。

---

## 5. 可观测与数据飞轮

- `trace_id` 从 HTTP 中间件 → AgentState → 审计日志 → 指标表，一个 id 还原完整链路；
- `metric_events` 记 token、TTFT、延迟、召回条数、缓存命中、状态；
- `daily_stats` 每天 01:00 预聚合，看板不扫明细；
- BadCase 闭环（点踩/输出拦截/人工抽检 → 分类 → 归因 → 回流评估集）本骨架未实现，
  表结构与埋点已就位，属于 B 档迭代。
