# webapp · 可运行演示切片（拿来即可演示）

**定位**：一个零依赖、可直接打开界面的完整功能切片——离线档可跑，默认已接真实外部模型
（生成走 Intern-AI Discovery 网关的 DeepSeek，嵌入与重排走硅基流动 BGE 系列），
限流/故障时自动降级并留痕。它是生产架构的「可演示替身」，
**不是上线版，也不替代 `backend/` 骨架**。契约与
`docs/next-version-messaging-and-plugins.md` 一致，便于面试时讲清「哪一层做了、哪一层只设计」。

> 真实外部模型下的完整评估实测见 **`docs/evaluation-report.md`**（面试讲解版；
> 事实源 `webapp/out/eval_report.json`，五套回归全绿）。

| 演示切片 | 生产架构（v0.1 骨架 / v0.2 方案） |
|---|---|
| **PostgreSQL（唯一真值）** | PostgreSQL（`ingest_jobs` / 审计台账） |
| 进程内信号量队列 | Celery + Redis broker → v0.2 RabbitMQ |
| 词法门禁 + 外部 BGE 向量 + cross-encoder 重排（三档可切） | PG 全文 + Milvus 向量 + RRF 融合 |
| 真实 DeepSeek SSE 流式（限流/故障回退离线占位） | DeepSeek V4.1 Flash SSE 流式 |
| 无构建静态前端 | React + Vite |

---

## 1. 启动（三步：起库 → 装依赖 → 起服务）

> **SQLite 已从运行时移除**：`webapp/` 现在必须连真 PostgreSQL。这不是可选优化，
> 是 2026-11-15 的架构变更——理由与代价见 `docs/production/01-data-migration.md` §12。

```bash
# 1) 起 PostgreSQL（本机 5432 若被占用，用 KB_PG_PORT 换端口）
KB_PG_PORT=55432 docker compose -f docker-compose.infra.yml up -d postgres

# 2) 装依赖（多了 psycopg）
python -m pip install fastapi uvicorn python-multipart "psycopg[binary]"

# 3) 起服务。DSN 解析顺序：环境变量 → 项目根 .env → 标准 5432 默认值
#    本机（5432 被别的项目占用）把下面这行写进 .env 即可，不必每次 export：
#      WEBAPP_DATABASE_URL=postgresql://kb:kb_demo_only@127.0.0.1:55432/kb
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。首次启动会用 `scripts/pg_init.sql` 建表并种入演示数据
（幂等，重启不会重复插）。
想**一键看到含管理后台的全貌**，直接打开 `http://127.0.0.1:8765/?demo=admin`（自动登录演示管理员，
仅本地演示切片使用，生产环境必须移除该分支）。

| 账号 | 口令 | 能看到什么 |
|---|---|---|
| `admin` | `admin123` | 全部功能 + **管理后台**（badcase 查看/导出/完成度） |
| 自行注册 | 自定 | 文档管理、流式问答、badcase **采集**（看不到管理后台） |

自测：`python webapp/selftest.py`（33 项端到端检查，报告写 `webapp/out/selftest_report.json`）。
API 文档：`http://127.0.0.1:8765/api-docs`（共 36 条 API，含 12 条 `/api/evaltest/*`）。

> **外观**：顶栏可切换中文 / English、6 套主题色、浅色 / 深色模式，三者独立持久化、刷新保留。
> **评估测试子页**：评估配置、运行记录、指标、日志追踪、按项目/部门/调用维度统计 token 与成本、
> 预算上限与超限处理、限流配额与插件边界；规则与验收标准见 `docs/enterprise-readiness.md`。

---

## 2. 功能清单（对照需求逐项）

