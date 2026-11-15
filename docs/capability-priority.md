# 中型公司知识库能力优先级清单

> **文档定位**：企业内部知识库「开发框架骨架 + 可运行演示切片」，面向中小企业 AI Agent 工程师 / FDE 岗位面试，**不是上线交付版**。系统口径为**单组织、多用户、多部门**；多租户仅作为未来研发方向（见 §5），本轮不实现。
> **底数**：`docs/kb-capability-checklist.md` §2 的 **50 条能力对照**（7 个能力域 A–G），按用户要求**重新按高 / 中 / 低三级标注优先级**；原清单的 P0/P1/P2 是「中型公司固有重要性」，本表的优先级是「本项目本期纳入与否」，两者口径不同，不互相覆盖。
> **本文只做优先级与范围定义，不改源码**。所有「涉及文件路径」是事实引用，不是本次改动；本轮新增文件只有 `docs/capability-priority.md` 与 `docs/plugin-selection.md`。
> **冻结边界**：不动 `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`；`eval/` 的 `__TBC__` / `NOT_COMPARABLE` / `real_api` 契约门禁保持阻断。验收命令全部离线或走桩，**不运行真实外部 LLM 调用**。

---

## 0. 口径

### 0.1 三级优先级（与三组范围一一对应）

| 优先级 | 含义 | 对应分组 |
|---|---|---|
| **高** | 本期可落地（演示级）或本期架构改造必需：已有 seam 可复用、能单测、能讲清边界 | 本期可落地（演示级） |
| **中** | 下期工程加固：有价值但需补基础设施或评估集，本期做会分散主线 | 下期（工程加固） |
| **低** | A/B 档上线级、明确不做、或未来研发方向；留在本期之外 | A/B 档上线级（本期之外） |

### 0.2 三态现状（原清单四态的映射规则）

| 本表状态 | 判定 | 原清单对应 |
|---|---|---|
| `已实现` | 有可运行代码 / 表结构 / 接口支撑，注明档次（骨架最小集 / 规则级 / 演示级） | `已实现`、`演示级` |
| `只设计` | 有接口 / 开关 / 文档 / 注册表条目，逻辑未落地 | `架构预留` |
| `未做` | 代码与设计里都没有，或文档明确标注未做 | `未做` |

「已实现」**不等于上线级**；`webapp/` 一律注明「演示级（SQLite + 进程内队列 + 组件替身）」。

### 0.3 事实源与来源

- 底数与落点：`docs/kb-capability-checklist.md` §2 大表、§4 三组差距归纳、§5 Top10 建议、§0 状态四档定义。
- 规则与验收：`docs/enterprise-readiness.md` §6 插件清单 / §8 V1–V14 验收标准、`docs/next-version-messaging-and-plugins.md` §5 P0–P2 清单、`docs/org-model-change-plan.md`（A13 七条断言、D1–D6 裁决、A/B 档边界）。
- 现状待补充项：`docs/CHANGELOG.md` §6（5 项待补充 + 4 项已确认未落地）。
- 外部来源**只引用** `docs/kb-capability-checklist.md` §1.1 已核实的 6 个：Microsoft Learn《Retrieval Augmented Generation overview》、Microsoft Learn《Azure AI Search 安全最佳实践》、MCP 规范 2025-11-25、OWASP 中国《OWASP 大语言模型应用程序十大风险 2025》、Ragas evaluation 文档、NIST AI 600-1《AI RMF: Generative AI Profile》。**本文不新增、不改写任何 URL**；§1.3 明确「未核实」的量化门槛（QPS / SLA / RTO / 合规条款逐条映射）继续不作为验收依据。

---

## 1. 分组结论速览

| 分组 | 条数 | 代表能力 |
|---|---|---|
| 本期可落地（演示级）· 高 | **24** | A3 RBAC + 部门树、C3 双重权限过滤、E8 越权用例集、F1 计量 / 预算 / 限流、F2 缓存治理、G4 降级幂等对账 |
| 下期（工程加固）· 中 | **11** | B4 中文分词 + ParserProvider、E5 评测 CI 闸门、E7 trace→span、B7 审批流、G5 DLQ |
| A/B 档上线级（本期之外）· 低 | **15** | A2 SSO/OIDC、A5/A6 多租户、A7 逐段落 ACL、E4 质量基线、G2 监控告警 SLO、G3 容灾演练、G6 压测回滚 |

> **范围纪律（用户原话）**：「列出尚未完成不等于本次要补完，A/B 档上线级事项留在本期之外」。`README.md` §0 已把压测、容灾演练、监控告警、合规留存定性为「团队质量工作，不是一个人加班能替代的」；在 A/B 档骨架里做只会分散证据，且容易把「只设计」说成「已落地」。

---

## 2. 分域能力清单（50 条）

理由列格式：`业务` ／ `面试` ／ `依赖前置`。

