# 插件三维度选型 · 与项目适配 + 面试高频考察

> **文档定位**：本项目是「开发框架骨架 + 可运行演示切片」，面向中小企业 AI Agent 工程师 / FDE 岗位面试，**不是上线交付版**。系统口径为**单组织、多用户、多部门**；多租户仅作为未来研发方向写入 `docs/capability-priority.md`，本轮不实现。
> **本文只做选型与验收定义，不改源码**。所有「接入模块」是**已实查的事实引用**，不是本次改动；本轮新增文件只有 `docs/plugin-selection.md` 与 `docs/capability-priority.md`。
> **冻结边界**：不动 `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`；`eval/` 的 `__TBC__` / `NOT_COMPARABLE` / `real_api` 契约门禁保持阻断。**全部验收命令走离线或 monkeypatch 桩，不触发真实外部 LLM 调用**。

---

## 0. 口径与事实源

### 0.1 三态落地状态

| 状态 | 判定 |
|---|---|
| `已实现` | 有可运行代码 / 表结构 / 接口支撑；含「骨架最小集」「规则级」「演示替身」，但必须注明档次，不等于上线级 |
| `只设计` | 有接口 / 开关 / 文档 / 注册表条目，但目标能力的逻辑未落地 |
| `未做` | 代码与设计里都没有，或项目文档明确标注未做 |

「已实现（演示级）」指仅 `webapp/` 可运行切片中以 SQLite + 进程内队列 + 组件替身跑通；「已实现（规则级骨架）」指 `backend/` 中有正则 / 节点级实现但未接模型级检测。两者都**不能讲成生产已落地**。

### 0.2 三维度打分口径（各 1–5 分，分数越高越有利）

| 维度 | 5 分含义 | 1 分含义 |
|---|---|---|
| 适配性 | 与「单组织多部门 + 骨架/演示切片」完全契合，已有 seam 可直接复用 | 与当前架构冲突，或需先补一套基础设施 |
| 考察热度 | 面试必问、能展开 2–3 层追问 | 极少被问到，或讲不出取舍 |
| 实现成本 | **成本友好度**：5＝复用现有代码、几乎零新增；1＝需新建模块 / 外部依赖 / 重建索引 | — |

**综合分 = 适配性 + 考察热度 + 实现成本友好度**（满分 15）。同分按「是否有可 A/B 量化的收益证据」破同分，依据 `docs/next-version-messaging-and-plugins.md` §8 的三问：挂哪个版本化对象 / 用哪个指标判定收益 / 怎么回滚。

### 0.3 事实源（已实查，不重复造）

| 来源 | 引用内容 |
|---|---|
| `docs/enterprise-readiness.md` §6 | 插件能力清单 11 项、`webapp/observability.py::PLUGINS` 注册表、限流 / 权限 / 成本规则、§8 V1–V14 验收标准 |
| `docs/kb-capability-checklist.md` | 50 条能力对照、6 个可核对来源、§4 三组差距归纳、§5 Top10 建议 |
| `docs/next-version-messaging-and-plugins.md` §5 | P0 / P1 / P2 插件清单、后排三项、§8「每项插件三问」 |
| `docs/org-model-change-plan.md` | 多用户多部门改造总纲、A13 七条断言、隔离强度正确因果、D1–D6 裁决 |
| `webapp/observability.py` | `PLUGINS`(499–533)、`cost_of()`(57)、`record_usage()`(65)、`check_budget()`(97)、`usage_summary()`(128)、`rate_check()`(197)、`CONFIG_KEYS`(252)、`log_event()`(286)、`list_logs()`(302)、`run_offline_eval()`(415) |
| `webapp/providers.py` | `classify_429()`(78)、`backoff_seconds()`(95)、`OpenAILikeLLMProvider.stream()`(220–283)、`LLMProvider.complete()`(119)、`rerank()`(530)、`rerank_with_llm()`(579)、`_apply_rerank_items()`(514) |
| `webapp/rag.py` | `retrieve()`(181–255)、`_get_or_compute_embeddings()`(129–166) |
| `webapp/settings.py` | `effective_embedding()`(71–102)、`cache_key`、`rerank_provider` 枚举(45) |
| `backend/app/agent/orchestrator.py` | `INJECTION_PATTERNS`(36–40)、`PII_PATTERNS`(41–44)、`_query_hash()`(31)、`guard_node()`(87–98)、`permission_filter_node()`(118–129)、`verify_node()`(153–161)、`persist_node()`(165–180) |
| `webapp/routers_evaltest.py` | `APIRouter(prefix="/api/evaltest")`(20)、`/usage` `/budget` `/ratelimit` `/plugins` `/permissions` 等 12 个端点 |
| `webapp/tests/test_evaltest.py`、`test_providers.py`、`test_settings.py` | 可量化断言（详见 §1 各卡与 §5） |
| `eval/core/contracts.py`、`eval/compare_runs.py`、`eval/run_eval.py` | `contract_gate()` / `assert_real_ready()` / `compare()` / `--check-contracts` |

6 个外部来源只引用 `docs/kb-capability-checklist.md` §1.1 已核实的清单（Microsoft Learn RAG overview、Microsoft Learn 搜索安全最佳实践、MCP 规范 2025-11-25、OWASP 中国 LLM Top 10 2025、Ragas evaluation、NIST AI 600-1），**本文不新增、不改写任何 URL**。

---

## 1. 插件卡片（12 项）

### P01 · 429 分类退避重试（`retry_circuit`）

