# 项目长期记忆 · P5 企业级知识库项目

## 当前版本：**v1.1.0**（2026-11-16，tag `v1.1.0`，main = `5f15d91`）
v1.0.0 是骨架定版（四件真基础设施 + 真外部模型实跑）；v1.1.0 加了**多格式评估 `evalfmt/`**。
1.0 判据 = 真 PG + 真 Redis + 真 Milvus + 真外部模型四件全部实跑通过、数字可复现、未做部分明确标注；
**不是「可上线产品 1.0」**。未做清单在 `docs/production/07-remaining-work.md`（20 项三态）。
定版实测：单测 241 + backend 26 + eval 14 + evalbench 16；e2e 33/33；showcase 31/31；demo 16/16；
`verify_infra` → `INFRA_READY`；真模型热态首字 1.56s / 冷启 23.84s（推理模型固有抖动，不是 bug）。

## `evalfmt/`（多格式评估，与冻结的 `eval/`、`evalbench/` 平级）
入口 `python -m evalfmt.run_eval --mode vector --answer on`；说明 `evalfmt/README.md`。
10 份语料 × 8 种可入库格式（txt/md/csv/json/py/log/yaml/pdf），**每次跑 DROP+CREATE 独立 schema**
（演示库 80+ 历史文档会让召回率不可复现）。
四层指标：解析保真率 / Recall@k+MRR（**文档级**）/ 引用准确率@1 / 答案正确率+幻觉率+拒答率+陷阱违规率。
实测：词法 Recall@1 66.7%；向量 Recall@1 100%、MRR 1.000、幻觉率 0%(0/14)、拒答 100%、陷阱 0%；
无字图片离线识别 0%、视觉链路 100%(74119 / SN-74119)。
**它抓出了两个真缺陷并已修**：① 内网 IP 被当 PII 脱敏（配网脚本保真率仅 50%）→ `_public_ipv4` 内网放行、公网拦截；
② 词法门禁在向量排序前筛候选，跨语言零重叠直接掐死召回 → 门禁改为只判「有无相关信号」，不挑候选。
**指标自身也修了两个 bug**：幻觉判定不该把问题里复述的实体算编造；归一化要覆盖字形变体（× vs x）但不做模糊匹配。
口径：样本仅 9 题，**100% 只证明链路通了，不证明系统强**；两个模式都要报（差 33 个点）。

## 定位与用户约定
企业级知识库系统的**开发框架骨架**，目标是「学习 + 面试讲解 + 逐步生产化」，不是上线交付版。
沿用 A/B/C 三档口径，**面试场上不要把 A 档说成 C 档**。

- 技术栈固定：React / FastAPI / PostgreSQL / Milvus / Redis + Celery / 外部 DeepSeek（不部署本地模型）。
- 用户正在学 FastAPI 与数据库，之后学 Java SpringBoot。代码要可读、注释解释**设计取舍**；
  文档必须区分「已实现 / 只设计 / 未做」，不夸大完成度。
- 架构必须预留：逐行/逐段精细权限、Agent 内部编排（LangGraph/多 Agent/MCP）、高可用加固。
- 重点功能：大文件与多文档批量上传的异步处理及进度反馈。

## 组织模型（2026-11-13 变更，覆盖历史表述）
单组织 + 多用户多部门。**多租户不做，只作为未来方向写入文档**。
- webapp 成本/限流统计维度已从 `tenant` 对齐为 `department`（列名 `department_id`、维度名 `department`）。
- **webapp 没有部门级权限**；`backend/` 的 `tenant_id` / `TenantRepository` 是历史命名技术债。
- `docs/org-model-change-plan.md` 13 步仍是「方案已定、代码尚未改造」，目标口径见该方案 §3.3/§5。

## 架构主干
**五个纯函数包**（项目根，不反向 import 应用层，可单独复制）：
`parsing/`（字节→结构化文本；local 已实现 / ocr_api 只设计 / multimodal 已实测跑通）、
`knowledge/`（证据适用性与冲突裁决）、`cache/`（L1 LRU + L2 PG 持久化）、
`guardrails/`（PII 四出口）、`showcase/`（演示站 8767）。
上游应用：`webapp/`（演示切片 8765，admin/admin123，**跑在真 PostgreSQL 上**）、`backend/`（骨架）、
`frontend/`（React+Vite）、`eval/` + `evalbench/`、`demo/`（冻结区）。

## 硬约束（改任何东西前先读）

### 1. 权限与检索顺序
`app/services/permission.py::decide()` 是**最终且唯一权威**纯函数。
**先按权限过滤 → 再重排 → 再进模型**；Milvus expr + `filter_candidates` 双重过滤保留，
列表侧还要逐条 `decide()` 复核（A13 双向一致性对拍是第一验收项）。`visible_department_ids()` 只缩候选、不产出 allow/deny。

### 2. 真值与通道
PG 是状态真值，Redis 只是实时通道；**先写 PG 文本再写 Milvus 向量**；删除走软删 + 异步清理 + 每日对账。
删文档前**先删 `chunk_embeddings`**（源头治理幽灵向量）。

