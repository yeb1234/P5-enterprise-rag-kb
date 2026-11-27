# 企业知识库 / 企业 RAG 系统 · 能力清单与差距对照（调研版）

> 面向对象：中文企业级知识库项目（React / FastAPI / PostgreSQL / Milvus / Redis+Celery / 外部 DeepSeek），
> 定位为「可运行开发框架骨架 + 工程实现版」。
> 编制日期：2026-11-12 ｜ 检索范围：2025–2026 年公开资料。

---

## 0. 边界声明（先读这一段）

1. **这是一份「调研清单」，不是本项目已实现承诺。** 第 1 节是外部资料核实结论，第 2 节是「中型公司常见要求」与「本项目现状」的对照，第 5 节是「建议」，三者都不代表已经落地。
2. **本项目是 A / B 档骨架，不是上线交付版。** `README.md` §0 已写明：链路完整、接口可跑、约束渗透到表结构与代码分层，但**没有压测、没有容灾演练、没有全量回归**。请勿把设计文档里写的方案说成落地。
3. **状态列口径（四档，取值唯一）**：
   - `已实现`：`backend/` 骨架或 `webapp/` 切片中有可运行代码、表结构或接口支撑（含「骨架最小集 / 规则级 / 演示替身」，不等于上线级）。
   - `演示级`：仅 `webapp/` 可运行切片里以单租户 / SQLite / 进程内队列 / 组件替身方式跑通，能演示闭环，但不是生产骨架实现。
   - `架构预留`：代码里留了接口、表字段、开关或设计文档，但功能逻辑未落地（开关默认关闭、P0/P1/P2 插件清单、「只设计未演练」）。
   - `未做`：代码与设计里都没有落地，或项目文档明确标注「明确不做 / 未做」。
4. **判定依据与免责**：本清单「已实现」的判定依据是 `README.md` §4 接口/落点表、`docs/operations.md` 状态标注、`webapp/README.md` 自测结论；本次**未逐文件审阅 `backend/` 源码**，若 README 与源码有出入，以源码为准。凡 `docs/next-version-messaging-and-plugins.md` 的 P0/P1/P2 插件清单、`docs/operations.md` 的「只设计 / 未做」、`docs/permission-model.md` 的「架构预留」，一律不标 `已实现`。
5. **不编造**：外部量化门槛（具体 QPS、文档量、SLA 数值、合规条款逐条映射、厂商定价）未查到统一权威结论的，统一写「未核实」，见 §1.3。

---

## 1. 调研来源与核实结论

### 1.1 主来源（6 个，可核对 URL）

| # | 来源 URL | 要点（与能力清单的对应） | 核实状态 |
|---|---|---|---|
| 1 | https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview?tabs=videos | 微软官方把企业 RAG 挑战归为：查询理解、多源接入、token 约束、响应时间、安全治理。经典 RAG = **hybrid search（关键词+向量）+ semantic ranking 重排**；indexers 支持 **incremental indexing**、skills 做分块/向量化/OCR/图像描述；agentic retrieval 内置 **citation tracking（溯源）** 与 query activity log；安全用 **document-level security trimming（查询时按身份裁剪）**、Entra ID 权限元数据、private endpoints。 | **已核实**（WebFetch 抓取原文，2026-11-12） |
| 2 | https://learn.microsoft.com/en-us/azure/search/search-security-best-practices | 官方安全最佳实践：**document-level access control（行级安全）** = 索引时写入权限元数据、查询时按调用者身份过滤；四条途径 security filters / POSIX-like ACL + RBAC scopes / Purview sensitivity labels / SharePoint ACLs；**权限变更需 reindex，否则 ACL 过期**；启用 diagnostic logging 与监控；CMK 加密有 30–60% 性能代价。 | **检索核实**（Microsoft Learn 官方页面，未逐字抓取全文；与来源 1 的 security trimming 结论一致） |
| 3 | https://modelcontextprotocol.io/specification/2025-11-25 | MCP 官方规范（2025-11-25）：JSON-RPC 2.0、Host/Client/Server、能力协商；Server 暴露 **Resources / Prompts / Tools**，Client 可提供 Sampling / Roots / Elicitation；附加 Progress tracking / Cancellation / Logging。「Security and Trust & Safety」四原则：**用户同意与控制、数据隐私、工具安全（工具=任意代码执行、调用前须显式同意）、LLM 采样控制**；明确「协议本身不能在协议层强制，实现者 SHOULD 建同意/授权流、访问控制与数据保护」。 | **已核实**（WebFetch 抓取原文，2026-11-12） |
| 4 | http://www.owasp.org.cn/sys-nd/26.html | OWASP 中国发布《OWASP 大语言模型应用程序十大风险 2025》：**LLM01 提示注入 / LLM02 敏感信息披露 / LLM03 供应链 / LLM04 数据和模型投毒 / LLM05 不当输出处理 / LLM06 过度代理 / LLM07 系统提示泄露 / LLM08 向量和嵌入的弱点 / LLM09 虚假信息 / LLM10 无界消费**。其中 LLM08（检索忽略逐用户 ACL）对应权限过滤，LLM10（denial-of-wallet）对应 token/成本治理。 | **已核实**（WebFetch 抓取 OWASP 中国官方发布页，2024-12-31 发布并标注 2025 版；主站为 genai.owasp.org，另有 bdemerson / Nemko / SentinelPanda 等多家二手来源交叉印证同一 10 项列表） |
| 5 | https://docs.ragas.io/en/v0.1.21/getstarted/evaluation.html | Ragas 官方：评测输入是 **question / contexts / ground_truth 三元组 golden test set**；四个核心指标 —— **faithfulness**（答案对上下文的事实一致性，衡量幻觉）、**context_precision**（检索上下文相关度）、**answer_relevancy**（答案切题度）、**context_recall**（是否召回全部必要信息）。补充 Red Hat OpenShift AI 官方文档：answer_correctness / answer_similarity，并明确「**建立自动化质量闸门（quality gates）防止回归、每次代码提交或模型更新后自动触发**」。 | **已核实**（WebFetch 抓取 Ragas 官方原文）+ **检索核实**（Red Hat 官方片段，docs.redhat.com） |
| 6 | https://nvlpubs.nist.gov/nistpubs/ai/nist.ai.600-1.pdf | NIST AI 600-1《AI RMF: Generative AI Profile》（2024-07-26）：把 AI RMF 1.0 的 **GOVERN / MAP / MEASURE / MANAGE** 四函数落到生成式 AI；治理是贯穿性函数，要求文档化、法律/合规要求理解与记录、人工复核、全生命周期与第三方软件/数据风险；Measure 对应对抗鲁棒性、可靠性与评测。 | **检索核实**（NIST 官方 PDF 摘要，未逐页抓取全文；AI RMF 1.0 主文档见 nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf） |