- **解决什么问题**：上游 429 有两种完全不同的语义——频率超限可退避重试，额度耗尽重试只会白烧时间。不区分会把 `quota_exceeded` 也拖进 3 次重试。
- **接入模块**：`webapp/providers.py::classify_429()`(78)、`backoff_seconds()`(95)、`OpenAILikeLLMProvider.stream()`(263–283)、`complete()`(334)；`webapp/observability.py::PLUGINS` 第 509–511 行 `id="retry_circuit"`。
- **系统边界**：**管**单次请求内的 HTTP 429 / 5xx 分类、1/2/4s + 抖动退避、尊重 `Retry-After`、上限 30s、最多 3 次、流式吐字后不再重试；**不管**全局并发闸、熔断、队列吸收突发、跨请求配额。数据：上游响应体与 `Retry-After` 头 → 内存中的 `attempt` 计数 → 不落库（命中不影响问答主链路）。
- **打分**：适配性 **5**（纯函数、零外部依赖、与架构变更无关）｜考察热度 **5**（rate vs quota 是面试标准追问）｜实现成本友好度 **5**（代码与测试已齐）＝ **15**。
- **本轮落地**：`是` — 已落地，本期纳入回归，无需随去 tenant 改造。**状态：已实现**。
- **可量化验收**：`python -m pytest webapp/tests/test_providers.py -q`；断言 `test_classify_429_splits_quota_and_rate`（`classify_429(quota)=="quota"`、`classify_429(rate)=="rate"`）、`test_backoff_uses_retry_after_and_jitter`（`backoff_seconds(0,5)==5.0`、`backoff_seconds(1,999)==30.0`）、`test_quota_429_fails_fast_without_retry`（`sleeps == []`）、`test_rate_429_retries_then_raises`（`len(sleeps)==3`）。全部走 monkeypatch 桩。

### P02 · 结构化日志与 trace→span（`observability`）

- **解决什么问题**：一次 RAG 回答慢或错，要能定位落在检索、重排还是生成，而不是只看一条结果日志。
- **接入模块**：`webapp/observability.py::log_event()`(286)、`list_logs()`(302)、`eval_logs` 表；`webapp/routers_evaltest.py::GET /api/evaltest/logs`；`backend/app/agent/orchestrator.py::state.trace()`、`persist_node()`(165)、`_query_hash()`(31)；`backend/app/services/audit.py::record()`；`PLUGINS` 第 500–502 行 `id="observability"`。
- **系统边界**：**管**事件级日志、`run_id` 维度追踪、按级别过滤、`X-Trace-ID` 贯通请求 / 任务 / 审计；**不管**跨服务 trace 传播、逐 span 的 `object_refs`、OTel / Langfuse 接入。数据：各节点 `state.audit` 与 API 调用 → `eval_logs` / `audit_logs` 只追加台账 → 日志面板只读；审计只存 `query_hash`（16 位 sha256 摘要），**不存提问原文**。
- **打分**：适配性 **3**（事件级已有，span 需新接一套体系）｜考察热度 **5**（「怎么定位瓶颈」必问）｜实现成本友好度 **2**（span 需新增埋点与展示，属下期工程加固）＝ **10**。
- **本轮落地**：`部分` — 事件级日志保持；本期仅随部门维度补 `department_id` 快照字段。**状态：只设计**（指 trace→span 目标能力；事件级日志为 `已实现`）。
- **可量化验收**：`python webapp/selftest.py` → 33/33 `DEMO_READY`；`GET /api/evaltest/logs` 返回 200 且按 `run_id` / `level` 过滤生效；`python -m pytest webapp/tests/test_evaltest.py -q` 中 `test_offline_eval_run_is_real_and_leaves_trace` 断言 `eval_done` 日志的 `run_id` 与运行记录一致。span 能力**不设通过断言**，文档必须标注「只设计」。

### P03 · Prompt 注入防护（`guardrails` 输入侧）

- **解决什么问题**：用户提问里夹带「忽略之前指令」「泄露系统提示」时，编排层应在检索前拦截并留痕。
- **接入模块**：`backend/app/agent/orchestrator.py::INJECTION_PATTERNS`(36–40)、`guard_node()`(87–98)、`DEFAULT_ORDER`(185)；`backend/app/services/audit.py::record()`；`docs/kb-capability-checklist.md` F5。
- **系统边界**：**管**规则级关键词/正则拦截、空问题拦截、拦截动作写审计（`result="blocked"`、只存 `query_hash`）；**不管**语义级注入检测、工具描述作为不可信输入的治理、模型级三道闸、`webapp/` 演示切片接入。数据：`state.query` → 正则匹配 → `state.blocked` → 审计台账；不进检索、不进模型。
- **打分**：适配性 **4**（backend 骨架已有，但 webapp 切片未接）｜考察热度 **5**（OWASP LLM01 必问）｜实现成本友好度 **4**（规则级已有，接到切片只需一个入口）＝ **13**。
- **本轮落地**：`部分` — backend 规则级保持；webapp 接入列下期。**状态：已实现（backend 规则级骨架）**；模型级检测 `只设计`。注意 `webapp/observability.py::PLUGINS` 第 530–532 行 `id="guardrails"` 标的是「未接入（设计项）」，指的是**演示切片**，不是 backend 骨架，两者不矛盾。
- **可量化验收**：`cd backend && python -m pytest -q tests`（基线 16 passed）；断言（本期不改代码，仅列为可执行验证）命中 `INJECTION_PATTERNS` 时 `state.blocked is True`、`block_reason` 含「疑似 Prompt 注入」、审计记录 `result == "blocked"` 且 `detail` 无提问原文（仅 `query_hash`，长度 16）。

