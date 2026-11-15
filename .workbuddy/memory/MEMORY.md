# 项目长期记忆 · P5 企业级知识库项目

## 定位与用户约定
企业级知识库系统的**开发框架骨架**，目标是「学习 + 面试讲解 + 逐步生产化」，不是上线交付版。
沿用 A/B/C 三档口径，**面试场上不要把 A 档说成 C 档**。

- 技术栈固定：React / FastAPI / PostgreSQL / Milvus / Redis + Celery / 外部 DeepSeek（不部署本地模型）。
- 用户正在学 FastAPI 与数据库，之后学 Java SpringBoot。代码要可读、注释解释**设计取舍**；
  文档必须区分「已实现 / 只设计 / 未做」，不夸大完成度。
- 架构必须预留：逐行/逐段精细权限、Agent 内部编排（LangGraph/多 Agent/MCP）、高可用加固。
- 重点功能：大文件与多文档批量上传的异步处理及进度反馈。

## 组织模型口径（2026-11-13 变更，覆盖历史表述）
单组织 + 多用户多部门。**多租户不做，只作为未来研发方向写入文档**。
- webapp 的成本/限流统计维度已从 `tenant` 对齐为 `department`（列名 `department_id`、维度名 `department`）。
- **webapp 没有部门级权限**；`backend/` 的 `tenant_id` / `TenantRepository` 保留为历史命名技术债。
- `docs/org-model-change-plan.md` 的 13 步仍是「方案已定、代码尚未改造」，目标口径以该方案 §3.3/§5 为准。

## 架构主干
**五个纯函数包**（项目根，均不反向 import 应用层，可单独复制到别的项目）：
| 包 | 职责 | 入口 |
|---|---|---|
| `parsing/` | 原始字节 → 结构化文本（三级策略：`local` 已实现 / `ocr_api` 只设计 / `multimodal` 已实测跑通） | `parsing/README.md` |
| `knowledge/` | 证据适用性与互斥裁决（新鲜度、冲突图、authority 注册表） | `knowledge/README.md` |
| `cache/` | L1 进程内 LRU + L2 SQLite 持久化（重启可恢复） | `cache/README.md` |
| `guardrails/` | PII 四出口（ingest/context/output/audit） | `guardrails/` |
| `showcase/` | 独立演示站（端口 8767），单向 import 上述包 | `docs/showcase/03-phase2-spec.md` |

上游应用：`webapp/`（可运行演示切片，端口 8765，admin/admin123）、`backend/`（FastAPI 骨架）、
`frontend/`（React + Vite）、`eval/` + `evalbench/`（评测）、`demo/`（演示物料，冻结区）。

## 硬约束（改任何东西前先读）

### 1. 权限与检索顺序
- 权限判定收敛到 `app/services/permission.py::decide()` 一个纯函数，**最终且唯一权威**。
- **先按权限过滤 → 再重排 → 再进模型**。Milvus expr + `filter_candidates` 双重过滤保留，
  列表侧还要逐条 `decide()` 复核（A13 双向一致性对拍是第一验收项）。
- `visible_department_ids()` 只缩候选、不产出 allow/deny。

### 2. 真值与通道
PG 是状态真值，Redis 只是实时通道；**先写 PG 文本再写 Milvus 向量**；删除走软删 + 异步清理 + 每日对账。
删文档前**先删 `chunk_embeddings`**（源头治理幽灵向量），启动再对账兜底。
**本项目 SQLite 未开 WAL**（`journal_mode` = 0，默认 DELETE 模式）——不要写「已开 WAL」。

### 3. 迁移与索引
- 基线迁移 `0001` 用 `metadata.create_all` 且冻结不动；从 `0002` 起必须写显式迁移。
- **索引绝对不能写进 `SCHEMA`**（实测复现）：旧库 `executescript(SCHEMA)` 时新列还不存在，
  `CREATE INDEX` 会先抛 `no such column`，整个脚本失败、迁移跑不到。索引一律在迁移函数末尾幂等创建，
  用 `r[1]` 取 `PRAGMA table_info` 列名（不依赖 `row_factory`）。