| 需求 | 落点 |
|---|---|
| 注册 / 登录账户 | `/api/auth/register|login|me`，pbkdf2 口令哈希 + HMAC JWT，令牌失效自动退回登录页 |
| 文档上传与管理 | 点击/拖拽多文件上传、列表、删除、状态轮询、一键示例说明书 |
| Chat 问答 + 流式输出 | SSE（fetch stream）逐 token 渲染，回车发送，会话状态面板 |
| 智能问答来源追溯 | 每条回答下方展示「引用来源」卡片（文档/分块序号/得分/检索方式），落 `chat_sources` 表；可经 `/api/chat/sources`、`/conversations`、`/history` 追溯与回放历史会话来源 |
| badcase 采集与持久化 | 每条助手回答下「标记为 badcase」→ 落 `badcases` 表 |
| 字段齐全 | `question` 原始问题 / `answer` 模型回答 / `verdict` 判定 / `issue_desc` 问题说明 / `source` 来源 / `created_at` 时间 / `eval_task_id` 关联任务（+ `status`、`created_by`） |
| 按任务 / 时间 / 状态筛选与检索 | `/api/badcases` 支持 `eval_task_id`、`status`、`source`、`start`/`end`、`q`（问题/回答/说明模糊检索） |
| 采集—保存—查看—导出闭环 | 问答页采集 → 库表保存 → admin 表格查看 → CSV/JSON **按当前筛选**下载 |
| admin 按条件下载 | 「下载 CSV / JSON（当前筛选）」按钮，导出与列表共用同一套筛选参数 |
| 评估任务完成度 | 卡片展示任务总数 / 已完成 / 待处理 / 完成率 / 状态，另有四项总览 |
| badcase 与任务关联一致 | 创建时校验 `eval_task_id` 存在（不存在直接 400）；完成度由该任务关联的 badcase 计数派生，不会出现「记录查不到但进度涨了」 |
| 入口清晰 | admin 登录后侧栏出现「管理后台」；普通用户完全不可见、接口也返回 403 |

**完成度口径**（界面内也标注了）：一条关联该任务的 badcase = 已评测一个样本；
已完成 = 关联记录数，待处理 = `max(0, 目标数 − 已完成)`，完成率 = 已完成 / 目标数。

---

## 2.1 侧栏导航：11 项 / 3 组（对照生产骨架 README §4）

原先只有 3 个入口；补齐的依据不是「看起来像企业级」，而是**项目文档里已经写好的模块**
（`README.md` §4 的看板 / 任务 / 审计 / 组织 / 检索 / 版本，`docs/` 的上传状态机、
评测对象、权限、运维、Agent 扩展）。每个入口要么读真实数据，要么明确标注「只设计」。

| 分组 | 入口 | 数据来源 | 状态 | 面试讲解要点 |
|---|---|---|---|---|
| 工作台 | 工作台概览 | `GET /api/overview` | 已实现 | 按角色聚合：普通用户统计强制带 `owner_id/user_id/created_by`，**不是把全局数字藏起来而是根本不查** |
| 工作台 | 智能问答 | SSE 流式 | 已实现 | 断线续传、检索为空拒答、占位生成明确标注；**来源可追溯**：回答下方展示引用来源卡片（文档/分块/得分/方式），点开看原文，历史会话可回放来源 |
| 工作台 | 文档管理 | `/api/docs` | 已实现 | 繁忙排队、格式降级留痕 |
| 工作台 | 检索调试 | `GET /api/search/preview` | 已实现（演示级） | **先滤权限再召回、再重排、再进模型**；切片只有 `status='ready'` 一层前置条件，Rerank 标「未启用」 |
| 质量与治理 | 任务中心 | `GET /api/system/queue` + `/api/system/busy` + `/api/docs` | 已实现 | `queued→analyzing→ready/error` 状态机；全局队列按**用户/部门**聚合（普通用户看部门+短码+计数，文件名明细仅 admin）；生产还有批次/页级 chord 与 SSE 权威快照 |
| 质量与治理 | 评测中心 | `GET /api/eval/objects`（直读 `eval/config/registry.json`）+ 完成度 | 数据真实 / 质量结论不可比 | 14 个版本化对象、禁原地改、变体只在内存生效；`comparable=false`、`NOT_COMPARABLE`、真实 API 仍 `__TBC__` |
| 质量与治理 | **评估测试** | `GET /api/evaltest/*` + `evalbench/runs/` 留存 | 已实现（演示级） | 评估配置 / 运行记录 / 指标 / 日志追踪 / token 与成本按项目·部门·调用维度聚合 / 预算上限与超限（全局 + 按用户覆盖）/ **三维业务配额（user / endpoint / model）+ IP 接入层防刷** / 插件边界；只读真实数据，写接口仅 admin |
| 质量与治理 | 管理后台 | badcase + 完成度 | 已实现（仅 admin） | 采集与查看权限分离，导出与列表共用筛选 |
| 质量与治理 | 审计日志 | `GET /api/admin/audit` | 已实现（演示级） | 只追加台账，可按动作/人回放「谁在什么时候做了什么」；**未接** `trace_id`、留存策略与脱敏 |
| 系统 | 模型设置（仅 admin） | `GET/PUT /api/settings` + `/api/settings/test` | 已实现 | LLM 本地/外部/离线可切、Bge M3 向量、Rerank 可配（不可用优雅跳过）；配置落 PostgreSQL 的 `app_settings`、**即时生效** |
| 系统 | 系统设置与边界 | `/api/system/busy` + `/api/admin/users` + 静态映射 | 混合 | 组件替身映射表、A/B/C 档、**明确不做**清单——面试时别把 A 档说成 C 档 |