### P04 · PII 脱敏（输出侧规则级）

- **解决什么问题**：答案里复述手机号 / 身份证号时，输出闸要替换掉，且审计日志里不能写原文。
- **接入模块**：`backend/app/agent/orchestrator.py::PII_PATTERNS`(41–44)、`verify_node()`(153–161)、`_query_hash()`(31)；`docs/kb-capability-checklist.md` F4 / F5。
- **系统边界**：**管**输出答案中手机号 `1[3-9]\d{9}` 与身份证 `\d{17}[\dXx]` 的正则替换 `[已脱敏]`、留 `pii_masked` trace、审计只存摘要；**不管**入库侧 / 召回侧脱敏、审计脱敏检索、模型级 PII 检测、多语种证件格式。数据：`state.answer` → 正则替换 → SSE 输出；原文不落审计。
- **打分**：适配性 **4**（骨架已有，仅覆盖两类规则）｜考察热度 **4**（合规红线常问，但深度不及注入）｜实现成本友好度 **4**（正则可直接扩，模型级另算）＝ **12**。
- **本轮落地**：`部分` — backend 输出闸保持；入库 / 召回同规则与模型级检测列本期之外。**状态：已实现（backend 规则级输出闸）**；模型级 `只设计`。
- **可量化验收**：`cd backend && python -m pytest -q tests`；断言（列为可执行验证）`verify_node` 对含手机号 / 身份证的答案输出 `[已脱敏]`、`state.audit` 含 `{"action":"pii_masked"}`、`_query_hash("任意提问")` 为 16 位十六进制且审计 `detail` 不含原文。

### P05 · 检索结果缓存（带授权快照）

- **解决什么问题**：切 embedding 模型后复用旧向量会让余弦静默归零；去 tenant 后缓存 key 若不带身份与授权快照，A 的结果会被 B 命中，是最隐蔽的越权。
- **接入模块**：`webapp/db.py::chunk_embeddings` 表(145)、`webapp/rag.py::_get_or_compute_embeddings()`(129–166)、`webapp/settings.py::effective_embedding()`(71–102) 的 `cache_key`；`PLUGINS` 第 512–514 行 `id="cache"`；改造目标口径见 `docs/org-model-change-plan.md` §5（`静态组织命名空间 + principal 授权快照`）。
- **系统边界**：**现状管**分块向量按 `(chunk_id, model)` 缓存与批量补齐，命名空间 `external:{model}` / `local:{model}` / `offline-lexical`；**现状不管**答案级语义缓存、权限身份维度、`config_snapshot_hash`、授权变更自动失效。数据：候选分块 → 查 `chunk_embeddings` → 缺失调 embedding → 写回；**权限判定不在这里**，`decide()` 才是唯一真值。
- **打分**：适配性 **5**（去 tenant 后 key 必须换授权快照，是安全必需）｜考察热度 **5**（「换模型后旧向量怎么办」+「缓存串部门」双高频）｜实现成本友好度 **1**（key 改造涉及缓存 / 对象 / 进度三类 key 与失效策略）＝ **11**。
- **本轮落地**：`部分` — 本期只做 key 维度改造（tenant → `ORG_NAMESPACE` + `principal` 授权快照，含授权版本），**不做**答案级语义缓存。**状态：已实现（向量层缓存）／只设计（授权快照）**。
- **可量化验收**：`python -m pytest webapp/tests/test_settings.py -q`；`test_embedding_cache_key_isolated_across_providers` 断言 local / external / offline 三个 `cache_key` 两两互异（`len(keys)==3`）。改造后补断言（列为可执行验证）：转岗或授权变更后旧授权快照 key 不命中、同参数检索不复用旧结果；`cache_key` 不含 `tenant` 字面量。

### P06 · 预算与成本看板（`cost_budget`）

- **解决什么问题**：把每次调用的 token 与成本记下来，按维度聚合，超预算时按策略告警或硬拦，防止无界消费（OWASP LLM10）。
- **接入模块**：`webapp/observability.py::PRICES` / `price_for()`(53) / `estimate_tokens()`(44) / `cost_of()`(57) / `record_usage()`(65) / `check_budget()`(97) / `usage_summary()`(128)；`webapp/db.py::usage_events` / `budgets` 表；`webapp/routers_evaltest.py::GET/PUT /api/evaltest/budget`、`GET /api/evaltest/usage`、`POST /api/evaltest/run`；`PLUGINS` 第 503–505 行 `id="cost_budget"`。
- **系统边界**：**管**逐调用台账、按字符估算 token（逐条标 `estimated=1`）、输入/输出分别计价、`policy=warn|block`、普通用户强制 `user_id` 条件（不查而非隐藏）；**不管**真实供应商 usage 对账、真实账单校准、多币种、预算周期重置、真实外部模型调用的成本拦截（预算只拦本页离线评估）。数据：问答 / 评估调用 → `usage_events` → `usage_summary()` 五维聚合 → 成本与预算面板；单价 `PRICE_NOTE` 固定标注「演示估算值，非供应商报价」。
- **打分**：适配性 **5**（已落地演示级，只读真实数据不造假）｜考察热度 **5**（「怎么防止打爆预算」+「warn 还是 block」必问）｜实现成本友好度 **5**（代码、面板、测试齐备）＝ **15**。
- **本轮落地**：`是` — 本期随「成本与限流统计维度从 tenant 改为 department」（任务 #13）把维度改挂部门，按事件发生时快照 `department_id`、不随转岗回溯重写。**状态：已实现（演示级）**。
- **可量化验收**：`python -m pytest webapp/tests/test_evaltest.py -q`；`test_cost_rules_and_estimation`（`estimate_tokens("")==0`、`estimate_tokens("配网")>=2`、`cost_of("default",0,0)==0`、`PRICE_NOTE` 含「演示估算」）、`test_budget_block_rejects_new_eval_run`（`allowed is False`、`reason=="budget_exceeded"`、日志含 `eval_rejected_budget`）、`test_usage_ledger_and_scope_isolation`（普通用户 `mine["totals"]["calls"]==0`，不查而非隐藏）。