### A · 认证与组织架构（7 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| A1 | 本地账号、口令与 JWT（签名 / 过期 / 刷新轮换 / 单点登出） | 高 | 已实现 | `backend/app/core/security.py`、`backend/app/api/v1/auth.py`、`webapp/` pbkdf2 + HMAC JWT | 业务：统一身份入口，一切权限的前提／面试：口令不可逆存储与令牌失效／依赖：无 | 本期纳入回归；去 tenant 后 `core/deps.py` 从 JWT 只取 `sub`、查 `User` 表实时构造 `Principal`，`role` 与 `department_id` **不写入 JWT** |
| A2 | SSO / OIDC / SAML / SCIM 组织同步 | 低 | 未做 | 无；`docs/operations.md` 标注「回调验签未做」 | 业务：对接企业 IdP／面试：可插拔 AuthProvider seam／依赖：外部 IdP 与回调验签 | **不纳入**（A/B 档之外点名）：需外部身份提供方，骨架阶段无 IdP 可演示，做半套会把「只设计」说成已落地 |
| A3 | RBAC 角色模型与部门树 | 高 | 已实现 | `backend/app/services/permission.py::decide()`、`docs/permission-model.md`、`backend/app/models/identity.py` | 业务：单组织多部门的核心可见范围来源／面试：纯函数 8 步判定可单测、物化路径子树／依赖：无，backend 已完整实现 | 本期纳入：角色 5→4（`ADMIN`/`dept_admin`/`dept_user`/`viewer`）、`Visibility` 两值 `dept｜organization`、删 `decide()` 的 tenant 分支；**不重建部门模型**（本次最大范围节省） |
| A4 | ABAC / 属性与条件策略（含 OPA 类） | 中 | 只设计 | `acl_grants.conditions` JSONB + GIN；`decide()` 未接完整条件引擎 | 业务：按敏感度 / 属性动态授权／面试：条件策略可解释可回放／依赖：条件表达式引擎 | 不纳入：下期工程加固；本期保持开关预留，且任何条件策略不得绕过 `decide()` 单一入口 |
| A5 | 多租户逻辑隔离（`tenant_id` 行级） | 低 | 已实现 → **本次移除（留痕）** | 原 `backend/app/db/TenantRepository`；改造后 `ScopedRepository(db, principal)` | 业务：单组织无跨组织边界／面试：可作为「为什么删掉一层边界」的谈资／依赖：多组织计费与合规 | **不纳入**：整体移除 `tenants` 表与 `tenant_id` 列（不留占位）；`kb-capability-checklist.md` A5 标注改「已实现 → 本次移除」并留痕，见 §5 |
| A6 | 多租户物理隔离（独立 Schema / 库 + 路由） | 低 | 只设计 | `docs/permission-model.md` §5「本骨架未实现」 | 业务：强合规客户才需要／面试：连接池按租户路由的代价／依赖：多套库与路由层 | **不纳入**：无组织间隔离需求，「连接池按租户路由」验收口径一并作废，转 §5 未来方向 |
| A7 | 片段 / 段落级 ACL（细粒度授权） | 低 | 只设计 | `chunks.acl`、`evaluate_fine_grained()`、`FINE_GRAINED_ACL_ENABLED=False` | 业务：同文档内按段落白名单／面试：三步走升粒度不改表／依赖：写入侧 `Chunk.acl` 填充 | **不纳入**（A/B 档之外点名）：本期保持开关关闭；打开需改写入侧与 `filter_candidates()` 的 chunk 级二次校验，会破坏红线③ |

### B · 文档接入（8 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| B1 | 分片上传 / 断点续传 / 秒传 / 批量聚合 | 高 | 已实现 | `docs/async-upload.md`、`backend/app/api/v1/uploads.py`、`webapp/` 排队演示 | 业务：大文件不进内存／面试：8MiB 恒定请求、刷新进度不归零／依赖：PG 台账 | 本期纳入回归；进度 key 改静态 `ORG_NAMESPACE` 时须与端点 `decide()` 订阅校验同批发布（`org-model-change-plan.md` 一票否决门禁） |
| B2 | 多格式解析（PDF / Office / HTML / MD） | 中 | 已实现（演示级） | `backend/app/services/parser.py`（pypdf）；`webapp/` 仅纯文本 | 业务：办公格式统一转文本／面试：换格式不动 chunks 表与引文契约／依赖：ParserProvider 抽象 | 不纳入：下期随 ParserProvider 一并；本期只保持 pypdf 骨架与纯文本切片 |
| B3 | 扫描件 OCR / 表格 / 双栏 / 多模态解析 | 低 | 未做 | `README.md` §8 已知限制 | 业务：复杂版式可检索／面试：解析质量评估集／依赖：OCR 服务与评估集 | **不纳入**：`README.md` §8 明确未做；需外部模型与评估集，属 A/B 档 |
| B4 | 中文分词 + 可插拔 ParserProvider | 中 | 只设计 | `README.md` §1.3、`docs/next-version-messaging-and-plugins.md` P0、`backend/app/services/parser.py` | 业务：中文不分词时 `to_tsvector` 基本失效／面试：质量源头决定上限／依赖：zhparser 类配置 + 解析质量评估集 | 不纳入：下期工程加固；现 `webapp/rag.py` 用中文相邻二元组近似，须标注「非真实分词」 |
| B5 | 增量索引 / 源侧变更捕获 / 近实时 | 中 | 未做 | `/documents/{id}/reindex` 手动全量、每日 `reconcile_vectors` 对账 | 业务：源变更近实时可检索／面试：按 `chunk_id` 幂等 upsert／依赖：变更捕获链路 | 不纳入：下期；本期保持手动 reindex + 每日对账，不宣称近实时 |
| B6 | 文档版本 / 快照 / 行级 diff / 追加式回滚 | 高 | 已实现 | `backend/app/services/version.py`、`README.md` §4 版本端点 | 业务：编辑可对比可回滚／面试：追加式不覆盖／依赖：无 | 本期纳入回归；不改契约 |
| B7 | 文档审批 / 发布审核工作流 | 中 | 未做 | 无（文档状态机只有上传 / 解析 / 索引态） | 业务：内容先审后发／面试：未发布文档不进检索／依赖：审批状态机 + 审计动作 | 不纳入：下期（Top10 #9）；本期状态机不加审批态，避免把「只设计」写成已落地 |
| B8 | 数据生命周期 / 保留 / 归档 / 中间产物清理 | 低 | 未做 | `docs/async-upload.md` §8、`docs/operations.md` §5 | 业务：临时产物可回收／面试：审计按月分区 + 冷归档／依赖：对象存储生命周期策略 | **不纳入**：属 A/B 档合规留存范畴；本期仅 `Department` 新增 `status` / `archived_at`（停用 / 归档替代物理删除） |

