# 安全前中后置 + DeepSeek V4.1 Flash 低思考快速档（技术分析与口径）

编制：智能体应用工程师 安智体 ｜ 适用：企业内部知识库「开发框架骨架 + 可运行演示切片」（A/B 档，学习与面试用，**非上线交付版**）

> **本文只做技术分析与口径约定，不含任何代码改动，也未执行任何真实外部 LLM 调用。**
> 回答用户两问：**「安全前中后置？」**（第一部分）、**「DeepSeek V4.1 用低思考」**（第二部分）。
> 事实源为项目内实读代码与既有实测文档；凡未实测的能力一律标注 **【尚未实测确认】** 或 **【以实测为准】**，不得当作已支持。
> **三态标注**：`已实现` = 有可运行代码/接口/表支撑（含规则级、演示级、骨架最小集）；`只设计` = 有架构预留或方案、无落地；`未做` = 两套体系都没有。
> **两套体系必须分开看**：`backend/` 是生产骨架（FastAPI + PG/Milvus/Redis + Agent 七节点编排）；`webapp/` 是可运行演示切片（SQLite + 进程内队列替身）。同一项安全能力在两者中状态不同，混写即失真。
> **状态时间基准**：以下三态均为「本文编制时已实读代码」的当前状态。团队正在并行落地 Phase 2 改造（部门维度、限流维度、低思考档等），改造合入后需按本文判据复核，不得把「计划中」当作「已实现」。
> 本文**不改动** `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md` 及任何源码。

---

# 第一部分 · 安全前中后置

## 1.1 直接回答

**三段都要，且顺序不能颠倒**：**前置**（请求进入、调模型之前）挡住「不该进来的人和不该进来的内容」；**中置**（生成过程）保证「进模型的候选与出模型的 token 全程受控」；**后置**（输出落地）保证「答案可追溯、可审计、可拒答、可降级」。

用一句话讲清分工：

| 段 | 时机 | 核心判据 | 一句话 |
|---|---|---|---|
| **前置** | 请求进入 / 调模型之前 | 主体是谁、能不能进、进来的内容是否安全、要花多少钱 | 「不让不该发生的事发生」 |
| **中置** | 生成过程（检索→拼上下文→流式解码） | 每个进模型的片段是否逐个授权、每个出模型的 token 是否实时可控 | 「让正在发生的事跑不出边界」 |
| **后置** | 输出落地 / 持久化 | 答案是否忠于引用、是否泄密、失败是否留痕、成本是否记账 | 「让已经发生的事可查、可否认、可兜底」 |

**为什么不能只做一头**：前置再严，也拦不住「检索回来的片段里夹了别的部门文档」或「模型在生成中途复述了手机号」；后置再全，也只是事后发现，越权内容已经进了上下文、甚至已推到前端。三段是**纵深防御**，不是三选一。

## 1.2 三段式定义与判据

| 段 | 应覆盖的控制点 | 判据（怎样算「有」） |
|---|---|---|
| **前置** | ① 身份鉴权与授权（登录态、角色、admin 门禁）② 输入注入检测 ③ 输入 PII 脱敏 ④ **检索前权限裁剪**（先按权限过滤候选，再重排、再进模型）⑤ 预算与限流（配额、RPM、并发闸） | 存在一个在**任何模型调用之前**执行的确定性门禁；未授权主体/越权候选/超限请求在到达模型前被拒，且拒绝不消耗模型调用 |
| **中置** | ① 候选集**逐条** `decide()` 复核（集合只是超集下推，最终 allow/deny 归纯函数）② SSE 流式输出实时检测（边生成边拦，而非生成完再扫）③ 上下文拼装防注入（检索片段与系统指令隔离，防片段内恶意指令）④ 工具调用鉴权（先注册、参数白名单、每次先 `check()`） | 每个进上下文的片段有可解释的授权结论；流式链路上存在逐 token/逐块的检查点；工具不能被自由 SQL 或任意 HTTP 绕过 |
| **后置** | ① 引用与 grounding 校验（答案是否忠于所引片段）② PII 复述扫描 ③ 降级/拒答留痕（`degraded` / `degrade_reason` / `blocked`）④ 审计与成本记账 | 输出落库前有一次确定性检查；任何降级/拦截都有结构化标记；台账可回放，且明确区分「真实 usage」与「估算」 |

> **红线顺序（本项目既有口径，不变）**：**先按权限过滤 → 再重排 → 再进模型**。任何「先召回后过滤」「先重排后过滤」都会让未授权片段短暂进入候选集或上下文，一次就是越权。

## 1.3 逐段对照本项目实际代码（三态标注）

### 1.3.1 前置