### P07 · LLM-as-reranker（内部 LLM 打分重排）

- **解决什么问题**：双塔/词法召回便宜但排序粗，用当前 LLM 给候选打分重排，且不可用时必须优雅跳过而不是假装用了。
- **接入模块**：`webapp/providers.py::rerank_with_llm()`(579)、`LLMProvider.complete()`(119)；`webapp/rag.py::retrieve()`(218–241) 的 `llm` 档；`webapp/settings.py::rerank_provider` 枚举 `off|llm|external`(45)；`webapp/routers_settings.py::POST /api/settings/test` 的 `target=="rerank"`(126–134)；`PLUGINS` 第 515–517 行 `id="rerank"`。
- **系统边界**：**管**召回后按 LLM 打分排序、返回 `None` 时标注 `llm_unavailable(skipped)`、把 `rerank` 标记写进来源元数据 `chat_sources`；**不管**独立 rerank 服务、延迟预算硬保障、跨请求缓存。数据：`retrieve()` 候选 → `docs` 文本 → LLM `complete()` → `scores` → `hits.sort()`；失败返回 `None`，**不写假分数**。
- **打分**：适配性 **5**（三档开关已在，无需外部账号）｜考察热度 **5**（双塔 vs 交叉编码器分工必问）｜实现成本友好度 **4**（已实现，但每次多一次 LLM 调用，有延迟成本）＝ **14**。
- **本轮落地**：`是` — 已落地，纳入回归。**状态：已实现**。
- **可量化验收**：`python -m pytest webapp/tests/test_settings.py -q`（`rerank_provider` 枚举校验）；`python webapp/selftest.py` 33/33；断言（列为可执行验证，走 monkeypatch 桩）`rerank_with_llm` 返回 `None` 时 `meta["rerank"]` 含 `skipped` 且 `hits` 顺序不变、来源记录 `rerank` 字段为 `off` 或 `applied:llm`。**不触发真实外部 LLM 调用**。

### P08 · 外部 cross-encoder rerank

- **解决什么问题**：LLM 打分不够稳定时，接真 cross-encoder（Cohere 风格 `/rerank`）拿区分度更高的相关性分数。
- **接入模块**：`webapp/providers.py::rerank()`(530)、`_apply_rerank_items()`(514)；`webapp/rag.py::retrieve()`(227–241) 的 `external` 档；`webapp/settings.py::external_rerank_base_url` / `external_rerank_model`(35–38，默认 `BAAI/bge-reranker-v2-m3`)；`webapp/eval_report.py`(161–171、209–222) 三层分数摊开；`webapp/routers_settings.py`(136–143)。
- **系统边界**：**管** `/rerank` 两种响应形态兼容、空 key / 错 key 返回 `None` 走跳过、`external_unavailable(skipped)` 留痕；**不管**模型托管、配额治理、延迟 SLO。数据：候选 `docs` → 外部 `/rerank` → `scores` → `hits.sort()`；**key 只进 `.env`（gitignore），不落库、不进日志**。
- **打分**：适配性 **4**（需外部账号与 key，离线演示会退化）｜考察热度 **5**（真相关 vs 干扰的分数对比是很好的证据）｜实现成本友好度 **3**（已有实现与实测记录，但依赖外部服务）＝ **12**。
- **本轮落地**：`是` — 已落地，纳入回归；验收走离线桩，不实跑外部 API。**状态：已实现**。
- **可量化验收**：`python -m pytest webapp/tests -q`（51 passed 基线）；断言（离线桩）`_apply_rerank_items` 对两种响应形态都返回长度为 `n` 的分数列表、`rerank()` 在空 key 时返回 `None` 且 `meta["rerank"]` 含 `skipped`。`docs/CHANGELOG.md` 已记录的实测区分度（真相关 0.9316 vs 干扰 0.0160）作为**历史证据**引用，本轮不重跑外部调用。

### P09 · 评估 CI 闸门 / 回归对比（`eval_gate`）