### C · 检索（6 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| C1 | 混合检索（词法 + 向量 + RRF 融合） | 高 | 已实现 | `backend/app/services/search.py`、`webapp/rag.py::retrieve()` | 业务：双路召回互补／面试：RRF 融合与可分别关闭／依赖：向量层 | 本期纳入回归；stub embedding 时必须标注「非语义」，**真实 Milvus 集群不纳入**（A/B 档之外点名） |
| C2 | 重排 Rerank（cross-encoder，可降级） | 高 | 已实现（演示级） | `webapp/providers.py::rerank()` / `rerank_with_llm()`、`webapp/rag.py::retrieve()`、`webapp/settings.py` | 业务：召回后精排提升排序／面试：双塔 vs 交叉编码器成本曲线／依赖：可选外部 key | 本期纳入回归（离线桩验收）：三档 `off/llm/external`、不可用时 `skipped` 留痕不假装用了；backend 骨架侧仍是 P0 设计，不冒充已落地 |
| C3 | 检索前 / 召回后双重权限过滤 | 高 | 已实现 | `docs/permission-model.md` §1、`backend/app/integrations/milvus_client.py::build_permission_expr()`、`backend/app/services/permission.py::filter_candidates()` | 业务：红线「先按权限过滤，再重排、再进模型」／面试：一次颠倒即越权／依赖：`visible_department_ids()` 纯拓扑查询 | **本期核心**：expr 首条件换 `dept_id in [...]` + `is_deleted`（二者不可被 `extra` 覆盖），显式授权 / owner / 角色 / 敏感度全由 `decide()` 逐条判定；列表侧补齐 `decide()` 复核（A13 第 1–7 条断言） |
| C4 | 引用溯源（页码 / 分块 / 得分 / 原文展开） | 高 | 已实现 | `backend` 带引用生成、`webapp/routers_chat.py::chat_sources` + `/api/chat/sources` | 业务：答案可核查／面试：怎么证明答案不是编的／依赖：检索元数据 | 本期纳入回归；逐句 grounding 高亮**不纳入**（`docs/CHANGELOG.md` §6 已确认未落地） |
| C5 | 元数据与分类标签过滤 | 高 | 已实现 | `backend` `categories/tree`、`tags`、`attributes` JSONB + GIN | 业务：按项目 / 时间 / 分类筛选／面试：元数据过滤与权限过滤叠加不冲突／依赖：无 | 本期纳入回归；确认元数据过滤不与部门候选集过滤互相放宽 |
| C6 | 查询理解 / 改写 / 多跳子查询 | 低 | 未做 | 无（`webapp/rag.py` 仅中文二元组词法近似） | 业务：复杂问句拆解／面试：改写结果可解释／依赖：多轮会话与子查询编排 | **不纳入**：原清单标「明确不做或低性价比」，本期不新增入口 |

### D · 生成与 Agent（8 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| D1 | SSE 流式输出 | 高 | 已实现 | `backend` `/search/answer`(SSE)、`webapp/` fetch stream | 业务：首 token 可见／面试：不缓冲、`proxy_buffering off`／依赖：无 | 本期纳入回归；不改契约 |
| D2 | 断线续传（`resume_from` / `Last-Event-ID`） | 高 | 已实现 | `webapp/` `resume_from`、`docs/async-upload.md` | 业务：重连不丢不重／面试：聊天日志按 `conversation_id` 幂等只写一次／依赖：无 | 本期纳入回归 |
| D3 | 多轮对话 / 会话记忆 | 中 | 已实现（演示级） | `webapp/` `/conversations`、`/history`、会话状态面板 | 业务：上下文连续／面试：无记忆策略的越界／依赖：上下文压缩策略 | 不纳入：下期；本期无对话压缩 / 记忆策略，须如实标注 |
| D4 | 检索为空拒答 / 不编造 | 高 | 已实现 | `webapp/` `degraded=true` 拒答话术、`eval/` generation 套件含拒答 | 业务：无命中明确说没找到／面试：降级文案不与真答案拼接／依赖：无 | 本期纳入回归（红线级） |
| D5 | Provider 适配器 / 多模型路由 / 故障切换 | 高 | 已实现 | `webapp/providers.py` seam、`webapp/settings.py`、`backend` 双 Provider | 业务：可切多家互为备份／面试：换模型不动检索 / 权限 / 评测逻辑／依赖：`.env` 密钥 | 本期纳入回归；密钥不落库、不下发前端、不进日志；验收不实跑真实外部 LLM |
| D6 | 工具调用 / Function calling / 工具注册表 | 中 | 只设计 | `backend/app/agent/ToolRegistry`、`NodeRegistry`、`Orchestrator` 协议 | 业务：从问答演进到工具 Agent／面试：工具调用仍走同一个 `decide()`／依赖：白名单与显式同意 | 不纳入：下期；本期只保留状态机与注册表接口，绝不开放自由 SQL 与任意 HTTP |
| D7 | MCP 工具网关 | 低 | 未做 | `README.md` §0「不能讲：MCP 网关已落地」、`next-version...md` P2 | 业务：标准化接内外服务／面试：工具描述视为不可信输入／依赖：白名单网关 + 用户显式同意 | **不纳入**：原清单标「明确不做或低性价比」，不做假入口 |
| D8 | 多 Agent 分流 / 编排框架（LangGraph 类） | 低 | 只设计 | `LinearRAGOrchestrator`、`Orchestrator` 协议 | 业务：按意图分流／面试：换编排层不动鉴权 / 审计 / 工具层／依赖：编排框架 | **不纳入**：线性节点编排够讲边界；分流部分原清单标「明确不做或低性价比」 |

