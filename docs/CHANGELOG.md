# 项目整体改进日志

> **记录范围**：2026-11-11 ~ 2026-11-12（项目开发前两日）全部已完成的改进与修正。
>
> **版本坐标系**（本日志的标注依据，均来自仓库内可核对的文档与配置）：
> - 仓库于 **2026-11-12 19:12** 初始化（`4992bed`），首日全部成果在**同一次基线提交**中纳入版本管理。因此 §2 条目统一标 `v0.1 基线 / 4992bed`，并按各模块自带的版本标识细分，不写「待补充」。
> - 骨架与方案：`v0.1 骨架`（`README.md` §1.2、`docs/next-version-messaging-and-plugins.md`）、`方案 v0.2`。
> - 评测体系：`EVAL-PLAN@0.1.0`、14 个对象 `@0.1.0`（`eval/config/registry.json`）、`cases_v0.1.0` / `fixtures_v0.1.0`、`eval-record/v0.1`。
> - 演示物料：`DEMO-KBQA-EVAL-001` 场景版本 `1.0.0`（`demo/scenario.json`）。
> - 演示切片：`webapp/main.py` 的 `version="0.2.0-demo"` → **`1.0.0`（2026-11-16 定版）**；静态资源 `v=3 → v=5 → v=9 → v=10 → v=11 → v=15`。
> - 评估工作台：`METRICS@1.0.0 → 1.1.0 → 1.2.0`、`self_built_v1`、分支 `eval/v1…v4` / `demo/v1` 与 commit。
> - **事实源**：`git log`、`.workbuddy/memory/2026-11-11.md`、`2026-11-12.md`、`MEMORY.md`、`README.md`、`webapp/README.md`、`docs/` 全部文档及各配置文件版本字段。
>
> **定位**：开发框架骨架 + 面试讲解版（A/B 档），非上线交付版。
> **统一列**：版本或日期 ｜ 改进项名称 ｜ 改动说明 ｜ 影响范围 ｜ 备注。

---

## v1.0.0 · 2026-11-16 · 真基础设施全链路跑通与定版

> **1.0 的含义**：**「开发框架骨架 + 面试讲解版」的 1.0**，不是「可上线产品 1.0」。
> 判据是「真 PG + 真 Redis + 真 Milvus + 真外部模型四件都实跑过、数字可复现、未做的部分明确标注」，
> 而不是「功能齐全」。未做清单见 `docs/production/07-remaining-work.md`（20 项，逐项三态）。

| 日期 | 改进项 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-16 | **版本号定版 `1.0.0`** | `webapp/main.py` 的 FastAPI `version` 由 `0.2.0-demo` → `1.0.0`；`frontend/package.json` 由 `0.1.0` → `1.0.0`；打 annotated tag `v1.0.0` | `webapp/main.py`、`frontend/package.json` | 无测试断言该字符串；`/api-docs` 显示版本随之更新 |
| 2026-11-16 | **部署文档去 SQLite 残留** | `docs/deployment-guide.md` 三处仍写「`webapp` 用 SQLite、无 Dockerfile、容器内多副本状态冲突」——均已过期。改为「有 `webapp/Dockerfile`、跑真 PG、走 `run-demo.sh` 的 build+run 路线」，并把多副本冲突点更正为**进程内队列与进程内限流闸** | `docs/deployment-guide.md` §2 项①、§2.1 说明、§5 解析服务边界 | 这三处会直接误导腾讯云上线步骤，不是文风问题 |
| 2026-11-16 | **新增剩余工作清单** | `docs/production/07-remaining-work.md`：20 项缺口逐项标「未做 / 只设计 / 半成品」，附阻塞原因、做完的判断标准、建议顺序（本周 / 面试前 / 生产前）与新环境启动陷阱 | `docs/production/`、`00-overview.md` §3/§8 | 逐项回代码核过（如 `rag.py:154` 仍读 PG 文本列），非凭印象 |

**1.0 定版的实测证据（2026-11-16 本机全量重跑，无任何预设环境变量，全部走 `.env` + `docker-compose.infra.yml`）**