- **解决什么问题**：改了提示词或参数怎么证明没退化；且占位结果绝不能参与闸门判定。
- **接入模块**：`eval/compare_runs.py::compare()`(43)、`eval/core/contracts.py::contract_gate()`(41–50) / `assert_real_ready()`(55–59)、`eval/run_eval.py --check-contracts`；`evalbench/compare.py`、`evalbench/iterations/`；`webapp/observability.py::run_offline_eval()`(415) / `list_runs()`(373) / `run_metrics()`(394)；`PLUGINS` 第 521–523 行 `id="eval_gate"`。
- **系统边界**：**管**版本化对象与前后对照、离线确定性运行留痕、`real_api` 档位下 blocking 字段仍为 `__TBC__` 时快速失败、`comparable=false` 固定；**不管**闸门接 CI、阈值冻结、质量基线、把占位结果当可比结论。数据：`evalbench/datasets/samples_v1.jsonl` → 确定性评分 → `eval_runs` / `eval_logs` / run 快照（带 Git branch/commit）→ 评估测试子页；**`eval/` 冻结对象零改动**。
- **打分**：适配性 **3**（`eval/` 冻结不可动，闸门只能在外围接）｜考察热度 **4**（「改配置怎么保证没退化」常问）｜实现成本友好度 **2**（接 CI + 冻结阈值属团队质量工作，且受冻结边界约束）＝ **9**。
- **本轮落地**：`否` — 闸门接 CI 与阈值冻结留本期之外；本期只保持数据侧与契约门禁现状。**状态：只设计**（数据侧为 `已实现·演示级`）。
- **可量化验收**：`python -m unittest discover -s eval/tests -t .` → 14 OK；`python -m unittest discover -s evalbench/tests -t .` → 16 OK；`eval/tests/test_harness.py::test_contract_gate_blocks_real_api` 断言 `gate["ready_for_real_api"] is False` 且 `blocking_pending_count > 0`；`python eval/run_eval.py --check-contracts` 列出 26 个 blocking 待确认项；`git diff --exit-code -- eval/ demo/` 为空。

### P10 · 内容安全输出审核

- **解决什么问题**：答案出给用户前的最后一道规则闸——PII 复述、无依据引用、内联思考标签外露。
- **接入模块**：`backend/app/agent/orchestrator.py::verify_node()`(153–161)、`DEFAULT_ORDER`(185)；`webapp/providers.py::_filter_inline_think()`(182)；`docs/kb-capability-checklist.md` F5；`PLUGINS` 第 530–532 行 `id="guardrails"`。
- **系统边界**：**管**输出答案 PII 正则替换、答案未标注 `[n]` 引用编号时追加核对提示、流式内联思考块跨 chunk 剥离；**不管**模型级三道闸（入库 / 召回 / 输出同规则）、敏感信息披露的语义判定、审核策略可配置。数据：`state.answer` / 流式 chunk → 规则处理 → SSE；处理动作写 `state.trace()`。
- **打分**：适配性 **4**（骨架节点已有，webapp 有独立的流式过滤）｜考察热度 **4**（合规常问）｜实现成本友好度 **3**（规则级已有，模型级需新增检测链路）＝ **11**。
- **本轮落地**：`部分` — 规则级保持；模型级三道闸留本期之外（与 `next-version-messaging-and-plugins.md` §5「明确后排」一致）。**状态：已实现（规则级）／只设计（模型级三道闸）**。
- **可量化验收**：`python -m pytest webapp/tests/test_providers.py -q`；`test_filter_inline_think_single_chunk` / `test_filter_inline_think_split_across_chunks` / `test_filter_inline_think_passthrough` 三条断言跨 chunk 标记拼接正确。`cd backend && python -m pytest -q tests` 16 passed；断言（列为可执行验证）`verify_node` 在无引用编号且有候选时追加提示语。

### P11 · 多维限流（`rate_limit`）

- **解决什么问题**：防单用户 / 单接口 / 单模型调用打满进程，命中时给统一 429 契约与可回放台账。
- **接入模块**：`webapp/observability.py::rate_check()`(197)、`rate_rules()`(174)、`rate_hits()`(185)、`_persist_hit()`(232)、`DIMENSIONS`；`webapp/main.py` 的 `@app.middleware("http")`；`webapp/routers_evaltest.py::GET/PUT /api/evaltest/ratelimit`、`POST /api/evaltest/ratelimit/probe`；`PLUGINS` 第 506–508 行 `id="rate_limit"`。
- **系统边界**：**管**四维滑动窗口计数、统一 429 响应体 + `Retry-After` / `X-RateLimit-Dimension` / `X-RateLimit-Limit` / `X-RateLimit-Window` 四个响应头、命中写 `rate_hits` + `eval_logs` WARN；**不管**网关层 Redis 令牌桶、全局并发闸、跨实例一致性（进程内窗口，单实例有效）。数据：请求 → 内存窗口计数 → 命中落台账 → 面板只读。
- **打分**：适配性 **5**（已落地演示级、默认阈值宽松不误伤自测）｜考察热度 **4**（常与 429 分类合并追问）｜实现成本友好度 **4**（已实现；本期需把 tenant 维度改 department）＝ **13**。
- **本轮落地**：`是` — 本期随任务 #13 把第二维 `tenant` 改 `department`，`X-RateLimit-Dimension` 枚举同步。**状态：已实现（演示级）**。注意 `webapp/tests/test_evaltest.py:53` 当前硬断言 `{"user","tenant","endpoint","model"}`，改造后该断言需同步更新并计入新基线；当前代码**仍是 tenant 维度**，不得写成已改造完成。
- **可量化验收**：`python -m pytest webapp/tests/test_evaltest.py -q`；`test_rate_limit_returns_429_with_headers` 断言阈值调至 2/60s 后第 3 次返回 429、`Retry-After` 存在、`X-RateLimit-Dimension == "user"`、`dimension=="user"` 且 `limit==2`、`rate_hits` 有命中记录；`test_new_tables_and_defaults` 断言四条规则值为正、`budgets` 默认 `warn`。

### P12 · 引用溯源（`citation`）