### E · 质量工程（8 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| E1 | 评测集 / golden QA 数据集（版本化） | 高 | 已实现 | `eval/config/registry.json`（14 对象）、`eval/datasets/cases_v0.1.0.jsonl` | 业务：回归有基准／面试：改对象必升版本、禁原地改／依赖：无 | 本期纳入**冻结边界**：`eval/` 零字节改动，`git diff --exit-code -- eval/ demo/` 为空 |
| E2 | 离线占位评测链路 / 确定性复现 | 高 | 已实现 | `eval/run_eval.py`、`eval/core/ids.py` | 业务：零密钥可跑可复现／面试：`run_id` 由 profile + seed + 各类 hash 确定性生成／依赖：无 | 本期纳入回归：14 OK；**质量基线冻结不纳入**（A/B 档之外点名，`real_api` 契约门禁保持阻断） |
| E3 | 真实 API 评估工作台 / 多提示词多模型对比 | 高 | 已实现（演示级） | `evalbench/`、`docs/evaluation-report.md`、`webapp/out/eval_report.json` | 业务：真实模型下可复跑／面试：逐轮采纳 / 否决有对比证据／依赖：外部 API（历史运行已留存） | 本期纳入回归：16 OK；报告须持续标注「工程实测记录，不是质量基线」，本轮不重跑真实调用 |
| E4 | 标准质量指标（RAGAS / MRR / NDCG / faithfulness） | 低 | 未做 | `eval/core/metrics.py` 仅占位；`README.md` §0「不能讲：MRR/NDCG 基线」 | 业务：召回与生成分层度量／面试：冻结可比阈值／依赖：真实 embedding + 冻结基线 | **不纳入**（A/B 档之外点名）：`eval/` 仍 `comparable=false` / `NOT_COMPARABLE`，`real_api` 被 26 个 blocking `__TBC__` 阻断，禁止解冻 |
| E5 | 回归闸门 / CI 质量门禁 | 中 | 只设计 | `eval/` 离线 health 闸门、`eval/compare_runs.py::compare()`、`next-version...md` P1 | 业务：每次改动可验证没退化／面试：占位结果不参闸／依赖：CI 接入 + 阈值冻结 | 不纳入：下期工程加固；本期保持数据侧与 `contract_gate()` 现状，闸门接 CI 未做 |
| E6 | Badcase 采集 / 筛选 / 导出 / 完成度 | 高 | 已实现 | `webapp/` `badcases` 表、`/api/badcases`、admin 完成度看板 | 业务：采集—保存—查看—导出闭环／面试：导出与列表共用筛选／依赖：无 | 本期纳入回归；「采集与查看权限分离」约定保持，完成率钳制在 `[0,1]` |
| E7 | 可观测（trace_id / span / 指标 / SLO） | 中 | 只设计 | `X-Trace-ID` 贯通（已实现部分）、`backend/app/agent/orchestrator.py::state.trace()`、`webapp/observability.py::log_event()` | 业务：从「看日志」到「看链路」／面试：一次 RAG 慢在哪一段／依赖：OTel / Langfuse 等价 | 不纳入：下期工程加固；本期只有事件级日志与 run 追踪，**无逐 span 与 `object_refs`**，文档须标「只设计」 |
| E8 | 越权 / 安全用例集与权限回归 | 高 | 未做 → 本期补齐 | `backend/tests/test_permission.py`（现覆盖 7 类）、`README.md` §7「20+ 条越权用例」、`docs/org-model-change-plan.md` A13 | 业务：删 tenant 后过滤首条件变动态超集，最便宜最高价值的安全证据／面试：先过滤还是检索后过滤／依赖：`decide()` 单一真值 | **本期核心**：扩到 20+ 条部门口径用例（跨部门 / 子树边界 / 显式 deny / owner / 缓存串部门 / 任务跨部门 / `department_id` 伪造 / 列表侧 `decide()` 复核负例）；明确剔除跨租户类与「分区层硬隔离」断言（该机制原本不存在） |

