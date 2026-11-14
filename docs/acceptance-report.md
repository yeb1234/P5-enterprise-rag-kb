# 最终全面测试与功能验收报告

> 交付物：`docs/acceptance-report.md`（本文件）｜产出人：凌无缺（测试验收工程师）
> 对应任务：用户任务 7「进行最终全面测试与功能验收，产出包含测试用例、覆盖范围与结果结论的测试报告」
> 项目定位：企业内部知识库系统的**开发框架骨架 + 可运行演示切片**，用于学习与面试讲解，**不是上线交付版**。

---

## 1. 报告范围与状态声明

### 1.1 项目定位与三态口径

本项目严格区分「**已实现 / 只设计 / 未做**」：`backend/` 为生产骨架（PG / Milvus / Redis / Celery / 外部 DeepSeek），`webapp/` 为零依赖 FastAPI + SQLite + 进程内队列的**演示替身**，`demo/` 与 `eval/` 为离线演示物料与冻结评测对象。本报告的验收对象是「可运行演示切片 + 骨架单元测试」，**不把演示替身说成生产实现，不把只设计/未做的能力写成已验收**。

用户新指令：系统为「**多用户多部门**」架构，**暂不实现多租户**；多租户仅作为未来研发方向写入文档。

### 1.2 本轮代码状态（决定本报告不下结论）

**本轮主理人正在把 `webapp` 演示切片的成本 / 限流统计维度从 `tenant` 改为 `department`，并给 LLM 增加低思考快速档，代码处于变更中。**

因此本报告：

- **不运行最终回归**（不执行 `pytest` / `selftest` / `node --check` 的验收性实跑，不产生本轮实测值）；
- **不下「全绿」结论**，第 5 章「结果结论」为**待主理人回填的表格骨架**；
- 最终实跑由主理人在**代码冻结后统一执行并回填**；
- 报告中出现的 `51 / 33 / 16 / 14 / 16 / 3` 等数字，一律是**历史基线或用例资产计数**，均标注日期与来源，不作为本轮实测；
- 与「维度从 tenant 改为 department」「低思考快速档」相关的条目，在表中明确标注 **【变更中·待回填】**，不计入本轮判定。

### 1.3 事实源与口径优先级

| 事实源 | 用途 |
|--------|------|
| `docs/org-model-change-plan.md` §8.4 / §8.5 | 三分区回归口径、可变/不可变基线、六个一票否决项（**本报告既定口径**） |
| `docs/CHANGELOG.md` §5「验证结果快照（2026-11-12 最终态）」 | 历史实跑命令与结果（引用时标注日期） |
| `docs/enterprise-readiness.md` §8（V1–V14） | 历史验收项与判定（引用时标注来源） |
| `README.md:22`、`docs/operations.md` §1、`docker-compose.yml` | 启动方式、服务与端口、编排定义 |
| 仓库内既有可执行资产（第 2.0 节） | 用例矩阵的唯一映射对象 |
| Phase 1+2 汇编 `phase1-bundle.md`（Part A–K） | 变更裁决口径，优先级 K>J>I>H>G>F>E>A>成员原文 |

历史事实中 `docs/enterprise-readiness.md` §8 V13 记为 `git diff main..HEAD -- eval/`；按 C24-3 裁决，当前有效口径为 **`git diff --exit-code -- eval/ demo/`**（本仓库当前分支 `demo/v1`、无 `main` 分支，工作区零 diff 口径）。本报告统一采用后者，历史条目仅作引用。

### 1.4 本报告不做什么

- 不修改任何源码；不触碰 `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`；
- 不新增校验脚本、不新增测试文件；用例一律映射到既有资产；
- 不运行真实外部 LLM 调用（用户上轮流量耗尽），所有判定均为离线 / 本地可执行；
- 不输出部署迁移 DDL、不做架构选型、不做前端设计；
- **不编造数值**：不设 QPS / P95 / 准确率 / 幻觉率达标率（依据 §8.4、`comparable=false`、阈值全 null、U16/U17/U21 属 Won't）。

---

## 2. 测试用例矩阵

### 2.0 可执行资产映射总览

| 资产 | 数量 | 执行命令 | 事实来源 |
|------|------|---------|---------|
| `webapp/selftest.py` | C01–C33，共 33 项 | `python webapp/selftest.py` → `webapp/selftest_report.json` | `docs/CHANGELOG.md` §5（2026-11-12）；断言见 `webapp/selftest.py` |
| `webapp/tests/` pytest | 51 项：`test_evaltest.py` 10、`test_settings.py` 13、`test_api.py` 8、`test_rag.py` 7、`test_providers.py` 13 | `python -m pytest webapp/tests -q` | `docs/CHANGELOG.md` §5（2026-11-12） |
| `backend/tests/` pytest | 16 项：`test_chunking.py` 4、`test_permission.py` 7、`test_progress.py` 5 | `cd backend && python -m pytest -q tests` | `docs/CHANGELOG.md` §5（2026-11-12） |
| `eval/tests/test_harness.py` | 14 项 unittest | `python -m unittest discover -s eval/tests -t .` | `docs/CHANGELOG.md` §5（2026-11-12） |
| `evalbench/tests/test_evalbench.py` | 16 项 unittest | `python -m unittest discover -s evalbench/tests -t .` | `docs/CHANGELOG.md` §5（2026-11-12） |
| `demo/selftest.py` | C01–C16，共 16 项 | `python demo/selftest.py` | `docs/CHANGELOG.md` §5（2026-11-12） |
| 静态语法 | `node --check` × 3 | `cd webapp/static && node --check app.js && node --check i18n.js && node --check theme.js` | `docs/CHANGELOG.md` §5、`enterprise-readiness.md` §8 V6 |

> 上表数量是**用例资产计数（历史基线）**，按 §8.4 属可变基线，判定口径为「命令全绿 + 新计数单独冻结写入 CHANGELOG」，**不得要求仍等于 51/33/16**。

**全局前置条件 G0**（下表各用例的「前置条件」列均以此为基础）：
`python -m uvicorn webapp.main:app --port 8765` 已启动（`README.md:22`，`webapp/selftest.py:20` 的 `BASE = "http://127.0.0.1:8765"`）；演示库为隔离库；`admin` 账号 `admin/admin123`（`webapp/tests/test_evaltest.py:31`）；不触发真实外部 LLM 调用（使用 offline provider / monkeypatch）。

**判定方式列约定**：`selftest Cxx` 指 `python webapp/selftest.py` 后读取报告 `checks[].passed` 与该用例断言；`pytest X::Y` 指 `python -m pytest webapp/tests/X.py::Y -q`（backend 用例加 `cd backend &&`）；`unittest X` 指对应 discover 命令。

### 2.1 认证与权限

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-AUTH-01 | G0 | GET `/` | 200 且含「企业级知识库」 | selftest C01：`st == 200 and "企业级知识库" in html` | `webapp/main.py`、`webapp/static/index.html` |
| TC-AUTH-02 | G0 | POST `/api/auth/login`（admin） | 200 且 `role == "admin"` | selftest C02 | `webapp/routers_auth.py:42` |
| TC-AUTH-03 | G0 + admin token | GET `/api/auth/me` 带 Bearer | 200 且 `role == "admin"` | selftest C03；`pytest test_api.py::test_login_and_me` | `webapp/routers_auth.py:61`、`webapp/security.py` |
| TC-AUTH-04 | G0 | POST `/api/auth/login` 错误口令 | 401 且提示含「错误」 | selftest C04 | `webapp/routers_auth.py:42` |
| TC-AUTH-05 | G0 | POST `/api/auth/register` 后直接登录 | 200、`role == "user"`、token 非空 | selftest C05 | `webapp/routers_auth.py:19` |
| TC-AUTH-06 | G0 + 普通用户 token | GET `/api/badcases`、`/api/admin/completion`、`/api/badcases/export?format=csv` | 三者均 403 | selftest C24：`st == 403 and st2 == 403 and st3 == 403` | `webapp/routers_badcase.py`、`routers_admin.py:19` |
| TC-AUTH-07 | 隔离库 + TestClient | 普通用户访问 admin 接口 / 写接口 | 403；admin 通过 200/201 | `pytest test_api.py::test_user_cannot_access_admin`；`pytest test_evaltest.py::test_write_endpoints_admin_only` | `webapp/routers_evaltest.py:60/68/126/164/197` |
| TC-AUTH-08 | backend 测试环境 | 构造 Subject/Resource 跑 `decide()` | 显式 deny 优先、viewer 禁写、敏感度上限、子树判定全部符合 | `cd backend && pytest -q tests/test_permission.py`（7 项） | `backend/app/services/permission.py:110–171`、`backend/tests/test_permission.py` |
| TC-AUTH-09 | G0 + admin token | GET `/api/admin/users` / `/api/admin/audit` | admin 200 且字段齐全；普通用户 403 | selftest C31 / C32 | `webapp/routers_ops.py:115/141` |