| 项 | 结果 |
|---|---|
| 单测（webapp + parsing + knowledge + cache + guardrails + showcase） | **231 passed**（44.34s） |
| `backend/` 单测 | **26 passed** |
| `eval/` 单测 | **14 passed** |
| `evalbench/` 单测 | **16 passed** |
| 端到端自测（打容器里的真服务） | **33/33 `DEMO_READY`** |
| showcase 集成校验 | **31/31 `INTEGRATION_READY`** |
| demo 物料自检 | **16/16** |
| 基础设施自检 `verify_infra.py` | **`INFRA_READY`**（3.93s）：PG 20 表 / 限流种子无偏差；Redis `limit=3` 内存 6 次 vs Redis 3 次 = **放大 2.0×**；Milvus 存活 83 条 / 覆盖 46 文档 / 错 tenant 命中 0 |
| 前端 | `node --check` ×3 通过；`npm run build` 通过 |
| 冻结区 | `git diff main..HEAD -- eval/ demo/` **零 diff** |
| **真外部模型链路**（LLM `deepseek-v4.1-flash-1017` + 硅基流动 bge-m3 嵌入 + bge-reranker 重排） | 冷启首字 **23.84s** / 200 tokens / 4 sources / `degraded=False`；**热态首字 1.56s**，两次总时长 3.50s、5.64s |
| 容器 | `kb-pg` `kb-redis` `kb-etcd` `kb-minio` `kb-milvus` `kb-webapp` `kb-showcase` **七个全 healthy** |

> 冷启 23.84s vs 热态 1.56s 是 DeepSeek 推理模型的固有抖动（此前实测 13–58s）。
> 上线若要改善首字体验，只能换非推理档或预热，**不是本次能修的 bug**。
>
> 本版**没有**改变 `eval/` `demo/` 任何冻结口径；`__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断语义保持原样。

---

## 0. 2026-11-15 · 运行时彻底移除 SQLite（`feat/pg-only`）

| 日期 | 改进项 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-15 | **`webapp/` 改连真 PostgreSQL** | 删除 `db.py` 的 SCHEMA / 三段「补列」迁移 / `DB_PATH`；新增 `pgkit.py` 承担连接、占位符翻译（`?`→`%s`、`:name`→`%(name)s`、`%`→`%%`）与类型收敛（`datetime`→ISO、`Decimal`→float） | `webapp/`、`cache/`、`pgkit.py`、`scripts/pg_init.sql` | 138 处 `conn.execute` 调用点一行未改，方言差异收敛到一个文件 |
| 2026-11-15 | **`cache/` L2 改落 PG** | `cache_entries` / `runtime_state` 不再写 SQLite；`CacheStore(db_path)` 语义改为 `CacheStore(schema)`，传路径直接报错 | `cache/store.py`、`cache/__init__.py`、`cache/recovery.py` | 参数语义变更加了正则校验，避免变成看不懂的 PG 报错 |
| 2026-11-15 | **测试隔离改独立 schema** | `webapp/tests/conftest.py` 建 `kb_test_<uuid8>` schema；`cache/tests/conftest.py` 每用例一个 schema；DDL 统一执行 `scripts/pg_init.sql` | 全部 Python 测试 | **代价：`pytest` 现在要求 PG 起着**，不再零外部依赖 |
| 2026-11-15 | **删除 12 项 SQLite 专有测试** | `test_department_migration.py` 整文件（测三段补列迁移）已删；补列幂等用例同删 | `webapp/tests/` | 被测代码已不存在；正向断言（列存在、权限口径）保留。243 → 231 |
| 2026-11-15 | **修复静默丢数据缺陷** | `record_usage` 的命名占位符 `:name` 不被 psycopg 支持，成本台账全空；`except Exception: pass` 把故障完全掩盖 | `webapp/observability.py`、`pgkit.py` | 改为失败 **WARN 一次**：旁路可以不影响业务，但不能不留痕 |
| 2026-11-15 | **4 处隐式 `rowid` 改显式主键** | SQLite 每表自带 rowid，PG 没有；同秒记录分页顺序会抖 | `observability.py`、`routers_ops.py` | 用 `id` / `run_id` 作 tiebreaker |
| 2026-11-15 | **一次性 ETL 归档** | `scripts/migrate_sqlite_to_pg.py` → `scripts/legacy/import_legacy_sqlite.py` | `scripts/legacy/` | 只用于导入旧 `.db`，不属于运行时；删掉不影响业务与测试 |

> 验证：`231 passed`（webapp + parsing + knowledge + cache + guardrails + showcase）·
> `webapp/selftest.py` **33/33 `DEMO_READY`**（打容器里的真服务）·
> `docker exec kb-webapp grep -rln "import sqlite3" /app` **无输出** ·
> 容器内 `pgkit.dsn()` → `postgresql://kb:kb_demo_only@kb-pg:5432/kb`（PostgreSQL 16.16）。
> 设计取舍与 8 个坑的完整记录见 `docs/production/01-data-migration.md` §12。