**权限**：`管理后台`、`审计日志`、`模型设置` 仅 admin 可见，接口同样 403；`系统设置与边界` 普通用户可见，
但成员列表只显示「仅管理员可见」。**普通用户 8 项、admin 11 项**（较上版新增「评估测试」，只读对所有登录用户开放，写接口仍仅 admin）。

**国际化与主题**：`static/i18n.js`（中文原文即 key 的 632 条字典 + DOM 自动扫描 + MutationObserver，
切换无刷新、可逆、表单提交值不变）× `static/theme.js`（6 套主题色与明/暗模式正交、三份独立
localStorage、head 同步防闪烁）× `static/style.css`（CSS 变量分层、暗色高对比状态色、响应式与
`focus-visible` / `prefers-reduced-motion`）。详见 `docs/enterprise-readiness.md` §7。

**故意没加的入口**（列出来本身就是谈资，避免把导航做成空壳）：

| 常见企业级入口 | 为什么这版不做 |
|---|---|
| 知识库/空间管理、分类标签树、文档版本 diff、回收站 | 生产骨架有 API 设计，但切片是单库单组织；做了只会是假数据或空壳 |
| 权限编辑、部门树、角色授权 | 只做只读成员列表；写授权会引出完整审批与审计闭环，超出演示范围 |
| 监控告警大盘、trace 瀑布图 | P0/P1 设计，无真实指标源；画假曲线不如明确标「未做」 |
| 渠道入口（飞书/企微）、MCP 工具网关 | P2 扩展，边界讲清楚即可，不做假入口 |
| 消息中心、个人设置、帮助中心 | 通用后台装饰，与知识库核心证据链无关 |

---

## 2.2 模型接入（本地 / 外部 / 离线）、检索与测试

**适配器 seam**：`webapp/providers.py` 定义 `LLMProvider` / `EmbeddingProvider` 接口 +
`OfflineLLMProvider`（占位）、`OpenAILikeLLMProvider`（OpenAI 兼容，外部与本地共用）、
`OpenAILikeEmbeddingProvider`（通用 `/v1/embeddings`，本地 LM Studio 与硅基流动共用；
`LocalEmbeddingProvider` 保留为兼容别名）。**切换由「模型设置」页写入
PostgreSQL 的 `app_settings`，即时生效、无需重启**，不动检索 / 权限 / 评测逻辑。

**当前默认（真实外部模型全链路，2026-11-11 实测）**：

| 组件 | 档位 | 模型 | 端点 |
|---|---|---|---|
| 生成 | `discovery` | `deepseek-v4.1-flash-1017` | Intern-AI Discovery 网关 |
| 嵌入 | `external` | `BAAI/bge-m3`（1024 维 / 435 ms） | 硅基流动 `/v1/embeddings` |
| 重排 | `external` | `BAAI/bge-reranker-v2-m3`（205 ms） | 硅基流动 `/v1/rerank` |