### 2.2 文档上传与异步队列

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-UP-01 | G0 + admin token | 批量 POST `/api/docs/upload` | `busy == True` 且队列有任务 | selftest C07：`busy.get("busy") is True` | `webapp/routers_docs.py:18`、`webapp/queue.py` |
| TC-UP-02 | TC-UP-01 后 | GET `/api/docs` | 上传任务全部进入台账（3 个 + 样例） | selftest C08：`len(up_ids) == 3` | `webapp/routers_docs.py:73` |
| TC-UP-03 | 队列排空后 | GET `/api/system/busy` | 200 且 `busy is False` | selftest C26 | `webapp/routers_docs.py:115` |
| TC-UP-04 | G0 | GET `/api/eval-tasks`；POST `/api/docs/seed-sample`；DELETE `/api/docs/{id}` | 任务列表 ≥3；种子与删除可用 | selftest C06 前置 + 接口冒烟 | `webapp/routers_docs.py:120/143/91` |
| TC-UP-05 | backend 测试环境 | 构造阶段进度与批量权重 | 进度单调、上传阶段 20%、按文件大小加权、取消文件排除、空批次不除零 | `cd backend && pytest -q tests/test_progress.py`（5 项） | `backend/app/services/progress.py`、`backend/tests/test_progress.py` |
| TC-UP-06 | backend 骨架 | 上传 202 + SSE 进度 | 返回 202，`type/seq/batch_id/file/ts` 事件结构完整 | `backend/app/api/v1/uploads.py` 冒烟（骨架，非本轮实跑） | `backend/app/api/v1/uploads.py`、`docs/async-upload.md` |
| TC-UP-07 | G0 | 上传不支持格式 `unsupported.pdf` | `status == "error"` 且 `error_msg` 非空（降级留痕、不静默） | selftest C10 | `webapp/routers_docs.py:18` |

### 2.3 解析分块与降级

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-PARSE-01 | TC-UP-02 后 | 轮询 `wait_docs_ready(timeout=20)` | ≥4 篇 ready 且 `chunk_count > 0` | selftest C09：`ok_ready >= 4` | `webapp/selftest.py:109`、`webapp/rag.py` |
| TC-PARSE-02 | G0 | 上传不支持格式 | 见 TC-UP-07 | selftest C10 | `webapp/routers_docs.py:18` |
| TC-PARSE-03 | backend 测试环境 | 切分带 page/section 的文本 | 分块保留 page 与 section 元数据 | `cd backend && pytest -q tests/test_chunking.py::test_split_keeps_page_and_section_metadata` | `backend/app/services/document.py` / chunking |
| TC-PARSE-04 | 同上 | 构造带重叠窗口的分块 | 窗口重叠存在且边界正确 | `test_chunking.py::test_chunk_window_overlap_exists` | `backend/tests/test_chunking.py:26` |
| TC-PARSE-05 | 同上 | 两次调用 stub embedding | 输出确定且已归一化 | `test_chunking.py::test_stub_embedding_is_deterministic_and_normalized` | `backend/tests/test_chunking.py:44` |
| TC-PARSE-06 | monkeypatch LLM 报错 | 发起 chat | 优雅降级、不抛出未捕获异常 | `pytest test_api.py::test_chat_model_error_degradation`；`pytest test_rag.py::test_model_error_degradation` | `webapp/rag.py`、`webapp/providers.py` |
| TC-PARSE-07 | monkeypatch 空模型输出 | 发起 chat | 主动降级且不编造答案 | `pytest test_rag.py::test_empty_model_output_degrades` | `webapp/rag.py` |
| TC-PARSE-08 | monkeypatch 嵌入不可用 | 触发检索 | 兜底到词法检索 | `pytest test_settings.py::test_embedding_fallback_to_lexical` | `webapp/settings.py`、`webapp/rag.py` |

### 2.4 检索与重排

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-RET-01 | G0 | chat 随机无意义探针 `probe-<uuid>` | `degraded is True`、`degrade_reason == "retrieval_empty"`、答案含「没有找到」 | selftest C12（`:208–212`） | `webapp/rag.py`、`webapp/routers_chat.py:55` |
| TC-RET-02 | G0 | GET `/api/search/preview?q=配网` 与 `?q=zzqqxx-nonexistent-9987` | 前者 `hit_count > 0`；后者 `hit_count == 0` 且降级 | selftest C29（`:337–341`） | `webapp/routers_ops.py:89` |
| TC-RET-03 | 单元环境 | 空检索输入 | 降级而非编造 | `pytest test_rag.py::test_retrieval_empty_degrades` | `webapp/rag.py` |
| TC-RET-04 | 单元环境 | 中文词法门禁分词 | 中文二元组正确切分 | `pytest test_rag.py::test_lexical_terms_chinese_bigram` | `webapp/rag.py` |
| TC-RET-05 | backend 单元环境 | RRF 融合两路召回 | 合并去重且排名正确 | `cd backend && pytest -q tests/test_chunking.py::test_rrf_fusion_merges_and_ranks` | `backend/app/services/search.py` |
| TC-RET-06 | monkeypatch rerank 不可用 | 触发重排 | `skipped` 留痕、不阻断 | `pytest test_settings.py::test_rerank_unavailable_skipped` | `webapp/providers.py` |
| TC-RET-07 | monkeypatch LLM rerank | 触发重排 | 打分解析生效并改变顺序 | `pytest test_settings.py::test_rerank_llm_applied_and_reorders`；`test_rerank_with_llm_parse` | `webapp/providers.py` |
| TC-RET-08 | 单元环境 | 计算余弦相似度 | 值域有界 | `pytest test_settings.py::test_cosine_bounds` | `webapp/rag.py` |
| TC-RET-09 | monkeypatch 两 provider | 检查 `effective_embedding()["cache_key"]` | 切模型不复用旧向量（命名空间隔离） | `pytest test_settings.py::test_embedding_cache_key_isolated_across_providers` | `webapp/providers.py` |
| TC-RET-10 | monkeypatch 外部嵌入 | 语义排序路径 | 走 semantic rank 且使用配置化 key | `pytest test_settings.py::test_retrieve_external_embedding_uses_semantic_rank`；`test_external_embedding_effective_config` | `webapp/providers.py`、`webapp/settings.py` |

### 2.5 SSE 流式与断线恢复

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-SSE-01 | G0 + admin token | POST `/api/chat/stream` | 200、逐 token >1、`done` 存在、`conversation_id` 非空 | selftest C11（`:197–199`） | `webapp/routers_chat.py:55` |
| TC-SSE-02 | TC-SSE-01 的 `conv` | 同问题带 `resume_from=30` 续传 | `m3.resume_from == 30` 且 token 索引从 30 起严格连续 | selftest C13（`:219–224`） | `webapp/routers_chat.py:55`、`webapp/rag.py` |
| TC-SSE-03 | 单元环境 | 模拟流中断 | 不追加 fallback 内容 | `pytest test_rag.py::test_stream_interrupted_does_not_append_fallback` | `webapp/rag.py` |
| TC-SSE-04 | 单元环境 | 校验续传索引 | 连续性成立 | `pytest test_rag.py::test_resume_from_index_continuity` | `webapp/rag.py` |
| TC-SSE-05 | monkeypatch | TestClient 发起 chat stream | 流式 happy path 通过 | `pytest test_api.py::test_chat_stream_happy` | `webapp/routers_chat.py:55` |
| TC-SSE-06 | offline provider | 调用 stream | 逐 token 产出 | `pytest test_providers.py::test_offline_provider_stream_tokens` | `webapp/providers.py` |
| TC-SSE-07 | 构造 `<think>` 片段 | 单块 / 跨块 / 普通文本三种输入 | 内联 think 被过滤、普通文本透传 | `pytest test_providers.py::test_filter_inline_think_single_chunk` / `_split_across_chunks` / `_passthrough` | `webapp/providers.py` |
| TC-SSE-08 | 构造空 choices | 流式读取 | 容忍空 choices 不崩 | `pytest test_providers.py::test_stream_tolerates_empty_choices` | `webapp/providers.py` |

### 2.6 来源追溯

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-SRC-01 | 已产生一次带来源的问答 | TestClient 校验来源追溯 | SSE `meta`/`done` 携来源且可回放 | `pytest test_api.py::test_chat_source_traceability` | `webapp/routers_chat.py:55`、`webapp/db.py`（`chat_sources`） |
| TC-SRC-02 | G0 | GET `/api/chat/sources` | 200 且来源记录与会话一致 | 接口冒烟（`routers_chat.py:185`） | `webapp/routers_chat.py:185` |
| TC-SRC-03 | G0 | GET `/api/chat/conversations`、`/api/chat/history` | 200 且历史会话可回放 | 接口冒烟（`routers_chat.py:198/232`） | `webapp/routers_chat.py:198/232` |
| TC-SRC-04 | 文档记录 | 核对 `docs/CHANGELOG.md` `v=9` 条目 | 「智能问答来源追溯」三态标注为演示级已落地 | 文档逐条核对（非执行用例） | `docs/CHANGELOG.md:55` |

