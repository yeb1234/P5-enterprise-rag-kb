# P5 知识库 · 长期记忆

## 定位与约定
**开发框架骨架**（学习+面试+逐步生产化），A/B/C 三档，**面试别把 A 档说成 C 档**。栈固定 React/FastAPI/PG/Redis + 外部 DeepSeek（Milvus 退为对照实验，本地镜像已删）。
用户学 FastAPI+DB（后 SpringBoot）：注释讲**设计取舍**，文档必分「已实现/只设计/未做」。三态定版 ≠ 可上线；`07-remaining-work.md` **4 闭环 / 16 未做**，§11 分档「值得做 1 / 只值得说 15」。
`webapp/`(8765, admin/admin123) + `backend/` + `frontend/` + `eval/`/`evalbench/`；纯函数包与 `demo/`(冻结区) 见 README。

## 三个已落地的改造（别破）
- **v1.3.0 向量切 pgvector**：`embedding vector(1024)` + HNSW，一条 SQL 做完过滤+排序；143 候选 69ms→**2.2ms**。`vec` 列保留回滚（`WEBAPP_VECTOR_BACKEND=python`）。论证见 `production/08`。
- **v1.3.1 连接层接 SQLAlchemy Engine 池化**：只接池不接 ORM（138 处按 SQL 文本写，改 ORM 是重写）。`close()`=还池；`connect+close` 0.42ms、端到端 18.59→**3.77ms**。**32× 是「算得少」，18× 是「连得省」**。
- **v1.3.2 日志结构化**：stdout 单行 JSON + `access_log` 中间件贯穿 trace_id（响应头/错误体/日志同值），path 不含 query、健康检查降 DEBUG。

## 公开基准（CmedqaRetrieval，详见 `evalbench/iterations`）
nDCG@10：BM25 16.29% → **bge-m3 33.93%**；前缀/融合更差；重排 32.79% → top20 36.09%（0.06 属噪声）。**三个负结果照说；砍弱基线让增益好看＝自欺**。延迟 110/2.6/357ms；缓存命中记 0.0s，须真调 API 探针。

## 组织模型
单组织+多用户多部门，**多租户只写未来方向、不做**。webapp 成本/限流维度已 tenant→department（列 `department_id`），**无部门级权限**；`backend/` 的 `tenant_id` 是历史债；`org-model-change-plan.md` 13 步仍「方案已定、未改造」。

## 硬约束
1 **权限** `permission.decide()` 唯一权威；**先过滤→再重排→再进模型**；`filter_candidates` 双保险 + 列表侧逐条复核（A13 对拍）。
2 **真值** PG 是状态真值，Redis 只是通道；先写文本再写向量；删文档**先删 `chunk_embeddings`**（治幽灵向量）。
3 **DDL** 唯一事实源 `scripts/pg_init.sql`；别往 `db.py` 写 `CREATE TABLE`，**改回 SQLite = 撤销一次大改造**。
4 **缓存 key 13 段**（含 authz/data_version/policy）。**TTL 不是权限控制**：先过滤再写，命中后 `key_matches()` 复核；主链路不接检索缓存。
5 **PII** **先检索后脱敏**（检索用原文，进 LLM/落库/SSE/导出用脱敏）；`StreamRedactor.push()` 扣住跨界候选整串。
6 **冻结区** `eval/` `demo/` 零 diff，`__TBC__`/`NOT_COMPARABLE` 不改；C33 冻 10 view+6 loader **存在性**（当前 14 个 `data-view`）；改 JS/CSS 必升 `?v=`（当前 v=15）；无 `gen_dict.py`，中文原文即 key、中英成对改删旧 key。
7 **验证** 能力判定用**结构化元数据+实测**，不用命名启发式；断言要防「测试自己假绿」。三个唯一事实源：`settings.effective_embedding()`、`providers.thinking_fields()`、`db.RATE_RULE_DEFAULTS`。
8 **测试** conftest 每进程一个 `kb_test_<uuid8>` schema；autouse 用例前复位限流+清 `_WINDOWS`；须 `pop WEBAPP_REDIS_URL` + `WEBAPP_RATE_BACKEND=memory`；镜像要 `COPY eval/config/`。
9 **基础设施** `docker-compose.infra.yml`，`KB_*` 覆盖；**5432/6379 被 siteyeb-pg/redis 占用**；Docker Hub 不可达→daocloud。
10 **Milvus** 镜像/卷已删（对照实验需重拉约 2.7GB 并重灌）；`num_entities` 是近似（断言用 `count(*)`）；换存储必须 `--reset-collection`。
11 **模型** `deepseek-v4.1-flash-1017` + `bge-m3`(1024) + `bge-reranker-v2-m3`；`WEBAPP_LLM_THINKING=disabled`→`thinking:{"type":"disabled"}`（换网关必重验）；10 模型 6 个支持 image（看 `input_modalities`）；429 分 rate/quota。

## 已验证的坑
网关末尾 `choices: []` 纯 usage 块要跳过；内嵌面板拦 `window.open`→改同标签 `<a>`；旁路 `except: pass` 把「功能已坏」伪装成「什么都没发生」；同名 bge-m3 跨实现余弦仅 **0.977**（同端点重调 0.99996）→ 缓存 key 必带实现前缀。
**扩展是库级、装不进 schema**：测试 search_path 必须带 public，否则 `vector` 与 `<=>` 都解析不到；官方 postgres 镜像加扩展只能换镜像（apk 装的是 PG18 的包）。
**池化让会话级 `SET` 跨借用人存活**（实测变 `pg_catalog`）：`connect_args.options` 管不住，`WEBAPP_DB_SCHEMA` 下必须 checkout 补 `SET` 复核；`postgresql://` 走 psycopg2 → 必须 `postgresql+psycopg://`。
**compose 的 `x-logging` 对 `docker run` 无效**：日志上限必须写进 `run-demo.sh`，否则 json-file 无上限写满磁盘。
**三条已证伪误报别再写**：内联处理器全有定义 / 两个 Dockerfile 都在 / `npm run build` 通过。

## 三查
① 数字实跑？② 三态分清？③ 冻结区零 diff？改约束性文本前必先读 `docs/HARNESS.md`（YEBSPEC@1.0，Y1–Y22）。