---

## 1. 版本坐标对照

| 版本标识 | 含义 | 出处 | 对应阶段 |
|---|---|---|---|
| `v0.1 骨架` / `4992bed` | 项目现状基线，首日全部成果一次性入库 | `README.md` §1.2；`git show 4992bed` | 2026-11-11 全量 |
| `方案 v0.2` | 下一版本消息层职责重划与插件清单 | `docs/next-version-messaging-and-plugins.md` 标题 | 2026-11-11 |
| `EVAL-PLAN@0.1.0` / 对象 `@0.1.0` | 评估方案与 14 个版本化对象 | `docs/evaluation-plan.md`、`eval/config/registry.json` | 2026-11-11 |
| `DEMO-KBQA-EVAL-001` `1.0.0` | 演示场景单一事实源版本 | `demo/scenario.json`、`docs/demo/01-demo-guide.md` | 2026-11-11 |
| `0.2.0-demo` | 可运行演示切片的应用版本 | `webapp/main.py` | 2026-11-11 起 |
| 静态资源 `v=3 / 5 / 9 / 10` | 侧栏补齐 / 模型设置页 / 来源追溯 / 三组件外部 API | `webapp/static/index.html`（基线为 `v=10`） | 2026-11-11 |
| 静态资源 `v=11` | i18n / 主题改造后的资源版本 | `webapp/static/index.html`（当前 HEAD） | 2026-11-12 |
| `METRICS@1.0.0 / 1.1.0 / 1.2.0` | 评估评分口径三次演进 | `evalbench/configs/metrics.json` | 2026-11-12 |
| `eval/v1…v4`、`demo/v1` | 每轮迭代独立分支 | `git branch` | 2026-11-12 |

---

## 2. 2026-11-11（首日 · `v0.1 基线 / 4992bed`）

> 本节所有改动于 2026-11-12 19:12 随基线提交 `4992bed` 一次性纳入 Git；「版本或日期」列给出该模块在文档中自带的版本标识。

