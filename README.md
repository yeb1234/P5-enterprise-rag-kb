# 企业级知识库系统 · 开发框架骨架

React + FastAPI + PostgreSQL + Milvus + Redis/Celery + 外部 DeepSeek 的**可运行骨架**。
面向企业内部使用，目标是「作为逐步学习与企业级加固的基础」：每一层都留了明确扩展点，
每一处工程取舍都写了理由，同时把「做到哪、没做到哪」划清楚。

> 配套文档：`docs/async-upload.md`（大文件与批量上传，重点）、`docs/permission-model.md`、
> `docs/agent-extension.md`、`docs/operations.md`、`docs/learning-roadmap.md`、
> `docs/next-version-messaging-and-plugins.md`（v0.2 消息层职责重划与插件清单）、
> **`docs/evaluation-report.md`**（真实外部模型配置下的评估测试报告 · 面试讲解版）。
>
> **真实 API 评估工作台**：`evalbench/`（独立体系，多提示词/多参数/多模型对比、逐轮 Git 分支收敛）；
> 运行与结论见 `evalbench/README.md`，结果展示页 `evalbench/out/index.html`。
> 它**不修改** `eval/` 冻结对象，`eval/` 的 `__TBC__` / `NOT_COMPARABLE` 口径保持不变。
>
> **企业级就绪改造（限流 / 权限 / 成本 / 验收标准）**：`docs/enterprise-readiness.md`；
> **中型公司知识库能力清单（含来源核实与 Top10 建议）**：`docs/kb-capability-checklist.md`；
> **整体改进日志（版本坐标、改动清单、缺陷修正、验证快照）**：`docs/CHANGELOG.md`。
> 演示切片前端已支持中英文切换、6 套主题色 × 明/暗模式，并新增「评估测试」可观测子页。
>
> **当前组织口径（2026-11-14）**：单组织 + 多用户多部门，**暂不实现多租户**；多租户仅作为未来研发方向。
> webapp 演示切片的成本 / 限流统计维度已对齐为 `department`，backend 的 `tenant_id` / `TenantRepository`
> 是历史命名技术债（完整 13 步清理方案见 `docs/org-model-change-plan.md`，**方案已定、代码尚未改造**）。
> LLM 默认 `deepseek-v4.1-flash-1017`（Discovery 网关）+ **低思考 / 快速档**，可在「模型设置」页切换推理强度。
>
> **本轮复核产物（11 份 Markdown）**：改动总纲 `docs/change-manifest.md`（含最终状态回填）、
> 架构复核 `docs/architecture-review.md`、限流与安全 `docs/rate-limit-and-security-review.md`、
> 成本指标 `docs/cost-metrics-spec.md`、插件选型 `docs/plugin-selection.md`、
> 能力优先级 `docs/capability-priority.md`、前端检查 `docs/frontend-audit.md`、
> 验收报告 `docs/acceptance-report.md`、部署与腾讯云方案 `docs/deployment-guide.md`、
> Git 规范 `docs/git-conventions.md`、安全前中后置与低思考档 `docs/llm-safety-positions.md`。
> **独立面试演示页**：`interview/index.html`（单文件、零外部依赖，可直接双击打开）。
> 部署编排见 `docker-compose.yml`，提交前检查清单见 `docs/git-conventions.md`。
>
> **可插拔文档解析（2026-11-14 新增，重点）**：入口 `parsing/README.md`。
> 三级策略 `自研 P0 / 收费 OCR P1 / 多模态兜底 P2` 可在前端「解析实验室」切换，
> 质量门禁决定逐页升级、预算闸门把最贵链路限制在 10% 的页（上限/熔断线，非随机分流），
> 每页留下可核对的解析轨迹。原理资料 `docs/parsing/01–07`，交付话术 `docs/PARSING-DELIVERY.md`，
> 待补 API 清单 `docs/parsing/07-api-contracts.md`。
> **2026-11-14 实测纠正**：网关 `/models` 的 `input_modalities` 显示 10 个模型中 **6 个支持 image**，
> P2 多模态已接通 `deepseek-v4.1-flash-1017` 并经双图对照实测跑通；P1 收费 OCR 仍未接通、字段保持 `__TBC__`。
> `PARSING_OFFLINE=1` 可一键全离线，未接通或失败时优雅降级并标 `degraded`。见 `docs/parsing/07-api-contracts.md`。
> 独立解析服务：`docker compose --profile parsing up -d --build parser`（默认 11 服务不受影响）。
>
> **知识治理（2026-11-14 新增，面试高频）**：入口 `knowledge/README.md`，交付话术 `docs/KNOWLEDGE-DELIVERY.md`。
> 两块能力：**入库新鲜度**（时间衰减 `0.5^(年龄/半衰期)` + `valid_from` / `expires_at` / `scope` 适用性门控 +
> 过期三档处置 + 排序前后对照）与**事实冲突检测**（claim 级规则抽取、防误报、冲突图按簇裁决、
> `EvidenceRecord` 结构化证据）。前端「知识治理」视图可改策略并看到排序翻转与裁决日志；
> 内置合成场景 `DEMO-FRESHNESS-CONFLICT-001` 不写业务库、零外部调用。
> 调研 / 候选功能 / 面试问答 / 设计取舍见 `docs/knowledge/01–04`；23 项离线单测在 `knowledge/tests/`。
> 真实检索链的新鲜度重排**默认关闭**（`WEBAPP_FRESHNESS_ENABLED=1` 开启），生成层注入证据记录仍为只设计。
>
> **缓存与重启稳定性（2026-11-14 新增）**：入口 `cache/README.md`，交付话术 `docs/CACHE-DELIVERY.md`。
> 双层缓存 `L1 内存 LRU + L2 SQLite`、13 段缓存 key（含授权快照 / 数据版本 / 策略版本）、
> 懒失效（版本递增让旧 key 自然不命中）、TTL / 负缓存 / 抖动 / 容量淘汰；
> 启动自检 7 项（孤儿任务幂等重投、幽灵向量对账、过期缓存清理、策略从 `runtime_state` 恢复），
> 前端「缓存与恢复」视图可现场做缓存演示与模拟重启。调研与设计见 `docs/cache-recovery/01–02`，
> 19 项离线单测在 `cache/tests/`。**真·进程重启演练**（隔离库启停两次）结论见 `docs/CACHE-DELIVERY.md` §3。
> 检索结果缓存默认不接主问答链路；答案缓存 / 语义缓存仍未做。
>
> **通用开发规约（可复制到别的项目）**：`docs/HARNESS.md`（YEBSPEC@1.0，Y1–Y22 规则 + 最小文件骨架 + 交付前闸门）。