| 控制点 | `backend/` 生产骨架 | `webapp/` 演示切片 | 状态 |
|---|---|---|---|
| 身份鉴权 / 角色门禁 | `core/deps.py::get_current_user()` 由依赖注入构造 `Principal`；`require_roles()`；`services/permission.py::decide()` 纯函数统一判定 | `webapp/deps.py::get_current_user()`（Bearer 解析 + 查 `users` 表）、`require_admin()`（非 admin 返回 403）；`webapp/main.py` 挂载路由 | **已实现**（两端各自实现，webapp 为演示级 admin/user 两级） |
| 输入注入检测 | `agent/orchestrator.py::INJECTION_PATTERNS`（3 条正则）+ `guard_node()`：命中即 `state.blocked=True`、`block_reason="疑似 Prompt 注入，已拦截"`，`NodeRegistry.run()` 检测 `blocked` 立即 `break`，**不进入 generate、不调模型**，审计 `result="blocked"` 且只存 `query_hash` | 无对应实现（全目录检索无 inject/pii 规则） | **backend 已实现（规则级骨架）／webapp 未做** |
| 输入 PII 脱敏 | 无输入侧脱敏（仅输出侧 `verify_node()` 扫描，见后置） | 无 | **未做**（两端均无输入侧脱敏） |
| 检索前权限裁剪 | `backend/app/services/search.py::keyword_search()` 经 `services/document.py::apply_filters()` 下推可见条件；`semantic_search()` 由 `services/permission.py::milvus_expr()` 构造 Milvus expr 粗筛；同文件 `filter_candidates()` 逐个 `decide()` 二次校验 | `webapp/rag.py::_load_ready_chunks()` 为 `SELECT ... JOIN documents d ON d.id=c.doc_id WHERE d.status='ready'` —— **取全库 chunks，无 owner / 部门 / 角色条件下推** | **backend 已实现（骨架）／webapp 未做** |
| 预算与限流 | `core/config.py::MAX_CONCURRENT_INGEST_PER_TENANT`；`middleware/rate_limit.py` + `redis_client.token_bucket()`（按 IP 令牌桶，基础） | `webapp/main.py::rate_limit_gate` 四维滑动窗口（`user/tenant/endpoint/model`，进程内）；`webapp/observability.py::check_budget()` | **已实现（演示级）**；但预算只拦 `/api/evaltest/run` 离线评估，**不拦真实外部模型调用**（`docs/enterprise-readiness.md` §5.2 明示） |

### 1.3.2 中置

| 控制点 | `backend/` 生产骨架 | `webapp/` 演示切片 | 状态 |
|---|---|---|---|
| 候选集逐条 `decide()` 复核 | `agent/orchestrator.py::permission_filter_node()`（七节点第三道）：召回结果进上下文前逐个 `PermissionService.filter_candidates()` → `decide()`；空则 `blocked` 拒答 | 无。`routers_chat.py::chat_stream()` 调 `rag.retrieve()` 后直接把 `retrieved` 交给 `rag.stream_answer()` 拼上下文 | **backend 已实现／webapp 未做（核心缺口）** |
| SSE 流式输出实时检测 | 无逐 token 检测；`verify_node()` 是**生成完成后**一次性扫描 | `routers_chat.py::gen()` 逐 token 直发 SSE（只做 `is_disconnected()` 与 `: ping` 心跳），无内容检查 | **未做**（两端均无流式实时检测） |
| 上下文拼装防注入 | `integrations/llm.py::RAG_SYSTEM_PROMPT` 以文字约束「只依据【资料片段】、不得复述密级信息」；`build_rag_messages()` 把片段编号后拼入 user 消息 | `webapp/providers.py::OpenAILikeLLMProvider._messages()` 的 system 提示词「只能依据【参考】内容回答，不得编造」 | **已实现（提示词级约束）／片段隔离标记未做**：两端都只有文字约束，检索片段未被标记为不可信数据、无转义或分隔符隔离 |
| 工具调用鉴权 | `agent/tools.py::ToolRegistry.invoke()`：工具先注册、参数白名单、每次调用先 `PermissionService.check()` → `decide()`，再记审计；当前只注册 `kb.search` 一个工具，禁止自由 SQL 与任意 HTTP | 无工具层（线性 RAG，无 Agent） | **backend 已实现（骨架）／webapp 不适用** |

### 1.3.3 后置

| 控制点 | `backend/` 生产骨架 | `webapp/` 演示切片 | 状态 |
|---|---|---|---|
| 引用 / grounding 校验 | `verify_node()` 只判断「答案里有没有 `[n]` 编号」，无编号时追加「本回答未标注引用编号，请核对原文」提示；**不校验答案是否忠于所引片段** | `chat_sources` 表落库、`/api/chat/sources` 可回放（`routers_chat.py::_persist()`），前端有引用卡片；**同样不做逐句 grounding 校验** | **部分已实现**（引用编号提示 + 来源可追溯）；**逐句 grounding / 引用高亮 = 只设计**（`docs/evaluation-report.md` §9、§10 明示） |
| PII 复述扫描 | `verify_node()`：`PII_PATTERNS`（手机号 / 身份证）命中替换为 `[已脱敏]` 并 `state.trace("verify", action="pii_masked")` | 无输出侧 PII 扫描 | **backend 已实现（规则级）／webapp 未做** |
| 降级 / 拒答留痕 | `guard_node` 拦截 → `state.blocked` + `block_reason` → `run()` 补 `answer="无法回答：…"`；`permission_filter_node` 空候选拒答 | `rag.py::stream_answer()` 五类标记：`retrieval_empty`、`model_error`、`quota_exceeded`、`stream_interrupted`、`empty_model_output`；`routers_chat.py` 的 `degraded`/`degrade_reason` 随 `meta`/`done` 下发并落 `chat_logs` | **已实现**（两端各有结构化留痕，webapp 更完整） |
| 审计与成本记账 | `persist_node()` 写 `MetricEvent` + `services/audit.record()`（只追加） | `db.log_action()` 审计动作；`observability.py::record_usage()` 逐调用台账，但 token 为**字符估算**并逐条标 `estimated=1`，单价为演示值 | **已实现（演示级）**；**真实供应商 usage 对账 = 未做** |

### 1.3.4 两套体系的关键差异（主理人特别要求指出）

- **`backend/app/agent/orchestrator.py` 的七节点编排已经把前 / 中 / 后置放进同一条链**：
  `DEFAULT_ORDER = ["guard", "retrieve", "permission_filter", "assemble", "generate", "verify", "persist"]` ——
  `guard`（前置）→ `retrieve` + `permission_filter`（中置）→ `assemble` + `generate` → `verify` + `persist`（后置）。
  `NodeRegistry.run()` 逐节点执行并在 `state.blocked` 时 `break`，是状态机式可控编排，天然可插检查点。**但它是规则级骨架**：`INJECTION_PATTERNS` / `PII_PATTERNS` 都是正则，没有内容安全模型；`docs/kb-capability-checklist.md` F5 明确「三道闸模型级为『只设计』」，`webapp/observability.py::PLUGINS` 的 `guardrails` 项标注「内容安全与 PII 脱敏 · 未接入（设计项）· 未做」。
