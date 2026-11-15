# P5 企业级知识库项目 · 长期记忆

## 版本与判据
v1.0.0 = 四件真基础设施（真 PG / Redis / Milvus / 外部模型）**实跑通过**的骨架定版，
**不是「可上线产品 1.0」**；未做清单 `docs/production/07-remaining-work.md`（20 项三态）。
v1.1.0 = 多格式评估 `evalfmt/`；**v1.2.0 = 公开检索基准 `evalfmt/publicbench`**（tag `v1.2.0`）。
回归基线：单测 255 + backend 26 + eval 14 + evalbench 16；e2e 33/33；showcase 31/31；demo 16/16；`verify_infra`→`INFRA_READY`。

## 公开基准（v1.2.0，2026-11-16 实跑）
CmedqaRetrieval（C-MTEB）：100001 passage / 3999 query / 7449 qrels，切 2429 调参 / 1570 测试。
测试集 nDCG@10：**BM25 16.29% → 向量 bge-m3 33.93%**；查询前缀 31.05%（更差）、加权融合 w=0.85 31.98%（仍更差）。
重排（同 400 条子集）：基线 32.79% → top20 **36.09%**、top50 36.02%（差 0.06 点属噪声，按成本取 20）。
**三个负结果要说清**：① 前缀对 bge-m3 无用；② 融合单调趋向纯向量 → BM25 在此数据只贡献噪声，**不融合**；
③ top20/top50 并列，不能说「调出最优深度」。基线词项预算 12→100 使 BM25 从 11.13% 升到 15.18%——
**基线弱一点增益就好看一点，那是自欺**。
口径：绝对值与 C-MTEB 榜**不可比**（第三方 API 嵌入、重排只对候选 top-k、无官方清洗），只讲相对差异。
工程四件事：预分配 float32（否则 OOM）/ 每 15s 落盘断点续跑 / 429 单独给 13 次重试（5xx 只 8s）/
BM25 倒排用 `array('i')` 平铺（1800 万项 1GB→120MB）。**4 路并发只快 1.4 倍却把重试打满，用 2 路**。
坑：`_pick_terms` 曾用 `set()` + 仅按 df 排 → Python 哈希种子随机使同一数据跑出 0.1098/0.1105，**排序键必须全序 (df, 词项)**。
`WEBAPP_EMBEDDING_API_KEY` 优先于 `RERANK_API_KEY`；相关单测必须 `delenv` 清掉，否则断言会带走 `.env` 真值。

## 定位与用户约定
企业级知识库**开发框架骨架**，目标「学习 + 面试讲解 + 逐步生产化」。沿用 A/B/C 三档口径，**面试别把 A 档说成 C 档**。
技术栈固定 React / FastAPI / PostgreSQL / Milvus / Redis + 外部 DeepSeek（不部署本地模型）。
用户正学 FastAPI+DB（之后 SpringBoot）。代码注释要解释**设计取舍**；文档必须分「已实现 / 只设计 / 未做」。

## 组织模型
单组织 + 多用户多部门，**多租户不做**（只写进未来方向）。webapp 成本/限流维度已 `tenant`→`department`（列 `department_id`）；
**webapp 没有部门级权限**；`backend/` 的 `tenant_id` / `TenantRepository` 是历史命名技术债；
`docs/org-model-change-plan.md` 13 步仍是「方案已定、代码未改造」。

## 架构主干
六个纯函数包（项目根，不反向 import 应用层）：`parsing/`（字节→文本，multimodal 已实测）、`knowledge/`（适用性与冲突裁决）、
`cache/`（L1 LRU + L2 PG）、`guardrails/`（PII 四出口）、`showcase/`（8767）、`evalfmt/`（多格式评估）。
上游：`webapp/`（演示 8765，admin/admin123，跑真 PG）、`backend/`、`frontend/`、`eval/`+`evalbench/`、`demo/`（冻结区）。

## 硬约束
1. **权限**：`permission.decide()` 唯一权威。**先权限过滤 → 再重排 → 再进模型**；Milvus expr + `filter_candidates` 双保险，
   列表侧逐条 `decide()` 复核（A13 双向对拍是第一验收项）。`visible_department_ids()` 只缩候选、不产出 allow/deny。
2. **真值**：PG 是状态真值，Redis 只是实时通道；先写 PG 文本再写 Milvus 向量；删除 = 软删 + 异步清理 + 每日对账；
   删文档**先删 `chunk_embeddings`**（治幽灵向量）。
3. **DDL**：唯一事实源 `scripts/pg_init.sql`（initdb / `init_db()` / 测试共用）。别再往 `db.py` 写 `CREATE TABLE`；
   **改回 SQLite = 撤销一次大改造**。