> **想直接看界面？** 另有一个「拿来即可演示」的可运行切片 `webapp/`：
> `python -m uvicorn webapp.main:app --port 8765`，浏览器打开 `http://127.0.0.1:8765`，
> 用 `admin / admin123` 登录即可看到完整功能。说明见 `webapp/README.md`。

---

## 0. 先说清楚：这是骨架，不是交付版

沿用你那份《全景设计、本周排期与交付档次》里的三档口径，本仓库落在 **A 档与 B 档之间**：
链路完整、接口可跑、约束渗透到了表结构与代码分层，但**没有压测、没有容灾演练、没有全量回归**。

| 能讲 | 不能讲 |
|---|---|
| 分片续传 + 批量聚合 + Celery 多阶段流水线 + SSE 层级进度 | 已验证的 2GB 大文件性能数据 |
| PG 全文/标签/属性 + Milvus 语义 + RRF 融合 + 权限双向过滤 | 召回准确率、MRR/NDCG 基线 |
| JWT 刷新轮换、RBAC、审计留痕、逐段落权限的架构预留 | 企业级安全合规（三道闸只有规则级） |
| 版本快照/行级 diff/回滚、看板与报表、跨库对账 | 高可用（单机 Compose，无主备切换实测） |
| Agent 节点编排 + 工具注册表 + 状态机接口 | 多 Agent 分流、MCP 网关已落地 |

**一句话边界**：这是「架构验证版」，不是「上线版」。上线需要补压测定型、越权用例集、
容灾演练、监控告警与长期稳定性——这些是团队的质量工作，不是一个人加班能替代的。

---

## 1. 技术栈与两处必须知道的取舍

### 1.1 DeepSeek 没有 Embedding API