- **`webapp/` 演示切片只做了前置的 admin 门禁与后置的降级留痕**：前置有 `get_current_user` / `require_admin` / 四维限流；后置有 `degraded` 留痕、来源落库、用量估算台账。**中置的候选集逐条权限复核与流式输出检测完全缺失** —— `retrieve()` 返回的全库 chunks 不经任何授权复核即进入 `stream_answer()` 拼上下文，SSE 逐 token 直发。这也与 `docs/enterprise-readiness.md` §4.1「数据可见范围 · 演示级」、§10「多租户/多部门/行级 ACL 未实现隔离」、`observability.py::PLUGINS` 中 `permission` 项「架构预留 · 切片只有 admin 门禁 + owner 过滤」一致。

## 1.4 攻击路径 → 拦截段 → 当前是否被拦 → 缺口修正方案

> 「当前是否被拦」分列两套体系；`✅` 已拦 / `⚠️` 部分（仅规则级或仅一侧） / `❌` 未拦。

| # | 攻击路径 | 应由哪一段拦截 | backend | webapp | 缺口修正方案 |
|---|---|---|---|---|---|
| 1 | **Prompt 注入**：提问里夹「忽略之前指令 / 打印系统提示词」 | 前置（输入检测）+ 中置（上下文隔离） | ⚠️ 规则级 `guard_node`，3 条正则，可绕过 | ❌ 无任何输入检测 | 前置补规则集并与 `guard_node` 同源；中置把检索片段标记为不可信数据（分隔符 + 转义）；模型级检测属后续 |
| 2 | **越权文档召回**：检索把兄弟部门 / 敏感片段拼进上下文 | 前置（检索前权限裁剪）+ 中置（逐条 `decide()`） | ✅ Milvus expr 粗筛 + `filter_candidates()` 二次校验 | ❌ `_load_ready_chunks()` 全库、无逐条复核 | webapp 在 `_load_ready_chunks()` 之后按登录用户可见范围裁剪候选，并在 `stream_answer()` 之前对每个候选做确定性授权复核（先过滤后算 / 后写） |
| 3 | **PII 复述**：答案里出现手机号 / 身份证号 | 后置（输出扫描）+ 中置（流式实时） | ⚠️ `verify_node()` 生成后规则级替换 `[已脱敏]` | ❌ 无 | 后置补与 `verify_node` 同源的正则扫描；中置在 SSE 逐块输出前检查（跨块 PII 需缓冲窗口，避免被切碎漏检） |
| 4 | **跨部门列表越权**：候选集是超集时列表接口直接返回越权文档 | 中置（列表侧逐条复核） | ⚠️ 骨架有 `visible_document_clause()`，列表侧逐条 `decide()` 为 Phase 2 待补 Must | ❌ 列表按 `owner_id`/`user_id` 过滤（演示级），无 `decide()` 纯函数 | 列表接口对每条返回记录过一次 `decide()`；与检索入口做双向一致性对拍（两个入口最终集合差集为空） |
| 5 | **缓存串权限**：A 的检索结果被 B 命中，或转岗后旧缓存复用 | 前置（key 隔离）+ 中置（命中后复核） | ⚠️ `search.py::_cache_key()` 带 tenant/user/role/dept（骨架） | ❌ 无检索结果缓存；`chunk_embeddings` 按 `(chunk_id, model)` 缓存（与权限正交，不构成此风险） | 缓存 key = 静态 `ORG_NAMESPACE` + `principal` 授权快照 + query 摘要；命中后再轻量 `decide()` 复核；进度快照按 `batch_id` 定位、不挂授权快照（两类 key 按数据性质分治） |
| 6 | **模型输出幻觉**：答案编造知识库之外的内容 | 前置（无依据拒答）+ 后置（grounding 校验） | ⚠️ 提示词约束 + 无候选拒答 + 无引用编号提示 | ⚠️ 词法门禁 + 检索为空拒答 + 来源可追溯 | 前置保持「无依据不进模型」；后置补逐句 grounding / 引用有效性校验（**当前两套都是「只设计」**） |
| 7 | **拒绝服务 / 配额耗尽**：刷接口打爆额度或阻塞 worker | 前置（限流 / 预算 / 并发闸） | ⚠️ 令牌桶基础版 + 并发上限常量 | ⚠️ 四维进程内滑动窗口 + `classify_429()` 分 rate/quota；**预算不拦真实 LLM 调用** | 前置补真实调用前的预算校验与全局并发闸；`quota_exceeded` 明确不重试（已实现）、`rate` 退避重试（已实现）；生产换 Redis 令牌桶 + 网关层 RPM |
| 8 | **审计缺失 / 事后不可追**：拦截、降级、越权尝试无痕 | 后置（只追加审计 + 结构化留痕） | ✅ `persist_node()` + `audit.record()`，拦截也留痕 | ⚠️ `db.log_action()` + `degraded` 落 `chat_logs`；**无安全事件专属台账** | 后置把注入命中 / 权限拒绝 / PII 命中写成结构化安全事件（带 `trace_id`、只存摘要不存原文）；与 `chat_sources` 溯源对齐 |
| 9 | **流式中断被伪装成完整答案**：出正文后断流、或零正文空白气泡 | 后置（降级留痕） | ⚠️ 无流式编排（骨架线性 `chat_stream` 有降级） | ✅ `stream_interrupted` / `empty_model_output` 已留痕并兜底 | 保持现状；新增低思考档时不得绕过这两个标记（见第二部分 2.8） |
| 10 | **工具越权 / 任意 SQL / 任意 HTTP**：诱导 Agent 调用未注册能力 | 中置（工具鉴权） | ✅ `ToolRegistry.invoke()` 先 `check()` 再 `handler`，只注册 `kb.search` | ➖ 不适用（无工具层） | 保持「先注册、参数白名单、危险操作走人工确认 API 不进注册表」；新增工具不得绕过 `decide()` |