### 4. 缓存
- key **13 段**：schema/org/kind/query/embedding/model/top_k/retrieval_mode/rerank/freshness/**authz**/**data_version**/**policy_version**。
  缺 embedding 模型名 → 余弦静默归零；缺 authz → 跨用户串权限；缺 data_version → 旧答案。
- **TTL 不是权限控制**：调用方必须先按权限过滤再写；命中后用 `key_matches()` 复核三重版本字段。
- 懒失效：只 `bump_data_version` / `bump_grant_version` / `sync_policy_version`，不批量删。
- 主链路 `rag.retrieve()` **默认不接检索结果缓存**（避免打断 33 项 e2e）；答案缓存/语义缓存未做。

### 5. PII
- **先检索、后脱敏**：`rag.retrieve()` 用原文 question（脱敏破坏词法召回），进 LLM / 落库 / SSE / 导出一律用脱敏文本。
- `StreamRedactor.push()` 必须**扣住跨界候选串整串**（只留尾部 HOLD=24 会把长标识符前 10 位先发出去，撤不回）。
- 演示样本校验位必须按算法算：`110101199003071938`（MOD 11-2）、`6222021234567894`（Luhn）。
  `11010119900307193X` 非法且前 17 位恰好过 Luhn，曾把负向对照误报成 BANK_CARD。
- `safety_events` 只存 gate/kind/数量/动作/规则版本，**不存原文/preview/可逆映射**；异常 fail-closed。
- **边界**：规则级检测 ≠ 模型级 NER；`backend/app/agent/orchestrator.py` 骨架未接本包；可逆 token 默认关闭。

### 6. 冻结区与前端
- **冻结区 `eval/` `demo/` 零 diff**；`__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径不得改动。
- **C33 断言**：`webapp/selftest.py:369-377` 冻结的是 **10 个具名 view + 6 个具名 loader 的存在性检查**
  （`nav_ok==10 && loaders_ok==6`），不是侧栏总数。目前侧栏 14 个 `data-view`，新增视图不破坏 C33。
- 前端零构建三件套：`i18n.js`（**中文原文即 key**）、`theme.js`、`app.js`。改 JS/CSS 必须升 `?v=`。
  **工作区没有 `gen_dict.py`**（实查），改文案只能手工成对改中文 key + 英文值并删旧 key。
- 静态资源当前 **`?v=15`**。

### 7. 验证纪律
- **演示数据不改正确性逻辑**；**能力判定必须用结构化元数据 + 实测，不用命名启发式**。
- **修复要覆盖所有实现路径**（单机 `observability.rate_check` + 分布式 `ratelimit_dist.check_rate`），
  并加 HTTP 级回归，不能只修一条路径就宣称修好。
- 指标零值样本必须预置，否则 Prometheus 里指标「不存在」。

### 8. 三个唯一事实源（重复实现就是事故）
- `settings.effective_embedding()`：端点/模型/key/缓存命名空间。
- `providers.thinking_fields()`：低思考字段发不发。
- `db.RATE_RULE_DEFAULTS`：限流默认配额（`_seed()` 与测试隔离共用，`pg_init.sql` 必须人工对齐）。

### 9. 测试与镜像
- `webapp/tests/conftest.py` 是**会话级共享临时库**，任何用例改了 `rate_rules` / `budgets`
  都会污染后续用例。已加 autouse fixture「用例开始前复位规则 + 清 `obs._WINDOWS` + 丢 limiter 实例」。
  **不要改回「靠用例自己收尾」**——崩溃时走不到 finally。
- `webapp/Dockerfile` 必须 `COPY eval/config/`：`/api/eval/objects` 读 `eval/config/registry.json`，
  漏了就让端点 500（本地 uvicorn 200、容器 500 是「打包漏文件」的典型症状）。
- 演示容器**不是 compose 起的前缀卷**，是 `run-demo.sh` 的 `docker run -v kb-webapp-data`（固定卷名）。
  用 `docker compose -f docker-compose.showcase.yml up` 会因容器名冲突 + 新空卷丢掉演示数据。

### 10. 本机基础设施（已实跑，别再试错）
- **5432 被 `siteyeb-pg`、6379 被 `siteyeb-redis` 占用**（别的项目容器，不停它）；
  **Docker Hub 不可达**。国内源实拉结论：`milvusdb/milvus` → `docker.m.daocloud.io` ✅、
  `coreos/etcd` → `quay.m.daocloud.io` ✅、`postgres/redis` → daocloud ✅、
  **`minio/minio` 四源全失败**（daocloud denied / 1panel blob 403 / 1ms·rat.dev·proxy.net no such manifest）。
- `docker-compose.infra.yml` 的宿主端口与镜像名全部可用环境变量覆盖（默认值不变）：
  `KB_PG_PORT` / `KB_REDIS_PORT` / `KB_MILVUS_PORT` / `KB_PG_IMAGE` / `KB_REDIS_IMAGE` / … / `KB_MILVUS_STORAGE`
  本机起法：`KB_PG_PORT=55432 KB_REDIS_PORT=56379 KB_MILVUS_STORAGE=local KB_ETCD_IMAGE=quay.m.daocloud.io/coreos/etcd:v3.5.16 KB_MILVUS_IMAGE=docker.m.daocloud.io/milvusdb/milvus:v2.5.10 docker compose -f docker-compose.infra.yml up -d postgres redis etcd milvus`
- **minio 放 `profiles:["minio"]` + milvus 对它 `depends_on.required:false`**，靠 `storageType=local` 起 Milvus。
  local 只支持 standalone 单副本，**生产必须对象存储**，别把妥协讲成设计。
- 一行自检：`python scripts/verify_infra.py` → PG 20 表 / Redis 放大 2.0× / Milvus 存活 83 条 + expr 反证 → `INFRA_READY`。
- **已验证结果**：PG 迁 903 行 / 20 表全一致 / 时间戳逐字节相同；Redis `limit=3` 两进程内存 6 次 vs Redis 3 次；
  Milvus 83 条向量 / 幽灵 0 / 漏迁 0 / 20-20 回查 score=1.0 / 错 tenant 0 条 / 收窄 46→1。
- 迁移脚本报「尝试 / 实际写入 / 跳过」三个数（`cur.rowcount`）。**只报一个数就是没真跑过**。

### 11. Milvus 三个必知陷阱
1. **`num_entities` 是近似值且含已删未压实墓碑**：`upsert`=delete+insert，重跑 83→166；
   正确性断言必须用 `query(count(*))`。
2. **库里有逐元素完全相同的向量**（同一样本反复上传，实测 5 组、最多 9 条）：
   「top1 必须是它自己」是错断言，正确断言是「在 top-k 且 score≈1」，并列单独报。
3. `backend/app/integrations/milvus_client.py` 全用 pymilvus **ORM 风格**，2.6 已告警 3.1 移除 —— 升级前必须重写。

## 已验证的可复现坑（别再踩）
1. **Docker 29 + Compose v5 bake 在中文路径报错**：`x-docker-expose-session-sharedkey … non-printable ASCII`。
   → 拆 `docker build` + `docker run`（`run-demo.sh`），或 `COMPOSE_BAKE=false`。
2. **`docker exec` 路径被 Git Bash 转换** → `MSYS_NO_PATHCONV=1`；`/tmp` 在 Git Bash 与 Windows curl 间不一致 → 用项目内相对路径。
3. **网关流末尾发 `choices: []` 纯 usage 块** → 解析必须跳过空 `choices`，否则 IndexError 把正常回答判成 `model_error`。
4. **`webapp/queue.py` 遮蔽标准库 `queue`** → 脚本放 `webapp/` 下直接跑时 TestClient 起不来。
5. **`webapp/selftest.py` C12「检索为空」探针**含通用二元组会误命中 → 用随机无意义探针。
6. **`webapp/observability.py` 的 `model` 维度**：`model=""` 退化成 `"-"` 会让全站非 chat 共用 10 QPS 桶 → 空值跳过该维度。
7. **`/metrics` 只输出有样本的行** → 空时只有 HELP/TYPE = 指标不存在 → 预置零值样本（6 个指标扩到 8 个）。
8. **内嵌预览面板拦截 `window.open` / `target="_blank"`** → 改 `<a>` 同标签跳转。
9. **`docker compose` 的 `parser` 服务用 `profiles: ["parsing"]`**：默认 `up` 仍 11 服务，`--profile parsing` 才 12。
10. **三条已证伪的误报，别再写进交付物**：① `index.html` 的 40 个内联处理器全部有定义；
    ② `backend/Dockerfile` / `frontend/Dockerfile` 都存在且 backend 装了 curl、`/health/live` 存在；
    ③ `frontend/src/` 有 16 个文件、9 个导入全在，`npm run build` 通过。

## 模型与档位现状
- 默认 `discovery:deepseek-v4.1-flash-1017` + 硅基流动 `BAAI/bge-m3`（1024 维）+ `BAAI/bge-reranker-v2-m3`。
- **低思考档已验证生效**：`WEBAPP_LLM_THINKING=disabled|enabled`，走 `thinking: {"type":"disabled"}` 字段。
  实测基线 979ms/`reasoning_tokens=55` vs disabled 467ms/0；元数据里的布尔 `reasoning` 参数实测无效。换网关必须重验。
- **Discovery 网关 10 个模型里 6 个支持 image**（看 `input_modalities`，不是看名字）。此前「全纯文本」结论是错的。
- 429 必须分两类：`rate_limit_exceeded` 退避 1/2/4s + 抖动；`quota_exceeded` 不重试。
- 配置命名空间隔离：webapp 一律 `WEBAPP_` 前缀，`guardrails` 一律 `GUARDRAILS_*`，不要扁平命名。

## 目录入口
- 总览与启动 `README.md`；重点方案 `docs/async-upload.md`
- 权限 `docs/permission-model.md`；Agent 扩展 `docs/agent-extension.md`
- 运维 `docs/operations.md`；路线 `docs/learning-roadmap.md`
- **通用规约 `docs/HARNESS.md`（YEBSPEC@1.0，Y1–Y22）**：三态标注、冻结区、命名空间隔离、验证门禁、
  交付前闸门 + 真实踩坑表。改任何约束性文本前先读它。
- 案例 `docs/case-studies.md`；生产化 `docs/production/01–06`
- 讲解页 `interview/index.html`（主）、`cases.html`（14 故障点）、`journey.html`（演进+问答）
- showcase `docs/showcase/`；交付话术 `docs/KNOWLEDGE-DELIVERY.md` / `docs/CACHE-DELIVERY.md`

## 验证命令
```bash
python -m pytest webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests showcase/tests -q
cd backend && pytest -q tests
python webapp/selftest.py                 # 33 项，需真实外部模型
PARSING_OFFLINE=1 python showcase/verify_integration.py
python demo/selftest.py                   # 16/16，verdict == DEMO_READY
cd frontend && npm run build
git diff main..HEAD -- eval/ demo/        # 必须为空
```

## 交付前三条自查
1. 数字/结论是否**实跑**得出，而不是推断或沿用旧快照？
2. 「已实现 / 只设计 / 未做」是否标清，有没有把目标态写成现状？
3. 冻结区是否零 diff，报告是否按字节还原？
