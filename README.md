# 企业级知识库系统（P5）

面向企业内部多部门的知识库与 RAG 问答系统。一条完整链路：**文档接入 → 解析分块 → 检索重排 → 带引用生成**，配套权限、审计、内容安全、缓存恢复、评测工作台与只读 MCP 出口。

系统按「可运行」标准组织：`webapp/` 是单进程即可演示的完整切片，`backend/` 是多副本服务端与 Celery 流水线，`frontend/` 是 React 管理端，三者共用同一套契约与数据库。

- 版本：**v1.4.0**（2026-11-27，合并版）
- 技术栈：FastAPI + PostgreSQL 16/pgvector + Redis 7 + Celery + MinIO + React 18
- 测试：**481 passed / 1 skipped / 0 failed**（12 个测试套件，2026-11-27 实测）

---

## 1. 能力概览

| 能力 | 落点 | 关键约束 |
|---|---|---|
| 文档接入 | 分片上传、断点续传、批次聚合、秒传去重 | 单请求恒定 8MiB，大文件不进 API 内存；PG 记任务真值 |
| 解析与分块 | `parsing/` 三级策略（自研 / 收费 OCR / 多模态兜底） | 质量门禁逐页升级，预算闸门限制最贵链路占比 |
| 检索 | 向量检索 + 可选图谱 / LLM Wiki 三路取证，RRF 融合后重排 | 默认只开向量；图谱/Wiki 各一个开关，单路失败只缺席、不拖垮主链路 |
| 问答 | SSE 流式输出、引用回溯到页码/段落、检索为空明确拒答 | 不编造；降级可见、留痕 |
| 知识治理 | 入库新鲜度（时间衰减 + 生效/过期窗口）、事实冲突检测与裁决 | 策略可切换，排序翻转与裁决可观测 |
| 内容安全 | 规则级 PII/敏感信息检测，覆盖入库、进模型、输出、日志导出四个出口 | 命中台账只存类别与数量，不存原文 |
| 缓存与恢复 | L1 内存 LRU + L2 PostgreSQL，13 段缓存 key，启动自检 7 项 | 版本递增做懒失效；TTL 不承担权限控制 |
| 组织与权限 | JWT 刷新轮换、RBAC、部门树、显式授权、审计只追加 | 权限判定收敛到单一 `decide()` 函数 |
| 评测 | `eval/` 离线契约体系 + `evalbench/` 真实 API 工作台 + 公开基准 | 冻结区零 diff，结果不可比就标 `NOT_COMPARABLE` |
| 对外接口 | 只读 MCP 网关（JSON-RPC 2.0） | P5 保有入库、权限与索引维护职责 |

---

## 2. 系统架构

```
                    ┌──────────────────────────────┐
   React 管理端 ───▶ │ nginx :8080                  │
                    └──────────────┬───────────────┘
                                   │
                 ┌─────────────────┴─────────────────┐
                 │ backend :8000（FastAPI，多副本）   │
                 │  ├─ 认证 / 组织 / 知识库 / 文档     │
                 │  ├─ 检索 / 问答(SSE) / 版本 / 审计  │
                 │  ├─ 上传批次 / 任务中心 / 看板      │
                 │  ├─ MCP 只读网关                    │
                 │  └─ Celery worker ×2 + beat         │
                 └───────┬───────────────┬─────────────┘
                         │               │
              PostgreSQL 16 + pgvector   Redis 7（缓存 / 队列 / 进度）
                         │               │
                    MinIO（对象存储）   Neo4j（可选，graphrag profile）
                                        Milvus（可选对照，compose profile）

  独立可运行面：
    webapp   :8765  单进程完整切片（登录 / 文档 / 流式问答 / 评测 / badcase / 安全）
    showcase :8767  解析工作台（左分析、右入库 JSON、参数可调）
```

核心设计约束：

1. **PG 是状态真值**，Redis 只是实时通道；先写文本再写向量，删除先清向量再软删文档。
2. **权限先行**：`permission.decide()` 是唯一权威；检索前过滤、召回后二次复核。
3. **DDL 单一事实源**：`scripts/pg_init.sql`，不在业务代码里建表。
4. **失败要可见**：解析、检索、模型、缓存各层降级都写状态与原因，不静默吞异常。

---