嵌入与重排共用同一账号同一把 key。**配置命名空间已隔离**：provider 开关一律用
`WEBAPP_` 前缀（`WEBAPP_LLM_PROVIDER` / `WEBAPP_EMBEDDING_PROVIDER` / `WEBAPP_RERANK_PROVIDER`）——
因为 `backend/` 骨架对 `EMBEDDING_PROVIDER` 的枚举是 `stub|openai_compatible`，
与切片的 `offline|local|external` 互斥，共用扁平命名会让 backend 启动校验失败。
`settings.effective_embedding()` 是「端点 / 模型 / key / **缓存命名空间**」唯一事实源：
`cache_key` 带 provider + 模型名，切模型不会复用旧向量（否则维度不一致时余弦会静默归零）。

- **LLM 四选一**：外部 A（Sensenova 代理 DeepSeek）/ 外部 B（Intern-AI Discovery 网关，同 key 多模型）/
  本地 LM Studio（`http://127.0.0.1:1234/v1`）/ 离线占位。外部密钥只在 `.env`，
  不落库、不下发前端；两个外部供应商互为备份（一家限流就切另一家）。
- **Discovery 网关实测（同一 RAG 上下文、`max_tokens=512`）**：
  `minimax-m3` 1.3s（无 reasoning，最快）/ `kimi-k2.6` 2.9s / `glm-5.3` 6.5s /
  `deepseek-v4.1-flash-1017` 9.6s（会先推理，冷启动可达 13–58s，热态 0.5s）/
  `deepseek-v4-flash-0731` 22.7s / `qwen3.8-27b` 32s / `intern-s2` 40.7s 且正文为空（token 全给 thinking）。
  **注意**：`deepseek-v4.1-flash-1017` 是推理模型，`max_tokens` 给小了（如 24）正文会是空的——
  连接测试因此改为 256 并在响应里标注 `reasoning_only`。另外该网关延迟抖动很大
  （同一问题实测 12s / 15s / 63.5s），演示建议用 `minimax-m3`；
  但 `minimax-m3` 会把思考内联成 `<mm:think>…</mm:think>` 塞进 content，
  已加流式过滤器剥掉（跨 chunk 切分的标签也能处理），否则用户会看到一堆英文思考。
- **本地模型实测（同一 RAG 问题、同一上下文）**：
  `qwen/qwen3-vl-8b`（Q4_K_M，8B）可用，36.5s / 131 chunks、答案带「参考 1 · wifi.txt」引用；
  `qwen2.5-coder-7b-instruct` 11.9–15.2s / 74–102 chunks，**快约 2.5 倍**，演示首选。
  `qwen/qwen3-8b` 属推理型：先吐 199 个 `reasoning_content` chunk 才出正文，实测 39s，
  不推荐做在线问答。**Qwen3-VL 的视觉能力在本机 LM Studio 不可用**——`/api/v0/models` 的
  `capabilities` 只有 `tool_use`（无 `vision`），传图会 400（`data:` URL 直接让引擎 terminated，
  裸 base64 报 `Invalid url`），所以切片仍是纯文本问答。
- **Embedding 三档**：`offline`（纯词法）/ `local`（LM Studio `text-embedding-bge-m3`，1024 维）/
  `external`（硅基流动 `BAAI/bge-m3`，1024 维，**当前默认**）。检索是
  **词法门禁 + 向量排序**（hybrid）——先用二元组门禁保证不相关不硬凑（保住「检索为空拒答」
  的边界），再按余弦排序。向量按 `(chunk_id, cache_key)` 缓存于 `chunk_embeddings`，
  `cache_key` 含 provider + 模型名，切模型天然隔离（见上文命名空间说明）。