4. **缓存 key 13 段**（含 authz / data_version / policy_version）。**TTL 不是权限控制**：先过滤再写，命中后 `key_matches()` 复核三重版本。
   懒失效只 bump 版本不批量删。主链路 `rag.retrieve()` 默认不接检索缓存；答案 / 语义缓存未做。
5. **PII**：**先检索、后脱敏**（检索用原文，进 LLM / 落库 / SSE / 导出用脱敏）。`StreamRedactor.push()` 必须扣住跨界候选**整串**。
6. **冻结区**：`eval/` `demo/` 零 diff；`__TBC__` / `NOT_COMPARABLE` 口径不得改。**C33** 冻结的是 10 具名 view + 6 具名 loader
   的**存在性检查**，不是侧栏总数（当前 14 个 `data-view`，新增视图不破坏 C33）。改 JS/CSS 必升 `?v=`（当前 **v=15**）；
   无 `gen_dict.py`，中文原文即 key、成对改中英并删旧 key。
7. **验证**：能力判定用**结构化元数据 + 实测**，不用命名启发式；指标零值样本必须预置；断言要防「测试自己假绿」。
8. **三个唯一事实源**：`settings.effective_embedding()`、`providers.thinking_fields()`、`db.RATE_RULE_DEFAULTS`（与 pg_init 人工对齐）。
9. **测试**：conftest 每进程一个 `kb_test_<uuid8>` schema；autouse fixture 用例开始前复位限流 + 清 `_WINDOWS`
   （**别改回靠用例自己收尾**，崩溃走不到 finally）；必须 `pop WEBAPP_REDIS_URL` + `WEBAPP_RATE_BACKEND=memory`。
   `webapp/Dockerfile` 必须 `COPY eval/config/`。演示容器用 `run-demo.sh`，不要用 compose 起。
10. **基础设施**：`docker-compose.infra.yml`，端口/镜像名可用 `KB_*` 覆盖。**5432/6379 被 siteyeb-pg/redis 占用**；
    Docker Hub 不可达 → daocloud 加速站，minio 只有华为云 SWR 有。`local` 存储只支持 standalone，生产必须对象存储。
    自检 `python scripts/verify_infra.py` → `INFRA_READY`。迁移脚本必须报「尝试 / 实际写入 / 跳过」三个数。
11. **Milvus 四坑**：`num_entities` 是近似值（断言用 `query(count(*))`）；库里存在逐元素相同向量（「top1==自己」是错断言）；
    `milvus_client.py` 是 ORM 风格、3.1 将移除；换对象存储必须 `--reset-collection`（否则 `load()` 永久挂住）。
12. **模型**：`discovery:deepseek-v4.1-flash-1017` + `BAAI/bge-m3`(1024) + `bge-reranker-v2-m3`。
    `WEBAPP_LLM_THINKING=disabled` → `thinking:{"type":"disabled"}`（实测 979ms→467ms；布尔 `reasoning` 参数无效，换网关必须重验）。
    网关 10 个模型里 6 个支持 image（看 `input_modalities`，不看名字）。429 分 rate（退避 1/2/4s）/ quota（不重试）。
    命名空间隔离：webapp 一律 `WEBAPP_`，guardrails 一律 `GUARDRAILS_`。

## 已验证的坑（别再踩）
Docker 29 + Compose v5 bake 中文路径报错 → 拆 `docker build`+`docker run`；`docker exec` 路径被转换 → `MSYS_NO_PATHCONV=1`；
网关末尾发 `choices: []` 纯 usage 块要跳过（否则正常回答判成 model_error）；`webapp/queue.py` 遮蔽标准库 `queue`；
`/metrics` 只输出有样本的行 → 零值样本必须预置；内嵌预览面板拦截 `window.open` → 改同标签 `<a>`；
compose 的 `parser` 服务用 `profiles:["parsing"]`，默认 `up` 仍 11 服务；旁路 `except: pass` 会把「功能已坏」伪装成「什么都没发生」。
**三条已证伪的误报，别再写进交付物**：index.html 内联处理器全有定义 / 两个 Dockerfile 都在且能 build / frontend 导入齐全 `npm run build` 通过。

## 验证命令
`python -m pytest webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests showcase/tests evalfmt/tests -q`；
`cd backend && pytest -q tests`；`python webapp/selftest.py`（33 项，需真模型）；`PARSING_OFFLINE=1 python showcase/verify_integration.py`；
`python demo/selftest.py`；`cd frontend && npm run build`；`git diff main..HEAD -- eval/ demo/`（必须为空）。

## 交付前三自查
① 数字/结论是否**实跑**得出？② 三态是否标清、有没有把目标态写成现状？③ 冻结区零 diff？
改任何约束性文本前先读 `docs/HARNESS.md`（YEBSPEC@1.0，Y1–Y22）。