### 2.7 badcase 采集 - 筛选 - 导出

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-BC-01 | 一次问答 + 有效 `eval_task_id` | POST `/api/badcases` | 8 个必需字段齐全非空 | selftest C14（`:238–240`） | `webapp/routers_badcase.py:70` |
| TC-BC-02 | G0 | 关联不存在的评估任务 | 400 拒绝（一致性） | selftest C15 | `webapp/routers_badcase.py:70` |
| TC-BC-03 | 已有 badcase | GET `/api/admin/completion` | 完成度与关联 badcase 数一致 | selftest C16 | `webapp/routers_admin.py:19` |
| TC-BC-04 | 已有 badcase | 按任务 + 状态筛选 | 命中目标记录 | selftest C17 | `webapp/routers_badcase.py:96` |
| TC-BC-05 | 已有 badcase | 关键字检索问题说明 | 命中目标记录 | selftest C18 | `webapp/routers_badcase.py:96` |
| TC-BC-06 | 已有 badcase | 按时间范围筛选 | 命中目标记录 | selftest C19 | `webapp/routers_badcase.py:96` |
| TC-BC-07 | 已有 badcase | GET `/api/badcases/export?format=csv` 带筛选 | CSV 内容与筛选一致 | selftest C20 | `webapp/routers_badcase.py:147` |
| TC-BC-08 | 已有 badcase | 导出 JSON | 与库内记录逐项一致 | selftest C21 | `webapp/routers_badcase.py:147` |
| TC-BC-09 | 已有 badcase | admin 更新判定与状态 | 200、`status == "resolved"` | selftest C22 | `webapp/routers_badcase.py` |
| TC-BC-10 | TC-BC-09 后 | 回查完成度 | 保持稳定关联 | selftest C23 | `webapp/routers_admin.py:19` |
| TC-BC-11 | TestClient | 创建 → 筛选 → 完成度 | 全链路通过 | `pytest test_api.py::test_badcase_create_filter_completion` | `webapp/routers_badcase.py` |
| TC-BC-12 | 插入 25 条 badcase | GET `/api/admin/completion` | `completion_rate <= 1.0`、`pending >= 0`、`overall <= 1.0` | `pytest test_evaltest.py::test_completion_rate_clamped` | `webapp/routers_admin.py:19` |

### 2.8 评估测试子页与冻结评测对象

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-EV-01 | G0 + admin | GET `/api/eval/objects` | `count == 14`、`registry_id == "REG-KBQA-EVAL"`、`comparable is False`、`quality_verdict == "NOT_COMPARABLE"` | selftest C30（`:345–350`） | `webapp/routers_ops.py:99`、`eval/config/objects/*.json`（14 个） |
| TC-EV-02 | 隔离库 | 查表结构与默认值 | 7 张表存在；`rate_rules` 维度集合正确（**【变更中·待回填】** tenant→department）；budgets 默认 `warn` | `pytest test_evaltest.py::test_new_tables_and_defaults` | `webapp/db.py`、`webapp/observability.py` |
| TC-EV-03 | G0 + admin | 遍历 `/api/evaltest/*` 只读端点 | 全部 200（V8：12 个端点） | `pytest test_evaltest.py::test_evaltest_read_endpoints` | `webapp/routers_evaltest.py:52–220` |
| TC-EV-04 | G0 + admin/普通用户 | 调用写接口 | 普通用户 403、admin 200/201 | `pytest test_evaltest.py::test_write_endpoints_admin_only` | `webapp/routers_evaltest.py:60/68/126/164/197` |
| TC-EV-05 | 单元环境 | 调用 `estimate_tokens` / `cost_of` | 空串 0、估算 >0、`PRICE_NOTE` 含「演示估算」 | `pytest test_evaltest.py::test_cost_rules_and_estimation` | `webapp/observability.py` |
| TC-EV-06 | 两笔不同用户用量 | 普通用户查 `usage?dim=call_type` | `scope == "仅本人调用"` 且 `calls == 0`（不查而非隐藏） | `pytest test_evaltest.py::test_usage_ledger_and_scope_isolation`；V11 | `webapp/observability.py`、`routers_evaltest.py:107` |
| TC-EV-07 | G0 + admin | POST `/api/evaltest/run` | `ok`、`comparable is False`、`n_records >= 1`、run 数 +1、`eval_done` 日志留痕 | `pytest test_evaltest.py::test_offline_eval_run_is_real_and_leaves_trace` | `webapp/routers_evaltest.py:68` |
| TC-EV-08 | TestClient | GET `/`、`/i18n.js`、`/theme.js`、`/style.css`、`/app.js` | 全部 200 且标记串存在 | `pytest test_evaltest.py::test_static_assets_and_i18n_theme`；V12 | `webapp/static/*` |
| TC-EV-09 | eval 冻结对象 | 跑 14 项 harness | 14 OK；registry 14 对象与稳定哈希、六步有序、`real_api` 阻断、双跑确定性 | `python -m unittest discover -s eval/tests -t .` | `eval/tests/test_harness.py`（14 个 `def test_`） |
| TC-EV-10 | evalbench | 跑 16 项 unittest | 16 OK（归一化、拒答、faithfulness、格式、聚合、prompt 版本等） | `python -m unittest discover -s evalbench/tests -t .` | `evalbench/tests/test_evalbench.py`（16 个 `def test_`） |
| TC-EV-11 | demo 物料 | 跑 16 项自测 | 16/16 `DEMO_READY`；7 字段与 `run_id=RUN-095a2a4a40c6` 全一致 | `python demo/selftest.py`；`:158 config_hash`、`:221 config_snapshot_hash`、`:225`「六步 全部一致」 | `demo/selftest.py`、`demo/demo_runner.py:193` |
| TC-EV-12 | 冻结边界 | 对比工作区 | `eval/` 与 `demo/` 零字节改动 | `git diff --exit-code -- eval/ demo/` | `eval/`、`demo/` |

### 2.9 限流与预算

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-RL-01 | 阈值调至 2/60s | 连续 3 次 GET `/api/evaltest/config` | 第 3 次 429 + `Retry-After` + `X-RateLimit-Dimension == "user"` + 命中台账 | `pytest test_evaltest.py::test_rate_limit_returns_429_with_headers`；V9 | `webapp/observability.py`、`routers_evaltest.py:151/164` |
| TC-RL-02 | 构造两类 429 | 调用 `classify_429()` | **区分 `rate_limit_exceeded` 与 `quota_exceeded` 两类** | `pytest test_providers.py::test_classify_429_splits_quota_and_rate` | `webapp/providers.py` |
| TC-RL-03 | quota 类 429 | 发起调用 | **零重试、快速失败** | `pytest test_providers.py::test_quota_429_fails_fast_without_retry` | `webapp/providers.py` |
| TC-RL-04 | rate 类 429 | 发起调用 | 1/2/4s + 抖动退避、尊重 `Retry-After`，上限后抛出 | `pytest test_providers.py::test_rate_429_retries_then_raises`；`test_backoff_uses_retry_after_and_jitter` | `webapp/providers.py` |
| TC-RL-05 | `limit_cny=0, policy=block` | POST `/api/evaltest/run` | `ok is False`、`reason == "budget_exceeded"`、ERROR 日志留痕 | `pytest test_evaltest.py::test_budget_block_rejects_new_eval_run`；V10 | `webapp/observability.py`、`routers_evaltest.py:126` |
| TC-RL-06 | quota 超限 | 走 rag 链路 | 映射为独立 reason、不混同 rate | `pytest test_rag.py::test_quota_exceeded_maps_to_own_reason` | `webapp/rag.py` |
| TC-RL-07 | 首次 429 后成功 | 重试链路 | 退避后成功返回 | `pytest test_providers.py::test_retry_then_success` | `webapp/providers.py` |
| TC-RL-08 | 持续 429 | 发起调用 | 到达上限后抛出、不无限重试 | `pytest test_providers.py::test_always_429_raises` | `webapp/providers.py` |
| TC-RL-09 | **【变更中·待回填】** 维度改造中 | 校验四维与响应头 | `user｜department｜endpoint｜model` 且不含 tenant；`usage_events.department` 与面板映射正确；**不断言 429 按部门隔离生效** | 待主理人代码冻结后按 A7 五条断言回填 | `webapp/observability.py:252`、`db.py:325`、`routers_chat.py:129`、`routers_evaltest.py:72` |
| TC-RL-10 | **【变更中·待回填】** 低思考快速档 | 切换档位并离线验证 | 模型选择可配置、离线兜底不触发真实外部 LLM 调用 | 待主理人代码冻结后回填（不运行真实外部 LLM） | `webapp/settings.py`、`webapp/providers.py` |