| 版本或日期 | 改进项名称 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-11 · `v0.1 骨架` | 全栈开发骨架首次搭建 | 按指定技术栈构建 React 18 + FastAPI + PostgreSQL + Milvus + Redis/Celery + MinIO 骨架；分片 8MiB 断点续传、批量聚合、Celery 多阶段流水线、SSE 层级进度；确立四条核心约束（tenant 注入、权限纯函数、先过滤再重排、PG 为真值） | `backend/`、`frontend/`、`nginx/`、`docker-compose.yml`、`README.md` | 后端 16/16、前端 build 成功、47 条 OpenAPI 路径、13 个 Celery 任务、4 个 beat；参考《全景设计、本周排期与交付档次》 |
| 2026-11-11 · `v0.1 骨架` | 首日设计文档组 | 产出异步上传、权限模型、Agent 扩展、运维边界、学习路线 5 份文档，统一「已实现 / 只设计 / 未做」三态口径 | `docs/async-upload.md`、`permission-model.md`、`agent-extension.md`、`operations.md`、`learning-roadmap.md` | `docs/operations.md` 逐条标注端口与安全清单状态；权限文档预留逐段落 ACL 三步走 |
| 2026-11-11 · `EVAL-PLAN@0.1.0` | 离线占位评测体系 | 14 个版本化对象（各带 8 字段变更记录、DAG 无环校验）+ 26 条用例 + 6 份 fixtures + 六链路确定性占位 harness；35 个接入契约槽位全部 `__TBC__`（26 blocking / 9 optional） | `eval/`、`docs/evaluation-plan.md` | 14 项 unittest；`comparable=false`、`composite_score=null`、阈值全 null；复现主键含 `code_hash` |
| 2026-11-11 · `DEMO-KBQA-EVAL-001` `1.0.0` | 演示物料包 | 单一事实源 `scenario.json`（8 术语 / 9 步 / 9 句话术 / 5 边界用例）+ 四部分文档 + 一键演示、记录查看、16 项自测工具；12 分钟 9 步、零网络零密钥 | `demo/`、`docs/demo/` | 16/16 `DEMO_READY`、9/9 步对齐、42/42 逐项；7 字段与 `RUN-095a2a4a40c6` 全一致 |
| 2026-11-11 · `方案 v0.2` | 消息层职责重划与插件清单 | 按「数据能不能丢」重划 RabbitMQ / Redis / Celery / PG 职责；9 项插件按 P0–P2 排序；同步修正 3 处「换 RabbitMQ 只改一处配置」过时结论 | `docs/next-version-messaging-and-plugins.md`、`README.md` §1.2、`backend/app/tasks/celery_app.py` 注释、`docs/operations.md` | 仅文档/注释改动；demo 16/16 复跑通过，`run_id` 不变 |
| 2026-11-11 · `0.2.0-demo` | 可运行演示切片 `webapp/` | 新增零依赖 FastAPI + SQLite + 无构建静态前端；注册登录（pbkdf2 + HMAC JWT）、上传管理、SSE 流式问答、badcase 采集、admin 完成度与导出；全链路降级与断线恢复 | `webapp/`、`README.md`、`.gitignore` | 26/26 `DEMO_READY`（后续扩至 33/33）；SQLite/进程内队列为 PG/Celery 演示替身 |
| 2026-11-11 · 静态资源 `v=3` | 侧栏补齐 9 项 / 3 组 | 新增概览、检索调试、任务中心、评测中心、审计日志视图与 5 条 API；`audit_logs` 只追加台账 + 8 类动作埋点 | `webapp/routers_ops.py`、`db.py`、`static/` | API 14→19；确立「不查而非隐藏」权限分离；33/33 |
| 2026-11-11 · `0.2.0-demo` | 真实 LLM 接入 | 接入 Sensenova 代理 DeepSeek；新增 `LLMProvider` / `EmbeddingProvider` 适配器 seam，离线实现保留兜底；模型报错优雅降级 | `webapp/providers.py`、`rag.py`、`tests/`、`.env` | 密钥仅进 `.env`（gitignore）；pytest 13/13、selftest 33/33；`eval/` 冻结口径未破 |
| 2026-11-11 · 静态资源 `v=5` | 本地模型与模型设置页 | LM Studio Bge M3 嵌入（1024 维）、hybrid 检索（词法门禁 + 向量排序）、`app_settings` 运行时配置与 admin 设置页 | `webapp/settings.py`、`routers_settings.py`、`providers.py`、`rag.py`、`static/` | 配置即时生效；pytest 21/21；本地向量仍是 Milvus 替身 |
| 2026-11-11 · `0.2.0-demo` | Rerank 三档重构 | 确认 LM Studio 无 `/v1/rerank`，移除 `local` 档；改为 `off` / `llm`（LLM-as-reranker） / `external`（Cohere 风格，兼容 TEI） | `webapp/providers.py`、`rag.py`、`routers_settings.py`、`static/` | 实测 LLM 打分 9.0 / 2.0；pytest 24/24；不可用时优雅跳过并留痕 |
| 2026-11-11 · `0.2.0-demo` | 硅基流动真 cross-encoder 接入 | 外部 rerank 实测跑通并对比区分度；空 key / 错 key 返回 `None` 走跳过 | `.env`、`webapp/providers.py`、设置页 | 真相关 0.9316 vs 干扰 0.0160；24/24、33/33 |
| 2026-11-11 · `0.2.0-demo` | 本地 Qwen3-VL 8B 实测 | 文本链路可用（36.5s / 131 chunks、带引用）；视觉能力在本机 LM Studio 不可用，保持纯文本问答 | `webapp/providers.py`、`rag.py`、`tests/` | 对比基准：coder 7B 11.9–15.2s（快约 2.5 倍）；pytest 25/25 |
| 2026-11-11 · `0.2.0-demo` | Intern-AI Discovery 网关 | 新增第四档 LLM（14 个模型可切），与 Sensenova 互为备份；同上下文横向延迟基准 | `webapp/settings.py`、`providers.py`、`routers_settings.py`、`static/` | pytest 31/31；网关延迟抖动大，演示建议 `minimax-m3` |
| 2026-11-11 · `0.2.0-demo` | 429 分类与退避改造 | 区分 `rate_limit_exceeded`（1/2/4s + 抖动退避、尊重 `Retry-After`、上限 30s）与 `quota_exceeded`（不重试）；流式吐字后不再重试 | `webapp/providers.py`、`rag.py`、`static/app.js`、`webapp/README.md` | pytest 36/36；明确未做全局并发闸/令牌桶 |
| 2026-11-11 · 静态资源 `v=9` | 智能问答来源追溯 | 新增 `chat_sources` 表；SSE `meta`/`done` 携来源；三个回放接口；前端来源卡片、原文弹窗、历史会话回放 | `webapp/db.py`、`routers_chat.py`、`static/` | pytest 37/37；API 19→24；逐句 grounding 高亮仍属设计 |
| 2026-11-11 · 静态资源 `v=10` | 三组件全切外部 API + 评估报告 | 嵌入与重排切硅基流动、LLM 切 Discovery DeepSeek；新增 `effective_embedding()` 唯一事实源与缓存命名空间隔离；`WEBAPP_` 前缀隔离配置；产出面试向评估报告 | `webapp/settings.py`、`providers.py`、`rag.py`、`eval_report.py`、`.env`、`docs/evaluation-report.md` | pytest 41/41、e2e 33/33、backend 16/16；报告日期 2026-11-11，明确「非质量基线」 |