### F · 成本与治理（7 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| F1 | Token 计量 / 预算 / 配额 / 限流 | 高 | 已实现（演示级）／backend 侧只设计 | `webapp/observability.py::cost_of()` / `check_budget()` / `rate_check()`、`webapp/db.py::usage_events` / `budgets`、`backend/app/middleware/rate_limit.py` | 业务：OWASP LLM10 无界消费／面试：按部门核算 vs 按部门隔离配额／依赖：真实供应商 usage | 本期纳入：维度 tenant → `department`，语义为**核算成本 / 归因**（不是准入隔离配额）；按事件发生时快照 `department_id`、不随转岗回溯重写；backend 分级配额与全局并发闸**不纳入** |
| F2 | 缓存治理（key 带身份与配置快照） | 高 | 已实现（向量层）／只设计（授权快照） | `webapp/settings.py::effective_embedding()`、`webapp/rag.py::_get_or_compute_embeddings()`、`webapp/db.py::chunk_embeddings`、`next-version...md` P1 | 业务：缓存串部门是最隐蔽的越权／面试：换 embedding 模型后旧向量怎么办／依赖：授权快照与失效策略 | **本期核心**：key 改「静态组织命名空间 + `principal` 授权快照（含授权版本）」，转岗 / 授权变更后旧 key 不命中；答案级语义缓存不纳入 |
| F3 | 审计日志（只追加 / 可回放） | 高 | 已实现 | `backend/app/services/audit.py::record()`、`/audit-logs`、`webapp/` 演示级台账 | 业务：登录 / 上传 / 检索 / 导出 / 拦截全留痕／面试：权限拦截（denied）也留痕／依赖：无 | 本期纳入回归；审计只存 `query_hash`（16 位摘要）不存提问原文；部门归档后历史 `department_id` 保持可解析 |
| F4 | 合规：留存 / 脱敏 / 合规导出 / 异常告警 | 低 | 未做 | `docs/operations.md` §2 明确「未做」 | 业务：合规红线／面试：审计按月分区 + 冷归档 + 只读账号／依赖：留存策略与脱敏检索 | **不纳入**（A/B 档）：`docs/operations.md` 标 C 档；本期只保持规则级 PII 输出闸，不做审计脱敏检索 |
| F5 | 内容安全（注入 / PII / 输出过滤三道闸） | 高 | 已实现（规则级骨架）／只设计（模型级） | `backend/app/agent/orchestrator.py::INJECTION_PATTERNS` / `PII_PATTERNS` / `guard_node()` / `verify_node()` | 业务：OWASP LLM01 / LLM02／面试：企业知识库合规红线／依赖：模型级检测链路 | 本期纳入回归（规则级）；模型级三道闸**不纳入**（`next-version...md` §5 明确后排），`webapp` 演示切片未接入须如实标注 |
| F6 | 密钥轮换 / 合规框架映射 | 低 | 未做 | `docs/operations.md` §2「未做」 | 业务：密钥治理／面试：NIST / ISO / EU AI Act 谈资／依赖：轮换机制与依赖扫描 | **不纳入**：`kb-capability-checklist.md` §1.3 已把合规条款逐条映射标「未核实」，不作验收依据 |
| F7 | 数据出域 / 模型供应商治理 | 中 | 未做 | `webapp/README.md`（两家外部供应商互为备份） | 业务：托管 vs 自托管的出域取舍／面试：密钥不落库不下发前端不进日志／依赖：供应商协议 | 不纳入：下期；本期只保持 `.env` + gitignore 的密钥纪律，不写供应商治理结论 |

### G · 运维（6 条）

| 编号 | 能力名 | 优先级 | 现状 | 涉及文件路径 | 理由（业务／面试／依赖） | 本期范围与不纳入理由 |
|---|---|---|---|---|---|---|
| G1 | 健康检查（liveness / readiness 分离） | 高 | 已实现 | `/health/live`、`/health/ready`、`docs/operations.md` §3 | 业务：依赖未就绪摘流量但不重启实例／面试：Milvus 挂了语义降级为关键词／依赖：无 | 本期纳入回归；不改契约 |
| G2 | 监控告警 / Prometheus / SLO | 低 | 未做 | `next-version...md` P1、`docs/operations.md` §3「未做」 | 业务：三项 SLO（首 token / 整答 / 错误率）／面试：先定口径再接告警／依赖：指标采集 + 告警通道 | **不纳入**（A/B 档之外点名）：无压测就无阈值，接了告警也是空壳 |
| G3 | 备份容灾 / 主备切换 / 演练（RTO / RPO） | 低 | 只设计 | `docs/operations.md` §3「只设计（未演练）」 | 业务：备份可校验、切换有实测／面试：一次主备 + 一次回滚演练／依赖：演练环境 | **不纳入**（A/B 档之外点名）：`README.md` §0 定性为团队质量工作 |
| G4 | 降级 / 幂等 / 跨库对账 | 高 | 已实现 | `acks_late`、`chunk_id=uuid5`、每日 `reconcile_vectors`、`docs/async-upload.md` §6 | 业务：向量是可重建派生数据／面试：PG 文本先落库再写 Milvus／依赖：无 | 本期纳入回归：PG 为状态真值、Redis 只做实时通道；所有降级「留痕 + 可见」，绝不静默失败 |
| G5 | 任务可靠性（DLQ / ack 落库后 / 故障域分离） | 中 | 只设计 | `backend/app/tasks/celery_app.py`、`next-version...md` v0.2 | 业务：任务不可丢／面试：ack 在业务落库之后／依赖：RabbitMQ / DLQ 演练 | 不纳入：下期；`next-version...md` §3 的迁移触发条件（任务不可丢 / 时长逼近可见性超时 / 队列级配额）当前均不满足，A 档保持 Redis broker 不变是有意取舍 |
| G6 | 容量压测 / 全量回归 / 灰度回滚 | 低 | 未做 | `README.md` §0「没有压测、没有全量回归」 | 业务：P95 首 token / 整答 / 错误率有报告／面试：压测报告 + 回滚演练／依赖：压测环境与容量基线 | **不纳入**（A/B 档之外点名）：`docs/CHANGELOG.md` §6 已确认未落地；本期只做命令级回归，不做容量压测 |

