# 项目长期记忆 · P5 企业级知识库项目

## 项目定位
面向企业内部使用的企业级知识库系统**开发框架骨架**，用于逐步学习与企业级加固，
不是上线交付版。沿用《中小企业知识库项目 · 全景设计、本周排期与交付档次》的
A/B/C 三档口径与「面试场上不要把 A 档说成 C 档」的诚实边界原则。

## 用户约定
- 技术栈固定：React / FastAPI / PostgreSQL / Milvus / Redis + Celery / 外部 DeepSeek（不部署本地模型）。
- 用户正在学 FastAPI 与数据库，后续计划学 Java SpringBoot；代码要可读、注释解释设计取舍，
  文档要区分「已实现 / 只设计 / 未做」，不要夸大完成度。
- 架构必须预留：逐行/逐段落精细权限、Agent 内部编排（LangGraph/多 Agent/MCP）、后续高可用加固。
- 重点功能：大文件与多文档批量上传的异步处理及进度反馈。

## 关键设计约束（改动时别破）

> **2026-11-13 需求变更**：用户明确「不再做多租户架构，改为多用户多部门」。完整调整方案见
> `docs/org-model-change-plan.md`（专家团八位成员产出 + 主理人裁决 C1–C35）。
> **重要状态：方案已定，代码尚未改造**。下列第 1、5 条是改造前的现状描述，改造后目标口径以方案
> §3.3 / §5 为准；在代码改造完成前，不要按新口径改坏现有可运行代码，也不要把目标态说成已实现。

1. 【现状】`tenant_id` 只由依赖注入产生，所有查询走 `TenantRepository`。
   【目标】删 `tenants` 表与所有 `tenant_id` 列；`Subject` 一律服务端从 DB 构造（JWT 只带 `sub`）；
   请求体 `department_id` 只作筛选 / 授权目标且须经 `decide()` 复核；查询走 `ScopedRepository`。
2. 权限判定收敛到 `app/services/permission.py::decide()` 一个纯函数 —— **不变，并强化为最终且唯一权威**；
   新增单一 `visible_department_ids()`（纯部门树拓扑查询，只缩候选、不产出 allow/deny）。
3. **先按权限过滤，再重排、再进模型** —— 不变；Milvus expr + `filter_candidates` 双重过滤保留，
   **并新增列表侧逐条 `decide()` 复核**（本次最关键的越权缺口，A13 双向一致性对拍为第一验收项）。
4. PG 是状态真值，Redis 只是实时通道；先写 PG 文本再写 Milvus 向量；删除走软删 + 异步清理 + 每日对账 —— 不变。
5. 【现状】缓存 / 进度 / 对象 key 一律带 tenant 前缀。
   【目标】静态 `ORG_NAMESPACE` 命名空间；成本 / 用量按 `department_id` **事件快照、不回溯**；
   检索缓存**先按 `decide()` 过滤再写**且 key 携带 principal 授权快照；进度 key 只含 `ORG_NAMESPACE + batch_id`
   （不按部门分片，否则转岗后 SSE 断连空白）。**禁止把 `tenant_id` 机械全局替换成 `department_id`**。
6. 基线迁移 `0001` 用 `metadata.create_all`；从 `0002` 起必须写显式迁移 —— 不变，`0001` 冻结不动，
   新增 `0002_single_org_departments.py`（downgrade 结构可逆、数据语义不可逆）。

> C33 实测口径（`webapp/selftest.py:369-377`）：冻结的是 **10 个具名 view + 6 个具名 loader 的存在性检查**
> （`nav_ok==10 && loaders_ok==6`），不是侧栏总数；实际侧栏 11 个 `data-view`、`VIEW_META` 11 键、
> `LOADERS` 9 键、`ADMIN_VIEWS` 3 个。新增第 12 个视图不破坏 C33、不需改断言数字。

## 目录入口
- 总览与启动：`README.md`
- 重点方案：`docs/async-upload.md`
- 权限：`docs/permission-model.md`；Agent 扩展：`docs/agent-extension.md`
- 运维边界：`docs/operations.md`；学习/交付路线：`docs/learning-roadmap.md`
- 评测体系：`eval/README.md`（离线启动）、`docs/evaluation-plan.md`（完整方案 + 开通清单）
- 消息层/插件方案：`docs/next-version-messaging-and-plugins.md`（含第 7 节落地映射）
- 评估报告（面试讲解版）：`docs/evaluation-report.md`，事实源 `webapp/out/eval_report.json`
  （`python webapp/eval_report.py` 可复跑，隔离临时库、密钥脱敏）。