- **解决什么问题**：每条回答能点开看原文，证明答案不是编的，且历史会话可回放来源。
- **接入模块**：`webapp/routers_chat.py` 的 `chat_sources` 表与三个回放接口；`webapp/rag.py::retrieve()` 返回的 `retrieval_mode` / `rerank` / `score` 元数据；`PLUGINS` 第 518–520 行 `id="citation"`。
- **系统边界**：**管**分块级来源卡片（文档 / 分块 / 得分 / 检索方式 / 原文弹窗）、SSE `meta` / `done` 携来源、历史会话回放；**不管**逐句 grounding 高亮、页码级引文、跨文档归因。数据：检索命中 → `chat_sources` 落库 → 前端来源卡片；与答案同会话关联。
- **打分**：适配性 **5**（零外部依赖、只读真实数据）｜考察热度 **4**（「怎么证明不是编的」高频，但深度略低于权限）｜实现成本友好度 **5**（代码与回放接口齐备）＝ **14**。
- **本轮落地**：`是` — 已落地，纳入回归。**状态：已实现**（逐句 grounding `未做`）。
- **可量化验收**：`python webapp/selftest.py` 33/33 `DEMO_READY`；`python -m pytest webapp/tests -q` 51 passed；断言（列为可执行验证）一次问答后 `chat_sources` 记录数 ≥ 1 且 `retrieval_mode` / `rerank` 字段非空，三个回放接口均返回 200。

---

## 2. 汇总排序表

| 排名 | 编号 | 插件 | 适配性 | 考察热度 | 成本友好度 | 综合分 | 本轮落地 | 落地状态 |
|---|---|---|---|---|---|---|---|---|
| 1 | P01 | 429 分类退避重试 | 5 | 5 | 5 | **15** | 是 | 已实现 |
| 1 | P06 | 预算与成本看板 | 5 | 5 | 5 | **15** | 是 | 已实现（演示级） |
| 3 | P07 | LLM-as-reranker | 5 | 5 | 4 | **14** | 是 | 已实现 |
| 3 | P12 | 引用溯源 | 5 | 4 | 5 | **14** | 是 | 已实现 |
| 5 | P03 | Prompt 注入防护 | 4 | 5 | 4 | **13** | 部分 | 已实现（规则级） |
| 5 | P11 | 多维限流 | 5 | 4 | 4 | **13** | 是 | 已实现（演示级） |
| 7 | P04 | PII 脱敏 | 4 | 4 | 4 | **12** | 部分 | 已实现（规则级） |
| 7 | P08 | 外部 cross-encoder rerank | 4 | 5 | 3 | **12** | 是 | 已实现 |
| 9 | P05 | 检索结果缓存（授权快照） | 5 | 5 | 1 | **11** | 部分 | 已实现 / 只设计 |
| 9 | P10 | 内容安全输出审核 | 4 | 4 | 3 | **11** | 部分 | 已实现 / 只设计 |
| 11 | P02 | 结构化日志与 trace→span | 3 | 5 | 2 | **10** | 部分 | 只设计（span） |
| 12 | P09 | 评估 CI 闸门 | 3 | 4 | 2 | **9** | 否 | 只设计 |

同分破同分规则见 §0.2：P07 与 P12 同为 14，P07 有可 A/B 量化的打分收益（rerank 分数可摊开对比），列第 3；P12 紧随其后，同为 Top 推荐的可讲项。

---

## 3. 最终选型结论

### Top 3 推荐纳入（本期纳入回归与讲解主线）

| 序 | 插件 | 推荐理由 |
|---|---|---|
| 1 | **P01 429 分类退避重试** | 综合分 15，纯函数 + monkeypatch 桩可完整单测，零外部依赖、与去 tenant 改造无关因而零风险。`rate` 退避 / `quota` 不重试是面试必问，且能自然引出「流式吐字后不再重试」「上限 30s」两个工程细节。四项测试断言齐全，可直接作为「可量化验收」样板。 |
| 2 | **P06 预算与成本看板** | 综合分 15，是项目里唯一把「token 估算 → 逐调用台账 → 五维聚合 → 预算策略 → 前台面板」全链路跑通的能力，且全程只读真实数据、单价主动标注「演示估算非报价」。对应 OWASP LLM10，面试可同时讲业务价值与诚实边界；本期随任务 #13 改挂 department 维度，正好是一条可讲的架构适配案例。 |
| 3 | **P07 LLM-as-reranker** | 综合分 14，三档 `off/llm/external` 开关 + 不可用时 `skipped` 留痕，满足 `next-version-messaging-and-plugins.md` §8 三问中的「用什么指标判定收益」；能展开双塔召回 vs 交叉编码器的成本曲线，且离线可降级，不需要真实外部账号即可演示。 |

**并列说明**：P12 引用溯源同为 14 分、成本友好度 5，列第 4，仍是本期必讲项；它不是被淘汰，只是缺少可 A/B 量化的收益指标。

### 明确不纳入本轮的取舍