## 3. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | React 18 + TypeScript + Vite + AntD + Recharts | 管理端；演示切片内嵌静态页面 |
| 服务端 | FastAPI + Pydantic v2 + SQLAlchemy 2（只接连接池，不接 ORM） | 138 处 SQL 文本保留，迁移成本可控 |
| 数据库 | PostgreSQL 16 + pgvector | 向量列 `vector(1024)` + HNSW；Milvus 仅作对照 |
| 缓存/队列 | Redis 7 + Celery | Redis 兼任 broker、结果、缓存与进度 pub/sub |
| 存储 | MinIO / 本地目录 | `STORAGE_BACKEND` 切换 |
| 模型 | DeepSeek 兼容网关 + BGE-M3 嵌入 + BGE-reranker | 生成与向量化拆成两个 Provider；无密钥时降级为抽取式回答 |
| 可观测 | 结构化 JSON 日志 + trace_id 贯穿 | 响应头、错误体、日志同值 |

---

## 4. 快速开始

### 4.1 只跑 webapp（最快看到界面）

```bash
# 1) 起基础设施（PG 必须；Redis 可选）
docker compose -f docker-compose.infra.yml up -d postgres redis

# 2) 配置
cp .env.example .env
#    至少确认 WEBAPP_DATABASE_URL；本机 5432 被占用时改 KB_PG_PORT，
#    并同步 .env 里的端口

# 3) 起服务
pip install "fastapi>=0.115" "uvicorn>=0.30" "psycopg[binary]>=3.2" \
            "SQLAlchemy>=2.0,<2.1" "pypdf>=5.0" "Pillow>=10.0" "python-multipart>=0.0.9"
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`，用 `admin / admin123` 登录。首次可在登录页初始化演示数据。

也可以直接跑 `./run-demo.sh`：构建并启动 webapp（8765）与解析工作台（8767），日志上限、网络别名与健康检查都已写好。

### 4.2 起完整栈

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
# http://localhost:8080
# admin@demo.local / Admin@12345（另有 dev@demo.local、viewer@demo.local）
```

启用可选组件：

```bash
docker compose -f docker-compose.infra.yml --profile milvus up -d   # 对照用 Milvus
docker compose --profile parsing up -d --build parser               # 独立解析服务

# 可选：图谱检索（Neo4j 只在 graphrag profile 启动，默认不进主链路）
docker compose -f docker-compose.infra.yml --profile graphrag up -d neo4j
# 再在 .env 里按需打开：WEBAPP_RAG_ROUTE_GRAPH=on、WEBAPP_RAG_ROUTE_WIKI=on
# Wiki 另需指定业务场景 WEBAPP_WIKI_SCENARIO=<场景名>，未指定则不启动
```

---

## 5. 测试

全量测试要求 PostgreSQL 可用（SQLite 已移除）。测试每个进程使用独立 schema `kb_test_<uuid8>`，结束后自动 `DROP`，不污染业务库。

```bash
# 指向可用的 PG（示例；按本机端口改）
export WEBAPP_DATABASE_URL=postgresql://kb:kb_demo_only@127.0.0.1:5432/kb

# 逐目录跑（eval/ 与 evalbench/ 都有 tests/ 包，同一进程会重名冲突）
for d in webapp/tests backend/tests knowledge/tests cache/tests guardrails/tests \
         parsing/tests showcase/tests evalbench/tests evalfmt/tests eval/tests \
         kgraph/tests llmwiki/tests; do
  python -m pytest "$d" -q