- **真实 API 评估工作台**：`evalbench/`（平行体系，不修改 `eval/` 冻结对象）。
  入口 `evalbench/README.md`，展示页 `evalbench/out/index.html`，迭代结论
  `evalbench/iterations/iteration_log.md`，事实源 `evalbench/runs/<run_id>/`。
  Git 分支 `main` → `eval/v1-baseline` → `eval/v2-prompts` → `eval/v3-params`
  → `eval/v4-models` → `demo/v1`（交付分支，每个 run 的 config_snapshot 回写 branch/commit）。
  最终配置 `v2_grounded` + 参数 B(`temp=0 / passage_num=5 / noise_rate=0.6`) + `minimax-m3`，
  最终 run `RUN-a48c08c0b721`；`kimi-k2.6` 会复述 prompt，不纳入默认选型。
- 可运行演示切片：`webapp/README.md`；启动 `python -m uvicorn webapp.main:app --port 8765`，
  admin/admin123；端到端自测 `python webapp/selftest.py`（33/33，真实外部模型下运行，限流走降级仍全绿），
  单元测试 `python -m pytest webapp/tests -p no:cacheprovider`（41/41，隔离库+强制离线）。
  侧栏 10 项分 3 组，普通用户 7 项、admin 10 项；新页面只读真实数据，纯设计项明确标注
  （见 README §2.1「故意没加的入口」）。SQLite 与进程内队列是 PG/Celery 的演示替身，
  不要把它当生产实现，也不要改坏「采集与查看权限分离」的约定。
- **模型设置页（仅 admin）**：`/api/settings` 读写 SQLite `app_settings`，可切 LLM
  （offline / sensenova / discovery / local）、Embedding（offline / local / **external**）、
  Rerank（off / llm / **external**）。配置即时生效、无需重启；`/api/settings/test` 做连接测试。
  本地 LLM 默认 `qwen2.5-coder-7b-instruct`（Qwen3 是推理模型，content 可能为空）。
  **本地模型实测基准**（同一 RAG 问题/上下文）：`qwen/qwen3-vl-8b` 可用但慢（36.5s / 131 chunks，
  答案带引用），`qwen2.5-coder-7b-instruct` 11.9–15.2s（快约 2.5 倍，演示首选），
  `qwen/qwen3-8b` 先吐 199 个 reasoning chunk 才出正文（39s，不推荐）。
  **Qwen3-VL 视觉不可用**：LM Studio 的 `capabilities` 只有 `tool_use` 无 `vision`，
  传图 400（data: URL 会让引擎 terminated），切片保持纯文本。
  零正文时 `rag.stream_answer` 标 `degraded=empty_model_output` 并用检索片段兜底（不再出空白气泡）。
  检索是「词法门禁 + 向量排序（hybrid）」，向量缓存 `chunk_embeddings`；
  **当前默认 external 硅基流动 `BAAI/bge-m3`（1024 维 / 435ms）**，本地 LM Studio 为可选档。
  **Rerank 三档**：`off` / `llm`（LLM-as-reranker，用当前 LLM 打分，实测能压下误含关键词的干扰文档）/
  `external`（Cohere 风格 `/rerank`：Jina、TEI 自动兼容）。**LM Studio 本体不支持 `/v1/rerank`**
  （把 reranker 归类为 embeddings），所以没有 local 档；不可用时统一优雅跳过（不假装用了）。
  **`external` 已用硅基流动 `BAAI/bge-reranker-v2-m3` 实测跑通**（key 在 `.env` 的 `RERANK_API_KEY`）：
  真相关 0.9316 vs 干扰 0.0160，`rerank=applied:external`；空 key / 错 key 均返回 None 走跳过不抛错。
- 演示切片已接**真实外部 LLM（两家可切，互为备份）**：`sensenova`（DeepSeek 代理）与
  `discovery`（Intern-AI 网关，14 个模型，key 在 `.env` 的 `DISCOVERY_API_KEY`）。
  `webapp/providers.py` 是适配器 seam（LLMProvider/EmbeddingProvider 接口 + Offline/OpenAILike 实现），
  切换只改设置页或 `.env` 的 `WEBAPP_LLM_PROVIDER`（置 offline 恢复零依赖演示）。
  **Discovery 模型选型实测**：minimax-m3 1.3s 最快（无 reasoning）；kimi-k2.6 2.9s；
  deepseek-v4.1-flash-1017 是推理模型（9.6s，冷启可达 58s，max_tokens 给小会正文为空）；
  intern-s2 40.7s 且正文空。演示优先 minimax-m3，要「DeepSeek」名号用 v4.1-flash-1017。
  模型报错优雅降级为本地拼接并标记 `model_error`；已出正文后中断标
  `stream_interrupted`（不拼接兜底文案）；空正文标 `empty_model_output`。SSE 不中断。
  **429 必须分两类**：`rate_limit_exceeded` 退避 1/2/4s + 抖动（尊重 `Retry-After`，上限 30s，
  最多 3 次）；`quota_exceeded`（墨点耗尽）**不重试**，标 `quota_exceeded` 让用户换模型/供应商。
  流式一旦开始吐字就不再重试（避免重复内容）。当前**没做全局并发闸/令牌桶**，
  生产应在网关层按 RPM 限流 + 队列吸收突发。
  密钥仅进 `.env`（已 gitignore），不进源码；此接入**不影响** eval/ 冻结对象的 `__TBC__` 与
  `NOT_COMPARABLE` 口径。