### 2.10 i18n 与主题

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-I18N-01 | G0 | GET 5 个静态资源 | 均 200 且标记串存在 | `pytest test_evaltest.py::test_static_assets_and_i18n_theme`；V12 | `webapp/static/index.html`、`app.js`、`i18n.js`、`theme.js`、`style.css` |
| TC-I18N-02 | G0 | GET `/` 与 `/app.js?v=8` | **C33：`nav_ok == 10 and loaders_ok == 6`**（十视图双向齐全） | selftest C33（`:375–377`） | `webapp/static/index.html`、`app.js` |
| TC-I18N-03 | Node 可用 | 语法检查 3 个 JS | 3/3 通过 | `cd webapp/static && node --check app.js && node --check i18n.js && node --check theme.js`；V6 | `webapp/static/*.js` |
| TC-I18N-04 | i18n 字典 | 校验 `t()` 覆盖 | 字典零缺口；HTML 中文片段 277 条仅 2 条刻意保留 | `enterprise-readiness.md` §8 V7（历史验收项，脚本校验） | `webapp/static/i18n.js`、`index.html` |
| TC-I18N-05 | G0 | 检查首页主题标记 | `data-theme` / `data-mode` / `kb-swatches` 至少一项存在 | `pytest test_evaltest.py::test_static_assets_and_i18n_theme` | `webapp/static/index.html`、`theme.js` |
| TC-I18N-06 | G0 | 检查评估子页挂载 | `view-evaltest` / `data-view="evaltest"` / `id="et-config"` / `id="et-cost"` 齐全 | 同上 | `webapp/static/index.html`、`app.js` |
| TC-I18N-07 | 静态源码核对（不启动服务） | 提取 `index.html` 全部内联事件处理器（`onclick` / `onchange` 等）→ 与 `app.js` 的 `function` / `const` / `window.*` 定义对拍 → 跨文件全局单独核验 | **已复核：内联事件处理器与函数定义一致，跨文件全局由 `i18n.js` / `theme.js` 提供**，无 ReferenceError 风险 | 主理人正则对拍（40 个 inline handlers / 94 个 `app.js` 定义名，详见 6.4）；本报告不重复实跑、不预填结论 | `webapp/static/index.html`、`app.js`、`i18n.js:921/945`、`theme.js` |

### 2.11 部署编排

| 用例 ID | 前置条件 | 操作步骤 | 预期结果 | 判定方式（命令 / 断言） | 代码 / 接口路径 |
|---------|---------|---------|---------|------------------------|----------------|
| TC-DEP-01 | 仓库根 | 解析 `docker-compose.yml` | 11 个 service 定义齐备（postgres/redis/minio/etcd/milvus/api/worker-light/worker-heavy/beat/frontend/nginx），端口与 `docs/operations.md` §1 一致 | `docker compose config --quiet`（**编排校验，不启动容器、不调用外部服务**） | `docker-compose.yml`、`docs/operations.md:9–17` |
| TC-DEP-02 | 同上 | 检查 healthcheck | postgres/redis/minio/milvus/api 均有 healthcheck 且 `depends_on` 带 `service_healthy` | `docker compose config` 输出核对；人工逐项 | `docker-compose.yml:16–20,28–32,43–47,73–77,92–96` |
| TC-DEP-03 | 编排定义 | 检查迁移为显式执行 | `api` 启动不自动迁移；迁移命令 `docker compose exec api alembic upgrade head` 有文档依据 | `docs/operations.md:76` 逐条核对 | `docs/operations.md:76`、`backend/migrations/` |
| TC-DEP-04 | 仓库根 | 检查密钥外置与默认密钥拒绝启动 | `.env.example` 存在；生产默认 `JWT_SECRET_KEY` 时 `lifespan` 拒绝启动 | `git grep -E 'sk-[A-Za-z0-9]{20,}'` 零命中；`docs/operations.md:19–20` 核对 | `.env.example`、`backend/app/core/config.py`、`backend/app/main.py` |
| TC-DEP-05 | 仓库根 | 检查端口与反代 | nginx 8080 反代 `/api/`（SSE 关缓冲）、api 8000 `--workers 2` | `docs/operations.md:9–17`、`nginx/nginx.conf` 核对 | `nginx/nginx.conf`、`docker-compose.yml:136–141` |
| TC-DEP-06 | G0 + admin/普通用户 | GET `/api/overview` 双角色 | admin 含 6 个字段且 `admin` 子块为 dict；普通用户 `admin is None`、`scope == "仅本人数据"` | selftest C27（`:325–328`） | `webapp/routers_ops.py:22` |
| TC-DEP-07 | TC-DEP-06 | 对比双角色统计范围 | 普通用户 docs/chat 计数 ≤ admin，且为「不查而非隐藏」 | selftest C28（`:329–333`） | `webapp/routers_ops.py:22` |
| TC-DEP-08 | G0 + admin | GET 概览字段 | `docs/chunks/chat/badcases/system/admin` 齐全 | selftest C27 | `webapp/routers_ops.py:22` |

### 2.12 用例统计

| 模块 | 用例数 | 主要映射资产 |
|------|--------|-------------|
| 认证与权限 | 9 | selftest C01–C05/C24/C31/C32；webapp pytest 2 项；backend 7 项 |
| 文档上传与异步队列 | 7 | selftest C06–C08/C10/C26；backend 5 项 |
| 解析分块与降级 | 8 | selftest C09/C10；backend 4 项；webapp pytest 4 项 |
| 检索与重排 | 10 | selftest C12/C29；backend 1 项；webapp pytest 7 项 |
| SSE 流式与断线恢复 | 8 | selftest C11/C13；webapp pytest 8 项 |
| 来源追溯 | 4 | webapp pytest 1 项；接口冒烟 3 项 |
| badcase 采集-筛选-导出 | 12 | selftest C14–C23；webapp pytest 2 项 |
| 评估测试子页 | 12 | selftest C30；webapp pytest 7 项；eval 14；evalbench 16；demo 16；冻结 diff |
| 限流与预算 | 10 | webapp pytest 8 项；【变更中】2 项 |
| i18n 与主题 | 7 | selftest C33；webapp pytest 1 项；node --check ×3；主理人复核 TC-I18N-07 |
| 部署编排 | 8 | selftest C27/C28；compose / operations 静态核对 |
| **合计** | **95 条** | 其中 2 条标注【变更中·待回填】，93 条为既有资产可映射用例（含 1 条主理人复核记录 TC-I18N-07） |

---

## 3. 覆盖范围：不可变区 / 可变区 / 新增回归

> 本节按 `docs/org-model-change-plan.md` §8.4 既定的**三分区**口径组织，**不使用**「冻结验收 / 改动模块回归 / 全量回归」命名（C25-3 已废弃该转述误差）。

### 3.1 不可变区 I1–I3