你指定的「deepseek v4.1 flash」对应官方 model id 是 **`deepseek-flash`**（V4.1-Flash-1017，
需要更强推理可切 `deepseek-v4-pro`）。DeepSeek 的 OpenAI 兼容接口只有 chat/completions，
**不提供 /embeddings**。所以这套系统把「生成」与「向量化」拆成两个独立 Provider：

| 角色 | Provider | 配置 |
|---|---|---|
| 生成 / 问答 | `DeepSeekClient`（OpenAI 兼容 HTTP，含 SSE 流式） | `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_BASE_URL` |
| 向量化 | `OpenAICompatibleEmbedding` 或 `StubEmbeddingProvider` | `EMBEDDING_PROVIDER` / `EMBEDDING_BASE_URL` / `EMBEDDING_DIM` |

- 语义检索要接**任意外部 embedding 服务**（如 bge-m3 的 OpenAI 兼容端点），仍然「不部署本地模型」；
- 默认 `stub` 是确定性特征哈希伪向量，只反映字面重合度，**仅用于无密钥跑通链路，禁止用于效果评估**；
- 没有 API Key 时 LLM 自动降级为「抽取式回答（只回检索片段）」，骨架照样能演示全流程；
- 换 embedding 模型必须重建 Milvus collection 并全量重算向量，代码里已做维度校验并给出明确报错。

### 1.2 Celery broker 用 Redis 而不是 RabbitMQ

你的要求里「缓存与任务队列使用 Redis 与 Celery」，所以这里 Redis 兼任 broker + result + 缓存 + 进度 pub/sub。
少一个中间件，学习期排障成本低；代价是没有 RabbitMQ 的投递确认与死信语义。
补法写在 `app/tasks/celery_app.py`：`acks_late` + `reject_on_worker_lost` + `visibility_timeout`
+ **PG 任务台账是真值** + beat 定时捞回心跳超时的任务。

**v0.2 演进**：引入 RabbitMQ 不是「改一个 broker 配置项」，而是按「数据能不能丢」重划职责——
RabbitMQ 管不可丢的投递与死信，Redis 退回可重建的缓存/限流/进度，Celery 只管执行语义，
ack 时机、beat 职责与可观测口径都要跟着变。迁移触发条件与分阶段结论见
`docs/next-version-messaging-and-plugins.md`；当前骨架保持 Redis broker 是有意取舍。

### 1.3 相对设计文档的其他差异

| 项 | 设计文档 | 本骨架 | 原因 |
|---|---|---|---|
| 模型 | vLLM 本地推理 | 外部 DeepSeek | 按本次指定，不部署本地模型 |
| 消息中间件 | RabbitMQ | Redis | 按本次指定 |
| 解析 | MinerU + OCR + 多模态 | pypdf + 规则语义分块，接口已抽象 | 先固定页级元数据契约，换解析器不动下游 |
| 编排 | LangGraph 主链路 | 线性节点编排 + `Orchestrator` 协议 | 预留接口，避免骨架期绑死框架 |
| 范围 | RAG 问答引擎 | 知识库资源系统（CRUD/分类/版本/看板）+ RAG | 按本次功能清单 |

---

## 2. 分层结构

```
frontend/                  React 18 + TS + Vite + AntD + Recharts
  src/api/                 axios 封装、Bearer + 刷新轮换、全部端点
  src/hooks/useProgressStream.ts   fetch-SSE 进度订阅（含轮询降级）
  src/pages/               看板 / 文档 / 上传中心 / 检索 / 版本对比 / 任务中心
backend/
  app/core/                配置、JWT、错误码契约、日志、依赖注入
  app/db/                  Base/Mixin、Session 生命周期、租户强过滤 Repository
  app/models/              identity / knowledge / pipeline / metrics 四组模型
  app/schemas/             Pydantic 契约（统一错误与分页）
  app/api/v1/              11 组路由
  app/services/            permission / document / version / search / upload / progress / stats / parser / audit
  app/integrations/        Redis / MinIO / Milvus / Embedding / DeepSeek 适配器
  app/tasks/               Celery 配置、入库流水线、维护与对账任务
  app/agent/               State / NodeRegistry / ToolRegistry / LinearRAGOrchestrator
  app/middleware/          纯 ASGI trace 中间件、限流依赖
  migrations/              Alembic 基线（全文触发器、GIN 索引）
  tests/                   权限、进度、分块、融合纯函数用例
nginx/                     反代 + SSE 关缓冲 + 安全响应头
docker-compose.yml         PG / Redis / MinIO / etcd / Milvus / api×2 / worker×2 / beat / frontend / nginx
```