- **网关兼容坑（已修，别再踩）**：部分网关在流末尾发 `choices: []` 的纯 usage 块，
  解析必须跳过空 `choices`，否则 IndexError 会把正常回答判成 `model_error`。
- **本轮目标配置（2026-11-11，默认生效）**：`discovery:deepseek-v4.1-flash-1017` +
  硅基流动 `BAAI/bge-m3` 嵌入 + 硅基流动 `BAAI/bge-reranker-v2-m3` 重排，三组件全走真实外部
  API（嵌入/重排同一把 `RERANK_API_KEY`，嵌入可另用 `WEBAPP_EMBEDDING_API_KEY`）。
  实测：嵌入 1024 维 / 435ms、rerank 205ms、真相关 rerank 0.9686 vs 干扰 0.0168
  （余弦仅 0.7296 vs 0.6023）、e2e 59.7s / 70 chunks / 3 条来源 / 未降级；检索为空诚实拒答。
  **59.7s 是推理模型冷启 + reasoning，演示应切 minimax-m3（约 1.3s）**。
- **配置命名空间隔离（务必保持）**：webapp 的 provider 开关一律 `WEBAPP_` 前缀
  （`WEBAPP_LLM_PROVIDER` / `WEBAPP_EMBEDDING_PROVIDER` / `WEBAPP_RERANK_PROVIDER`），
  因为 `backend/` 骨架的 `EMBEDDING_PROVIDER` 枚举是 `stub|openai_compatible`，与 webapp 的
  `offline|local|external` 互斥，共用扁平命名会让 backend 启动校验失败；`EMBEDDING_API_KEY`
  同理归 backend。改 `.env` 键名时必须同步 `webapp/tests/conftest.py`（强制离线）与
  `webapp/live_smoke.py`，否则测试会误连真实 API。
- **`settings.effective_embedding()` 是端点/模型/key/缓存命名空间的唯一事实源**：
  `cache_key` 必须含 provider + 模型名，否则切模型会复用旧向量，维度不一致时 `_cosine`
  静默返回 0（检索看似正常实则全错）。已有单测锁定。
- 其他真实坑：`webapp/queue.py` 会遮蔽标准库 `queue`（脚本放 webapp/ 下直接跑时 TestClient
  起不来，需修 sys.path 顺序）；C12「检索为空」原查询含「技术/方法/观测」通用二元组，
  演示库累积文档后会误命中，已改随机无意义探针；`backend/` 骨架曾因缺
  `sqlalchemy/pydantic_settings/minio/psycopg` 而「没报错只是没跑到」，按
  `backend/requirements.txt` 装齐后 16/16。
- 演示物料：`docs/demo/README.md`（四部分索引）、`demo/demo_runner.py`（一键演示）、
  `demo/selftest.py`（16 项自测）；场景事实源 `demo/scenario.json`

## 演示物料约定
- 场景 `DEMO-KBQA-EVAL-001`：12 分钟 9 步，从开场到结果确认，档位 `offline_placeholder`。
- 术语/流程/数据/预期以 `demo/scenario.json` 为唯一事实源；改动任一对象、评测代码或数据集
  都会改变 run_id，需重跑 `python demo/selftest.py` 并同步更新 `04` 文档中的哈希与 run_id。
- 演示侧 `demo/out/demo_run_result.json` 与验证侧 `eval/results/<run_id>/` 必须逐字段对齐；
  验收判定为自测报告 `summary.verdict == "DEMO_READY"`。

## 评测体系约定
- 真实 API（DeepSeek / 阿里云百炼 embedding / 待测说明书分块问答系统）的所有接口地址、
  鉴权、请求/响应结构、配额、错误码、模型版本与具体模型名称一律 `__TBC__`，不得推断填写。
