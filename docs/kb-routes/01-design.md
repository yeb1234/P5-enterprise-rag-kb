# P5 三路取证：向量检索 / 图谱检索 / LLM Wiki

编制：2026-11-27 ｜ 状态：**已实现并在真 API 上端到端验证；是演示切片，不是上线交付版**

> **理念来源**：B 站视频《企业知识库怎么选？RAG、GraphRAG、LLM Wiki，用订单排障拆开三路取证》
> （BV1rTyZzfEF9）。本文件只记「视频里的理念 → 本项目怎么落地 → 实测如何」，
> 不照抄视频，也不把视频的结论当成我们的实测结论。

---

## 1. 视频里的核心理念

**同一个问题，三个仓库各取所需，分层取证、按需组合：**

| 仓库 | 装什么 | 擅长回答什么 |
|---|---|---|
| 文本 / 向量仓库（RAG） | 手册、合同、产品文档的原文与分块 | 「原文里具体怎么写的」——局部事实 |
| 关系仓库（GraphRAG） | **只放关系**：实体、术语及其关联 | 「谁和谁有关联、顺着关系能走到哪」——多跳线索 |
| Wiki 仓库（LLM Wiki） | 模型整理出来的业务概念 / 流程 / 规则 + 证据链 | 「这项业务是怎么运转的」——被反复查阅的知识 |

三个关键做法：

1. **图里只放关系，不放原文**；原文仍在文本仓库里；
2. **多路检索后先融合、再重排**；被多路同时命中的结果排在最前（视频里叫「双路命中」）；
3. **Wiki 的每个结论都带证据链**：模型把少量资料整理成 Markdown 页面，
   每条结论另存一条 claim，记下它出自哪个源文件第几行，并带源文件指纹——
   源文件一改，指纹就变，这条结论就得重做。

本项目把这些理念落到**已有的演示切片**里，没有引入新的数据库服务到默认路径。

---

## 2. 三路的实现位置

| 组件 | 文件 | 说明 |
|---|---|---|
| 开关与路由 | `webapp/settings.py`（`ROUTE_KEYS`、`routes_on()`）、`webapp/rag.py`（`_fuse`、`_multi_route`） | 每路一个开关：向量 / 图谱 / Wiki，默认只有向量开 |
| 图数据库 | `kgraph/`（纯函数包）+ Neo4j 5（compose `graphrag` profile） | 术语共现图；可切 LLM 抽取带类型关系 |
| Text2Cypher | `kgraph/cypher.py` + `kgraph/retriever.py` | 自己写，三道防护：关键词白名单 → EXPLAIN 预检 → 只读会话 |
| LLM Wiki | `llmwiki/`（门禁 / 场景筛信源 / 构建 / 检索） | MD 页面 + claims 证据链落 PG 的 `wiki_pages` / `wiki_claims` |
| 适配层 | `webapp/kb_routes.py` | 读 PG、取模型、脱敏、落库、后台任务状态 |
| 接口 | `webapp/routers_routes.py` | `GET /api/kb/routes`、`POST /api/kb/graph/sync`、`POST /api/kb/wiki/build` |

**默认行为不变**：只开向量一路时，走的还是原来的「词法门禁 → 向量排序 → 可选重排」代码，
输出结构只**增加**了 `routes` / `fusion_score` 字段，原有字段一个没删。全量回归 405 项通过。

---

## 3. 图谱这一路：Neo4j 只放关系

### 3.1 选型

按视频理念要「自己写 Text2Cypher、多跳查询、可视化」，对照 2026-11 的公开资料：

- **Neo4j + 官方 Python 驱动**：图查询能力、Cypher 表达力、可视化工具链最成熟，
  与本项目「工程骨架」定位匹配；
- **没装 `neo4j-graphrag` 包**：它的 1.24.0 核心依赖 `pypdf>=6.19`，
  而本机是 pypdf 5.9.0——装上会动到 `parsing/` 的解析链路。
  所以只用官方 `neo4j` 驱动（6.4.1，仅依赖 pytz），Text2Cypher 自己写。
  `neo4j-graphrag` 1.16+ 的 EXPLAIN 防护思路被我们照搬（见下）。

### 3.2 图里存什么

```
(:Document {kb,doc_id,title})-[:HAS_CHUNK]->(:Chunk {kb,chunk_id,doc_id,idx})
(:Chunk)-[:MENTIONS]->(:Entity {kb,name,kind,df})
(:Entity)-[:CO_MENTION {weight}]->(:Entity)     # 术语共现，离线抽取
(:Entity)-[:REL {kind}]->(:Entity)              # 带类型关系，LLM 抽取（可选）
```

- **正文不进图**。`chunk_id` 是回到 PG 的钩子；融合前逐条复核「分块还在、文档仍 ready」，
  图里有而 PG 里没有的候选直接丢弃。