## 1.5 缺口台账（可核对，**共 12 条：前置 5 / 中置 4 / 后置 3**）

> 「缺口」= 对照 1.2 判据，本项目当前未达到或仅规则级/单侧达到的项。`体系` 列标明缺口主要落在哪一侧；backend 已实现的项不重复计为缺口。

| 编号 | 段 | 体系 | 缺口 | 状态 | 最小修正落点 |
|---|---|---|---|---|---|
| G1 | 前置 | webapp | 输入 Prompt 注入检测缺失 | 未做 | `chat_stream()` 入口接与 `guard_node` 同源的规则集，命中返回带标记结果、不调模型 |
| G2 | 前置 | 两者 | 输入侧 PII 脱敏缺失（backend 只有输出侧扫描） | 未做 | 提问入库/入模前规则级脱敏，与后置 `PII_PATTERNS` 同源 |
| G3 | 前置 | webapp | 检索前权限候选集裁剪缺失（全库 chunks 入检索） | 未做 | `_load_ready_chunks()` 之后按登录用户可见范围裁剪，先过滤后算 embedding |
| G4 | 前置 | webapp | 真实外部 LLM 调用的预算拦截缺失 | 未做（`check_budget()` 只拦离线评估） | `chat_stream()` 调模型前做一次预算校验；保留 `estimated=1` 口径诚实标注 |
| G5 | 前置 | 两者 | 模型级输入内容安全审核缺失 | 只设计 | 内容安全模型 / 输入输出审核服务；当前仅提示词与正则约束，不冒充已做 |
| G6 | 中置 | webapp | 候选集逐条 `decide()` 授权复核缺失 | 未做（核心） | `retrieve()` 与 `stream_answer()` 之间插入确定性逐条复核 |
| G7 | 中置 | 两者 | SSE 流式输出实时检测缺失（backend 为生成后一次性） | 未做 | 逐块检查点 + 跨块缓冲窗口（防 PII / 标签被切碎漏检） |
| G8 | 中置 | 两者 | 上下文拼装防注入隔离缺失（仅提示词文字约束） | 只设计 | 检索片段与系统指令隔离标记、不可信数据转义、编号边界 |
| G9 | 中置 | 两者 | 流式首 token 延迟（TTFT）实时埋点缺失 | 未做 | 现有 `test_llm()` 只有整请求 `latency_ms`； SSE 链路上计首 token 时间（也支撑第二部分 2.7 的生效验证） |
| G10 | 后置 | 两者 | 逐句 grounding / 引用有效性校验缺失 | 只设计 | 现有仅「无 `[n]` 追加提示」+ 来源可追溯；需答案—片段逐句比对 |
| G11 | 后置 | webapp | 输出 PII 复述扫描缺失 | 未做 | 与 `verify_node()` 同源正则，`done` 事件前扫描 |
| G12 | 后置 | webapp | 真实 token / 成本记账缺失（字符估算 `estimated=1`） | 未做 | 接供应商 `usage`，与估算台账分列；单价上线前用真实账单校准 |

**统计口径说明**：12 条 = 前置 5（G1–G5）+ 中置 4（G6–G9）+ 后置 3（G10–G12）。其中 **中置 4 条全部命中 `webapp/` 演示切片**（G6/G7 为「完全缺失」，G8/G9 为「两端均缺」），即主理人指出的「中段缺失」；`backend/` 七节点已覆盖前/中/后置的骨架级实现，其剩余项集中在**模型级检测与逐句 grounding**（G5/G8/G10 等「只设计」），属 honest boundary，不算骨架实现缺陷。

## 1.6 面试怎么讲（三句话版本）

> 1. **「三段是纵深防御，不是三选一」**：前置在调模型之前挡人和挡内容（鉴权、注入检测、检索前权限裁剪、限流预算），中置在生成过程里保证每个进上下文的片段都过了 `decide()` 逐条授权、流式输出有实时检查点，后置在落地时做引用校验、PII 扫描、降级留痕和审计记账。
> 2. **「我这套骨架把三段放进了一条状态机链」**：`backend/app/agent/orchestrator.py` 的七节点 `guard → retrieve → permission_filter → assemble → generate → verify → persist` 就是前中后置的落点，`blocked` 立刻 `break`，全程不调模型也返回带标记结果；`webapp/` 演示切片刻意只做前置 admin 门禁和后置降级留痕，中段的候选逐条复核和流式检测是**明确没做**的，我不拿演示替身冒充生产实现。
> 3. **「诚实边界」**：现在所有注入/PII 拦截都是**规则级正则**，不是模型级安全；项目里**没有内容安全模型**，逐句 grounding、引用高亮、真实 usage 计费、全局并发闸都还在「只设计/未做」。规则能挡住已知模式，挡不住语义变形，这一段我不会说成「已具备企业级内容安全」。

配套可讲：`docs/evaluation-report.md` §6 降级矩阵（8 类故障都有留痕）、§9 Q&A、「能讲 / 不能讲」表；`docs/kb-capability-checklist.md` F5（规则级注入/PII 已跑、模型级三道闸只设计）；`docs/enterprise-readiness.md` §6 插件清单里 `guardrails` 明确标注「未接入（设计项）· 未做」。

---

# 第二部分 · DeepSeek V4.1 Flash「低思考 / 快速档」