---

## 3. 2026-11-12（次日 · Git 纳入版本管理）

| 版本或日期 | 改进项名称 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-12 19:12 · `4992bed` | Git 仓库初始化 | `git init` + `main` 基线提交，收纳首日全部 187 个文件；`.gitignore` 排除密钥、依赖与产物 | 仓库根（`.env` 未入库） | 分支 `main`；`git grep` 校验无原始密钥 |
| 2026-11-12 19:25 · `b66d116` | 真实 API 评估工作台骨架 | 新增平行体系 `evalbench/`：公开基准调研、12 条自建样本、3 版提示词、3 组参数、`METRICS@1.0.0`、执行器、模型探测、展示页；不改 `eval/` 冻结对象 | `evalbench/`（29 文件 / 1890 行） | 样本依据 RGB / SQuAD 2.0 口径自建，非官方抽样；RGB 许可仅非商业 |
| 2026-11-12 19:35 · `2361ece` · `eval/v1-baseline` | 第 1 轮基线评估 | 3 模型 × 12 样本；评分口径由 `METRICS@1.0.0` 定稿至 `1.2.0`，前后对照留痕 | `evalbench/runs/`、`iterations/comparisons/` | 质量主指标饱和（全 1.0）；新增 `answer_economy` 作为收敛对照维度 |
| 2026-11-12 19:39 · `3a7dc41` · `eval/v2-prompts` | 第 2 轮提示词收敛 | 三版提示词对比，收敛到 `v2_grounded`；修复聚合延迟字段被末类别覆盖 | 分支 `eval/v2-prompts` | `answer_economy` 0.25→1.00、延迟 −42%；v3 无可判定增益且慢 19%，否决 |
| 2026-11-12 19:41 · `466a5cc` · `eval/v3-params` | 第 3 轮参数收敛 | A/B/C 三组对比，采纳 B（`temp=0`） | 分支 `eval/v3-params` | 主要依据为评估可复现性而非单次延迟差；C 否决 |
| 2026-11-12 19:49 · `5c0efc7` · `eval/v4-models` | 第 4 轮模型适配与选型 | 最终配置 × 3 模型分别记录；kimi 兼容性三步适配实证 | 分支 `eval/v4-models` | 最终配置 `v2_grounded` + B + `minimax-m3`；最终 run `RUN-a48c08c0b721` |
| 2026-11-12 19:57 · `a089a7f` · `demo/v1` | Demo 版本交付 | 展示页、逐轮迭代结论与项目记忆；每个 run 的快照回写 branch/commit | `evalbench/out/`、`iterations/iteration_log.md` | 6 个分支、7 个提交线性可追溯 |
| 2026-11-12 21:21 · `60720aa` | 评估测试可观测子页与治理层 | 新增 6 面板子页、12 个 API、7 张台账表、四维限流中间件、成本/预算引擎、权限矩阵与 11 项插件清单 | `webapp/observability.py`、`routers_evaltest.py`、`main.py`、`db.py`、`routers_chat.py`、`static/index.html` | 只读真实数据，写接口仅 admin；默认限流阈值宽松不误伤自测 |
| 2026-11-12 21:21 · `60720aa` · 静态资源 `v=11` | 前端 i18n / 多主题色 / 明暗模式 | 632 条字典的全量中英切换；6 套主题色 × 明/暗正交组合；三份独立持久化；样式变量全量化与响应式/可访问性优化 | `webapp/static/i18n.js`、`theme.js`、`style.css`、`index.html`、`app.js` | 无刷新、可逆、防闪烁；13 组对比度实测全部 ≥4.76（WCAG AA 正文） |
| 2026-11-12 21:21 · `60720aa` | 规则与能力清单文档 | 模块化改动清单、限流/权限/成本规则、验收标准；中型公司知识库能力清单（网络调研） | `docs/enterprise-readiness.md`、`docs/kb-capability-checklist.md`、`README.md`、`webapp/README.md` | 能力清单含 6 个可核对来源、50 条对照、Top10 建议 |
| 2026-11-12 21:21 · `60720aa` | 新增验收测试 | 10 项 pytest 覆盖表结构、权限边界、成本规则、预算拒绝、429 反馈、资源完整性等 | `webapp/tests/test_evaltest.py` | pytest 由 41 项增至 51 项 |
| 2026-11-12 21:32 · 静态资源 `v=11` | 前端缓存版本对齐 | `index.html` 中 `app.js?v=10` 与 `style.css?v=11` 不一致，会命中旧缓存；统一为 `v=11` | `webapp/static/index.html` | 本次日志核对时发现的遗留不一致 |