| 插件 / 能力 | 取舍 | 代价与理由 |
|---|---|---|
| P09 评估 CI 闸门接 CI、阈值冻结 | 不纳入 | `eval/` 14 个版本化对象与 `__TBC__` 契约门禁是冻结边界，`real_api` 仍阻断、`comparable=false` / `NOT_COMPARABLE` 不得参与闸门。硬做会破坏冻结对象，且属团队质量工作。本期只保持数据侧与门禁现状。 |
| P02 trace→span（OTel / Langfuse） | 不纳入 | 事件级日志与 `X-Trace-ID` 已够讲「能复现」，span 需要新增埋点、跨服务传播与展示页，属下期工程加固；主动说明「现在只到事件级」比做半套更有说服力。 |
| P05 答案级语义缓存 | 不纳入 | 本期只做**授权快照 key 改造**（安全必需），答案级缓存需要失效策略与命中率证据，会分散本期主线。 |
| P03/P04/P10 模型级三道闸 | 不纳入 | `next-version-messaging-and-plugins.md` §5 已把「PII 三道闸模型级检测」列为明确后排；骨架规则级足够讲边界，模型级属 A/B 档上线级。 |
| P08 真实外部 rerank 调用 | 验收不实跑 | 用户纪律要求不运行真实外部 LLM 调用；验收一律走 monkeypatch 桩，历史实测数据只作证据引用。 |
| P09/P02 与 `eval/` 冻结对象的联动 | 禁止 | 任何「顺手把占位结果改成可比」的改动都会破坏 `demo` 的 `run_id=RUN-095a2a4a40c6` 与 `config_snapshot_hash` 不变性。 |

---

## 4. 面试怎么讲（每题 2–3 句，含诚实边界）

| 题 | 讲法 |
|---|---|
| 429 为什么要分 rate 和 quota？ | 「`rate_limit_exceeded` 是频率超限，按 1/2/4s 加抖动退避、尊重 `Retry-After`、上限 30s、最多 3 次；`quota_exceeded` 是额度耗尽，重试只会白烧时间，直接失败。流式一旦开始吐字就不再重试，避免重复计费和答案拼接。」**边界**：只做单次请求级，没有全局并发闸和熔断。 |
| 怎么防止某个部门把预算打爆？ | 「逐调用记 `usage_events`，token 按字符估算并逐条标 `estimated=1`，输入/输出分别计价；预算默认 `warn` 只留痕，`block` 才拒绝新的离线评估。普通用户强制按 `user_id` 过滤，是『不查而非隐藏』。」**边界**：单价是演示估算值，不是供应商报价；预算只拦离线评估，不拦真实外部调用。 |
| 双塔召回和 cross-encoder 怎么分工？ | 「先用词法门禁 + 向量排序便宜地召回，再用 LLM 打分或外部 `/rerank` 精排；我做了 `off/llm/external` 三档开关。不可用时返回 `None` 并写 `skipped`，绝不假装用了或写假分数。」**边界**：没有延迟 SLO 与压测证据，降档是留痕可见的，不是静默失败。 |
| 怎么证明答案不是编的？ | 「检索命中会落 `chat_sources`，SSE 的 `meta`/`done` 带来源，前端能点开原文、回放历史会话；答案没标引用编号时输出闸还会追加核对提示。」**边界**：只到分块级，逐句 grounding 高亮和页码级引文没做。 |
| 缓存切了 embedding 模型怎么办？ | 「`effective_embedding()` 是缓存命名空间的唯一事实源，`cache_key` 带 provider + 模型名，所以本地 Bge M3 与硅基流动 bge-m3 天然隔离，不会复用旧向量让余弦静默归零。」**边界**：现在只缓存分块向量，答案级语义缓存和授权快照失效还没做。 |
| 一次 RAG 回答慢在哪一段？ | 「`X-Trace-ID` 贯通请求 / 任务 / 审计，各节点写 `state.audit`，`eval_logs` 能按 `run_id` 和级别过滤，评估子页把运行、指标、日志摊开。」**边界**：这是事件级日志和 run 追踪，不是 OTel 的逐 span 下钻；定位瓶颈还做不到逐段计时。 |
| 提示注入 / PII 怎么防？ | 「编排层 `guard_node` 用规则拦注入并写审计，`verify_node` 在输出侧把手机号、身份证替换成 `[已脱敏]`；审计只存 16 位 `query_hash`，不存提问原文。」**边界**：都是正则规则级，模型级三道闸和入库/召回同规则没做，`webapp` 演示切片还没接入这套闸。 |
| 改了提示词怎么证明没退化？ | 「`eval/` 有 14 个版本化对象、确定性双跑产生新 `run_id`、`compare_runs` 做前后对照，`evalbench` 有真实 API 工作台和逐轮采纳对比。」**边界**：`comparable=false` / `NOT_COMPARABLE`，`real_api` 仍被契约门禁阻断；闸门接 CI 和阈值冻结没做，所以我不给「质量达标」的数值结论。 |
| 限流怎么做的？ | 「四维滑动窗口（用户 / 部门 / 接口 / 模型），命中给统一 429 加 `Retry-After` 等四个响应头，并写 `rate_hits` 台账可回放；默认阈值宽松，不误伤 51 项 pytest 与 33 项 selftest。」**边界**：进程内窗口，单实例有效，没有网关层 Redis 令牌桶和全局并发闸。 |
| 为什么插件优先级这么排？ | 「我按适配性、考察热度、成本友好度各 1–5 分排，优先做已有 seam、能单测、能 A/B 量化收益的；每项都要能回答挂哪个版本化对象、用什么指标判定、怎么回滚。」**边界**：分数是本文的相对选型工具，不是外部基准；`eval/` 冻结对象和 demo 的 `run_id` 一律不动。 |

---

## 5. 可量化验收总表