- 占位结果 `comparable=false`，禁止写入质量基线或据此冻结阈值；`real_api` 档位在
  `OBJ-ADAPTER-CONTRACTS` 的 blocking 字段未消解前保持阻断。
- 所有可版本化部分（提示词/分块/embedding/模型标识/检索/生成/数据集/评分规则/阈值/基线）
  为独立对象，已产生 run 的版本禁止原地修改；变体只在内存生效，采纳后须新建版本。

## webapp 前端与治理约定（本轮新增，改动时别破）
- 前端三件套在 `webapp/static/`（零构建）：`i18n.js`（中文原文即 key 字典 + DOM 扫描 +
  MutationObserver + `data-i18n` 可逆切换）、`theme.js`（6 主题色 × 明暗正交）、
  `index.html`/`app.js`/`style.css`。资源版本 v=11，改 JS/CSS 要升版本。
- **三份独立 localStorage**：`kb_lang` / `kb_theme` / `kb_mode`，互不覆盖；
  `theme.js` 必须在 `<head>` 同步执行（CSS 之前）防闪烁；`kb-boot` 首帧禁用过渡。
- i18n 关键约束：用户数据容器在 `SKIP_SELECTORS`；无 value 的 `<option>` 必须固化中文原文到
  value（否则翻译会改 badcase 判定提交值）；动态生成的界面文案加 `data-i18n="中文原文"` 才能
  在语言切换时可逆重设。`selftest` C01 要求 HTML 保留「企业级知识库」，C33 要求 10 个 view
  与 6 个 `function loadXxx` 名不变（新增 view 不能删/改这些）。
- **工作区没有 `gen_dict.py`（实查）**：`i18n.js` 是 944 行手写 `(function(){ var EN = { '中文原文': 'English' } })()`，
  全仓 `gen_dict.py` / `KB_DICT` / md5 生成器零命中。改文案必须「改中文原文源 → 同步改 EN 字典里
  成对的中文 key 与英文值并删旧 key → `node --check` + 切回中文逐字回原文 → 升 `index.html` 四处 `?v=`」，
  **不存在跑生成器重算 md5 这一步**；只改英文显示值会让切回中文跳回旧文案。
- 新增治理层：`observability.py`（成本/预算/限流/用量/插件注册表）、`routers_evaltest.py`
  （12 个 `/api/evaltest/*`）、`main.py` 限流中间件。默认限流阈值必须宽松，否则会打断
  33 项 selftest；预算默认 `policy=warn`（`block` 才硬拦）。单价是**演示估算**，非供应商报价。
- 评估测试子页 `view-evaltest` 只读真实数据（`evalbench/runs/` + 台账），写接口仅 admin；
  离线运行 `comparable=false`，不写质量基线。测试用 autouse fixture 把 `obs.EVALBENCH_RUNS`
  monkeypatch 到 tmp_path，**否则会污染 evalbench/runs/**。
- 规则与验收总纲：`docs/enterprise-readiness.md`；中型公司能力清单：`docs/kb-capability-checklist.md`。

## evalbench 约定（改动时别破）
- 样本是**自建 12 条**（`self_built_v1`），依据 RGB（CC BY-NC-SA 4.0 **仅非商业**）与
  SQuAD 2.0 的任务/指标口径，**不是官方数据抽样**，不得称「在 RGB/SQuAD 上的分数」。
- `eval/` 冻结对象、`__TBC__`、`NOT_COMPARABLE`、`real_api` 阻断口径**未被 evalbench 改动**；
  改动后应验证 `git diff main..HEAD -- eval/` 为空、`demo` 的 run_id 不变。
- 结果按 (model, prompt_hash, params_hash, sample, context_hash, repeat) 缓存在
  `evalbench/cache/`（已 gitignore），续跑只补失败项；429 失败留痕不评分；
  `comparable=false` 的离线 run 不进任何质量结论。
- **已产生 run 不原地修改**：评分口径变更用 `rescore.py` 生成新 run（旧 run 保留为前后对照）；
  聚合 bug 用 `rebuild_summaries.py` 只重建派生 summary，records / config_snapshot 不动。
  当前口径 `METRICS@1.2.0`，含 `answer_economy`（主指标饱和后新增，仅用于收敛对照）。
- 展示页由 `python evalbench/report.py --final-run <run_id>` 从 runs/comparisons/probe 汇总生成，
  不要手写 HTML 里的数字。

## 验证命令
```bash
cd backend && pytest -q tests
cd frontend && npm run build
# 全量导入：python -c "import app.main; print(len(app.main.app.openapi()['paths']))"
```