- 节点全部带 `kb=p5kb` 命名空间，与 `shared_infra.py` 同口径——共用 Neo4j 时不撞车。
- 共现边权做归一化（`co / sqrt(df_a × df_b)`），否则「文档」这种泛词会成为万能枢纽，
  多跳全从它走。

### 3.3 Text2Cypher 的三道防护

模型生成的 Cypher 是「自然语言变成可执行语句」，必须拦：

1. **关键词白名单**：出现 CREATE / MERGE / DELETE / SET / REMOVE / CALL 等写关键词直接拒；
2. **EXPLAIN 预检**：交给 Neo4j 先编译，语法错、未知函数在这里暴露，不碰数据；
3. **只读会话**：执行时用 READ 会话，驱动层再拒一次写。

附加两条：必须有 `$kb` 命名空间过滤；必须返回 `chunk_id` 列且带 LIMIT。
任何一步失败 → **回退手写模板并把原因写进报告**（`fallback_reason`），不静默。

**两个实测踩到的坑（都写了单测防回归）：**

- Neo4j 5.26 里 `MATCH (e {name: $names})` 传列表参数**恒不成立**（静默返回 0 行）；
  放进 `NOT (...)` 里更会空真，让本该排除的行混进来。必须用 `WHERE e.name IN $names`。
- 「跳数」口径：1 跳 = 实体直接提到分块；2 跳 = 中间隔一个关联术语。
  第一版把 hops 当成实体间的边数，设 2 却走出了 3 跳。

### 3.4 多跳的边界

离线抽的是**术语共现图**，不是 LLM 语义图谱：问「账号绑定后能做什么」时，
「定后」这类跨词二元组也会命中（它来自「绑定后」被切开）。已通过
「二字词需出现在 ≥3 种文本里」和「最长匹配剪枝」压低噪声，但**不能说它是语义理解**。
真要语义关系，把 `rag_graph_extractor` 切成 `llm`，每个批次一次模型调用。

---

## 4. Wiki 这一路：门禁 + 证据链

### 4.1 三道门禁，缺一不启动

对应视频「场景、信源、新鲜度稳定才启动」：

| 门禁 | 判据 |
|---|---|
| 业务场景 | `wiki_scenario` 非空，且与构建时一致 |
| 信源 | 有 ready 文档；已构建过；**信源指纹**与构建时一致（构建实际用到的那批文档） |
| 新鲜度 | 无过期 / 未生效文档；陈旧占比 ≤ `wiki_stale_ratio_max`（默认 0.1） |

门禁未过 → 路由状态 `gate_blocked`，`reasons` 里写明缺哪一条。
**不是关掉就完事，而是把「为什么不启动」摆出来。**

### 4.2 场景先筛信源

实测：场景填「智能设备使用与故障排查」、提示词也写了「忽略无关分块」，
模型仍把知识库自己的设计文档整理成 24 页——**场景只是个标签**。
所以加了 `llmwiki/relevancy.py`：场景切成检索同口径的词，丢掉没有区分度的
（在多数文本里都出现的「使用」），只把含强词的分块送去整理。

真 API 实测：场景「企业知识库平台交付与上线口径」→ **20/171 段**入选，
强词 `上线 / 业知 / 付与 / 口径 / 库平`，8 页 / 31 条结论，4 批 0 失败、112.6 秒。

### 4.3 页面与证据链

```
wiki_pages(id,title,section,body_md,summary,scenario,built_at)
wiki_claims(id,page_id,statement,evidence_json,source_hash,ordinal)
```

- 页面按四类目录组织：业务概念 / 业务流程 / 业务规则 / 排障台账；
- 每条 claim 的 `evidence` 指向 `chunk_id` 并附原话 quote；
- `source_hash` 是全部分据正文合并后的指纹，**检索时重算**：
  原文改过或有分块被删，这条结论整条弃用并计入 `stale_claims`；
- 模型输出也过 `guardrails` 脱敏出口，失败则 fail-closed 不落库；
- 构建是全量替换（先删 claims 再删 pages）。

**为什么构建走后台任务**：真 API 下 12 批要 228 秒，不能让 HTTP 请求干等。
`POST /api/kb/wiki/build` 立即返回 202 + 任务状态，`GET /api/kb/routes` 轮询进度
（`jobs.wiki_build.progress = "3/12"`）；同名任务正在跑时再次点击返回 409。
状态只在进程内存，**多副本或进程重启后会丢**——这是演示切片的已知边界。

---

## 5. 融合：多路命中优先 + RRF

`webapp/rag.py::_fuse`：

1. **被多路同时命中的排前面**（视频的「双路命中」）；
2. 同组内按 RRF：`Σ 1/(60 + 名次)`。用名次而非分数相加，
   因为余弦、跳数衰减分、页面匹配分三者不同量纲。