done
```

2026-11-27 实测结果：

| 套件 | 通过 | 覆盖 |
|---|---:|---|
| `webapp/tests` | 197 | API、检索、流式、限流、安全、缓存、自适应重排、三路路由、MCP、日志、设置 |
| `backend/tests` | 38 | 权限、分块、进度、检索契约、答案质量 |
| `knowledge/tests` | 23 | 新鲜度衰减、冲突检测与裁决 |
| `cache/tests` | 19 | L1/L2、13 段 key、启动自检、重启恢复 |
| `guardrails/tests` | 34 | PII 规则、校验位、四出口链路 |
| `parsing/tests` | 22 | 页级元数据、质量门禁、预算闸门 |
| `showcase/tests` | 12 | 解析工作台契约 |
| `evalbench/tests` | 16 | 评估工作台指标与运行器 |
| `evalfmt/tests` | 22 | 格式化与冻结口径 |
| `eval/tests` | 13 + 1 skipped | 14 个评测对象与哈希稳定性；跳过项需先跑两次离线评测生成对比 run |
| `kgraph/tests` | 60 | 术语建图、Cypher 三道防护、多跳查询 |
| `llmwiki/tests` | 25 | 三道门禁、证据链、增量构建 |
| **合计** | **481** | 0 failed，1 skipped |

说明：`webapp/tests` 与 `cache/tests` 必须在 PG 可用时跑；Redis 相关用例使用内存后端，不依赖本机 Redis。

---

## 6. 评测与性能

### 6.1 公开基准（`evalbench/`，nDCG@10）

| 配置 | nDCG@10 |
|---|---:|
| BM25 基线 | 16.29% |
| bge-m3 向量 | 33.93% |
| bge-m3 + 重排 | **36.09%** |

三个负结果（前缀、融合、弱基线对照）保留在 `evalbench/iterations`，不删差数据。中文集补了 CMRC 2018，生成质量用 RAGTruth 与自建 span 级幻觉评测。

### 6.2 已落地的性能改造

| 版本 | 改造 | 实测 |
|---|---|---|
| v1.3.0 | 向量从外部库切到 pgvector + HNSW | 143 候选 69ms → **2.2ms** |
| v1.3.1 | SQLAlchemy Engine 连接池（只接池不接 ORM） | 端到端 18.59ms → **3.77ms** |
| v1.3.2 | stdout 单行 JSON + trace_id 贯穿 | 请求、任务、审计可用同一 id 还原 |
| v1.3.3 | 查询向量缓存（LRU 512 / TTL 1800s） | 同一问省 **325ms**，检索 p50 855 → 529ms |
| v1.3.4 | 自适应重排（`s1−s2 ≥ 0.05` 跳过 rerank） | 检索 mean **814 → 701ms（−14%）**，p95 −11%，R@5 −0.75~−1.0pt |

参数收敛结论：公开集采纳 `temperature=0.2`（A 组）；重排深度 20 仍最优。细节见 `docs/retrieval-param-convergence.md`、`docs/cmrc-chain-evaluation.md`、`docs/cmedqa-retrieval-evaluation.md`。

v1.4.0 把三路取证并入主链路：默认仍只开向量，图谱 / Wiki 需显式开；多路时按「多路命中优先 + RRF」融合，再走重排。设计、任务与接口见 `docs/kb-routes/01–05`。

---

## 7. 目录结构

```
backend/            FastAPI 服务端：core / db / models / schemas / api / services / integrations / tasks / agent
frontend/           React 管理端（Vite + AntD）
webapp/             单进程完整切片：路由、检索、提供商、缓存、评测、静态页面
parsing/            文档解析：页级元数据、质量门禁、预算闸门、多模态兜底
knowledge/          知识治理：新鲜度、事实冲突检测与裁决
kgraph/             图谱检索：术语共现建图、多跳查询、Text2Cypher 防护
llmwiki/            LLM Wiki：三道门禁、页面与 claims 证据链
guardrails/         内容安全：PII 规则、校验位、四出口脱敏
cache/              缓存与恢复：L1/L2、13 段 key、启动自检
eval/               离线评测契约（冻结区：对象、口径、结果不可比标记）
evalbench/          真实 API 评估工作台：多提示词/多参数/多模型，逐轮分支收敛
evalfmt/            评测结果格式化与对照
showcase/           解析工作台（独立页面 + 契约测试）
nginx/              反代、SSE 关缓冲、安全响应头
scripts/            DDL、初始化、运维脚本
docs/               设计、复核、交付与运维文档
demo/               冻结演示场景
```

---

## 8. 文档导航

| 主题 | 文档 |
|---|---|
| 版本与改动 | `docs/CHANGELOG.md`、`docs/change-manifest.md` |
| 架构复核 | `docs/architecture-review.md`、`docs/enterprise-readiness.md` |
| 上传与解析 | `docs/async-upload.md`、`docs/parsing/01–07` |
| 权限与安全 | `docs/permission-model.md`、`docs/rate-limit-and-security-review.md`、`docs/content-safety/01–02`、`docs/llm-safety-positions.md` |
| 知识治理 | `docs/knowledge/01–02`、`docs/knowledge/04-design.md` |
| 三路取证 | `docs/kb-routes/01–05` |
| 缓存与恢复 | `docs/cache-recovery/01–02` |
| 生产化 | `docs/production/00–08`、`docs/deployment-guide.md`、`docs/operations.md` |
| 评测 | `docs/evaluation-plan.md`、`docs/evaluation-report.md`、`docs/retrieval-param-convergence.md` |
| 对外接口 | `docs/mcp.md` |
| 工程规范 | `docs/HARNESS.md`、`docs/git-conventions.md` |
| 组织模型 | `docs/org-model-change-plan.md`、`docs/multi-user-hardening.md` |

---

## 9. 配置

配置全部走环境变量，模板见 `.env.example`。分组如下：

| 组 | 前缀 / 键 | 说明 |
|---|---|---|
| 应用 | `APP_NAME`、`ENV`、`DEBUG`、`LOG_LEVEL`、`CORS_ORIGINS` | 运行模式与日志 |
| 认证 | `JWT_SECRET_KEY`、`JWT_ALGORITHM`、`ACCESS_TOKEN_EXPIRE_MINUTES`、`REFRESH_TOKEN_EXPIRE_DAYS` | 生产环境禁止默认密钥启动 |
| 数据库 | `POSTGRES_*`、`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`WEBAPP_DATABASE_URL`、`WEBAPP_DB_SCHEMA` | pgvector 扩展需在建库镜像中预装 |
| 缓存/队列 | `REDIS_*`、`REDIS_CACHE_DB`、`REDIS_BROKER_DB`、`REDIS_RESULT_DB` | 多 DB 隔离 |
| 存储 | `STORAGE_BACKEND`、`MINIO_*`、`LOCAL_STORAGE_DIR` | MinIO 或本地 |
| 模型 | `DEEPSEEK_*`、`EMBEDDING_*`、`LLM_FALLBACK_TO_EXTRACTIVE` | 无 Key 时降级 |
| 上传 | `MAX_UPLOAD_SIZE_MB`、`UPLOAD_CHUNK_SIZE_MB`、`MAX_BATCH_FILES`、`MAX_CHUNK_CONCURRENCY` | 分片与并发 |
| 检索 | `WEBAPP_VECTOR_BACKEND`、`WEBAPP_RERANK_PROVIDER`、`WEBAPP_FRESHNESS_ENABLED`、`ADAPTIVE_RERANK_MARGIN` | 向量后端、重排、新鲜度开关 |
| 检索路由 | `WEBAPP_RAG_ROUTE_VECTOR/GRAPH/WIKI`、`WEBAPP_RAG_GRAPH_HOPS`、`WEBAPP_WIKI_SCENARIO` | 默认只开向量；图谱需 Neo4j `graphrag` profile |
| MCP | `MCP_ENABLED`、`MCP_SERVICE_KEY`、`MCP_TENANT_ID`、`MCP_USER_ID` | 默认关闭 |