- **Rerank 三档**：`off` / `llm`（用当前 LLM 给候选打分重排，LLM-as-reranker，无需额外服务）/
  `external`（Cohere 风格 `/rerank`，自动兼容 TEI 的 `texts` 形态）。
  LM Studio 本体不暴露 `/v1/rerank`（它把 reranker 归类为 embeddings），所以没有 local 档；
  不可用时统一优雅跳过（`llm_unavailable` / `external_unavailable`），不假装用了。
  **`external` 已用硅基流动 `BAAI/bge-reranker-v2-m3` 实测跑通**：真相关文档 0.9316 排第一、
  仅误含关键词的干扰文档 0.0160 被压到末位（同一 query 下 LLM 打分为 9.0 / 2.0，区分度明显更弱）；
  空 key 与错误 key 均返回 `None` 走跳过，不抛错。密钥只读 `.env` 的 `RERANK_API_KEY`
  （嵌入可用 `WEBAPP_EMBEDDING_API_KEY` 单独指定），不落库、不下发前端。
  **本轮真实配置复测**（见 `docs/evaluation-report.md`）：真相关 0.9686 / 关键词干扰 0.0168 /
  无关 0.0009，`rerank=applied:external`，区分度明显优于单用余弦（0.7296 vs 0.6023）。
- **模型报错优雅降级**：5xx / 超时，提供者内部退避重试 3 次；仍失败则
  `rag.stream_answer` 捕获异常，不改 SSE 流、不丢本次问答，回退「本地拼接回答」并标记
  `degraded=model_error`。**已经吐出正文后才中断**的情形单独标 `stream_interrupted`，
  只提示「以上为已收到的部分内容」，**绝不把降级文案拼在真答案后面**。
  兼容性：部分网关（如 Discovery）会在结尾发 `choices: []` 的纯 usage 块，
  流式解析已跳过空 `choices`，不会因此把正常回答误判成 `model_error`。
  **嵌入不可用**同理回退词法排序（`retrieval_mode=lexical(fallback)`）。
- **限流与配额（按 TokenPlan 口径分开处理）**：网关限制 RPM 50（生成模型）/ TPM 2,000,000 /
  5 小时·7 天墨点额度，同一用户的全部 API Key 合并计算。**两种 429 必须区别对待**：

  | 错误码 | 含义 | 我们的处理 |
  |---|---|---|
  | `rate_limit_exceeded`（Anthropic 侧 `rate_limit_error`） | 频率超了，等会儿能好 | 指数退避 **1s / 2s / 4s + 随机抖动**，有 `Retry-After` 就听它（上限 30s），最多 3 次 |
  | `quota_exceeded` | 墨点/额度用尽 | **不重试**，直接抛 `quota_exceeded` → 降级为本地拼接，`degrade_reason=quota_exceeded`，前端提示换模型或换供应商 |

  两条硬约束：**流式一旦开始吐字就不再重试**（否则用户会看到重复内容）；
  抖动是为了避免限流恢复瞬间多个请求同时打过去（惊群）。实现见
  `providers.classify_429()` / `providers.backoff_seconds()`，各配单测锁定。
- **空输出降级**：模型正常收尾但一个正文 token 都没给（推理模型只吐 `reasoning_content`、
  或 `max_tokens` 被推理吃光）时，不再返回空白气泡，而是标记 `degraded=empty_model_output`
  并用检索片段兜底；前端提示「换非推理模型」。
- **中文检索（离线兜底）**：词法用英文整词 + 中文相邻二元组，让「配网」这类短语可命中。

**测试**：

- `webapp/selftest.py`：33 项端到端（真实 Discovery LLM + 硅基流动嵌入/重排下运行，限流走降级仍全绿）；
- `webapp/eval_report.py`：可复跑的真实链路评估采集，结构化事实源写 `webapp/out/eval_report.json`；
- `webapp/tests/`（pytest，隔离库 + 强制离线）：覆盖适配层、429 重试、LLM 选择、设置持久化、
  检索为空 / 嵌入不可用 / 模型报错三种降级、rerank 跳过、续传连续、chat 路由、鉴权、badcase 闭环，
  **以及来源追溯**（SSE 携带来源、落 `chat_sources`、三个回放接口取回）；
- `webapp/live_smoke.py`：真实端点冒烟，429 时打印 WARNING 并跳过，不阻塞。

---

## 3. 降级与断线恢复矩阵（本轮重点）