---

## 3. 三组范围分组

### 3.1 本期可落地（演示级）· 高（24 条）

`A1` `A3` `B1` `B6` `C1` `C2` `C3` `C4` `C5` `D1` `D2` `D4` `D5` `E1` `E2` `E3` `E6` `E8` `F1` `F2` `F3` `F5` `G1` `G4`

共同特征：已有可运行代码或 seam、能单测 / 能 selftest、能讲清「做了什么 / 没做什么」。其中 `A3` `C3` `E8` `F1` `F2` 是**去 tenant 改造的必需项**，`E1` `E2` `E3` 属**冻结边界**（只回归、不改动）。本期不新建能力，重点是「删一层 tenant → 五个维度词改挂 department → 把 backend 已有能力下沉到演示切片讲通并测住」。

### 3.2 下期（工程加固）· 中（11 条）

`A4` `B2` `B4` `B5` `B7` `D3` `D6` `E5` `E7` `F7` `G5`

共同特征：有价值但需补基础设施（分词配置与评估集、ParserProvider、CI 接入、OTel span、IdP 之外的治理），本期做会分散主线。按 `next-version-messaging-and-plugins.md` §5 的 P0/P1 顺序推进，每项仍须回答「挂哪个版本化对象 / 用什么指标判定 / 怎么回滚」。

### 3.3 A/B 档上线级（留在本期之外）· 低（15 条）

`A2` `A5` `A6` `A7` `B3` `B8` `C6` `D7` `D8` `E4` `F4` `F6` `G2` `G3` `G6`

`README.md` §0 已定性「这是架构验证版，不是上线版」；以上的多数在 `docs/kb-capability-checklist.md` §4.3 被列为「必须承认未做」、`docs/enterprise-readiness.md` §9 列为 C 档团队质量工作。**主动列出来比含糊带过更有说服力**。

---

## 4. A/B 档之外点名清单（至少 8 项）

| # | 点名项 | 对应 50 条编号 | 事实依据 | 本期之外的原因 |
|---|---|---|---|---|
| 1 | 真实压测与容灾演练 | `G6` + `G3` | `README.md` §0「没有压测、没有全量回归」；`docs/operations.md` §3「只设计（未演练）」 | 无压测环境与容量基线，属团队质量工作 |
| 2 | 监控告警 SLO | `G2` | `docs/operations.md` §3「未做」；`next-version...md` P1 | 指标口径未冻结，接告警会是空壳 |
| 3 | SSO / OIDC | `A2` | `docs/operations.md` 标「回调验签未做」，无 OIDC 入口 | 需外部 IdP；骨架阶段做半套会破坏「只设计」标注 |
| 4 | 真实供应商账单校准 | `F1` 的边界（**不在 50 条编号内，单列不新增编号**） | `docs/CHANGELOG.md` §6：`PRICES` 为演示估算值，非报价；`webapp/observability.py::PRICE_NOTE` 固定标注「演示估算」 | 需真实账单与供应商 usage 对账，本期不做；成本结论只能用于相对比较与预算演练 |
| 5 | 屏幕阅读器实测 | **不在 50 条编号内，单列不新增编号** | `docs/CHANGELOG.md` §6：已做 13 组对比度数值计算（均 ≥4.76），未做辅助技术实测；`docs/enterprise-readiness.md` §10.7 | 需辅助技术实测环境，数值计算 ≠ 可访问性验收 |
| 6 | 质量基线冻结（`eval/` 的 `real_api` 契约门禁未消解，保持阻断） | `E4` + `E2` 边界 | `eval/` 仍 `comparable=false` / `NOT_COMPARABLE`；`eval/core/contracts.py` 26 个 blocking `__TBC__`；`eval/tests/test_harness.py::test_contract_gate_blocks_real_api` 断言 `ready_for_real_api is False` | **冻结对象禁止改动**；`real_api` 保持快速失败，占位结果绝不参与闸门 |
| 7 | 逐段落 ACL 全量实现 | `A7` | `FINE_GRAINED_ACL_ENABLED=False`、`chunks.acl` 预留、写入侧未接 | 本期保持开关关闭；打开需改写入侧与 chunk 级二次校验，会破坏红线③与 A13 |
| 8 | 真实 Milvus 集群 | `C1` / `C3` 的边界（**不在 50 条编号内，单列不新增编号**） | `docs/CHANGELOG.md` §6：「逐句 grounding 高亮、Milvus 真实向量库 未做 / 组件替身」；`webapp/` 用 SQLite + 本地向量替身 | 骨架用替身即可演示闭环；真实集群与重建 collection 属上线级 |
| 9 | 内容安全 PII / 审计留存合规 | `F4` | `docs/operations.md` 标 C 档；`docs/CHANGELOG.md` §6 已确认未落地 | 本期只保留规则级输出闸，模型级三道闸与审计脱敏检索不做 |
| 10 | 逐句 grounding 高亮 | `C4` 的边界（**不在 50 条编号内，单列不新增编号**） | `docs/CHANGELOG.md` §6 已确认未落地；`webapp/routers_chat.py` 只到分块级引用 | 需答案逐句归因与前端高亮，本期不做 |