---

## 10. 已知限制

- **组织口径**：单组织 + 多用户多部门；多租户只写方向，未实现。`backend/` 的 `tenant_id` 是历史命名，13 步改造方案在 `docs/org-model-change-plan.md`，代码尚未改。
- **没有部门级权限**：部门只用于成本/限流统计维度，授权仍靠角色与显式 ACL。
- **未做压测与容灾演练**：无 P95 首 token 基线、无主备切换 RTO/RPO、无告警通道；单机 Compose，非高可用部署。
- **解析边界**：pypdf 为主，扫描件/双栏/复杂表格未做 OCR 与多模态全量兜底；收费 OCR 未接通，字段保持 `__TBC__`。
- **Milvus 仅作对照**：默认主链路走 pgvector，Milvus 不进默认编排。
- **图谱 / Wiki 是演示规模**：图谱是术语共现图（非语义图谱）；Wiki 任务状态在进程内存，多副本 / 重启会丢；增量重建与跨项目 Neo4j 权限未做。详见 `docs/kb-routes/01-design.md`。
- **隔离强度**：多用户隔离靠代码约束与 schema 约定，不是数据库约束；漏写过滤条件就是越权风险，生产需配越权用例集。
- **演示口令**：`admin/admin123`、`admin@demo.local/Admin@12345` 仅用于本地；默认密钥与演示数据不得用于生产。

---

## 11. 版本

当前 **v1.4.0**（2026-11-27，合并版）：P5F 的检索性能 / MCP / 公开基准，与三路取证（向量 / 图谱 / LLM Wiki）汇合后的最终版本。版本坐标与逐项改动见 `docs/CHANGELOG.md`；性能与参数收敛的实测记录见 `docs/retrieval-param-convergence.md`。