核心约束只有四条，其余都是这四条的下游结果：

1. `tenant_id` 只由依赖注入产生，业务代码不得从请求体读；所有查询走 `TenantRepository`。
2. 权限判定收敛到 `services/permission.py::decide()` 一个纯函数，接口/检索/Agent 工具全调它。
3. **先按权限过滤，再重排、再进模型**：Milvus expr 过滤 + 召回后 `filter_candidates` 二次过滤。
4. PG 是状态真值，Redis 只是实时通道；先写 PG 文本再写 Milvus 向量，删除走软删 + 异步清理 + 每日对账。

---

## 3. 快速启动

```bash
cp .env.example .env
# 至少填：DEEPSEEK_API_KEY（可留空走降级）、JWT_SECRET_KEY
# 语义检索要真实效果：EMBEDDING_PROVIDER=openai_compatible + EMBEDDING_BASE_URL/API_KEY
docker compose up -d --build

# 初始化表结构（api 容器启动不会自动迁移，显式执行更可控）
docker compose exec api alembic upgrade head

# 打开 http://localhost:8080
# 登录页点「初始化演示数据」→ 用 admin@demo.local / Admin@12345 登录
```

三个演示账号：`admin@demo.local`（tenant_admin）、`dev@demo.local`（研发部 dept_user）、
`viewer@demo.local`（市场部 viewer，只读）。用后两个账号登录可以直接验证部门隔离与只读拦截。

本地无 Docker 时也能学代码：`pip install -r backend/requirements.txt` 后
`STORAGE_BACKEND=local` 起 FastAPI，再用 `cd frontend && npm install && npm run dev`；
PG/Redis/Milvus 仍需可用，或用 testcontainers 起临时实例。

**验证顺序建议**：bootstrap → 建知识库/分类 → 批量上传几份 PDF → 看 SSE 进度走到 100%
→ 检索并展开引文 → 编辑文档生成 v2 → 版本 diff → 看板 → 任务中心。

---

## 4. 功能与接口一览

| 模块 | 主要端点 | 落点 |
|---|---|---|
| 认证 | `POST /auth/login|refresh|logout|bootstrap`、`GET /auth/me` | JWT + httpOnly 刷新 Cookie 轮换 |
| 组织 | `/users`、`/departments`、`/acl-grants` | 部门树物化路径、显式授权 |
| 知识库 | `/knowledge-bases` | 归属部门 + visibility |
| 分类标签 | `/categories/tree`、`/tags` | `path_ids` 数组 + GIN 索引，含子孙筛选 |
| 文档 | `GET/POST/PATCH/DELETE /documents`、`/download`、`/reindex`、`/restore` | JSONB 属性、PG 全文、软删三方联动 |
| 版本 | `/documents/{id}/versions`、`/compare`、`/{v}/restore` | 不可变快照 + 行级 diff + 追加式回滚 |
| 检索 | `POST /search`、`POST /search/answer`(SSE) | 双路召回 + RRF + 权限二次过滤 + 带引用生成 |
| **上传** | `/uploads/batches`、`/init`、`/{id}/chunks/{idx}`、`/complete`、`/batches/{id}/stream` | 见 `docs/async-upload.md` |
| 任务 | `/tasks`、`/{id}/retry`、`/{id}/cancel` | PG 台账、幂等重试 |
| 看板 | `/stats/overview|trend|report|export.csv` | 日报预聚合、审计导出 |
| 审计 | `/audit-logs` | 只追加、可按 trace_id 回放 |
| 健康 | `/health/live`、`/health/ready` | liveness / readiness 分离 |

统一响应契约：列表 `{items,total,page,page_size}`；错误 `{code,message,detail,trace_id}`。
`X-Trace-ID` 贯穿请求、任务、审计日志，用一个 id 能还原整条链路。

---

## 5. 重点：大文件与批量上传

一句话：**分片传 → 服务端合并 → Celery 按阶段并行处理 → PG 记真值 → Redis 推实时 → SSE 一条流看全批**。