## 2.1 先摆事实：模型已经是用户要的，缺的是「低思考」

| 项 | 实查结果 | 来源 |
|---|---|---|
| LLM provider | `WEBAPP_LLM_PROVIDER=discovery` | `.env:12`；`webapp/settings.py::DEFAULTS` 读 `WEBAPP_LLM_PROVIDER` |
| 模型 id | `DISCOVERY_MODEL=deepseek-v4.1-flash-1017` | `.env:22`；`providers.py::get_llm()` discovery 分支默认值同款 |
| 网关 | `DISCOVERY_BASE_URL=https://discovery-api.intern-ai.org.cn/v1` | `.env:20` |
| Embedding / Rerank | 硅基流动 `BAAI/bge-m3` / `BAAI/bge-reranker-v2-m3` | `.env:36-49`；`docs/evaluation-report.md` §1 实测可用 |
| 请求体实际字段 | `{"model", "messages", "stream": True, "temperature": 0.3}` —— **没有任何推理强度字段** | `webapp/providers.py::OpenAILikeLLMProvider._stream_raw()` |

**结论：用户要的「DeepSeek V4.1 Flash」已经接通并实测可用**（`evaluation-report.md` §1：单次探活 1159 ms ✅），**缺的只有「低思考 / 快速」这一档控制**。这是参数与配置问题，不是换模型问题。

## 2.2 为什么推理模型需要「低思考」

用户要「低思维快速为主」，这个诉求有实测支撑，不是偏好问题：

| 现象 | 项目内实测证据 | 对「快速档」的影响 |
|---|---|---|
| **冷启动 / reasoning 阶段延迟高且抖动大** | `docs/evaluation-report.md` §5：端到端问答 **59.7 s**；探活几次测到 **0.6 s / 9.6 s / 58 s**；`.env:24-25` 记「冷启动可达 13–58 s、热态约 0.5 s，测试脚本已放宽到 180 s」；`evalbench/probe_models.py::DIFFERENCES`：`deepseek-v4.1-flash-1017` 26.4 s，而 `minimax-m3` 0.8 s | 演示/面试要的是可预期的首字延迟，不是 60 秒空白 |
| **reasoning token 占用预算** | `docs/evaluation-report.md` §1：`test_llm` 给 **256 token**（不是 24），原因是「token 给小了会只吐 reasoning 却把正文吃光，早期因此误判过『模型可用』」；`providers.py::test_llm()` docstring 同款 | 推理阶段消耗 `completion_tokens`，挤压正文预算；低思考档可把预算还给正文 |
| **零正文 / 空白气泡** | `docs/CHANGELOG.md:88/90` 记录两个已修问题：`empty_model_output`（推理模型只吐 `reasoning_content`）+ 连接测试 `token 24→256` 并返回 `reasoning_only` 标记；`webapp/rag.py::stream_answer()` 有 `empty_model_output` 兜底 | 低思考档若配合过小的 `max_tokens` 仍会复发，见 2.8 |

### ⚠️ 一处必须纠正的归因口径差异（两份既有文档说法不同）

- `docs/evaluation-report.md` §1 / §5 把「零正文」归因于**推理模型把预算吃光**（并称 `deepseek-v4.1-flash-1017` 是推理模型）。
- `evalbench/probe_models.py::DIFFERENCES`（更晚、专门做兼容探测的实测）明确写：
  > 「实测 6 个模型在 `max_tokens=256` 的探活下均返回正文（2 字）、`reasoning_only=False`；因此**『零正文』不是推理模型专属，本轮未复现 `reasoning_only` 现象**」；
  > 「实测 `max_tokens=24` 时 6 个模型全部返回空 content（**含非推理型**），属**网关侧 token 预算行为**；统一要求 `max_tokens>=512`，**不把它归因于模型类型**」；
  > 文件头 `recompute()` 的 `interpretation_note`：「原始测量值未改动；仅修正把网关共性误归因为推理模型的解释」。

**本文采用的口径**：**观测事实成立**（小预算下正文为空、延迟抖动大），但**归因以 `probe_models.py` 的实测为准 —— 是网关侧 token 预算共性，不能断言「只有推理模型才会」**。因此「低思考档」的正当性来自**延迟与可预期性**（有实测：59.7 s、26.4 s、冷启动 13–58 s），而**不是**靠「推理模型必然吃光 token」这个未证实的因果。这条也写进 2.9 的不编造清单。

## 2.3 项目里已经做了什么 / 没做什么（避免重复造轮子或说错）

| 位置 | 内容 | 判定 |
|---|---|---|
| `backend/app/core/config.py:69` | `DEEPSEEK_THINKING: Literal["disabled","enabled"] = "disabled"` | 存在配置项 |
| `backend/app/integrations/llm.py:51-53` | `if settings.DEEPSEEK_THINKING == "disabled": payload["thinking"] = {"type": "disabled"}`，注释「V4 系列默认开启 thinking；检索问答要的是低延迟，显式关闭」 | **这是 backend 生产骨架针对 DeepSeek 官方端点 `api.deepseek.com` 的私有字段写法**，属于「代码里写了」，**没有实测证据证明该字段在官方端点或 Discovery 网关生效** |
| `webapp/providers.py` | `OpenAILikeLLMProvider` 走 Discovery 网关，payload 无 thinking / reasoning 字段；`test_llm()` 已读 `usage.completion_tokens_details.reasoning_tokens` 与 `reasoning_content`，返回 `reasoning_only` 标记；`_filter_inline_think()` 可剥离内联 `<mm:think>` / ` thinking` 思考块 | **探测与剥离能力已具备**，但**没有主动控制推理强度**；且探测字段是「观测」，不是「控制」 |
| `evalbench/core/runner.py` | `max_tokens >= 512` 的 `require_min_max_tokens_512` 适配、`_parse_answer()` 处理 `reasoning_only` | 评测侧已有 token 预算防御，可作参考 |