| 环节 | 触发 | 表现 | 恢复 |
|---|---|---|---|
| 文档格式 | 上传非纯文本（如 pdf） | 文档状态 `error` + 明确原因，不静默丢弃 | 用户换成 txt/md 重新上传 |
| 分析繁忙 | 并发上限 2，连传多个文件 | 顶部橙色横幅「处理中 N / 排队 M」、侧栏状态点变黄、文档行显示排队进度 | 队列自然排空，横幅自动消失 |
| 检索为空 | 知识库无命中 | `degraded=true` + 拒答话术，明确「没有找到」，不编造 | 上传文档后重问 |
| 嵌入不可用 | 选了 `local`/`external` 但端点不可达 | `retrieval_mode=lexical(fallback)`，回退词法排序并留痕 | 端点恢复后自动用回向量排序 |
| Rerank 不可用 | 选 `external` 但端点不支持，或选 `llm` 而当前 LLM 为离线 | `*_unavailable(skipped)`，跳过不报错 | 换支持 rerank 的服务，或把 LLM 切到本地/外部 |
| 模型空输出 | 推理模型只吐 `reasoning_content`，或 `max_tokens` 被推理吃光 | `degraded=empty_model_output` + 检索片段兜底，不出空白气泡 | 换成非推理模型（如 `qwen2.5-coder-7b-instruct`）重试 |
| 流式中断（已出正文） | 生成到一半连接断 / 网关报错 | `degrade_reason=stream_interrupted`，**保留已收到内容、不拼接降级文案** | 重新提问；已部分内容仍可标记 badcase |
| 频率超限（429 rate） | 超过 RPM / TPM | 1s/2s/4s + 抖动退避，最多 3 次；仍不行则 `model_error` | 自动恢复；频繁触发就该降并发或排队 |
| 额度耗尽（429 quota） | 墨点/额度用完 | **不重试**，`degrade_reason=quota_exceeded` + 本地拼接兜底 | 换模型/换供应商，或等额度重置 |
| 流中断 | SSE 中途断开 | 状态栏提示「正在续传（第 n 次）」 | 携带 `resume_from` 重连，服务端跳过已发 token，最多 3 次 |
| 重复落库 | 续传导致多次收尾 | 按 `conversation_id` 幂等，聊天日志只写一次 | 自动 |
| 后端不可达 | 服务停止 / 网络断 | 顶部红色横幅「连接中断，正在自动重连」 | 每 2.5s 探测，恢复后提示并自动刷新数据 |
| 令牌失效 | 过期 / 被登出 | 统一 401 拦截 | 退回登录页并提示 |
| LLM 调用失败 | 真实端点 429 / 超时 | `degraded=model_error` + 本地拼接回答，SSE 不中断、不丢问答 | 限流消退后重试即可恢复真实生成 |

> 设计口径：**所有降级都「留痕 + 可见」**，绝不静默失败；这与演示物料里 C-013~C-020 边界用例的「不静默」原则一致。

---

## 4. 对应插件方案（哪些落地了、哪些仍是设计）

| 插件方案条目 | 本切片状态 |
|---|---|
| 可观测（OTel / LangSmith 等价） | **演示级**：繁忙横幅、会话连接状态、降级原因、审计台账（8 类动作）；真实 trace/span 与按 trace_id 回放仍是 P0 设计 |
| 评测 CI 闸门 | **数据侧落地**：badcase 采集闭环 + 完成度看板；闸门接入 CI 仍未做，占位结果仍 `comparable=false` |
| 缓存与配额治理 | **演示级**：并发上限（配额）与 department 无关（切片单组织）；缓存跨部门串用的治理仍是 P1 设计 |
| Embedding | **已落地三档**：`offline` / `local`（LM Studio Bge M3）/ `external`（硅基流动 `BAAI/bge-m3`，当前默认）；1024 维，hybrid 检索排序；向量缓存 `chunk_embeddings` 按 provider + 模型隔离 |
| Rerank | **已落地三档**：`off` / `llm`（用当前 LLM 打分重排）/ `external`（硅基流动 `BAAI/bge-reranker-v2-m3`，实测 0.9316 vs 0.0160，区分度优于 LLM 打分）；LM Studio 本体不支持 `/rerank`，故无 local 档 |
| 中文分词 / ParserProvider | 未做：中文仍用二元组近似；解析器只支持纯文本 |
| RabbitMQ / Celery / Prometheus / MCP / 渠道入口 | 未做，见 `docs/next-version-messaging-and-plugins.md` |