### 3. 迁移与 DDL
建表 DDL 唯一事实源是 `scripts/pg_init.sql`（容器 initdb / `init_db()` / 测试三处共用）。
不要再往 `db.py` 里写 `CREATE TABLE IF NOT EXISTS`；**改回 SQLite = 撤销一次大改造**。

### 4. 缓存
key **13 段**：schema/org/kind/query/embedding/model/top_k/retrieval_mode/rerank/freshness/**authz**/**data_version**/**policy_version**。
缺 embedding 模型名→余弦静默归零；缺 authz→串权限；缺 data_version→旧答案。
**TTL 不是权限控制**：先按权限过滤再写，命中后用 `key_matches()` 复核三重版本。
懒失效只 bump 版本不批量删。主链路 `rag.retrieve()` **默认不接检索缓存**（避免打断 33 项 e2e）；答案/语义缓存未做。

### 5. PII
**先检索、后脱敏**：`rag.retrieve()` 用原文 question，进 LLM/落库/SSE/导出用脱敏文本。
`StreamRedactor.push()` 必须**扣住跨界候选串整串**（只留尾部 HOLD=24 会把长标识符前 10 位先发出去，撤不回）。
样本校验位按算法算：`110101199003071938`（MOD 11-2）、`6222021234567894`（Luhn）。
`safety_events` 只存 gate/kind/数量/动作/规则版本。边界：规则级 ≠ 模型级 NER。

### 6. 冻结区与前端
**`eval/` `demo/` 零 diff**；`__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径不得改。
**C33**：`webapp/selftest.py:369-377` 冻结的是 **10 具名 view + 6 具名 loader 的存在性检查**（`nav_ok==10 && loaders_ok==6`），
不是侧栏总数；当前侧栏 14 个 `data-view`，新增视图不破坏 C33。
前端零构建三件套 `i18n.js`（**中文原文即 key**）/`theme.js`/`app.js`，改 JS/CSS 必升 `?v=`；当前 **`?v=15`**。
**工作区没有 `gen_dict.py`**，改文案手工成对改中文 key + 英文值并删旧 key。

### 7. 验证纪律
**演示数据不改正确性逻辑**；**能力判定用结构化元数据 + 实测，不用命名启发式**。
修复要覆盖所有实现路径（`observability.rate_check` + `ratelimit_dist.check_rate`）并加 HTTP 级回归。
指标零值样本必须预置，否则 Prometheus 里指标「不存在」。**断言必须防「测试自己假绿」**。

### 8. 三个唯一事实源（重复实现就是事故）
`settings.effective_embedding()`、`providers.thinking_fields()`、`db.RATE_RULE_DEFAULTS`（与 `pg_init.sql` 人工对齐）。

### 9. 测试与镜像
`webapp/tests/conftest.py` 每进程一个 `kb_test_<uuid8>` schema，`cache` 每用例一个 schema；
autouse fixture **用例开始前**复位限流规则 + 清 `obs._WINDOWS` + 丢 limiter（**不要改回靠用例自己收尾**，崩溃走不到 finally）。
conftest 必须 `pop WEBAPP_REDIS_URL` + 钉死 `WEBAPP_RATE_BACKEND=memory`。
`webapp/Dockerfile` 必须 `COPY eval/config/`（否则 `/api/eval/objects` 本地 200 容器 500）。
演示容器是 `run-demo.sh` 的 `docker run -v kb-webapp-data`（固定卷名），**不要用 compose 起**（容器名冲突 + 空卷丢数据）。

### 10. 本机基础设施（已实跑）
- **5432 被 `siteyeb-pg`、6379 被 `siteyeb-redis` 占用**（别的项目，不停它）；**Docker Hub 不可达**。
- 国内源实拉：`milvusdb/milvus`→`docker.m.daocloud.io` ✅、`coreos/etcd`→`quay.m.daocloud.io` ✅、
  `postgres/redis`→daocloud ✅、**`minio` 四源全失败** → 改走**华为云 SWR**：
  `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z`。
  （除「加速站」外还有第三类来源：**云厂商公共镜像仓库**，如华为云 `ddn-k8s`）
- `docker-compose.infra.yml` 端口/镜像名全部可用 `KB_*` 覆盖；minio 在 `profiles:["minio"]`，
  milvus 对它 `depends_on.required:false`。`local` 存储只支持 standalone，**生产必须对象存储**。
- 一行自检 `python scripts/verify_infra.py` → `INFRA_READY`。
- **已验证结果**：PG 迁 903 行 / 20 表全一致 / 时间戳逐字节相同 / 重跑写入 0；
  Redis `limit=3` 两独立进程内存 6 次 vs Redis 3 次（放大 2.0×）；
  Milvus 83 条 / 幽灵 0 / 漏迁 0 / 20-20 回查 score=1.0 / 错 tenant 0 条 / 收窄 46→1。
- 迁移脚本必须报「尝试 / 实际写入 / 跳过」三个数（`cur.rowcount`），**只报一个数就是没真跑过**。

### 11. SQLite 已彻底移除（2026-11-15）
`webapp/` 与 `cache/` 运行时零 sqlite3 依赖。138 处 `conn.execute` 经 `pgkit.py` 适配
（`?`→`%s`、`:name`→`%(name)s`、有参数时 `%`→`%%`、`datetime/Decimal` 收敛 ISO/float）。
`CacheStore(db_path)` 语义已改为 `CacheStore(schema)`，传路径直接 ValueError。
`scripts/legacy/import_legacy_sqlite.py` 是一次性 ETL，不属于运行时。
**测试必须 PG 起着**，不再有「零依赖 clone 就能跑单测」。
坑：**旁路 `except: pass` 会把「功能已坏」伪装成「什么都没发生」**——`record_usage` 因命名占位符不支持静默丢光成本台账，业务全绿日志干净。现改为 WARN 一次。

### 12. Milvus 四个陷阱
1. `num_entities` 是近似值且含已删未压实墓碑（`upsert`=delete+insert，重跑 83→166）；断言必须 `query(count(*))`。
2. 库里有逐元素完全相同的向量（实测 5 组、最多 9 条）：「top1==自己」是错断言，应为「在 top-k 且 score≈1」。
3. `backend/app/integrations/milvus_client.py` 全是 pymilvus **ORM 风格**，2.6 已告警 3.1 移除，升级前必须重写。
4. **换对象存储必须 `--reset-collection`**：元数据在 etcd、数据在对象存储；换 `storageType` 后
   `has_collection` 为真但 segment 不存在 → `load()` **永久挂住**。`utility.*` 前必须先 `connections.connect`。

## 已验证的可复现坑
1. Docker 29 + Compose v5 bake 在中文路径报错 → 拆 `docker build`+`docker run` 或 `COMPOSE_BAKE=false`。
2. `docker exec` 路径被 Git Bash 转换 → `MSYS_NO_PATHCONV=1`；用项目内相对路径而非 `/tmp`。
3. 网关流末尾发 `choices: []` 纯 usage 块 → 解析必须跳过空 `choices`，否则把正常回答判成 `model_error`。
4. `webapp/queue.py` 遮蔽标准库 `queue` → 脚本放 `webapp/` 下跑时 TestClient 起不来。
5. `selftest.py` C12「检索为空」探针含通用二元组会误命中 → 用随机无意义探针。
6. `observability.py` 的 `model` 维度 `""` 退化成 `"-"` 会让全站非 chat 共用桶 → 空值跳过该维度。
7. `/metrics` 只输出有样本的行 → 零值样本必须预置。
8. 内嵌预览面板拦截 `window.open`/`target="_blank"` → 改 `<a>` 同标签跳转。
9. compose 的 `parser` 服务用 `profiles:["parsing"]`，默认 `up` 仍 11 服务。
10. **三条已证伪的误报，别再写进交付物**：① `index.html` 40 个内联处理器全有定义；
    ② `backend/Dockerfile`/`frontend/Dockerfile` 都在且 backend 装了 curl；③ `frontend/src/` 16 文件导入齐全、`npm run build` 通过。

## 模型与档位
默认 `discovery:deepseek-v4.1-flash-1017` + 硅基流动 `BAAI/bge-m3`（1024 维）+ `BAAI/bge-reranker-v2-m3`。
**低思考档已验证**：`WEBAPP_LLM_THINKING=disabled|enabled` → `thinking:{"type":"disabled"}`；实测 979ms/55 tok vs 467ms/0，
元数据布尔 `reasoning` 参数无效，换网关必须重验。**网关 10 模型里 6 个支持 image**（看 `input_modalities`，不看名字）。
429 分两类：`rate_limit_exceeded` 退避 1/2/4s+抖动；`quota_exceeded` 不重试。
配置命名空间隔离：webapp 一律 `WEBAPP_`，guardrails 一律 `GUARDRAILS_*`。

## 目录入口
`README.md`（启动三步：起库→装依赖→起服务）、`docs/HARNESS.md`（**YEBSPEC@1.0 Y1–Y22，改约束性文本前先读**）、
`docs/production/01–06`、`docs/permission-model.md`、`docs/agent-extension.md`、`docs/operations.md`、
`docs/async-upload.md`、`docs/learning-roadmap.md`、`docs/case-studies.md`、
`interview/index.html`（主讲解页，含 21 页「全面研究」章节）、`cases.html`、`journey.html`、
`docs/KNOWLEDGE-DELIVERY.md`、`docs/CACHE-DELIVERY.md`。

## 验证命令
```bash
python -m pytest webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests showcase/tests -q
cd backend && pytest -q tests
python webapp/selftest.py                 # 33 项，需真实外部模型
PARSING_OFFLINE=1 python showcase/verify_integration.py
python demo/selftest.py                   # 16/16
cd frontend && npm run build
git diff main..HEAD -- eval/ demo/        # 必须为空
```

## 交付前三自查
1. 数字/结论是否**实跑**得出？ 2. 三态是否标清、有没有把目标态写成现状？ 3. 冻结区零 diff、报告按字节还原？