> **2026-11-14 实测结论**：虽然 `backend/` 的 `thinking` 字段原本针对官方 DeepSeek 端点、`webapp/` 用的是 Intern-AI Discovery 网关（不同实现），但把同一字段移植到 Discovery 后**经对照实测确认生效**：`reasoning_tokens` 55 → 0、耗时 979 ms → 467 ms。此前「不能把 backend 写了字段说成 Discovery 已支持」的谨慎是对的，但结论现已由实测而非推断给出。换到其它网关时仍需重新验证，不可无条件移植。

## 2.4 OpenAI 兼容网关控制推理强度：常见三类参数（通用知识 + 本项目实测结论见 2.5）

> 以下是行业里 OpenAI 兼容网关常见的三类做法，**仅作方案枚举**。取值、字段名、是否生效**因实现而异**；本项目 Discovery 网关的实测结论见 §2.5：第 ③ 类私有 `thinking` 字段生效，元数据列出的布尔 `reasoning` 参数无效。

| 类 | 典型写法 | 常见出处/语义 | 风险 |
|---|---|---|---|
| ① 推理努力档 | `"reasoning_effort": "low"`（或 `minimal/low/medium/high`） | OpenAI o 系列及部分兼容网关；用离散档位表达「花多少 token 思考」 | 取值集合不统一；部分网关静默忽略未知值（返回 200 但没生效） |
| ② 思考开关 | `"enable_thinking": false`，或 `"chat_template_kwargs": {"thinking": false}` | Qwen3 / DashScope 兼容、vLLM 等；布尔或模板参数关闭思考 | 同一网关不同模型可能不同；嵌套字段易被网关丢弃 |
| ③ 私有字段 | 如本项目 backend 已写的 `"thinking": {"type": "disabled"}` | 供应商自定义对象/字符串 | 完全私有，跨网关不可移植；**已写 ≠ 已验证生效** |

此外还有「把思考内容放 `reasoning_content` 而非 `content`」的**响应侧差异**（`providers.py::test_llm()` 与 `evalbench/core/runner.py::_parse_answer()` 都已处理），但那是**观测适配**，不是控制开关，不能混为一谈。

## 2.5 Discovery 网关：2026-11-14 参数对照实测（结论已确认）

**方法**：同一问题（球棒与球），固定 `temperature=0`、`max_tokens=256`、`stream=false`，
只改变推理开关字段，依次为「无参数基线 / `thinking={"type":"disabled"}` /
`reasoning=false` / `reasoning=true`」，记录墙钟耗时、正文、`reasoning_tokens`
与是否出现 `reasoning_content`。

| 组 | 耗时 | 正文 | `reasoning_tokens` | `reasoning_content` | 判定 |
|---|---|---|---|---|---|
| 基线（无参数） | 979 ms | `0.05` | 55 | 有 | 默认会思考 |
| **`thinking={"type":"disabled"}`** | **467 ms** | `0.05` | **0** | **无** | **生效** |
| `reasoning=false` | 3413 ms | `0.05` | 55 | 有 | 无效 |
| `reasoning=true` | 945 ms | `0.05` | 55 | 有 | 无效 |

**结论**：
1. 项目里既有的 `thinking` 对象字段在 Discovery 网关**确实生效**，把推理 token
   从 55 降到 0、正文不再夹带思考内容，耗时约减半；这也是当前默认档的依据。
2. `/models` 元数据把 `reasoning` 列为 `supported_parameters` 的 boolean，
   **实测不生效**——「元数据声明支持」与「实际生效」不是一回事，这个反例本身就是
   面试里能讲清楚的工程判断。
3. 延迟只能作辅助证据：冷启动抖动大，单次对比无统计意义；主判据是
   `reasoning_tokens=0` 与 `reasoning_content` 消失。

**同日另一项实测（与本节独立）**：网关 `/models` 的 `input_modalities` 显示
10 个模型里 6 个支持 `image`，`deepseek-v4.1-flash-1017` 双图对照传图实测通过，
详见 `docs/parsing/07-api-contracts.md`。此前「全为纯文本、无视觉模型」的判断是
只读模型名造成的误判，已全面纠正。

**仍然成立的限制**：这是参数/能力的**功能连通性实测**，不是质量基线；
未做真实语料集评测、未做压测、单价与配额仍 `__TBC__`；换到其它网关必须重新验证。

## 2.6 工程方案：「参数可配置 + 探测回退」

> 参数可配置部分**已落地**：`WEBAPP_LLM_THINKING` 白名单 + `settings.thinking_disabled()` + `providers.thinking_fields()`，三条请求路径共用；键一律 `WEBAPP_` 前缀以避开 backend 同名命名空间。探测回退部分保留为设计，因为本轮已用对照实测直接确认生效，无需再引入运行时自动探测。

**A. 参数可配置（不硬编码某一种）**
- 在 `settings.DEFAULTS` / `ALLOWED` / `ENUMS` 增加一个白名单键（例如 `WEBAPP_LLM_REASONING_MODE`），枚举收敛为 `off`（不发送任何推理字段，保持现状）/ `low`（发送候选参数）/ `auto`（探测驱动）。落 `app_settings`，沿用「每请求读取、即时生效、未知键拒绝」。
- 候选参数以**配置数据**形式存放（字段路径 + 取值 + 适用 provider/model），而不是写死在 `_stream_raw()` 里：先在 `off` 与某一候选之间做探测，确认生效后再固定；同一套配置可同时容纳 ①②③ 三类，**一次只启用一个**，避免多字段冲突。