**明确不做**：Milvus 真实向量库（外部 BGE 向量仍是替身）、压测、UI 框架、多租户行级权限、质量基线。
真实 LLM / 嵌入 / 重排均已接入；`eval/` 的占位对象与 `NOT_COMPARABLE` 口径**未改动**
（评测体系里真实服务字段仍全部 `__TBC__`，不推断填写）。

**限流这块的已知边界**：上游给的是 RPM 50 / TPM 2M 的**用户级共享配额**，而本切片是单进程，
**没有做全局并发闸/令牌桶**——只做了「单次请求的退避与不重试判断」。真上生产应在网关层按 RPM 限流、
把生成请求排队（Celery），并用队列长度而不是重试去吸收突发流量；这一条属于设计，不在切片里。

---

## 5. 面试 30 秒讲法

> 「我另外做了一个能直接打开跑的切片，把注册登录、上传排队、流式问答、badcase 采集
> 到管理员导出串成一条闭环。它用真 PostgreSQL 当真值、进程内队列替身 Celery，并已接真实 DeepSeek——
> 但**契约和生产架构一致**：LLM 走适配器 seam，检索/权限/评测逻辑一处没动，换掉替身就是真链路。
> 最想讲的是降级：检索为空会明确拒答不编造，模型限流会回退本地拼接而不崩，流断了带 resume_from 续传，
> 后端挂了前端自动重连并刷新，上传繁忙会显示排队位置。所有降级都可见、都留痕。
> 还能在设置页一键切 LLM（外部 DeepSeek / 本地 / 离线）、嵌入（硅基流动 / 本地 / 词法）、
> 重排（真 cross-encoder / LLM 打分 / 关），不支持的能力会明确跳过而不是假装用了。
> badcase 和完成度共用同一张关联表，创建时校验任务存在，所以不会出现进度和数据对不上。」

---

## 6. 自测结论

`python webapp/selftest.py` → **33 / 33 通过，`DEMO_READY`**（真实模型下运行，限流时走
降级仍全绿；覆盖登录鉴权、admin 门禁 403、繁忙排队、格式降级、流式与续传索引连续、
检索为空/模型报错两种降级、badcase 字段完整性、任务关联一致性、四种筛选、CSV/JSON 导出、
完成度前后一致、概览角色分级、统计权限分离、14 个评测对象与事实源一致、成员/审计/模型设置 admin 门禁、
十个视图与加载器双向齐全）。

`python -m pytest webapp/tests -p no:cacheprovider` → **51 / 51 通过**（隔离库 + 强制离线，
覆盖适配层、429 退避重试、LLM 选择、设置持久化/校验、检索为空/嵌入不可用/模型报错三种降级、
LLM 打分重排与外部 rerank 跳过、续传连续性、chat 路由、鉴权、badcase 闭环、来源追溯
SSE 携带来源 + 落库 + `/sources`/`/conversations`/`/history` 三个回放接口、
external 嵌入与缓存命名空间隔离，**以及本轮新增 10 项**：表结构/只读接口/写接口 403/
成本规则/用量范围隔离/预算 block 拒绝/离线运行留痕/429 响应头与台账/静态资源/完成率钳制）。

**回归快照**（2026-11-12，规则与验收标准详见 `docs/enterprise-readiness.md`）：
webapp pytest 51 / 51 · webapp e2e 33 / 33 `DEMO_READY` · demo 16 / 16
（`run_id=RUN-095a2a4a40c6` 未变） · eval unittest 14 OK · evalbench unittest 16 OK ·
backend pytest 16 / 16 · `node --check` 三个 JS 文件全通过。

> 历史快照（2026-11-11，真实外部配置下）见 `docs/evaluation-report.md`。
真实端点冒烟见 `webapp/live_smoke.py`（429 容忍）。