### 1.2 补充交叉印证（行业综述 / 工程博客，检索核实，非标准原文，不计入上面 6 个主来源）

- https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/ —— golden QA 集、评测流水线、**CI/CD 质量闸门**、**per-query 可观测（每次检索/每 token/每次反馈都记录）**、SLO 与漂移告警、pre/post-retrieval 合规过滤、**ABAC at query and document level**、版本化测试数据、近实时/增量索引、动态 token 预算窗口。
- https://supernovasai.com/learn/enterprise-ai-platforms-buyers-guide-2025 —— **SSO / SCIM / 细粒度 RBAC**、工作区隔离、PII 检测与脱敏、保留策略、不可篡改日志、**MCP 连接器与工具级权限/限流/预算**、提示词版本化与审批 / A-B、**token 用量与按团队/项目预算告警**、缓存与模型智能路由、golden 回归测试集。
- https://www.techtez.com/rag-done-right-how-to-build-enterprise-grade-knowledge-assistants/ —— 混合检索 + RRF + cross-encoder 重排、**authorization-aware retrieval（检索期按权限过滤，不检索无权内容）**、「only answer from retrieved docs / if unsure say don't know」**拒答**、内联引用、Recall@k / MRR / nDCG、per-tenant 用量与成本看板。
- https://www.bdemerson.com/article/owasp-llm-top-10 —— 对 OWASP 2025 十项的逐项解释（间接注入、LLM08 向量与嵌入弱点、LLM10 无界消费），并给出「按请求/按用户设 spend caps」的可操作建议。

### 1.3 明确「未核实」的结论（不编造）

- 「中型公司」的**量化门槛**（具体 QPS、文档总量、并发连接数、SLA 百分比、RTO/RPO 数值）：各来源均未给出统一权威数值 → **未核实**，本清单只做定性要求，不写数字基线。
- **合规条款逐条映射**（等保 2.0 三级条款、ISO 42001 Annex A 逐条、EU AI Act Article 15 义务细节）：仅在二手来源中被提及，未逐条核对官方原文 → **未核实**，不作为本项目验收依据。
- **行业通用配额/定价数值**（如统一的 RPM/TPM 上限）：项目内「RPM 50 / TPM 2M / 5h·7d 墨点」是本项目上游网关的实测约束，**不推广为中型公司通用标准** → 通用结论未核实。
- 「2026 年之后」的最新规范修订：检索命中 MCP 提到 2026-07-28 起 sampling 弃用（二手 SDK 文档），但**未逐字核对官方 changelog** → 该时间点 **未核实**，本清单不据此下结论。

---

## 2. 能力清单（大表）

> 列说明：`本项目状态` 四档见 §0.3；`是否纳入实现范围` 取值 —— `已在（已落地）` / `是（Top10）` / `条件（团队质量工作或上线级，非骨架范围）` / `否（明确不做或低性价比）`；`优先级` 为该能力对中型公司的固有重要性 P0/P1/P2。
> 共 **50 条**能力项，按 7 个能力域分组。