- 单请求恒定 8MiB，2GB 文件不进 API 内存；MinIO `compose_object` 服务端拼接；
- 分片幂等、断点续传（`/status` 返回 `missing_chunks`）、整文件 SHA256 流式校验；
- 同租户同 checksum 秒传复用；大文件浏览器不整文件哈希，改由服务端流式计算权威 checksum；
- 批次内 3 文件 × 单文件 4 分片并发；>100MB 且 >200 页的文件走 `ingest_heavy` 队列，
  按 `PARSE_PAGE_BATCH` 拆页级子任务 `chord` 并行，子任务独立重试；
- 进度是 `batch(size 加权) → file → stage(权重) → unit` 四层；阶段切换/完成/失败强制落 PG，
  高频小事件按 0.5s 节流，Redis 实时推送不节流；
- SSE 建连先发 PG 权威快照（支持刷新页面 / `Last-Event-ID`），15s 心跳，
  nginx `proxy_buffering off` + `X-Accel-Buffering: no`；Redis 挂了自动降级 2s 轮询；
- 前端用 `fetch + ReadableStream` 而不是 `EventSource`，因为 EventSource 无法带 `Authorization`，
  把 JWT 放 query string 会进日志与浏览器历史。

完整时序、状态机、失败矩阵见 `docs/async-upload.md`。

---

## 6. 安全性 / 高可用 / 体验（当前做到哪）

**安全（骨架最小集）**：JWT 签名校验与刷新令牌轮换、bcrypt 密码、httpOnly Cookie、
文件后缀+扩展名+首片魔数三重校验、可执行/宏文件黑名单、租户配额、Redis 令牌桶限流、
统一权限函数、召回双重过滤、规则级注入/PII 拦截、审计只追加、CORS 白名单与安全响应头、
密钥全部来自环境变量（生产若仍是默认密钥直接拒绝启动）。

**高可用（设计有、未演练）**：API 无状态可多副本（`--workers 2` + nginx upstream）、
连接池探活、readiness 与 liveness 分离、任务幂等 + ack late + 心跳捞回 + 每日对账、
Milvus 故障降级为仅关键词检索、向量视为可重建的派生数据。
**没有** PG 流复制实测、主备切换 RTO/RPO、备份校验、告警通道。

**体验**：分片并发与实时进度条、断点续传、失败可重试、秒传提示、引文可展开到页码原文、
流式逐字回答、版本 diff 高亮、看板图表与 CSV 导出。

---

## 7. 后续加固路线（按性价比）

1. **P0 证据**：并发流式压测（P95 首 token）、20+ 条越权用例、解析质量基线、注入样本拦截率。
2. **P0 检索真实化**：接外部 embedding 服务 + zhparser 中文分词 + cross-encoder 重排，跑 RAGAS 基线。
3. **P1 安全**：三道闸模型级检测、PII 入库/召回/输出同规则、审计分区与归档、密钥轮换。
4. **P1 运维**：Prometheus + 告警、trace_id 全链路、备份恢复演练、灰度与回滚。
5. **P2 治理与扩展**：知识冲突/时效、飞书组织同步、MCP 工具网关、多 Agent 分流、逐段落 ACL 开关。
6. **P3 交付**：物理隔离租户选项、K8s 迁移评估、运维手册与 SLA。

详细到「每项验证方式」见 `docs/learning-roadmap.md`。

---

## 8. 已知限制（别踩）

- `stub` embedding 不是语义向量，效果评估必须换真实服务；换模型需重建 collection。
- 中文全文默认 `simple` + `pg_trgm`/ilike 兜底，真正的分词要装 zhparser。
- 解析用 pypdf，扫描件/双栏/表格/图表未做 OCR 与多模态兜底；换 MinerU 只需替换 `services/parser.py`。
- SSE 是同步生成器 + Redis pub/sub，长连接占用线程池线程；大规模并发应换 ASGI 原生流或独立推送服务。
- 逻辑隔离靠代码约束，不是数据库约束；一条漏写 `tenant_id` 的查询就是越权，生产必须配越权用例集。
- 基线迁移 `0001` 用 `metadata.create_all` 建表；**从 0002 起必须写显式迁移**，不要继续全量同步。
- 演示口令与默认密钥只用于本地学习。