**B. 探测（复用已有 seam，不新增协议）**
- 复用 `providers.py::test_llm()`：它已返回 `latency_ms`、`reasoning_tokens`、`content_chars`、`reasoning_only`。设计一个「**带参数 vs 不带参数**」的对照探测，比较 `reasoning_tokens` 是否下降、`reasoning_content` 是否为空、正文是否完整、墙钟延迟是否改善。
- 探测结果**只记录、不自动断言生效**：参数被网关静默忽略（HTTP 200 但 `reasoning_tokens` 无变化）必须判为「未生效」，而不是「成功」。
- 与 `webapp/routers_settings.py::test_provider` 的 `/api/settings/test` 对齐，失败也返回 200 + 原因（现有约定），便于前端展示。

**C. 回退（不假装用了）**
- 网关返回 400 / 参数被忽略 / 探测不可用 → **自动退回 `off`**（不发送任何推理字段，保持现有链路），并把 `reasoning_mode_effective = "unsupported"`（或 `fallback`）结构化透出到设置页与日志。
- **严禁**在回退时仍对外宣称「已启用低思考快速档」；这与项目既有「rerank 不可用优雅跳过、不假装用了」「`estimated=1` 不冒充真实计费」是同一条纪律。
- 回退不得影响既有降级链路：`retrieval_empty` / `model_error` / `quota_exceeded` / `stream_interrupted` / `empty_model_output` 五个标记行为不变。

**D. 连接测试的代价（明确声明）**
- 上述对照探测**会消耗真实外部 LLM 流量与额度**（每次至少 2 次 `/chat/completions`）。用户上次流量已耗尽，**本轮不执行任何真实外部调用**；本文只给方案与判定口径。真要做，应在额度恢复后由主理人显式授权、单独跑一次、把结果落成结构化记录。

## 2.7 「低思考快速档」完整判定口径

**默认档位（用户「低思维快速为主」的诉求，2026-11-14 实测后定稿）**：
- 运行时默认 **`disabled`（低思考 / 快速）**：`WEBAPP_LLM_THINKING=disabled` 注入 `thinking={"type":"disabled"}`，设置页可切 `enabled`（完整推理）。
- **已实测生效**，不再用 `auto` 探测档：同题固定 `temperature=0`，基线 979 ms / `reasoning_tokens=55`；`thinking=disabled` 467 ms / 0；`reasoning=false` 3413 ms / 55；`reasoning=true` 945 ms / 55。元数据里的布尔 `reasoning` 参数**无效**，不要改用它。
- 判定边界：这是参数生效的对照实测，不是质量基线；冷启动抖动大（既有实测 0.6 / 9.6 / 58 s），单次延迟对比无统计意义，结论主要依据 `reasoning_tokens` 与 `reasoning_content` 是否消失。

**怎么验证生效（三个观测点，缺一不可）**：

| 观测点 | 现有可读位置 | 生效判据 | 当前可得性 |
|---|---|---|---|
| ① 首 token 延迟（TTFT） | `providers.py::test_llm()` 只有整请求 `latency_ms`；SSE 链路无逐 token 计时 | 同模型、同 prompt、同网络条件下，低思考档首 token 明显早于基线（`off`） | **⚠️ 当前无流式 TTFT 埋点**（对应缺口 G9），需先补埋点；补之前只能用 `latency_ms` 作近似，且必须标注「墙钟时间，非首 token」 |
| ② `reasoning_content` 是否为空 | `test_llm()` 已计算 `reasoning_only`；`evalbench/core/runner.py::_parse_answer()` 处理回退 | 非流式探测返回 `content` 有正文、`reasoning_content` 为空或显著变短 | ✅ 已有 |
| ③ `usage.completion_tokens_details.reasoning_tokens` | `test_llm()` 已读取并返回 `reasoning_tokens` | 与 `off` 基线对照，该值下降或为 0/不存在 | ✅ **已实测**：网关确实上报，基线 55 → `thinking=disabled` 时 0，且 `reasoning_content` 消失 |

**判定纪律**：三个观测点里 ②③ 可先做、① 需补埋点；**任一项显示「无变化」即判未生效并回退**，不得用「延迟这次碰巧快了」当生效证据（冷启动抖动 0.6/9.6/58 s 已被实测记录，单次对比无统计意义，需多次采样）。

**不生效时如何优雅降级（不假装用了）**：
1. 撤回该推理字段，保持 `off` 原链路；
2. 设置页 / 日志标 `reasoning_mode_effective="unsupported"` + 原因（参数被拒 / 被忽略 / 探测不可用 / 额度不足）；
3. 继续沿用 `.env:24-25` 与 `evaluation-report.md` §5 里既有的诚实话术：知道这个模型慢、知道为什么慢、知道什么时候该换（`minimax-m3` 约 1.3 s 无 reasoning，同网关）；
4. 不改变任何既有降级标记语义。

## 2.8 与 `max_tokens` / `temperature` / `stream_interrupted` / `empty_model_output` 的相互影响