| 能力域 | 能力项 | 中型公司常见要求 | 本项目状态 | 落点文件或模块 | 是否纳入实现范围 | 优先级 | 验收口径 |
|---|---|---|---|---|---|---|---|
| A 认证与组织架构 | A1 本地账号、口令与 JWT（签名/过期/刷新轮换/单点登出） | 统一身份入口，令牌可失效、可轮换，口令不可逆存储 | 已实现 | `backend/app/core/security.py`、`api/v1/auth.py`；`webapp/` pbkdf2+HMAC JWT | 已在（已落地） | P0 | 刷新令牌轮换后旧 token 失效；401 统一拦截退回登录 |
| A 认证与组织架构 | A2 SSO / OIDC / SAML / SCIM 组织同步 | 对接企业 IdP，账号随组织架构自动开通/回收 | 未做 | 无（`docs/operations.md` 列「回调验签未做」，无 OIDC 入口） | 是（Top10 #10） | P1 | 可插拔 AuthProvider seam + 一个 OIDC 验证切片，能演示「IdP 登录 → 映射本地角色」 |
| A 认证与组织架构 | A3 RBAC 角色模型与部门树 | 角色分层、部门层级、只读/可写分离 | 已实现 | `backend/app/services/permission.py`、`docs/permission-model.md`（5 角色 + 物化路径部门树） | 已在（已落地） | P0 | `decide()` 纯函数 8 步判定可单测；viewer 写操作被拒 |
| A 认证与组织架构 | A4 ABAC / 属性与条件策略（含 OPA 类） | 按敏感度、属性、条件动态授权，显式 deny 优先 | 架构预留 | `acl_grants.conditions` JSONB + GIN 索引；`decide()` 未接完整条件引擎 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 条件表达式可配置、可回放、可解释，且不绕过 `decide()` 单一入口 |
| A 认证与组织架构 | A5 多租户逻辑隔离（tenant_id 行级） | 共享 Schema + 行级隔离，所有查询强制带租户 | 已实现 | `backend/app/db/TenantRepository`、`docs/permission-model.md` §5 | 已在（已落地） | P0 | 业务代码拿不到「不过滤」Query；`tenant_id` 只由依赖注入产生 |
| A 认证与组织架构 | A6 多租户物理隔离（独立 Schema / 独立库 + 路由） | 强合规客户可物理隔离 | 架构预留 | `docs/permission-model.md` §5「本骨架未实现」 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 连接池按租户路由，跨租户查询在 DB 层即被阻断 |
| A 认证与组织架构 | A7 片段 / 段落级 ACL（细粒度授权） | 同一文档内按段落白名单授权 | 架构预留 | `chunks.acl`、`evaluate_fine_grained()`、`FINE_GRAINED_ACL_ENABLED=False`、写入侧未接 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 开关打开后，命中但无权限的片段不进 LLM 上下文 |
| B 文档接入 | B1 分片上传 / 断点续传 / 秒传 / 批量聚合 | 大文件不进内存、断点只补缺片、同 checksum 秒传、批次进度 | 已实现 | `docs/async-upload.md`；`backend/app/api/v1/uploads.py`；`webapp/` 排队演示 | 已在（已落地） | P0 | 单请求恒定 8MiB；刷新页面进度不归零；Redis 挂了降级轮询 PG |
| B 文档接入 | B2 多格式解析（PDF / Office / HTML / MD） | 常见办公格式统一转文本并保留页级元数据 | 演示级 | `backend/app/services/parser.py`（pypdf）；`webapp/` 仅纯文本 | 条件（随 #2 ParserProvider 一并） | P1 | 换格式不改 chunks 表与引文契约；页级元数据不丢 |
| B 文档接入 | B3 扫描件 OCR / 表格 / 双栏 / 多模态解析 | 扫描件、复杂版式、图表可检索 | 未做 | `README.md` §8 已知限制明确「未做 OCR 与多模态兜底」 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 解析质量评估集覆盖双栏/表格/扫描件，页码引文可信 |
| B 文档接入 | B4 中文分词 + 可插拔 ParserProvider（解析质量） | 中文分词、解析器可替换、不动下游 schema | 架构预留 | `README.md` §1.3「接口已抽象」；`next-version...md` P0 插件 | 是（Top10 #2） | P0 | `ParserProvider` 接口 + zhparser 类分词；解析质量 A/B 有评估集证据 |
| B 文档接入 | B5 增量索引 / 源侧变更捕获 / 近实时 | 源侧变更近实时入索引，索引不重建只 upsert | 未做 | 仅 `/documents/{id}/reindex` 手动全量 + 每日 `reconcile_vectors` 对账 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 源变更到可检索有可测时延；向量按 chunk_id 幂等 upsert |
| B 文档接入 | B6 文档版本 / 快照 / 行级 diff / 追加式回滚 | 不可变版本、可对比、可回滚 | 已实现 | `backend/app/services/version.py`、`README.md` §4 版本端点 | 已在（已落地） | P1 | 编辑生成 v2；compare 有行级 diff；restore 走追加式不覆盖 |
| B 文档接入 | B7 文档审批 / 发布审核工作流 | draft→review→published，发布前人工复核 | 未做 | 无（文档状态机只有上传/解析/索引态，无审批态） | 是（Top10 #9） | P1 | 状态机含审批态；未发布文档不进检索；审批动作入审计 |
| B 文档接入 | B8 数据生命周期 / 保留 / 归档 / 中间产物清理 | 保留策略、冷归档、临时产物可回收 | 未做 | `docs/async-upload.md` §8「中间产物未做生命周期清理」；`docs/operations.md` §5「无分区」 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 中间产物按 `intermediate/{doc}/` 可批量清；审计按月分区 + 冷归档 |
| C 检索 | C1 混合检索（词法 + 向量 + RRF 融合） | 关键词与语义双路召回、RRF 融合 | 已实现 | `backend/app/services/search.py`、Milvus + PG 全文 + RRF | 已在（已落地） | P0 | 双路召回可分别关闭；stub embedding 时明确标注「非语义」 |
| C 检索 | C2 重排 Rerank（cross-encoder，可降级） | 召回后精排，必须带降级开关护住 P95 | 演示级 | `webapp/providers.py`（三档 + 实测）；`backend/` 骨架为 P0 插件（架构预留） | 是（Top10 #3） | P0 | RRF 后加一层可降级重排；不可用时 `skipped` 留痕不假装用了 |
| C 检索 | C3 检索前 / 召回后双重权限过滤 | 先滤权限再重排再进模型，向量层也过滤 | 已实现 | `docs/permission-model.md` §1、`MilvusVectorStore.build_permission_expr()`、`filter_candidates()` | 已在（已落地） | P0 | 缓存 key 带 tenant+user+role+dept；无权片段不进上下文 |
| C 检索 | C4 引用溯源（页码 / 分块 / 得分 / 原文展开） | 每条回答可点开看原文，来源可回放 | 已实现 | `backend` 带引用生成；`webapp/` `chat_sources` + `/api/chat/sources` | 已在（已落地） | P0 | 引用卡片含文档/分块/得分/方式；历史会话来源可回放 |
| C 检索 | C5 元数据与分类标签过滤 | 按项目/产品/时间/语言/分类筛选 | 已实现 | `backend` `categories/tree`、`tags`、`attributes` JSONB + GIN | 已在（已落地） | P1 | 子孙分类可筛；元数据过滤与权限过滤叠加不冲突 |
| C 检索 | C6 查询理解 / 改写 / 多跳子查询 | 复杂问句拆子查询、用会话历史改写 | 未做 | 无（仅 `webapp/` 中文二元组词法近似） | 否（明确不做或低性价比） | P2 | 复杂问句可拆为多个子检索并并行；改写结果可解释 |
| D 生成与 Agent | D1 SSE 流式输出 | 逐 token 渲染，首 token 快 | 已实现 | `backend` `/search/answer`(SSE)；`webapp/` fetch stream | 已在（已落地） | P0 | 流式不缓冲；nginx `proxy_buffering off` |
| D 生成与 Agent | D2 断线续传（resume_from / Last-Event-ID） | 断线重连不重复、不丢已收内容 | 已实现 | `webapp/` `resume_from`；`docs/async-upload.md` `Last-Event-ID` 快照 | 已在（已落地） | P0 | 重连跳过已发 token；聊天日志按 conversation_id 幂等只写一次 |
| D 生成与 Agent | D3 多轮对话 / 会话记忆 | 上下文连续、可追溯历史 | 演示级 | `webapp/` `/conversations`、`/history`、会话状态面板 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 多轮引用上下文；无对话压缩/记忆策略的越界 |
| D 生成与 Agent | D4 检索为空拒答 / 不编造 | 无命中明确说「没找到」，只用检索片段作答 | 已实现 | `webapp/` `degraded=true` 拒答话术；`eval/` generation 套件含拒答 | 已在（已落地） | P0 | 空召回不出编造答案；降级文案不与真答案拼接 |
| D 生成与 Agent | D5 Provider 适配器 / 多模型路由 / 故障切换 | 生成/嵌入/重排解耦，可切多家并互为备份 | 已实现 | `webapp/providers.py` seam；`backend` 双 Provider（DeepSeek / Embedding） | 已在（已落地） | P1 | 设置页三档即时生效；换模型不动检索/权限/评测逻辑 |
| D 生成与 Agent | D6 工具调用 / Function calling / 工具注册表 | 结构化工具 schema、逐步计划、透明 trace | 架构预留 | `backend/app/agent/ToolRegistry`、`NodeRegistry`、`Orchestrator` 协议 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 工具调用仍走同一个 `decide()`；绝不开放自由 SQL 与任意 HTTP |
| D 生成与 Agent | D7 MCP 工具网关 | 标准化接入内外部服务，工具级权限/预算 | 未做 | `README.md` §0「不能讲：MCP 网关已落地」；`next-version...md` P2 | 否（明确不做或低性价比） | P2 | 白名单网关 + 用户显式同意；工具描述视为不可信输入 |
| D 生成与 Agent | D8 多 Agent 分流 / 编排框架（LangGraph 类） | 按意图分流，编排层可替换 | 架构预留 | `LinearRAGOrchestrator` + `Orchestrator` 协议（线性节点编排） | 否（明确不做或低性价比） | P2 | 换编排框架只改编排层，鉴权/审计/工具层不动 |
| E 质量工程 | E1 评测集 / golden QA 数据集（版本化） | 真实查询 + 理想答案，禁原地改 | 已实现 | `eval/config/registry.json`（14 对象）、`eval/datasets/cases_v0.1.0.jsonl` | 已在（已落地） | P0 | 用 `gold_evidence` 原文而非预置 chunk_id；改对象必升版本 |
| E 质量工程 | E2 离线占位评测链路 / 确定性复现 | 零密钥可跑、可复现、改动产生新 run_id | 已实现 | `eval/run_eval.py`、`eval/core/ids.py`；`verdict=OFFLINE_READY` | 已在（已落地） | P0 | 确定性双跑 true；配置/代码/数据集任一改动生成新 run_id |
| E 质量工程 | E3 真实 API 评估工作台 / 多提示词多模型对比 | 真实模型下可复跑采集、结构化事实源 | 演示级 | `evalbench/`、`docs/evaluation-report.md`、`webapp/out/eval_report.json` | 已在（已落地） | P1 | 事实源不含凭证；明确「工程实测记录，不是质量基线」 |
| E 质量工程 | E4 标准质量指标（RAGAS / MRR / NDCG / faithfulness） | 召回与生成分层度量，冻结可比阈值 | 未做 | `eval/core/metrics.py` 仅占位；`README.md` §0「不能讲：MRR/NDCG 基线」 | 是（Top10 #4） | P0 | 真实 embedding 下产出 faithfulness / context_precision / MRR / NDCG 并冻结基线 |
| E 质量工程 | E5 回归闸门 / CI 质量门禁 | 每次改动可验证「没退化」，占位结果不参闸 | 架构预留 | `eval/` 离线 health 闸门 + `compare_runs.py`；`next-version...md` P1「闸门接 CI 未做」 | 是（Top10 #5） | P1 | L0–L3 接 CI；`comparable=false` / `NOT_COMPARABLE` 绝不参与闸门 |
| E 质量工程 | E6 Badcase 采集 / 筛选 / 导出 / 完成度 | 采集—保存—查看—导出闭环，字段齐全 | 已实现 | `webapp/` `badcases` 表、`/api/badcases`、admin 完成度看板 | 已在（已落地） | P1 | 导出与列表共用筛选；创建时校验 `eval_task_id`，进度与记录不会脱节 |
| E 质量工程 | E7 可观测（trace_id / span / 指标 / SLO） | 每次检索/每 token/每次反馈都记录，可定位瓶颈 | 架构预留 | `X-Trace-ID` 贯通已实现；OTel / LangSmith 等价 span 为 P0 设计 | 是（Top10 #6） | P0 | trace_id 下钻到逐 span 与 `object_refs`；可展示一条完整 trace |
| E 质量工程 | E8 越权 / 安全用例集与权限回归 | 跨租户/跨部门/敏感度/缓存串租户全拦截 | 未做 | `tests/test_permission.py` 覆盖 7 类（可扩）；`README.md` §7「20+ 条越权用例」仍待补 | 是（Top10 #1） | P0 | 20+ 条越权用例全部拦截并纳入 CI；含缓存串租户与任务跨租户 |
| F 成本与治理 | F1 Token 计量 / 预算 / 配额 / 限流 | 按请求/用户/租户计量，预算告警，分级限流 | 架构预留 | `middleware/rate_limit.py` 按 IP 令牌桶（基础）；`redis_client.acquire_slot` 预留；`webapp/` 429 退避演示级 | 是（Top10 #7） | P1 | token 用量可查；按租户分级配额 + 全局并发闸；429 rate 退避 / quota 不重试 |
| F 成本与治理 | F2 缓存治理（key 带身份与配置快照） | 缓存不串租户，对象版本变更自动失效 | 架构预留 | `next-version...md` P1；`webapp/` `cache_key` 仅带 provider+模型名 | 是（Top10 #8） | P1 | 缓存 key 带 tenant/user/role + `config_snapshot_hash`；升版本自动失效 |
| F 成本与治理 | F3 审计日志（只追加 / 可回放） | 登录/上传/检索/导出/拦截全留痕，带 trace_id | 已实现 | `backend/app/services/audit.py`、`/audit-logs`；`webapp/` 演示级 | 已在（已落地） | P0 | 只追加；按 trace_id 可还原整条链路；权限拦截（denied）也留痕 |
| F 成本与治理 | F4 合规：留存 / 脱敏 / 合规导出 / 异常告警 | 留存策略、脱敏检索、异常拦截接告警 | 未做 | `docs/operations.md` §2 明确「未做」 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 审计按月分区 + 冷归档 + 只读账号；脱敏与导出有策略 |
| F 成本与治理 | F5 内容安全（注入 / PII / 输出过滤三道闸） | 规则+模型级检测，入库/召回/输出同规则 | 已实现 | `backend/app/agent/orchestrator.py`（规则级骨架）；三道闸模型级为「只设计」 | 已在（已落地） | P0 | 规则级注入/PII 拦截已跑；模型级三道闸属后续，不冒充已做 |
| F 成本与治理 | F6 密钥轮换 / 合规框架映射 | 密钥轮换、依赖扫描、对齐 NIST/ISO/EU AI Act | 未做 | `docs/operations.md` §2「未做」 | 否（明确不做或低性价比） | P2 | 有轮换与扫描机制；框架映射仅作谈资，不作验收依据（见 §1.3 未核实） |
| F 成本与治理 | F7 数据出域 / 模型供应商治理 | 托管 vs 自托管的出域取舍，供应商备份 | 未做 | 仅 `webapp/README.md` 提到两家外部供应商互为备份 | 条件（团队质量工作或上线级，非骨架范围） | P2 | 明确数据出域边界；密钥不落库、不下发前端、不进日志 |
| G 运维 | G1 健康检查（liveness / readiness 分离） | 依赖未就绪摘流量但不重启实例 | 已实现 | `/health/live`、`/health/ready`；`docs/operations.md` §3 | 已在（已落地） | P0 | Milvus 挂了 ready 标 `degraded`、语义降级为关键词，服务不整体不可用 |
| G 运维 | G2 监控告警 / Prometheus / SLO | 三项 SLO（首 token / 整答 / 错误率）+ 告警通道 | 未做 | `next-version...md` P1；`docs/operations.md` §3「未做」 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 指标从 `metric_events` / `daily_stats` 出，不重复埋点；先定口径再接告警 |
| G 运维 | G3 备份容灾 / 主备切换 / 演练（RTO/RPO） | 备份可校验、切换有实测 RTO/RPO | 架构预留 | `docs/operations.md` §3「只设计（未演练）」 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 一次主备切换演练 + 一次回滚演练并记录实测 RTO/RPO |
| G 运维 | G4 降级 / 幂等 / 跨库对账 | at-least-once + 幂等键，向量是可重建派生数据 | 已实现 | `acks_late`、`chunk_id=uuid5`、每日 `reconcile_vectors`、`docs/async-upload.md` §6 | 已在（已落地） | P0 | PG 文本先落库再写 Milvus；软删异步清 + 每日差集对账补孤儿 |
| G 运维 | G5 任务可靠性（DLQ / ack 落库后 / 故障域分离） | 不可丢投递与死信，缓存抖动不拖垮入库 | 架构预留 | v0.1 有 `acks_late`+PG 台账+beat 捞回；RabbitMQ/DLQ 属 `next-version...md` v0.2 设计 | 条件（团队质量工作或上线级，非骨架范围） | P2 | ack 在业务落库之后；DLQ 有演练记录；故障注入无重复向量 |
| G 运维 | G6 容量压测 / 全量回归 / 灰度回滚 | P95 首 token / 整答 / 错误率有压测报告 | 未做 | `README.md` §0「没有压测、没有全量回归」 | 条件（团队质量工作或上线级，非骨架范围） | P1 | 压测报告 + 越权用例集 + 注入拦截率 + 一次回滚演练 |