| 验收组 | 命令 | 断言 / 目标 | 覆盖插件 |
|---|---|---|---|
| 单元测试 | `python -m pytest webapp/tests -q` | 51 passed（基线；维度改造后断言与计数变化，按全绿 + 新计数冻结判定） | P05 P06 P07 P08 P10 P11 P12 |
| 供应商 / 退避专项 | `python -m pytest webapp/tests/test_providers.py -q` | `classify_429` 分流、`backoff_seconds` 上限 30s、quota 零重试、rate 三次重试、内联思考跨 chunk 过滤 | P01 P10 |
| 设置 / 缓存专项 | `python -m pytest webapp/tests/test_settings.py -q` | 三个 `cache_key` 互异、`rerank_provider` 枚举校验 | P05 P07 |
| 演示切片端到端 | `python webapp/selftest.py` | 33/33 `DEMO_READY` | P02 P06 P12 |
| 评估测试子页 | `python -m pytest webapp/tests/test_evaltest.py -q` | 成本规则、预算 block、用量范围隔离、429 四响应头、离线运行留痕、写接口 403 | P02 P06 P09 P11 |
| 生产骨架 | `cd backend && python -m pytest -q tests` | 16 passed（基线） | P03 P04 P10 |
| 评测 harness | `python -m unittest discover -s eval/tests -t .` | 14 OK；`test_contract_gate_blocks_real_api` 断言 `ready_for_real_api is False` | P09 |
| 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` | 16 OK | P09 |
| 契约门禁清单 | `python eval/run_eval.py --check-contracts` | 列出 26 个 blocking `__TBC__` 待确认项 | P09 |
| 冻结边界 | `git diff --exit-code -- eval/ demo/` | 输出为空 | 全部 |
| 演示物料 | `python demo/selftest.py` | 16/16，`run_id=RUN-095a2a4a40c6` 与 `config_snapshot_hash` 不变 | 全部 |
| 前端语法 | `node --check app.js && node --check i18n.js && node --check theme.js` | 全部通过 | P06 P12 |

**验收纪律**：①所有外部模型 / rerank 相关验证走 monkeypatch 桩或离线 provider，**不触发真实外部 LLM 调用**；②`eval/` 与 `demo/` 零字节改动；③本期维度改造会改变 `test_evaltest.py:53` 的硬断言与用例计数，判定方式为「命令全绿 + 新计数写入 `CHANGELOG.md`」，不要求仍等于 51 / 33；④`demo` 的 16/16 与 `run_id` 属**不可变基线**，严格不变。

---

## 6. 改动清单（涉及文件路径与改动范围）

| 文件 | 类型 | 改动范围 |
|---|---|---|
| `docs/plugin-selection.md` | **本轮新增** | 全文：口径、12 张插件卡、汇总排序、Top3 结论、面试话术、验收总表、改动清单 |
| `docs/capability-priority.md` | **本轮新增** | 全文：50 条能力重标优先级、三组范围、A/B 档之外清单、多租户未来方向 |
| `docs/enterprise-readiness.md` §6 | 只读引用 | 插件 11 项清单、§8 V1–V14 验收标准；**本轮不改** |
| `docs/kb-capability-checklist.md` | 只读引用 | 50 条底数、6 个来源、§4 差距归纳、§5 Top10；**本轮不改** |
| `docs/next-version-messaging-and-plugins.md` §5/§8 | 只读引用 | P0–P2 清单、后排三项、插件三问；**本轮不改** |
| `docs/org-model-change-plan.md` | 只读引用 | 改造总纲、A13 七条断言、隔离强度正确因果；**本轮不改** |
| `webapp/observability.py` | 只读引用 | `PLUGINS`(499–533) 与各函数行号；**本轮不改**。后续若落地 P06/P11 维度改造，范围限定在 `DIMENSIONS` / `CONFIG_KEYS` / `rate_check()` / `usage_summary()` / `record_usage()` 及对应表字段 |
| `webapp/providers.py`、`webapp/rag.py`、`webapp/settings.py`、`webapp/db.py` | 只读引用 | P01/P05/P07/P08 接入点；**本轮不改** |
| `webapp/routers_evaltest.py`、`webapp/tests/test_evaltest.py` | 只读引用 | 端点与验收断言；`test_evaltest.py:53` 的 tenant 硬断言属任务 #13 改造范围，**本轮不改** |
| `backend/app/agent/orchestrator.py` | 只读引用 | P03/P04/P10 规则级节点；**本轮不改** |
| `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md` | **禁止改动** | 冻结边界 |

> 「接入模块」列是**事实引用**，用于证明「已实现」有代码支撑；列出路径不等于本轮修改了这些文件。

---

## 7. 边界与纪律

1. 本文所有 `已实现` 判定均对应上表实查的真实代码与行号；`只设计` / `未做` 不冒充落地，演示级与规则级骨架都注明档次。
2. 当前代码里仍有 tenant 维度历史遗留（如 `test_evaltest.py:53` 硬断言、`observability.py:252 CONFIG_KEYS` 的 `tenant` 键），本文如实标注为**待改造**，不写成已完成；改造归任务 #13。
3. 多租户不在本轮实现范围，仅作为未来研发方向写入 `docs/capability-priority.md`；本文不夸大多租户能力，也不宣称残留已清零。
4. 打分是相对选型工具，不是外部基准；`eval/` 占位结果 `comparable=false` / `NOT_COMPARABLE` 永远不作为质量结论。
5. 外部来源只引用 `docs/kb-capability-checklist.md` §1.1 的 6 个已核实来源，本文未新增或改写任何 URL。