---

## 4. 缺陷修正清单（按发现阶段）

> 首日修正同样随 `4992bed` 基线入库；次日用 commit 标注。

| 版本或日期 | 改进项名称 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-11 · `0.2.0-demo` | Python 3.13 流式读取失效 | 已移除的 `HTTPResponse.makefile` 改为 `resp.fp.readline()` | `webapp/providers.py` | 仅接真实模型才暴露 |
| 2026-11-11 · `0.2.0-demo` | 中文检索整句当 token | 改为英文整词 + 中文相邻二元组，使「配网」可命中 | `webapp/rag.py` | 修复前永远空检 |
| 2026-11-11 · `0.2.0-demo` | 网关空 `choices` 误判 | 跳过流末尾 `choices: []` 的纯 usage 块；新增 `stream_interrupted` 不拼接兜底文案 | `webapp/providers.py` | 原会把正常回答判成 `model_error` |
| 2026-11-11 · `0.2.0-demo` | 零正文静默空白 | `empty_model_output` 标记 + 检索片段兜底 | `webapp/rag.py`、`tests/` | 推理模型只吐 `reasoning_content` 时触发 |
| 2026-11-11 · `0.2.0-demo` | 内联思考标签外露 | 新增 `_filter_inline_think()` 流式安全过滤（跨 chunk 切分可处理） | `webapp/providers.py` | `minimax-m3` 的 `<mm:think>` |
| 2026-11-11 · `0.2.0-demo` | 连接测试误报可用 | `test_llm` token 24→256，返回 `reasoning_only` 标记 | `webapp/providers.py` | 推理模型曾「正文空却报可用」 |
| 2026-11-11 · `0.2.0-demo` | 自测客户端超时过短 | 40s→180s | `webapp/selftest.py` | 网关冷启动可达 58s |
| 2026-11-11 · 静态资源 `v=9` | 来源接口双重前缀 | router 已有 `/api/chat` 前缀，装饰器改为 `/sources` 等 | `webapp/routers_chat.py` | 修复前历史/来源接口全部 404 |
| 2026-11-11 · 静态资源 `v=10` | 共享 `.env` 命名空间污染 | webapp provider 开关统一加 `WEBAPP_` 前缀，backend 零改动 | `webapp/settings.py`、`.env`、`tests/conftest.py`、`live_smoke.py` | 两边 `EMBEDDING_PROVIDER` 枚举互斥曾致 backend 启动失败 |
| 2026-11-11 · 静态资源 `v=10` | 向量缓存未隔离模型 | `cache_key` 带 provider + 模型名 | `webapp/settings.py`、`rag.py` | 否则切模型复用旧向量，余弦静默归零 |
| 2026-11-11 · 静态资源 `v=10` | 检索为空用例脆弱 | C12 查询改为随机无意义探针 | `webapp/selftest.py` | 原查询含通用二元组，累积文档后误命中 |
| 2026-11-11 · `v0.1 骨架` | backend 依赖缺失 | 按 `backend/requirements.txt` 补齐 | `backend/`（运行环境） | 此前「没报错只是没跑到」，补齐后 16/16 |
| 2026-11-11 · 静态资源 `v=10` | 脚本遮蔽标准库 | 修正 `sys.path` 顺序，`webapp/queue.py` 不再遮蔽 `queue` | `webapp/eval_report.py` | TestClient 曾无法启动 |
| 2026-11-12 · `2361ece` · `METRICS@1.1.0` | 评分器假阴性 | 拒答判定由固定关键词表改为「否定词 × 资料指代」组合 | `evalbench/core/scoring.py`、`configs/metrics.json` | 正确拒答曾被判 0 分；用 `rescore.py` 生成新 run，旧 run 保留对照 |
| 2026-11-12 · `2361ece` · `METRICS@1.2.0` | 指标饱和 | 新增 `answer_economy`，不参与主指标 | `evalbench/configs/metrics.json`、`core/scoring.py` | 使提示词/参数收敛有可判定依据 |
| 2026-11-12 · `3a7dc41` | 聚合延迟字段被覆盖 | `by_model.avg_latency_ms` 改为汇总全部记录 | `evalbench/core/scoring.py` | 用 `rebuild_summaries.py` 只重建派生 summary，records 未动 |
| 2026-11-12 · `5c0efc7` | 模型探测归因错误 | `max_tokens=24` 空正文改判为网关侧共性而非推理模型专属 | `evalbench/probe_models.py`、`out/models_probe.json` | `--recompute` 只改解释，不改原始测量值 |
| 2026-11-12 · `5c0efc7` | 展示页参数形式不一致 | `report.py --final-run` 同时支持空格与 `=` 形式 | `evalbench/report.py` | README 写的是空格形式 |
| 2026-11-12 · `60720aa` | 完成率超过 100% | 完成度与整体完成率钳制在 `[0,1]`；C16 断言同步 | `webapp/routers_admin.py`、`selftest.py` | 累积 badcase 超目标数时曾显示 105% |
| 2026-11-12 · `60720aa` | 无 value 选项翻译改值 | 翻译前固化中文原文到 `value` | `webapp/static/i18n.js` | 保证 badcase 判定提交值不变 |
| 2026-11-12 · `60720aa` | 动态文案切换不可逆 | 增加 `data-i18n` 精确重设 | `webapp/static/i18n.js`、`app.js` | 语言切换可逆、无刷新 |
| 2026-11-12 · `60720aa` | 测试污染评估运行目录 | router 统一引用可注入的 `obs.EVALBENCH_RUNS`，测试隔离到临时目录 | `webapp/routers_evaltest.py`、`tests/test_evaltest.py` | 避免 pytest 写入 `evalbench/runs/` |
| 2026-11-12 · `60720aa` | 写接口权限口径不一致 | `/ratelimit/probe` 收紧为 admin-only | `webapp/routers_evaltest.py` | 保持「所有写接口仅 admin」 |
| 2026-11-12 21:32 · `v=11` | 静态资源版本不一致 | `app.js?v=10` 统一为 `v=11` | `webapp/static/index.html` | 防止浏览器命中旧 JS 缓存 |