> 表内 `落点文件或模块` 中的 `next-version...md` 指 `docs/next-version-messaging-and-plugins.md`。

---

## 3. 框架 / 工程要求

> 这一节讨论「骨架本身该具备的工程属性」，与具体业务能力正交；多数是本项目**已经在讲**的强项，少数仍是设计。

| 工程要求 | 中型公司常见口径 | 本项目现状 | 落点 | 差距 / 建议 |
|---|---|---|---|---|
| 可测试性 | 纯函数可单测、端到端可复跑、隔离库 + 强制离线 | 已实现（演示级） | `tests/test_permission.py`；`webapp/selftest.py` 33 项；`webapp/tests/` 41 项；`eval/tests` 14 项 | 缺 20+ 条越权用例集与真实模型回归基线（见 E8 / E4） |
| 可观测 | 每次检索/每 token/每次反馈可记录，trace 可下钻 | 架构预留 | `X-Trace-ID` 贯通请求/任务/审计（已实现）；OTel / LangSmith 等价 span 为 P0 设计 | 从「看日志」升级到「看链路」，需 trace→span→object_refs（见 E7） |
| 配置化 | 密钥、阈值、模型全外置；切换不改代码；生产默认密钥拒绝启动 | 已实现 | `.env` + `core/config.py`；`webapp/providers.py` seam；`webapp/app_settings` 即时生效 | 配置命名空间已用 `WEBAPP_` 前缀隔离，避免与 `backend` 枚举冲突；建议补 `config_snapshot_hash` 进缓存 key（F2） |
| 版本化 | 评测对象、数据集、提示词、契约不可原地改，改动产生新 id | 已实现 | `eval/config/objects/*.json`（14 个版本化对象）、`eval/compare_runs.py`、`evalbench/prompts/CHANGELOG.md`；`run_id` 由 profile+seed+config_snapshot_hash+code_hash+dataset_hash 确定性生成 | 强项，可直接讲；注意 `eval/` 真实 API 字段仍 `__TBC__`，不要解冻 |
| 契约先行 | 统一响应/错误码、分页契约，接口未就绪时快速失败而非猜测 | 已实现 | `backend/app/schemas/`（统一错误与分页）；`eval/core/contracts.py` 接入契约门禁；`blocking` 字段未确认则 `real_api` 快速失败 | 强项；「不把 `__TBC__` 换成猜测地址/鉴权/配额」是本项目明确纪律 |
| 降级与幂等 | 所有降级「留痕 + 可见」，绝不静默失败；at-least-once + 幂等键 | 已实现 | `docs/async-upload.md` §6 失败矩阵；`webapp/README.md` §3 降级矩阵；`chunk_id=uuid5`；429 退避 / quota 不重试 | 强项；注意「流式一旦开始吐字就不再重试」「已出正文不拼接降级文案」两条硬约束 |
| 数据真值分层 | 状态真值在 DB，缓存/消息/向量都是可重建派生数据 | 已实现 | PG 是唯一真值；Redis 只做实时通道；先 PG 后 Milvus；`docs/next-version...md` §1 三者职责边界 | 强项；v0.2 引入 RabbitMQ 属「按数据能不能丢重划边界」，不是改一个配置项 |
| 边界诚实 / 不静默 | 明确「已实现 / 只设计 / 未做」，主动列不做什么 | 已实现 | `README.md` §0「能讲/不能讲」；`webapp/README.md` §2.1「故意没加的入口」；`docs/operations.md` 页首 | 本项目最鲜明的工程取舍，讲解时优先讲；本清单沿用同一口径 |
| 可回滚 / A-B | 每项改动能回答「挂哪个版本化对象 / 用什么指标判定 / 怎么回滚」 | 架构预留 | `next-version...md` §8「每项插件必须能回答三问」；`evalbench/iterations/comparisons/` 有采纳对比 | 闸门未接 CI（E5）；收益必须等真实 API 基线冻结后才给数值结论 |
| 依赖与故障域分离 | 缓存/队列/进度故障不互相拖垮，中间件职责不重叠 | 架构预留 | v0.1 Redis 兼 broker/result/缓存/进度（故障面集中）；v0.2 方案重划 | 触发条件（任务不可丢 / 时长逼近可见性超时 / 队列级配额）满足才升 v0.2 |