> 第 4、5、8、10 项来自 `docs/CHANGELOG.md` §6 的待补充 / 已确认未落地项，**不在 50 条能力编号内**，为忠实用户的点名要求单列，不新增编号、不改动 50 条底数。

---

## 5. 多租户作为未来研发方向

### 5.1 为什么当前选择单组织多部门

用户目标是中小企业**内部**知识库，组织边界只有一个；多租户会连带引入 `tenant_id` 注入与行级隔离、跨租户角色与 `Visibility` 三值、按租户配额与全局并发闸、缓存 / 对象 / 进度 key 的租户前缀、跨租户越权用例集、物理隔离选项与按租户账单，这一整套复杂度与「代码可读 + 面试讲解」的定位不匹配。

本次变更的实质按 `docs/org-model-change-plan.md` §601 的话术：**不是「把多租户改成多部门」，而是「删掉一层用不上的组织边界，把本来就在代码里的部门树提升为唯一边界」**。backend 已有 `Department`（`parent_id` + 物化路径 `path` + `level`）、`User.department_id`、`ACLGrant`、五角色与 `decide()` 判定，去 tenant 是**减维度 + 改口径**，不是从零新建权限系统。

代价必须如实说明（不夸大）：过滤首条件从「静态、互斥、全库唯一的 `tenant_id`」换成「动态、层级、可变、由 `visible_department_ids()` 计算出的部门 id 列表」，候选集是**超集**，过滤粒度依赖一次应用侧计算；这是**隔离强度下降**，补偿手段只有 `decide()` 最终权威 + A13 双向对拍。`is_partition_key=True` 是按字段值散列的**查询性能机制**（`core/config.py:53` 固定 32 个分区），**原本就不是访问控制**，不得写成「从分区层硬隔离降级」。

### 5.2 未来若做 SaaS 需要补什么

| 层 | 需要补的能力 | 对应本表编号 |
|---|---|---|
| 数据 | `tenants` 表、业务表 `tenant_id` 行级隔离、`TenantRepository` 强制下推、共享 Schema 下的唯一键与撞键预检 | `A5` |
| 强隔离 | 独立 Schema / 独立库 + 连接池按租户路由 | `A6` |
| 身份 | 跨租户角色（`platform_admin` / `tenant_admin` 分层）、`Visibility` 三值、SSO / SCIM 组织同步 | `A3` `A2` |
| 检索 | expr 首条件恢复静态租户键、分区键与 expr 双重约束、跨租户越权用例集 | `C3` `E8` |
| 治理 | 按租户分级配额与全局并发闸、缓存 / 对象 / 进度 key 租户前缀、按租户账单与成本归集 | `F1` `F2` |
| 合规 | 审计留存、脱敏检索、数据出域边界、容量压测与监控告警 | `F4` `F7` `G2` `G6` |

### 5.3 当前哪些接口 / 字段是历史遗留（不夸大）

以下条目在**多租户改造完成前**仍存在于仓库，是**待清理的历史遗留**，不代表系统仍是多租户；反之也不能宣称「多租户能力已完全移除」，以 Phase 2/3 的 A6「tenant 残留清零」验收为准：

| 位置 | 遗留内容 | 处置归属 |
|---|---|---|
| `webapp/observability.py:252` | `CONFIG_KEYS` 含 `tenant` 键 | 任务 #13 维度改造 |
| `webapp/observability.py` `DIMENSIONS` / `rate_check()` / `record_usage()` / `usage_summary()` | `user｜tenant｜endpoint｜model` 四维与 `usage_events.tenant` 字段 | 任务 #13 改挂 department |
| `webapp/tests/test_evaltest.py:53` | 硬断言 `{"user","tenant","endpoint","model"}` | 任务 #13 同步更新断言并计入新基线 |
| `backend/app/core/deps.py:51` | 从 JWT 取 `tenant_id` 构造 `TenantRepository` | Phase 2 后端改造，JWT 只带 `sub` |
| `backend/app/integrations/milvus_client.py:1-4`、`:133`、`:136-139` | docstring 与 `delete` / `list_chunk_ids` 里硬编码 `tenant_id == "..."` | Phase 2 检索链路改造 + A6 清零 |
| `backend/app/integrations/milvus_client.py:36`、`core/config.py:53` | `tenant_id` 为 `is_partition_key=True`、`MILVUS_NUM_PARTITIONS=32` | Phase 2 重建 collection（该字段不可 ALTER） |
| `docs/enterprise-readiness.md` §1 / §3.1 / §4.1 / §6 | 「单租户 `default`」、四维限流演示切片口径、插件「权限与多租户隔离」 | Phase 3 文档三态校准 |
| `docs/kb-capability-checklist.md` A5 / A6 / §4.1 第 3 条 / §5 Top10 #1·#7·#8 | 把「多租户逻辑隔离」标为 `已实现` / 「可直接讲已落地」 | Phase 3 改「已实现 → 本次移除」并留痕 |
| `webapp/routers_evaltest.py` 架构对齐规则集 | 「未建 department 表」、`TenantRepository`、四维枚举 | Phase 3 校准（backend 部门树**已实现**，该表述属②类失真） |
| `docs/CHANGELOG.md` §2 首日条目 | 「四条核心约束（tenant 注入…）」 | **历史日志不回改**，只在 §7 新增一条修订记录说明口径演进 |