| 参数 / 标记 | 现状（实查） | 与低思考档的相互作用 | 落地注意 |
|---|---|---|---|
| `max_tokens` | 流式 `_stream_raw()` **不传** `max_tokens`（走网关默认）；`complete()` 默认 256、`test_llm()` 用 256；`evalbench/core/runner.py` 要求 `>=512`（`require_min_max_tokens_512`） | 低思考档若同时收紧 `max_tokens`，会重现「正文被吃光」；而 `probe_models.py` 实测表明**小预算空正文是网关侧共性、非推理模型专属**（6 个模型 `max_tokens=24` 全部空） | 不为「快」而压低 token 预算；如需显式预算，遵循既有 `>=512` 口径，并把归因写成「网关预算行为」而非「推理模型」 |
| `temperature` | 流式硬编码 `0.3`；`complete()` 可传（默认 `0.0`）；`test_llm()` 不传；`probe_models.py` 实测 `temperature=0` / `top_p=1` 在 6 个模型被网关接受 | 推理强度与采样随机性是**两个正交维度**，低思考不应靠改 `temperature` 实现；对照探测须固定 `temperature`，否则延迟/内容差异无法归因 | 探测时固定同一 `temperature`；不要把「降温度」当成低思考 |
| `stream_interrupted` | `rag.py::stream_answer()`：已吐正文后断流只在尾部标记中断，**绝不把降级文案拼到真答案后面** | 低思考档降低首 token 延迟，可能改变断流发生时机；标记语义不得因档位切换而放宽 | 保持原逻辑；新增档位不得绕过该标记 |
| `empty_model_output` | `rag.py::stream_answer()`：模型正常收尾却零正文 → 用检索片段兜底并留痕（对应 `CHANGELOG.md:88` 已修问题） | 低思考档 + 过小 `max_tokens` 仍可能零正文；此标记是最后一道兜底，**必须保留且优先于「档位已生效」的宣称** | 只要触发 `empty_model_output`，本次即不得记为「低思考档成功」；先查预算与参数，再谈档位 |
| `reasoning_only` | `test_llm()` 返回；`probe_models.py` 实测「本轮未复现」 | 探测里的辅助信号，不是生效判据 | 不得把「未复现 `reasoning_only`」反推为「低思考已生效」 |

## 2.9 不编造清单（本部分的硬约束）

| 不做什么 | 为什么 |
|---|---|
| 不写「Discovery 网关支持 `reasoning_effort` / `enable_thinking`」 | 这两类参数从未在本网关实测；只有 `thinking` 对象字段经 §2.5 对照实测生效 |
| 不写「元数据里列了布尔 `reasoning` 参数所以它可用」 | 实测 `reasoning=false/true` 都是 `reasoning_tokens=55`，不生效；这个反面例子本身就是面试可讲的点 |
| 不把「零正文」断言为推理模型专属 | `evalbench/probe_models.py::DIFFERENCES` 明确修正为**网关侧 token 预算共性**（6 模型含非推理型全部空） |
| 不把「代码里写了」写成「已启用」 | 配置/字段存在 ≠ 参数被接受 ≠ 观测到生效 |
| 不编造价格、配额、模型能力或延迟数字 | 本文所有数字均引自 `docs/evaluation-report.md`、`.env`、`evalbench/probe_models.py` 的既有实测，未新增测量 |
| 不把「参数生效」写成「质量或成本结论」 | §2.5 是 4 次轻量对照调用，未做语料集评测、未做压测、单价与配额仍 `__TBC__` |

---

## 附录 A · 事实源索引（全部为项目内实读）

| 主题 | 文件 / 位置 |
|---|---|
| 前中后置七节点编排 | `backend/app/agent/orchestrator.py`：`INJECTION_PATTERNS`、`PII_PATTERNS`、`guard_node`、`permission_filter_node`、`verify_node`、`persist_node`、`DEFAULT_ORDER` |
| 权限纯函数与召回复核 | `backend/app/services/permission.py::decide()` / `filter_candidates()`；`backend/app/services/search.py` |
| 后端 LLM 与已有 thinking 字段 | `backend/app/integrations/llm.py:44-54`、`RAG_SYSTEM_PROMPT`、`build_rag_messages()`；`backend/app/core/config.py:69` |
| webapp 前端门禁 / 限流 | `webapp/deps.py`、`webapp/main.py::rate_limit_gate`、`webapp/observability.py::check_budget()/record_usage()/PLUGINS` |
| webapp 流式问答与检索 | `webapp/routers_chat.py`、`webapp/rag.py`（`_load_ready_chunks()` / `retrieve()` / `stream_answer()` / `search_debug()`）、`webapp/providers.py`（`OpenAILikeLLMProvider` / `test_llm()` / `classify_429()` / `_filter_inline_think()`） |
| 配置与命名空间 | `webapp/settings.py`（`DEFAULTS`/`ALLOWED`/`ENUMS`/`effective_embedding()`）、`.env`（`WEBAPP_` 前缀约定、`DISCOVERY_*`） |
| 既有实测与降级矩阵 | `docs/evaluation-report.md` §1 / §5 / §6 / §9 / §10；`docs/enterprise-readiness.md` §4 / §5 / §6 / §10；`docs/kb-capability-checklist.md` F5；`docs/CHANGELOG.md:88/90` |
| 推理预算与延迟实测 | `evalbench/probe_models.py::DIFFERENCES`（`max_tokens` 与归因修正）、`evalbench/core/runner.py`（`>=512` 适配、`_parse_answer()`） |
| 推理开关参数对照（2026-11-14） | 本文件 §2.5；基线 / `thinking=disabled` / `reasoning=false` / `reasoning=true` 四组同题对照 |
| 网关视觉能力（2026-11-14） | `parsing/vision_probe.py`、`docs/parsing/07-api-contracts.md` §1–§2：10 个模型中 6 个支持 image，双图对照传图实测通过 |

## 附录 B · 与本文档相关的其他在办任务（不越界，仅标注）

- 任务 #12「落地 DeepSeek V4.1 Flash 低思考快速档」属**代码实现**，由主理人推进；本文只提供口径与判定标准，不含代码改动。
- 任务 #19「三维度限流 + 权限安全复核」、#13「成本与限流统计维度改 department」与本文前置/后置缺口有交集，相关口径以主理人汇编为准。
- 本文缺口台账中的 webapp 中段缺口（G3/G6/G7）与 Phase 2 检索链路专项设计中的「列表侧逐条 `decide()` 复核」「先过滤后算/后写」是同一件事的不同表述，不重复计工。