---

## 4. 与本项目现状的差距归纳

### 4.1 可直接讲「已落地」的（有代码 / 接口 / 表结构 / 自测支撑）

- 注册登录 + JWT（刷新轮换、单点登出）、bcrypt/pbkdf2、httpOnly Cookie（A1）
- RBAC 五角色 + 部门树物化路径 + 敏感度上限 + 显式 deny 优先（A3）
- 多租户 `tenant_id` 行级隔离 + `TenantRepository` 强过滤（A5）
- 分片上传 / 断点续传 / 秒传 / 批量聚合 / 四级 SSE 进度（B1）
- 文档版本 / 不可变快照 / 行级 diff / 追加式回滚（B6）
- 混合检索（PG 全文 + Milvus 向量 + RRF 融合）（C1）
- 检索前 expr + 召回后 `filter_candidates` 双重权限过滤（C3）
- 引用溯源与来源追溯（`chat_sources`、页码/分块/得分、历史回放）（C4）
- 分类标签树与 JSONB 属性 + GIN 索引过滤（C5）
- SSE 流式输出 + 断线续传 `resume_from` / `Last-Event-ID`（D1、D2）
- 检索为空拒答、不编造（D4）
- Provider 适配器 seam：LLM / Embedding / Rerank 三档可切、即时生效（D5，演示级已落地）
- 评测体系：`eval/` 14 个版本化对象 + 26 条离线用例 + 确定性双跑（E1、E2）
- `evalbench/` 真实 API 评估工作台 + `docs/evaluation-report.md` 实测报告（E3，演示级）
- badcase 采集 / 筛选 / 导出 / admin 完成度看板（E6）
- 审计日志只追加 + `X-Trace-ID` 贯通（F3）
- 规则级注入 / PII 输出拦截（F5，骨架最小集）
- 健康检查 liveness/readiness 分离（G1）
- 降级 / 幂等 / 跨库对账 / 向量视为可重建派生数据（G4）
- 工程属性：配置外置、契约先行、版本化对象、边界诚实标注（§3）