### 5.4 不夸大声明

本文不写「已支持多租户」，也不写「tenant 已清零」；两类表述都以实际代码与 Phase 3 的 `git grep` 清零验收为准。多租户在本项目中的定位只有一个：**未来研发方向的文档条目与面试谈资**，不构成本期功能、不进入验收、不写入「已实现」。

---

## 6. 本期纳入范围的可量化验收

| 验收组 | 命令 | 判定 |
|---|---|---|
| 演示切片单元测试 | `python -m pytest webapp/tests -q` | 全绿；维度改造后断言与计数变化，按「全绿 + 新计数写入 `CHANGELOG.md`」判定，不要求仍等于 51 |
| 演示切片端到端 | `python webapp/selftest.py` | 33/33 `DEMO_READY` |
| 生产骨架 | `cd backend && python -m pytest -q tests` | 全绿（基线 16 passed）；新增 20+ 条部门口径越权用例后计数一并冻结 |
| 评测 harness | `python -m unittest discover -s eval/tests -t .` | 14 OK；`test_contract_gate_blocks_real_api` 断言 `ready_for_real_api is False` |
| 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` | 16 OK |
| 契约门禁 | `python eval/run_eval.py --check-contracts` | 列出 26 个 blocking `__TBC__` 项，`real_api` 保持阻断 |
| 冻结边界 | `git diff --exit-code -- eval/ demo/` | 输出为空 |
| 演示物料 | `python demo/selftest.py` | 16/16，`run_id=RUN-095a2a4a40c6` 与 `config_snapshot_hash` 不变 |
| tenant 残留 | 五组 `git grep`（见 `docs/org-model-change-plan.md` §5.11） | 排除区外零命中；排除区 `eval/`、`demo/`、`evalbench/`、`eval/config/`、`docs/evaluation-plan.md` 含 `__TBC__` 的行零字节改动 |
| 越权用例 | `backend/tests/test_permission.py` 扩到 20+ 条 | 100% 拦截；A13 七条断言（含列表 ↔ 检索集合一致性双向对拍）全绿 |

**验收纪律**：①`eval/` 与 `demo/` 零字节改动，`comparable=false` / `NOT_COMPARABLE` 不参与任何闸门；②所有外部模型 / rerank 验证走离线或 monkeypatch 桩，**不运行真实外部 LLM 调用**；③`demo` 的 16/16 与 `run_id` 是**不可变基线**，51 / 33 / 16 / 14 / 16 是**可变基线**。

---

## 7. 改动清单（涉及文件路径与改动范围）

| 文件 | 类型 | 改动范围 |
|---|---|---|
| `docs/capability-priority.md` | **本轮新增** | 全文：口径、50 条重标优先级、三组分组、A/B 档点名、多租户未来方向、验收总表、改动清单 |
| `docs/plugin-selection.md` | **本轮新增** | 全文：12 张插件卡、汇总排序、Top3、面试话术、验收总表 |
| `docs/kb-capability-checklist.md` | 只读引用 | 50 条底数、6 个来源、§4 差距归纳、§5 Top10；**本轮不改**；A5 标注改「已实现 → 本次移除」属 Phase 3 |
| `docs/enterprise-readiness.md`、`docs/next-version-messaging-and-plugins.md`、`docs/org-model-change-plan.md`、`docs/CHANGELOG.md` | 只读引用 | 规则 / 清单 / 总纲 / 待补充项；**本轮不改** |
| `docs/permission-model.md`、`README.md`、`docs/operations.md` | 只读引用 | 状态口径与落点；**本轮不改** |
| `backend/`、`webapp/`、`eval/`、`demo/`、`evalbench/` 全部源码与资源 | **禁止改动** | 本文所有路径均为事实引用，用于证明「已实现 / 只设计 / 未做」有据；列出路径不等于本轮修改 |

---

## 8. 边界与纪律

1. 50 条底数来自 `docs/kb-capability-checklist.md` §2，编号与能力名一一对应，**未新增编号**；第 4、5、8、10 项 A/B 档点名来自 `docs/CHANGELOG.md` §6，单列说明且不混入底数。
2. 每条「已实现」均注明档次（骨架最小集 / 规则级 / 演示级）；`webapp/` 的 SQLite + 进程内队列 + 组件替身不写成生产骨架。
3. 优先级是**本项目本期纳入与否**的判断，与原清单 P0/P1/P2 的「中型公司固有重要性」口径不同，不互相覆盖。
4. 外部量化门槛（QPS / SLA / RTO / 合规条款逐条映射 / 通用配额）沿用 `kb-capability-checklist.md` §1.3 的「未核实」，不作验收依据；本文未新增或改写任何 URL。
5. 严格遵循用户范围纪律：「列出尚未完成不等于本次要补完，A/B 档上线级事项留在本期之外」。
