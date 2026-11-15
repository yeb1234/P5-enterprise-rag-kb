# P5 知识库 · 长期记忆

## 定位与约定
**开发框架骨架**（学习+面试+逐步生产化），A/B/C 三档，**面试别把 A 档说成 C 档**。栈固定 React/FastAPI/PG/Milvus/Redis + 外部 DeepSeek。
用户学 FastAPI+DB（后 SpringBoot）：注释讲**设计取舍**，文档必分「已实现/只设计/未做」。v1.0.0 三态定版 ≠ 可上线；未做 18 项（20 项中 2 项已闭环）见 `docs/production/07-remaining-work.md`，§11 已分档「值得做 1 / 只值得说 17」。
**向量层已切 pgvector**（v1.3.0）：`embedding vector(1024)` + HNSW，一条 SQL 完成过滤+排序；143 候选 69ms→**2.2ms**。`vec` 文本列保留回滚（`WEBAPP_VECTOR_BACKEND=python`）。Milvus 三件套进 `profiles:["milvus"]` 默认不起，`verify_infra` 默认跳过。论证见 `docs/production/08-vector-store-decision.md`。
v1.1.0 `evalfmt/`；v1.2.0 公开基准（tag `v1.2.0`）。回归全绿：六个包 pytest + backend + eval + evalbench 全过；e2e 33/33；demo 16/16。
应用层 `webapp/`(8765, admin/admin123) + `backend/` + `frontend/` + `eval/`/`evalbench/`；六个纯函数包与 `demo/`(冻结区) 见 README。

## 公开基准（CmedqaRetrieval 10万/3999/7449，切 2429+1570）
nDCG@10：BM25 16.29% → **bge-m3 33.93%**；前缀 31.05%、融合 w=0.85 31.98% 都更差。重排同 400 子集基线 32.79% → top20 36.09% / top50 36.02%（0.06 属噪声，取浅）。
**三个负结果照说**：前缀无用；融合趋向纯向量→不融合；top20/50 并列别说「最优深度」。**砍弱基线让增益好看＝自欺**。
延迟 BM25 **110ms** / 向量 **2.6ms** / 重排 **357ms**；缓存命中记 0.0s，须真调 API 探针，增益与**同子集**基线比。

## 组织模型
单组织+多用户多部门，**多租户只写未来方向、不做**。webapp 成本/限流维度已 tenant→department（列 `department_id`），**无部门级权限**；
`backend/` 的 `tenant_id` 是历史债；`org-model-change-plan.md` 13 步仍「方案已定、未改造」。

## 硬约束
1 **权限** `permission.decide()` 唯一权威；**先过滤→再重排→再进模型**；Milvus expr + `filter_candidates` 双保险 + 列表侧逐条复核（A13 对拍）。
2 **真值** PG 是状态真值，Redis 只是通道；先写文本再写向量；删文档**先删 `chunk_embeddings`**（治幽灵向量）。
3 **DDL** 唯一事实源 `scripts/pg_init.sql`；别往 `db.py` 写 `CREATE TABLE`，**改回 SQLite = 撤销一次大改造**。
4 **缓存 key 13 段**（含 authz/data_version/policy）。**TTL 不是权限控制**：先过滤再写，命中后 `key_matches()` 复核；主链路不接检索缓存。
5 **PII** **先检索后脱敏**（检索用原文，进 LLM/落库/SSE/导出用脱敏）；`StreamRedactor.push()` 扣住跨界候选整串。
6 **冻结区** `eval/` `demo/` 零 diff，`__TBC__`/`NOT_COMPARABLE` 口径不改；C33 冻的是 10 view+6 loader **存在性**（当前 14 个 `data-view`，新增不破坏）；
   改 JS/CSS 必升 `?v=`（当前 v=15）；无 `gen_dict.py`，中文原文即 key、中英成对改并删旧 key。
7 **验证** 能力判定用**结构化元数据+实测**，不用命名启发式；指标零值样本必须预置；断言要防「测试自己假绿」。
8 **三个唯一事实源** `settings.effective_embedding()`、`providers.thinking_fields()`、`db.RATE_RULE_DEFAULTS`。
9 **测试** conftest 每进程一个 `kb_test_<uuid8>` schema；autouse 用例前复位限流+清 `_WINDOWS`（别靠用例收尾），
   须 `pop WEBAPP_REDIS_URL` + `WEBAPP_RATE_BACKEND=memory`；`webapp/Dockerfile` 要 `COPY eval/config/`；演示用 `run-demo.sh`。
10 **基础设施** `docker-compose.infra.yml`，`KB_*` 覆盖；**5432/6379 被 siteyeb-pg/redis 占用**；Docker Hub 不可达→daocloud。
11 **Milvus** `num_entities` 是近似（断言用 `count(*)`）；库里有逐元素相同向量；换存储必须 `--reset-collection`。
12 **模型** `deepseek-v4.1-flash-1017` + `bge-m3`(1024) + `bge-reranker-v2-m3`；`WEBAPP_LLM_THINKING=disabled`→`thinking:{"type":"disabled"}`
   （实测生效；换网关必重验）；10 模型 6 个支持 image（看 `input_modalities`）；429 分 rate/quota 两类。

## 已验证的坑
网关末尾 `choices: []` 纯 usage 块要跳过；内嵌面板拦 `window.open`→改同标签 `<a>`；
旁路 `except: pass` 会把「功能已坏」伪装成「什么都没发生」；同名 bge-m3 跨实现同文本余弦仅 **0.977**（同端点重调 0.99996）→ 缓存 key 必带实现前缀。**扩展是库级、装不进 schema**：测试 search_path 必须带 public，否则 `vector` 类型与 `<=>` 都解析不到；官方 postgres 镜像加扩展只能换镜像（apk 装的是 PG18 的包）。
**三条已证伪误报别再写**：内联处理器全有定义 / 两个 Dockerfile 都在 / `npm run build` 通过。

## 命令与三查
三查：① 数字实跑？② 三态分清？③ 冻结区零 diff？改约束性文本前必先读 `docs/HARNESS.md`（YEBSPEC@1.0，Y1–Y22）。