> 讲解口径：以上属于**骨架级 / 演示级已落地**，可讲「链路跑通、约束渗透」，但**不能讲**「压测定型、召回准确率达标、企业级安全合规」。

### 4.2 可讲「设计预留」的（接口/开关/文档在，逻辑未落地）

- 片段/段落级 ACL：`FINE_GRAINED_ACL_ENABLED=False` + `chunks.acl` + `evaluate_fine_grained()`（A7）
- ABAC 条件策略：`acl_grants.conditions` JSONB + GIN（A4）
- 物理隔离多租户：独立 Schema / 库 + 连接池路由（A6）
- 可插拔 ParserProvider 与中文分词（B4）；多格式/OCR/表格（B3 部分）
- Rerank 重排插件（`backend/` 骨架侧仍是 P0 设计；`webapp/` 已落地三档）（C2）
- 工具注册表 / 节点编排 / `Orchestrator` 协议（D6、D8）
- OTel / LangSmith 等价 trace-span（E7）；评测 CI 闸门（E5，有离线 health 闸门）
- 按租户配额 / 全局并发闸（`redis_client.acquire_slot` 预留）（F1）
- 缓存 key 带身份 + `config_snapshot_hash`（F2）
- 模型级三道闸（F5 的模型级部分）、PII 入库/召回/输出同规则
- 备份容灾「只设计未演练」、RabbitMQ/DLQ 的 v0.2 方案（G3、G5）
- Prometheus + 告警 / SLO（G2）