3. 图谱 / Wiki 命中的分块按**正文**归并到同一个代表分块——
   演示库里同一份手册传过很多遍，副本 X 和副本 Y 按 chunk_id 比对会被当成两条无关记录，
   双路命中就认不出来。
4. 图谱 / Wiki 的候选先回 PG 复核（分块还在、文档仍 ready），同文在同路内去重。

开了重排时，融合决定候选池，最终顺序由重排定；多路命中以 `routes` 字段展示。

---

## 6. 实测汇总

**离线单测**（conftest 强制 offline，不花钱）：

| 项 | 结果 |
|---|---|
| 全量 pytest（含 `kgraph` / `llmwiki` / 新增 webapp 集成） | **405 passed** |
| backend | 26 passed |
| 端到端 `selftest.py` | **33/33 DEMO_READY** |
| `verify_infra.py` | **INFRA_READY**（改为按必需表清单校验，加表不再误报） |
| 前端 `node --check`、静态版本号 | OK（统一升到 `?v=20`） |
| 冻结区 `eval/` `demo/` | 零 diff |

**真模型 API**（用户明确要求，控制批数）：

| 项 | 结果 |
|---|---|
| 图谱同步（离线抽取，后台任务） | 2.1 秒，108 文档 / 171 分块 / 2281 术语 / 6795 共现边 |
| 向量+图谱融合 | 双路命中稳定出现；「如何给设备配网」等问句有多路信号 |
| Text2Cypher | 模型生成 `WHERE e.name IN $names` 的只读 Cypher，过校验 + EXPLAIN，返回 27 行；写操作被拒后回退模板 |
| LLM 实体关系抽取 | 9 段文本抽出「复位键 —[长按进入]→ 配网模式」等 10 条带类型关系 |
| Wiki 构建（场景筛选后） | 20/171 段，4 批 0 失败，8 页 / 31 条结论，112.6 秒，门禁三项全过 |
| Wiki 路由检索 | 命中 3 页，证据分块回 PG 复核通过 |

---

## 7. 三态表

| 状态 | 项 |
|---|---|
| **已实现** | 三路开关与融合；`kgraph` 术语图 / 多跳 / Text2Cypher 三道防护；LLM 语义关系抽取（`rag_graph_extractor=llm`，真 API 18 批 0 失败、20 条带类型关系）；`llmwiki` 门禁 / 场景筛信源 / 证据链 / 后台构建；Wiki 任务状态落库 + 按变化文档增量重建；查询入口强制命名空间隔离；MCP 风格 HTTP JSON-RPC 工具端点；Web 设置页与检索调试页；434 项单测 |
| **只设计** | 图 embedding 向量检索；社区摘要 / 全局问答；Neo4j Enterprise 多库 + RBAC；MCP 的 SSE / stdio 传输与完整协议兼容；真正的分布式任务队列（租约 / 心跳 / 抢占） |
| **未做** | 生产级图数据库权限与备份；跨实例联邦查询 / 按用户的图级 ACL；增量重建失败自动回滚到上一次 Wiki 快照；把外部 P1/P2/P4 数据接进同一实例 |

### 2026-11-27 追加：四块跟进的实测

1. **LLM 语义建图**（`rag_graph_extractor=llm`）：71 段去重文本分 18 批送真模型，**0 失败**；
   抽出 121 个实体、20 条带类型关系入图，例如「复位键 —[长按进入]→ 配网模式」、
   「Milvus —[替代]→ PGVector」「departments —[属于]→ tenants」。
   过程中修了两个真 bug：失败只记计数不记原因（现在逐批记 `failures`，输入从 700 字压到 500 字、
   每段限 3 实体 2 关系，失败率从 2/3 降到 0/18）；模型 Cypher 不返回 `hop` 列导致多跳被标成 1 跳
   （现在按 `path` 节点数兜底）。详见 `kgraph/extract_llm.py` 与 `kgraph/retriever.py`。
2. **任务落库 + 增量重建**：`kb_jobs` 表持久化任务状态，`worker_id` 区分进程，
   重启/换副本的 `running` 读时标 `interrupted`，同名任务靠部分唯一索引跨进程互斥；
   Wiki 按文档内容哈希做增量，**信源无变化时零模型调用**。详见 `03-jobs-and-incremental.md`。
3. **命名空间隔离**：查询入口 `assert_namespaced()` 强制每个业务节点模式带 `kb`，
   「上半句守规矩、下半句偷读别的项目」会被拒；DDL 与内省查询走白名单（不读业务数据）。
   社区版没有 RBAC，不假称做了权限隔离。详见 `04-namespace-isolation.md`。
4. **MCP 接入**：HTTP JSON-RPC 子集暴露 `vector_search` / `graph_search` / `wiki_search` /
   `fusion_search` 四个工具，带鉴权与 JSON-RPC 错误契约；完整协议与 SSE / stdio 是设计方向。
   详见 `05-mcp-integration.md`。