| 目录 / 对象 | 为什么不可变 | 复核命令 |
|-------------|-------------|---------|
| `eval/`（14 个版本化对象 + `__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径） | 评测冻结契约；14 对象规范化哈希决定 `config_snapshot_hash`，零 diff 即哈希不变 | `git diff --exit-code -- eval/ demo/` |
| `eval/config/`（含 `eval/config/objects/*.json` 14 个） | 冻结对象注册表与配置快照来源 | 同上 |
| `demo/`（含 `demo/selftest.py`、`demo/demo_runner.py`、`run_id=RUN-095a2a4a40c6`、`config_snapshot`） | 演示物料单一事实源；run_id 与 7 字段逐字一致断言 | `python demo/selftest.py`（16/16、`DEMO_READY`、`:158/:221/:225`） |
| `evalbench/`（含 `evalbench/tests/` 16 项、`evalbench/runs|cache|out` 运行产物） | 评估工作台完全不动、无需适配层；运行产物非源码 | `python -m unittest discover -s evalbench/tests -t .`（16 OK） |
| `docs/evaluation-plan.md` 第 706 / 733 行含 `__TBC__` 的字面量及周边文字 | 冻结口径；按 C25-6 按「两条特指」执行，不做全文件排除（该文件 `__TBC__` 共 20+ 处） | `git diff --exit-code -- docs/evaluation-plan.md` + 逐行核对 |
| `node_modules/`、`docs/demo/`（主理人补充排除区，非架构师原文） | 第三方依赖 / 演示话术，非清零目标且禁止改动 | 排除区声明，仅记录 |

**不可变区复核命令（I1–I3）**：
1. `git diff --exit-code -- eval/ demo/`（exit 0）
2. `python demo/selftest.py`（16/16、`run_id` 未变）
3. `python -m unittest discover -s eval/tests -t .`（14 OK）与 `python -m unittest discover -s evalbench/tests -t .`（16 OK）

> 口径说明：`eval` 14 / `evalbench` 16 的**命令**在 §8.4「可变基线」列表中出现，是因为它们同属「命令全绿 + 计数冻结」判定；但在「回归三分区」中归入不可变区 I3（只读校验）。两者不矛盾：其断言对象是冻结对象，**计数不会增长**，零字节改动要求由不可变区承担。本报告按 §8.4 的三分区表述收录，并注明不把 14/16 写成可增长计数。

### 3.2 可变区 M1–M7

| 目录 / 对象 | 为什么可变 | 复核命令 |
|-------------|-----------|---------|
| `webapp/tests/`（51 项 pytest） | 维度断言、四维限流、用量范围隔离断言需同步改口径；计数允许增长 | `python -m pytest webapp/tests -q` |
| `webapp/selftest.py`（33 项 C01–C33） | 演示切片补齐部门视图与最小可见范围；计数允许增长 | `python webapp/selftest.py` |
| `backend/tests/`（16 项 pytest，含 `test_permission.py`） | 扩充部门口径越权用例，计数纳入可变区（§8.4） | `cd backend && python -m pytest -q tests` |
| `webapp/static/*.js`（3 个） | 静态资源与视图随演示能力调整 | `cd webapp/static && node --check app.js && node --check i18n.js && node --check theme.js` |
| `webapp/observability.py`、`webapp/db.py`、`routers_chat.py`、`routers_evaltest.py` | **本轮正在把成本 / 限流统计维度从 tenant 改为 department**（`CONFIG_KEYS` 4 处同步） | 【变更中·待回填】A7 五条断言；不运行真实外部 LLM |
| `webapp/settings.py`、`webapp/providers.py` | **本轮正在加低思考快速档** | 【变更中·待回填】离线校验，不运行真实外部 LLM |
| `docs/CHANGELOG.md`（**仅新增条目，历史不回改**） | 新计数冻结写入 §7 | 逐字核对新增条目，不改动历史行 |

**可变区复核命令**：`python -m pytest webapp/tests -q`、`python webapp/selftest.py`、`cd backend && python -m pytest -q tests`、14 OK / 16 OK、`node --check` ×3、`git grep -E 'sk-[A-Za-z0-9]{20,}'`（零命中）、新计数冻结写入 CHANGELOG。判定为「命令全绿 + 新计数单独冻结」，**不得要求仍等于 51/33/16**。

### 3.3 新增回归 N1–N10

| 目录 / 对象 | 为什么新增 | 复核命令 |
|-------------|-----------|---------|
| `backend/tests/test_permission.py` 内新增用例（不新建脚本） | A13 双向对拍、列表侧 `decide()` 复核负例、30 条部门口径越权用例 | `cd backend && python -m pytest -q tests -k a13` 等 |
| `backend/app/services/permission.py`、`document.py:25 apply_filters()` | 四处可见集合口径收敛、列表侧逐条复核 | A13 七断言 |
| `backend/app/integrations/milvus_client.py` | expr 首条件不可被 `extra` 覆盖、`__ORG__` 无部门哨兵 | A13⑥、A6 变异测试 |
| `backend/app/core/deps.py` / `security.py` | JWT 收敛只带 `sub`、Subject 服务端派生 | A13⑤（请求体伪造 + JWT 旧身份负例） |
| 缓存 / 任务 / 部门树 | 缓存串部门、任务跨部门、部门移动即时重算、旧授权快照不命中 | N2/N3/N9 专项用例 |
| `webapp/` 演示切片最小部门能力 | C5 分层补齐（部门视图、文档按部门过滤） | selftest 新增计数 + A13 演示侧对拍（若补） |

**新增回归十项**：N1 A13 七断言双向对拍、N2 缓存串部门、N3 任务跨部门、N4 `department_id` 伪造、N5 列表侧 `decide()` 复核负例、N6 JWT 旧身份负例、N7 Resource 属性完备性（批量回查 PG，失败一律 deny）、N8 无部门哨兵 `__ORG__`、N9 部门树移动、N10 A6 变异测试（`clauses[0]` 换成可被 `extra` 覆盖写法时 A13 必须失败）。

### 3.4 排除区两套口径（分别标注来源）

- **基线排除区（架构师逐字原文，C25-2 采用）**：`eval/`、`demo/`、`evalbench/`、`eval/config/`，以及 `docs/evaluation-plan.md` 第 706 行「账号/租户/工作空间 `EMBEDDING_SERVICE_ACCESS__TBC`」与第 733 行 `EMBEDDING_CACHE__TBC` 含 `__TBC__` 的行及周边文字。
- **主理人补充排除区（C25-2 明确标注为补充，非架构师原文）**：`evalbench/runs|cache|out`（运行产物目录，非源码）、`docs/demo/`（演示话术，属文档纠偏范围但非 tenant 清零目标）、`node_modules/`（第三方依赖）。

两套均「不作清零目标且禁止改动」。

### 3.5 复核命令清单与执行顺序

顺序按 §8.4：**I（不可变区）→ M（可变区）→ N（新增回归）→ 残留清零 → 新计数冻结 → Go/No-Go**。

```
I1  git diff --exit-code -- eval/ demo/
I2  python demo/selftest.py
I3  python -m unittest discover -s eval/tests -t .
    python -m unittest discover -s evalbench/tests -t .
M1  python -m pytest webapp/tests -q
M2  python webapp/selftest.py
M3  cd backend && python -m pytest -q tests
M4  cd webapp/static && node --check app.js && node --check i18n.js && node --check theme.js
M5  git grep -E 'sk-[A-Za-z0-9]{20,}'
N1–N10  见 3.3（按各专项用例命令）
残留清零  五组 git grep（见 docs/org-model-change-plan.md 对应章节）
新计数冻结  docs/CHANGELOG.md §7 新增一条
```

**失败处置**：不可变区任一失败 = P0 立即停止；可变区红 = P1（定位断言口径未同步还是实现缺陷，禁止 skip/xfail 迁就）；新增回归中越权/身份类失败 = P0。

---

## 4. 可量化验收标准

> 统一格式：**指标定义 + 阈值 + 验证命令 + 通过判定**。数值一律引用事实源；性能类给出可实测口径，**不编造 P95 / QPS 数字**；离线 `comparable=false` 不写质量基线。带【变更中·待回填】的条目不计入本轮判定。

### 4.1 功能类

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-FUN-01 | 端到端自测通过率 = `passed / total` | `verdict == "DEMO_READY"`，即 `passed == total`（历史基线 33/33） | `python webapp/selftest.py` → `webapp/selftest_report.json` | 报告 `summary.failed == 0` 且 `summary.verdict == "DEMO_READY"` |
| Q-FUN-02 | webapp 单元测试通过数 | 命令全绿；计数 N1 允许 ≠51，须冻结写入 CHANGELOG | `python -m pytest webapp/tests -q` | exit 0 且输出无 failed/error；N1 为实跑抄录 |
| Q-FUN-03 | backend 单元测试通过数 | 命令全绿；计数 N3 允许 ≠16（越权用例扩充），须冻结 | `cd backend && python -m pytest -q tests` | exit 0；N3 写入 CHANGELOG |
| Q-FUN-04 | 演示物料 16 项与 run_id 一致性 | `16/16`、`run_id == "RUN-095a2a4a40c6"`、7 字段全一致 | `python demo/selftest.py` | `DEMO_READY`，`:158/:221/:225` 断言全通过 |
| Q-FUN-05 | 冻结 harness 通过数 | eval `14 OK`、evalbench `16 OK`（计数不变） | 两条 `unittest discover` 命令 | 输出 `OK`，无 failure/error |
| Q-FUN-06 | 检索为空降级正确率 | `degraded is True and degrade_reason == "retrieval_empty" and "没有找到" in answer`（C12） | selftest C12；`pytest test_rag.py::test_retrieval_empty_degrades` | 两处均通过；随机探针保证零命中 |
| Q-FUN-07 | SSE 断线续传索引连续性 | `resume_from == 30`、首 token `index == 30`、全序列 `index == 30+i`（C13） | selftest C13；`pytest test_rag.py::test_resume_from_index_continuity` | 两处均通过 |
| Q-FUN-08 | 评测中心对象清单一致性 | `count == 14`、`registry_id == "REG-KBQA-EVAL"`、`comparable is False`、`quality_verdict == "NOT_COMPARABLE"`（C30） | selftest C30 | 四个断言同时为真 |
| Q-FUN-09 | 离线评估不得写质量基线 | `comparable == false`、`composite_score == null`、阈值全 null | `pytest test_evaltest.py::test_offline_eval_run_is_real_and_leaves_trace`；eval 14 harness | 报告 / 文档中不出现任何「质量基线达标率」数值 |
| Q-FUN-10 | 429 分类覆盖 | `rate_limit_exceeded` 可退避重试、`quota_exceeded` 零重试，两类可区分 | `pytest test_providers.py::test_classify_429_splits_quota_and_rate`、`test_quota_429_fails_fast_without_retry`、`test_rate_429_retries_then_raises` | 三项均通过 |

### 4.2 权限类

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-AUTH-01 | 部门口径越权拦截率 = 被拦截越权请求 / 越权请求总数 | **100%（30/30）** | N1–N10 专项用例 + `test_permission.py` | 零漏放；任一漏放即 No-Go（§8.5 第 3 项） |
| Q-AUTH-02 | A13 列表集合 ↔ 检索召回集合一致性 | 对称差为空，7 条断言 **7/7** | `cd backend && python -m pytest -q tests -k a13` | 7/7 全通过；任一失败即 No-Go |
| Q-AUTH-03 | admin 门禁拒绝率 | 普通用户访问 admin 读/写/导出接口 **100% 返回 403** | selftest C24/C31/C32；`test_evaltest.py::test_write_endpoints_admin_only`；`test_api.py::test_user_cannot_access_admin` | 所有负例 403；admin 正例 200/201 |
| Q-AUTH-04 | 「不查而非隐藏」 | 普通用户 `usage.calls == 0` 且 `scope == "仅本人调用"`；概览 `admin is None` | `test_evaltest.py::test_usage_ledger_and_scope_isolation`；selftest C27/C28（V11） | 计数为 0 而非隐藏全局数字；普通用户各项 ≤ admin |
| Q-AUTH-05 | 显式 deny 优先级 | 4 角色下显式 deny 文档全部拒绝 | `test_permission.py::test_explicit_deny_wins_over_everything`；A13③ | 4/4 deny；候选集内应拒绝文档两入口均过滤 |
| Q-AUTH-06 | 身份不可伪造 | 请求体伪造 `department_id` + JWT 注入旧 `role`/`department_id` 两类负例，返回集合与基线完全一致 | A13⑤ 专项用例（N4/N6） | 集合差集为空且无扩权 |
| Q-AUTH-07 | 四维统计维度（**【变更中·待回填】**） | `user｜department｜endpoint｜model` 且不含 tenant；`X-RateLimit-Dimension` 同步；**不断言 429 按部门隔离生效** | 代码冻结后按 A7① 回填；`pytest test_evaltest.py::test_new_tables_and_defaults` | 四维集合相等；历史 `{"user","tenant","endpoint","model"}` 断言已同步改写 |

### 4.3 性能类（可实测口径，不编造 P95 / QPS）

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-PERF-01 | 端到端自测总耗时可观测性 = `selftest_report.json.summary.duration_seconds`（`webapp/selftest.py:383` 产出） | **本轮不设固定秒数阈值**；代码冻结后主理人首跑登记为基线值 B，后续回归判定 `duration <= 1.5 × B` 且 `failed == 0` | `python webapp/selftest.py` → 读取 JSON | 字段存在且为有限正数（>0）；两次连续运行均产出该字段；B 与每次实跑值一并写入 CHANGELOG |
| Q-PERF-02 | 异步队列收敛窗口 = `wait_docs_ready(token, ids, timeout=20)` 内 ready 文档数（`webapp/selftest.py:109`） | **≥4 篇在 20s 内 ready 且 `chunk_count > 0`**（selftest C09 现有断言 `ok_ready >= 4`） | selftest C09 | `ok_ready >= 4` 且 chunks 列表非空；超时未收敛即失败 |
| Q-PERF-03 | 无重试放大 = quota 类 429 的重试次数 | **重试计数 == 0**（快速失败） | `pytest test_providers.py::test_quota_429_fails_fast_without_retry` | 通过；rate 类按 1/2/4s + 抖动退避且尊重 `Retry-After`（`test_backoff_uses_retry_after_and_jitter`） |
| Q-PERF-04 | 确定性计算 = 相同输入两次运行输出一致性 | **完全相等**；stub embedding 归一化（范数 1） | `cd backend && pytest -q tests/test_chunking.py::test_rrf_fusion_merges_and_ranks` 与 `::test_stub_embedding_is_deterministic_and_normalized`；`demo/selftest.py` C13 双跑一致 | 断言通过；demo 双跑除 `generated_at` 外逐字节一致 |
| Q-PERF-05 | 进度阶段百分比稳定性 = 上传阶段占比与批量加权 | 上传阶段 **20%**、进度单调、取消文件排除、空批次不除零（backend 现有断言） | `cd backend && pytest -q tests/test_progress.py` | 5/5 通过 |

> 说明：本项目为骨架 + 演示切片，无压测环境、无 SLA、无监控告警（U21/U22/G6 属 Won't），**不设 QPS / P95 / 准确率 / 幻觉率达标率**。以上均为「结构性 / 确定性 / 可观测」口径，阈值来自既有断言或基线登记法，不含编造的延迟数字。

### 4.4 安全类

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-SEC-01 | 冻结边界 diff 行数 = `eval/` 与 `demo/` 工作区改动 | **0 行、exit 0** | `git diff --exit-code -- eval/ demo/` | exit 0 且无输出；非 0 即 No-Go（§8.5 第 2 项） |
| Q-SEC-02 | 密钥入库命中数 | **0 命中**；`.env` 未入库 | `git grep -E 'sk-[A-Za-z0-9]{20,}'`；`git status --short` 核对 `.env` | 零命中（CHANGELOG §5 / V14 历史口径） |
| Q-SEC-03 | tenant 残留（排除区外） | **0 命中**（五组 `git grep` 排除区外） | 五组检索式（见 `docs/org-model-change-plan.md` 对应章节） | 任一命中即 No-Go（§8.5 第 4 项） |
| Q-SEC-04 | 演示物料真实 API 细节泄漏数 | **0 处**（`demo/selftest.py` C14 扫描 `demo/` 与 `docs/demo/`） | `python demo/selftest.py`（C14） | C14 通过 |
| Q-SEC-05 | 默认密钥拒绝启动 | 生产默认 `JWT_SECRET_KEY` 时 `lifespan` 抛错 | `docs/operations.md:19–20` + `backend/app/main.py` 核对（静态，不启动生产服务） | 代码路径存在且文档标注一致 |
| Q-SEC-06 | 三态标注失真条目数 | **0 条**（不把已移除能力标「已实现」、不把已实现部门树标「未做」、不把 Won't 写成已落地） | 四份事实源逐条核对（§4.4 / §5.9 口径） | 任一未修即 No-Go（§8.5 第 5 项） |

### 4.5 可观测类

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-OBS-01 | 离线评估运行留痕 = run 数增量与日志事件 | `len(after) == len(before) + 1`；`comparable is False` | `pytest test_evaltest.py::test_offline_eval_run_is_real_and_leaves_trace` | 存在 `eval_done` 且 `run_id` 匹配；断言通过 |
| Q-OBS-02 | 审计台账按动作筛选 | `login_rows >= 1`；普通用户 403 | selftest C32 | 每行 `action == "login"` 且 `actor`、`created_at` 非空 |
| Q-OBS-03 | 429 响应头与命中台账 | 第 3 次 429；`limit == 2`；`dimension == "user"` | `pytest test_evaltest.py::test_rate_limit_returns_429_with_headers`；V9 | `Retry-After` 非空；`X-RateLimit-Dimension == "user"`；`hit` 台账存在 |
| Q-OBS-04 | 预算 block 拒绝留痕 | `exceeded is True`、`allowed is False`、`reason == "budget_exceeded"` | `pytest test_evaltest.py::test_budget_block_rejects_new_eval_run`；V10 | ERROR 日志含 `eval_rejected_budget` |
| Q-OBS-05 | 用量台账不回溯（**【变更中·待回填】**） | 转岗前 N 笔 `department` 保持原值；新事件为新值；归档后零悬空引用 | 代码冻结后按 A7③ 回填 | 历史记录按 `department_id` 可解析；不计入本轮判定 |
| Q-OBS-06 | 完成率钳制 | 任务与整体 `completion_rate <= 1.0`、`pending >= 0` | `pytest test_evaltest.py::test_completion_rate_clamped` | 断言通过 |

### 4.6 展示类

| ID | 指标定义 | 阈值 | 验证命令 | 通过判定 |
|----|---------|------|---------|---------|
| Q-UI-01 | 侧栏十视图与加载器双向齐全 | **`nav_ok == 10 and loaders_ok == 6`**（C33） | selftest C33（`:375–377`） | 两计数同时达标，且 `/` 与 `/app.js?v=8` 均 200 |
| Q-UI-02 | 前端语法通过数 | **3/3** | `cd webapp/static && node --check app.js && node --check i18n.js && node --check theme.js` | 三条命令 exit 0 |
| Q-UI-03 | i18n 字典覆盖 | `t()` 字典零缺口；HTML 中文片段 **277 条、仅 2 条刻意保留** | `enterprise-readiness.md` §8 V7 脚本校验（历史验收项） | 覆盖校验通过；V7 判定一致 |
| Q-UI-04 | 静态资源可达数 | **5/5 均 200**（`/`、`/i18n.js`、`/theme.js`、`/style.css`、`/app.js`）且标记串存在 | `pytest test_evaltest.py::test_static_assets_and_i18n_theme`；V12 | 全 200；首页含「企业级知识库」；C01/C33 约束保持 |
| Q-UI-05 | 评估子页挂载完整性 | `view-evaltest`、`data-view="evaltest"`、`id="et-config"`、`id="et-cost"` 四项齐全 | 同上 | 四项全部命中 |
| Q-UI-06 | 评估结论展示口径 | 插件状态集合含 `{"已落地", "架构预留", "未做"}`；`total >= 10` | `pytest test_evaltest.py::test_evaltest_read_endpoints` | 两项断言通过；不出现「质量基线已达标」类表述 |

**合计 40 条可量化验收标准**（功能 10 / 权限 7 / 性能 5 / 安全 6 / 可观测 6 / 展示 6，含 2 条【变更中·待回填】：Q-AUTH-07、Q-OBS-05）。

---

## 5. 结果结论（待主理人回填）

### 5.1 回填说明（流程事实）

**本表由主理人在代码冻结后统一实跑回填，测试设计阶段不预填结论。** 这是本轮既定流程，不是留给用户的开放项。原因是本轮代码处于「成本 / 限流统计维度 tenant → department」与「LLM 低思考快速档」变更中，任何此刻的实跑值都不代表交付态。

回填规则：
1. 按第 3.5 节顺序执行 I → M → N → 残留清零 → 新计数冻结；
2. 「实测值」列填**实跑输出抄录**（`passed/total`、`OK` 数、diff 行数、计数 N1/N2/N3），不得预估；
3. 「判定」列只填 `通过` / `不通过` / `【变更中·待回填】`；任一 P0 或一票否决项触发即整体 `No-Go`；
4. 新计数 N1/N2/N3 与首跑耗时基线 B 一并写入 `docs/CHANGELOG.md` §7（仅新增一条，历史条目不回改）。

### 5.2 最终实跑回填（主理人于代码冻结后执行，2026-11-14）

> 以下全部为**本轮实测**，命令逐条实跑；不含任何历史快照，也不调用真实外部 LLM。
> 端到端自测用隔离临时库 + 三个 provider 强制 `offline` 拉起临时 8765 服务补跑，
> 跑完按字节还原原报告、停服、清理临时库。

| 用例组 | 执行命令 | 实测值 | 判定 |
|--------|---------|--------|------|
| I1 冻结边界零 diff | `git diff --exit-code -- eval/ demo/` | exit 0，无输出 | **PASS** |
| I2 演示物料自测 | `python demo/selftest.py` | 16/16；`run_id=RUN-095a2a4a40c6` 未变 | **PASS** |
| I3 冻结 harness | `python -m unittest discover -s eval/tests -t .` | Ran 14 tests, OK | **PASS** |
| I3 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` | Ran 16 tests, OK | **PASS** |
| M1 webapp 单元 | `python -m pytest webapp/tests -p no:cacheprovider -q` | **66 passed**（历史 51 → 66，新增 15 项） | **PASS** |
| M2 webapp 端到端 | `python webapp/selftest.py` | **33/33 `DEMO_READY`**（C33 `nav=10/10, loaders=6/6`）；原报告按字节还原 | **PASS** |
| M3 backend 单元 | `cd backend && python -m pytest -q tests` | 16 passed | **PASS** |
| M4 前端语法 / 构建 | `node --check app.js / i18n.js / theme.js`；`cd frontend && npm run build` | ×3 全过；build 通过（3505 modules / 6.83s，仅 1 条 chunk 体积警告） | **PASS** |
| M5 密钥检查 | `git grep -n -E 'sk-[A-Za-z0-9]{20,}'` | 零命中；`.env` 未入库 | **PASS** |
| N 新增回归 | `webapp/tests/test_thinking.py`（11 项）、`webapp/tests/test_department_migration.py`（4 项） | 15/15 通过；含全新库 / 旧库 / 半迁移态三态与统一 429 契约断言 | **PASS** |
| 残留清零 | 五组 `git grep` + 分层判定 | 全仓 `tenant` 词根底数 **675 行**（含文档、历史、backend 技术债）；webapp 可变区排除迁移函数体与反向断言后，残留 10 条全部属于迁移代码、技术债说明或「明确不做」边界，**代码标识符零命中** | **PASS（分层判定）** |
| 新计数冻结 | `docs/CHANGELOG.md` §7 逐字核对 | **本轮未回改**历史条目；新计数 66 / 33 / 16 / 14 / 16 / 16 以本报告为准 | 说明（非阻断） |
| 维度改造 A7 | `PRAGMA table_info(usage_events)`、`eval_configs` 键断言、`rate_rules` 三维断言、429 契约断言 | 全通过；数据工程师按 18 行映射表独立对拍，**不一致 0 行** | **PASS** |
| 低思考快速档 | `python -m pytest webapp/tests/test_thinking.py -q` + 网关参数对照实测 | 11/11；请求体注入 `thinking={"type":"disabled"}`、档位标签、缓存 key 不受影响。**2026-11-14 实测：该字段把 `reasoning_tokens` 从 55 降到 0、耗时 979ms → 467ms；元数据里的布尔 `reasoning` 参数无效** | **PASS（已实测生效）** |
| Q-01–Q-40 可量化标准 | 第 4 章各条验证命令 | 功能 / 权限 / 可观测 / 展示类核心项已实跑通过；性能 P95、SLO、屏幕阅读器、真实账单等 A/B 档项按范围纪律标注「未做」，不伪造通过 | **部分不适用（范围纪律）** |
| 演示 GIF | 本机工具探测 | **本轮未产出 GIF**；原因与替代方案见 §6 | 不适用 |
| **整体准出** | 本轮范围的回归 + 冻结边界 + 口径对拍 | **本轮范围 Go**（P0 = 0，上述各项无失败）；`docs/org-model-change-plan.md` §8.5 的六个一票否决项属于**完整 13 步改造**的准出门禁，该改造本轮未执行，故**本轮不适用**，不得据此宣称改造已验收 | **Go（本轮范围）** |

### 5.3 历史实跑事实（仅引用，非本轮实测）

> 以下均为**历史快照引用**，日期与来源逐条标注，**不得当作本轮实测**，也不构成对本轮代码的结论。

| 检查项 | 命令 | 历史结果 | 日期与来源 |
|--------|------|---------|-----------|
| webapp 单元测试 | `python -m pytest webapp/tests -q` | 51 passed | **2026-11-12**，`docs/CHANGELOG.md` §5「验证结果快照（2026-11-12 最终态）」 |
| webapp 端到端自测 | `python webapp/selftest.py` | 33/33 `DEMO_READY` | 同上 |
| 演示物料自测 | `python demo/selftest.py` | 16/16，`run_id=RUN-095a2a4a40c6` 未变 | 同上 |
| 评测 harness | `python -m unittest discover -s eval/tests -t .` | 14 OK | 同上 |
| 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` | 16 OK | 同上 |
| 生产骨架 | `cd backend && python -m pytest -q tests` | 16 passed | 同上 |
| 前端语法 | `node --check` × 3 | 全部通过 | 同上；`docs/enterprise-readiness.md` §8 V6 |
| i18n 覆盖 | 脚本校验 `t()` 字典 | 零缺口；HTML 中文片段 277 条仅 2 条刻意保留 | `docs/enterprise-readiness.md` §8 V7（无独立日期，文档内历史结论） |
| 限流 429 | 阈值 2/60s 第 3 次请求 | 429 + 4 个响应头 + 命中台账 | `docs/enterprise-readiness.md` §8 V9 |
| 预算 block | `limit=0, policy=block` | 运行被拒 + ERROR 日志留痕 | `docs/enterprise-readiness.md` §8 V10 |
| 用量范围隔离 | 普通用户查 `usage` | 只返回本人调用（0 条而非隐藏全局数字） | `docs/enterprise-readiness.md` §8 V11 |
| 冻结边界 | `git diff main..HEAD -- eval/` | 为空；`__TBC__` / `NOT_COMPARABLE` 口径未改 | **2026-11-12**，`docs/CHANGELOG.md` §5、`enterprise-readiness.md` §8 V13；**当前有效口径已改**为 `git diff --exit-code -- eval/ demo/`（C24-3） |
| 密钥检查 | `git grep -E 'sk-[A-Za-z0-9]{20,}'` | 无命中；`.env` 未入库 | **2026-11-12**，`docs/CHANGELOG.md` §5、`enterprise-readiness.md` §8 V14 |

---

### 5.4 知识治理专项回填（2026-11-14 20:00 实跑）

本轮新增 `knowledge/` 包（入库新鲜度 + 事实冲突检测）与 `webapp/` 接入层，按 `docs/knowledge/04-design.md` §5 的 A01–A16 验收：

| 验收组 | 命令 / 断言 | 实测 | 判定 |
|---|---|---|---|
| 算法层离线单测 | `python -m pytest knowledge/tests -q` | **23 passed**（零外部调用） | **PASS** |
| 接入层 API 与迁移 | `python -m pytest webapp/tests/test_knowledge_api.py -q` | **9 passed**（含旧库幂等迁移、策略非法值 400、开关默认关闭、负向对照） | **PASS** |
| 全量单测合计 | `python -m pytest webapp/tests parsing/tests knowledge/tests -q` | **128 passed** | **PASS** |
| 时间衰减与 floor | `test_decay_is_half_life_and_floored` | 半衰期折半、9999 天不归零 | **PASS** |
| 适用性门控不受开关影响 | `test_rerank_keeps_before_after_evidence` | 关衰减后未来生效文档仍被排除 | **PASS** |
| 两类裁决各有样本 | `test_authority_wins_without_review` / `test_recency_wins_but_requires_review` / `test_equal_authority_escalates_without_inventing_tiebreak` | 权威 / 需复核 / 并列转人工均命中 | **PASS** |
| 负向对照不误报 | `test_different_scope_is_not_conflict` / `test_same_value_is_corroboration_not_conflict` / `test_scenario_negative_controls_hold` | GB scope、同值断言均未报冲突 | **PASS** |
| 排序前后可对照 | `/api/knowledge/scenario` | 开 `D2→D4→D3→D1→D6`；仅关衰减 `D1→D2→D3→D4→D6` | **PASS** |
| 冻结区 | `git diff --exit-code -- eval/ demo/` | 返回 0 | **PASS** |

**诚实边界**：合成样本是功能可演示证明，不是质量基线；阈值是演示经验起始值；真实检索链的新鲜度重排默认关闭且只在 `top_k` 内生效；生成层注入 `EvidenceRecord` 与逐句 grounding 仍为只设计；规则级冲突检测不等于 NLI 模型级识别。详见 `docs/KNOWLEDGE-DELIVERY.md` §7。

---

## 6. 演示 GIF 可行性与替代方案

### 6.1 本机工具实测（本轮实查，非推测）

在本轮任务中对本机做了只读工具探测，结果如下：

| 工具 | 状态 | 说明 |
|------|------|------|
| `ffmpeg` | **可用** | WinGet 安装的 `ffmpeg 8.1`（`.../Gyan.FFmpeg.../bin/ffmpeg`），可从帧序列合成 GIF / MP4 |
| Python `PIL`(Pillow) / `imageio` / `cv2` | **可用** | 可从静态帧生成动效图，但只能处理已有图像帧 |
| `playwright`（CLI 与 Python 模块） | **未安装** | 无浏览器自动化，无法自动驱动页面并截图 |
| `selenium` | **未安装** | 同上 |
| `mss` / `pyautogui` | **未安装** | **无屏幕录制 / 截屏能力** |
| `node_modules/`（Playwright / Puppeteer） | **不存在** | 无前端侧录制依赖 |

### 6.2 本轮未产出 GIF 及原因

**本轮未产出演示 GIF。** 原因（均为事实，不是能力推脱）：

1. 本机**没有浏览器自动化 / 屏幕录制工具**（playwright / selenium / mss / pyautogui 均未安装），无法自动录制「关键功能操作流程」这类真实界面 GIF；仅有 `ffmpeg` 与图像库只能合成静态帧动效，不足以替代操作录屏。
2. 本轮代码处于**变更中**（tenant → department 维度改造、低思考快速档），界面与统计展示未冻结，此刻录制的画面不可复现、也不能代表交付态。
3. 本任务的交付边界是**只写 `docs/acceptance-report.md` 一个文件**，不新增脚本、不新增素材、不启动演示服务、不运行真实外部 LLM 调用（用户上轮流量耗尽）。

### 6.3 可复现的替代方案（建议，不在本轮落地）

**方案 A · HTML 演示页内动效图解（推荐，零外部依赖）**
由任务 #15「制作独立 HTML 面试演示页」承接：在演示页中用 CSS `@keyframes` / SVG 分步动画图解关键链路（上传 → 分块 → 检索 → SSE → 来源 → badcase → 评估子页），每步配 `docs/acceptance-report.md` 对应用例 ID 与 `webapp/selftest.py` 的 Cxx 编号。优点：可离线打开、零网络零密钥、可复现、与「演示切片可讲解」定位一致。

**方案 B · 后续具备录制条件时的脚本建议（供主理人决定是否实施）**
若后续安装浏览器自动化工具，可按以下顺序产出 GIF（**本轮不创建任何脚本文件**）：

```
# 1) 启动演示服务（离线 provider，不触发真实外部 LLM 调用）
python -m uvicorn webapp.main:app --port 8765

# 2) 安装浏览器自动化（示意，需用户自行决定）
# pip install playwright && playwright install chromium

# 3) 用 page.screenshot 按步骤抓帧到 frames/ 目录（示意）
#   步骤与 C01→C33 对齐，每步一帧，帧名按序号补零

# 4) 用本机已有 ffmpeg 合成 GIF（示意命令，帧率与分辨率按实际帧数调整）
ffmpeg -framerate 2 -i frames/%03d.png -vf "scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" demo.gif

# 5) 无 ffmpeg 时的纯 Python 替代（本机已具备 pillow / imageio）
# imageio.mimsave('demo.gif', frames, duration=0.5)   # frames 为 PIL Image 列表
```

产出后应在 `docs/CHANGELOG.md` 新增条目登记「GIF 对应代码版本 + 生成命令 + 帧数」，并在演示页标注「非质量基线、仅讲解用」。

### 6.4 主理人复核记录（前端内联事件处理器 / 部署编排）

> 本节为**主理人本轮复核留痕**，记录一条已证伪的前端 P0 误报与两项部署编排事实，便于追溯。复核由主理人执行；本报告未重复实跑、未据此新增任何 P0 / 阻断判定、未预填第 5.2 节实跑结论。

**① 前端内联事件处理器「未定义」误报（已证伪）**

- **复核方法**：正则提取 `webapp/static/index.html` 全部内联事件处理器（`onclick` / `onchange` 等）→ 与 `webapp/static/app.js` 的 `function` / `const` / `window.*` 定义名对拍 → 跨文件全局（`i18n.js` / `theme.js`）单独核验。
- **复核数据**：`HTML inline handlers: 40`、`app.js defined names: 94`，初筛 `MISSING: ['click','getElementById','if','preventDefault','setLang']`。
- **逐条判定**：`click` / `getElementById` / `preventDefault` 是内联代码片段里的 DOM 方法名，`if` 是关键字，均非待解析的全局函数，属正则误捕获；`setLang` 定义在 `webapp/static/i18n.js:921`（`function setLang`）并由 `:945` `window.setLang = setLang;` 暴露为跨文件全局，且 `index.html:9` 的 `/i18n.js?v=11` 先于 `:646` 的 `/app.js?v=11` 加载，运行时可用；`app.js` 里 29 个 `onclick` 目标函数（`exportBadcases` / `loadEvalTest` / `saveBudget` / `runEvalOnce` / `seedSample` / `toggleMode` 等）全部有定义。
- **结论：误报**。「6 个 onclick 触发 ReferenceError、P0 阻断」不成立。对应正面结论为「**已复核：内联事件处理器与函数定义一致，跨文件全局由 `i18n.js` / `theme.js` 提供**」，已作为 **TC-I18N-07** 写入第 2.10 节用例矩阵。
- **撤回情况：实际撤回 0 条**。本报告逐字核对后**从未包含**该 P0 条目或「前置修复」条目；`docs/` 下所有已落盘 Markdown 亦无 `onclick` / `ReferenceError` /「6 个 onclick」字样。该结论疑似来自尚未落盘的前端审查草稿或对话消息，建议由主理人向对应成员转达本复核结论。

**② 两个 Dockerfile 存在（据实核验）**

- `backend/Dockerfile`（406 B）、`frontend/Dockerfile`（369 B）**均存在**；`backend/Dockerfile:8` 已安装 `curl`（与 `docker-compose.yml` 中 api 服务 healthcheck 的 `curl -f http://localhost:8000/health/live` 配套）。
- 本报告第 2.11 节 TC-DEP-01/02 仅做「healthcheck 定义齐备」的静态核对，**从未写过「Dockerfile 全缺」或「healthcheck 必然失败」类结论，无需撤回**。

**③ compose 状态（不作结论）**

- 主理人本轮消息记录 `git diff --stat docker-compose.yml` 为空，即 compose 当时尚未实际改动。
- 本次核查时实跑同一命令返回 `docker-compose.yml | 63 +++... 62 insertions(+), 1 deletion(-)`，diff 内容为 `x-logging` 日志轮转锚点、`restart` / `mem_limit` / `cpus` 等韧性补齐字段（注释指向 `docs/deployment-guide.md §3`）。二者为**在途变更的时间差**，并非矛盾。
- **本报告既不写「已修复」，也不写「未改动」的绝对判断**；compose 最终状态由主理人在代码冻结后统一确认并回填第 5.2 节，本轮不预填。

---

## 7. 准出与缺陷分级（引用既定口径）

依据 `docs/org-model-change-plan.md` §8.5 / §8.6：越权 / 数据丢失 → **P0**（立即停）；回归失败 → **P1**（不可变区回归失败升 P0）；一般文档措辞 / 展示层 → **P2/P3**。准出为 **Go**（六项一票否决全过 + P0=0）/ **有条件 Go**（仅 P2/P3 且书面记录）/ **No-Go**（任一票否决或 P0 未清）。六个一票否决项：A13 非 7/7、`eval/`·`demo/` 非零 diff、30 条权限用例任一漏放、tenant 残留（排除区外）非零、三态标注失真未修、缓存串部门命中。

**本报告不给出准出结论**——准出判定属第 5.2 节待主理人回填项。

---

## 8. 交付说明

- 交付文件：**仅 `docs/acceptance-report.md`**（本文件）。
- 用例矩阵：**95 条**（11 个模块；93 条映射既有可执行资产，2 条标注【变更中·待回填】）。
- 可量化验收标准：**40 条**（功能 10 / 权限 7 / 性能 5 / 安全 6 / 可观测 6 / 展示 6）。
- 覆盖范围：三分区（不可变区 I1–I3 / 可变区 M1–M7 / 新增回归 N1–N10）+ 两套排除区。
- 结果结论：待回填表格骨架 1 组（16 行）+ 历史事实引用 13 行（均标注日期与来源）。
- 演示 GIF：**本轮未产出**（原因见 6.2），替代方案见 6.3。
- 主理人复核记录：见 6.4。本次返工**实际撤回 0 条**（本报告逐字核对后从未包含「6 个 onclick 未定义 / P0 阻断 / 前置修复 / Dockerfile 全缺 / 已修复」类条目）；新增 1 条已复核用例 TC-I18N-07 与 1 节追溯记录，并据实核验两个 Dockerfile 存在、compose 状态不预填结论。
- 未修改任何源码；未触碰 `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`；未新增脚本；未运行回归；未运行真实外部 LLM 调用；未编造数值。