---

## 5. 验证结果快照（2026-11-12 最终态）

| 检查项 | 命令 | 结果 |
|---|---|---|
| webapp 单元测试 | `python -m pytest webapp/tests -q` | 51 passed |
| webapp 端到端自测 | `python webapp/selftest.py` | 33/33 `DEMO_READY` |
| 演示物料自测 | `python demo/selftest.py` | 16/16，`run_id=RUN-095a2a4a40c6` 未变 |
| 评测 harness | `python -m unittest discover -s eval/tests -t .` | 14 OK |
| 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` | 16 OK |
| 生产骨架 | `cd backend && python -m pytest -q tests` | 16 passed |
| 前端语法 | `node --check` × 3（app.js / i18n.js / theme.js） | 全部通过 |
| 冻结边界 | `git diff main..HEAD -- eval/` | 为空；`__TBC__` / `NOT_COMPARABLE` 口径未改 |
| 密钥检查 | `git grep -E 'sk-[A-Za-z0-9]{20,}'` | 无命中；`.env` 未入库 |

---

## 6. 待补充事项与已确认边界

**仍待补充**（仓库与文档中确无记录，不推断）：

| 项目 | 说明 |
|---|---|
| 正式发布版本号（如 `v1.0.0`）、发版说明与变更人 | 项目至今只有基线 / 分支 / commit 与模块内部版本标识 |
| 首日各改进项的精确时刻 | 日志按「同日追加」记录，无时间戳 |
| 真实供应商计费单价与账单校准 | 当前 `PRICES` 为演示估算值，非报价 |
| 质量基线冻结与阈值 | `eval/` 仍 `comparable=false` / `NOT_COMPARABLE`，`real_api` 仍被契约门禁阻断 |
| 视觉对比度的屏幕阅读器实测 | 已做 13 组数值计算（均 ≥4.76），未做辅助技术实测 |

**已确认未落地**（有明确记录，不是信息缺失，故不标「待补充」）：

| 项目 | 状态 |
|---|---|
| 多租户行级隔离、部门树、逐段落 ACL | 架构预留；`webapp/` 仅单租户 `default` + admin 门禁 + owner 过滤 |
| 全局并发闸 / 令牌桶、压测、容灾演练、监控告警、灰度回滚 | 未做；限流为进程内滑动窗口，单实例有效 |
| 内容安全 / PII 脱敏、SSO/OIDC、审计留存合规 | 未做（`docs/operations.md` 标 C 档） |
| 逐句 grounding 高亮、Milvus 真实向量库 | 未做 / 组件替身 |

---

## 7. 本日志修订记录

| 版本或日期 | 改进项名称 | 改动说明 | 影响范围 | 备注 |
|---|---|---|---|---|
| 2026-11-12 21:32 | 改进日志补全 | 首日 15 项改进与 13 项修正由「待补充」改为真实版本标识（`v0.1 骨架 / 4992bed`、`EVAL-PLAN@0.1.0`、场景 `1.0.0`、`0.2.0-demo`、静态资源 `v=3/5/9/10`）；新增版本坐标对照、首日设计文档组、资源版本对齐两处条目；「待补充」收敛至 5 项确无记录的信息，另列 4 项已确认未落地边界 | `docs/CHANGELOG.md` | 依据 `git show 4992bed`、`docs/` 各文档头部版本字段与两日工作日志；Git 于 11-12 才初始化，首日成果全在基线提交中 |
| 2026-11-12 21:32 · `v=11` | 前端缓存版本对齐 | `app.js?v=10` → `v=11` | `webapp/static/index.html` | 与 `style.css?v=11` 一致 |

> 说明：本日志只记录已发生的改动，不把「设计中」「建议」「计划」写成已完成；已确认未落地的项单独列在 §6，不用「待补充」掩盖。