> 讲解口径：这些是**「留了扩展点、能讲清为什么现在不做、什么条件下才做」**，最能体现判断力；但必须说「架构预留 / 只设计」，**不能说成已落地**。

### 4.3 必须承认「未做」的（代码与设计都没有，或文档明确不做）

- SSO / OIDC / SAML / SCIM 组织同步（A2）
- 源侧变更捕获 / 近实时增量索引（B5，仅手动 `reindex` + 每日对账）
- 文档审批 / 发布审核工作流（B7）
- 数据生命周期 / 保留 / 冷归档 / 中间产物清理（B8）
- 查询理解 / 改写 / 多跳子查询（C6）
- MCP 工具网关（D7）、多 Agent 分流（D8 的分流部分）
- 真实 RAGAS / MRR / NDCG / faithfulness 质量基线（E4）
- CI 回归闸门接入（E5 的 CI 部分）、20+ 条越权用例集（E8）
- Token 计量 / 预算告警 / 按租户计量（F1 的计量与预算部分）
- 合规留存 / 脱敏 / 合规导出 / 异常告警（F4）
- 密钥轮换 / 依赖漏洞扫描 / 合规框架逐条映射（F6）
- 监控告警大盘 / Prometheus / SLO（G2）
- 压测报告（P95 首 token / 整答 / 错误率）、全量回归、灰度与回滚脚本（G6）
- 主备切换实测、拔电演练、备份校验（G3 的演练部分）

> 讲解口径：`README.md` §0 已定性「这是架构验证版，不是上线版」；以上属于**团队质量工作或上线交付范畴**，一个人加班替代不了。主动列出来比含糊带过更有说服力。

---

## 5. 建议纳入实现范围的 Top 10（按优先级顺序）

> 选取原则：① 能补上最贵的证据缺口（安全、检索质量、回归）；② 在「骨架 + 工程实现版」范围内**可独立实现、可 A/B、可回归**；③ 不把「压测 / 容灾演练 / 全量回归」这类团队质量工作塞进骨架。
> 与 §2 表中 `是（Top10）` 的 10 行一一对应。

| 序 | 能力项（对应 §2 行） | 优先级 | 为什么（理由） | 验收口径 |
|---|---|---|---|---|
| 1 | 越权 / 安全用例集与权限回归（E8） | P0 | 项目是 `tenant_id` **代码约束**的逻辑隔离，「一条漏写查询就是越权」；这是最便宜、最高价值的安全证据，也是 `README.md` §7 明确列的 P0 第一项 | 扩 `tests/test_permission.py` 到 20+ 条：跨租户、跨部门、敏感度、显式 deny、缓存串租户、任务跨租户；全部拦截并纳入 CI |
| 2 | 中文分词 + 可插拔 ParserProvider（B4） | P0 | 「质量源头决定上限」；中文不分词时 `to_tsvector` 基本失效，pypdf 覆盖不了扫描件/表格。抽象成 provider 后换解析器不动 chunks 表、Milvus schema 与引文契约 | `ParserProvider` 接口 + zhparser 类分词配置；配一份解析质量评估集；触发器改一处、业务代码不动、召回可量化 |
| 3 | RRF 后可降级 Rerank 重排插件（C2） | P0 | `webapp/` 已用 `bge-reranker-v2-m3` 实测区分度（0.9686 vs 0.0168），把已验证能力**回灌 `backend/` 骨架**即可；双塔召回便宜、交叉编码器准但贵，必须带降级开关护住 P95 | RRF 融合后加一层可开关重排；不可用时 `skipped` 留痕不假装用了；能讲清延迟预算与开关策略；A/B 有评估集证据 |
| 4 | 真实 Embedding + RAGAS 质量基线（E4） | P0 | 当前 `eval/` 全占位、`comparable=false`、`NOT_COMPARABLE`，`README.md` §0 明说「不能讲 MRR/NDCG 基线」；不接真服务就永远没有可比结论 | 接真实 embedding 后产出 faithfulness / context_precision / context_recall / answer_relevancy + MRR / NDCG；冻结一份可比基线，后续才给数值结论 |
| 5 | 评测 CI 回归闸门（E5） | P1 | 「改了配置怎么保证没退化」必须有答案；`eval/` 已有 L0–L3 与 `compare_runs.py`，缺的只是接 CI | L0–L3 接入 CI、L4/L5 审批后跑；`comparable=false` / 占位结果绝不参与闸门；变体只在内存、采纳后升对象版本 |
| 6 | 可观测 trace / span（E7） | P0 | `X-Trace-ID` 已贯通但只是「能复现」，不是「能定位瓶颈」；OTel / LangSmith 等价是 `next-version...md` 的 P0 插件 | trace_id 下钻到逐 span 与 `object_refs`；能展示一条「查询→召回→重排→生成→引用」完整 trace；说清托管 vs 自托管的数据出域取舍 |
| 7 | Token 计量 / 预算 / 配额 / 限流（F1） | P1 | OWASP LLM10「无界消费」直接对应；项目只有按 IP 令牌桶与 429 退避演示，**没有计量、没有预算、没有全局并发闸** | 按请求/用户/租户记录 token 用量；按租户分级配额 + 全局并发闸；429 `rate_limit_exceeded` 退避重试、`quota_exceeded` 不重试；有预算告警 |
| 8 | 缓存与对象版本联动的失效治理（F2） | P1 | 「缓存串租户是最隐蔽的越权」；当前 `webapp/` `cache_key` 只带 provider+模型名，未带身份 | 缓存 key 带 `tenant/user/role` + `config_snapshot_hash`；版本化对象变更自动失效；能讲一个真实越权案例与修复 |
| 9 | 文档审批 / 发布审核工作流（B7） | P1 | 中型公司知识库的普遍要求：内容先审后发；当前文档状态机只有上传/解析/索引态，没有「审批」这一环 | 状态机含 draft→review→published；未发布文档不进检索；审批动作入审计、可回放、可回滚 |
| 10 | SSO / OIDC 可插拔身份 Provider（A2） | P1 | 认证与组织架构是调研里最一致的要求（SSO / SCIM / 细粒度 RBAC）；项目目前只有本地账号 | 抽出 `AuthProvider` seam + 一个 OIDC 验证切片，能演示「IdP 登录 → 映射本地角色 → 走同一个 `decide()`」；不承诺完整联邦 |

**刻意不进 Top 10 的（列为「条件」而非「是」）**：G2 监控告警 / SLO、G3 备份容灾演练、G6 压测与全量回归、A6 物理隔离租户、F4 合规留存/脱敏、F6 合规框架逐条映射。理由：`README.md` §0 已把它们定性为「团队的质量工作，不是一个人加班能替代的」；在 A/B 档骨架里做只会分散证据、且容易把「只设计」说成「已落地」。D7 MCP 网关 / D8 多 Agent 分流列为 P2「明确不做或低性价比」，讲清边界即可，不做假入口。

---

## 附：本清单的「诚实边界」自检

- 全表 50 条中，`已实现` 项均为 `README.md` §4 / `operations.md` / `webapp/README.md` 明确有代码或自测支撑者；`docs/next-version...md` 的插件清单、`docs/operations.md` 的「只设计 / 未做」、`docs/permission-model.md` 的「架构预留」**没有一条被标成 `已实现`**。
- `webapp/` 的能力一律标 `演示级` 或在状态里注明「webapp 已落地 / backend 架构预留」，避免把 SQLite + 进程内队列的替身说成生产骨架。
- `eval/` 占位结果 `comparable=false` / `NOT_COMPARABLE` 与 `evalbench/` 真实 API 实测**是两套东西**：前者是冻结的占位评测体系，后者是演示切片的工程实测记录，都不等于质量基线。
- 外部未查到确切结论的（量化门槛、合规条款逐条映射、通用配额数值、2026 后规范时间点）统一写「未核实」，未编造数字。
