# 01 · 数据迁移：从 SQLite 演示库到 PostgreSQL + Milvus

> 配套文件（均**已实现**）：
> - `scripts/pg_init.sql` — SQLite `SCHEMA` 的 PG 方言翻译 + 必要种子
> - `scripts/migrate_sqlite_to_pg.py` — 迁移脚本（默认只读，`--apply` 才写）
> - `docker-compose.infra.yml` — PG 16 / Redis 7 / Milvus（etcd + minio + standalone）
>
> **三态**：PG 建表与迁移脚本 **已实现且已在本机真 PG 上跑通**（2026-11-15，PG 16-alpine，
> `--apply` 903 行、逐表比对 20/20 一致、时间戳逐字节相同）；**Milvus 向量迁移已实测**
> （真 Milvus 2.5.10 standalone，83 条向量、幽灵 0 / 漏迁 0、20/20 回查命中自己、权限 expr 收窄生效）；
> Celery 映射 **只设计**（`backend/` 有 13 个任务骨架，`webapp/` 仍是进程内队列）。
> 本文不编造任何「迁移耗时」「QPS 提升」数字。**没跑过的一律标「未验证」。**

---

## 1. 一句话结论

迁移不是「导出 CSV 再导入」。真正会咬人的是三件事：

1. **类型不是一一对应**——SQLite 的布尔是 `0/1` 的 `INTEGER`，时间是**字符串**，JSON 是**文本**。
   直接 `INSERT` 进 PG 会得到 `invalid input syntax for type boolean` 或静默错时区。
2. **向量库不是同一层**——`webapp` 的 `chunk_embeddings.vec` 是**文本列里塞 JSON 数组**，是 Milvus 的替身。
   搬到 PG **不能**沿着搬，必须落到 Milvus，且要处理「多个嵌入命名空间混在一张表」。
3. **回滚不能靠 `pg_dump`**——业务一旦切到 PG，SQLite 里就是冷数据。
   唯一安全的切法是**先影子读（双读比对）、再切写**，而不是「迁完就关掉旧的」。

---

## 2. 五种类型差异（每一类都有真实踩法）

| SQLite 实际存法 | PG 目标类型 | 直接搬会怎样 | 本项目的处理 |
|---|---|---|---|
| `INTEGER` 存 `0/1` 当布尔 | `boolean` | 报 `invalid input syntax for type boolean: "0"`（`0` 不是合法布尔字面量） | `BOOL_COLS` 集合显式转 `bool(int(v))` |
| `TEXT` 存 `2026-11-11T05:15:47Z` | `timestamptz` | 带上 `Z` 后缀时不同驱动/版本口径不一，可能被当本地时区，**整体偏 8 小时** | `TIME_COLS` 统一改写成 `+00:00` |
| `TEXT` 存 `{"region":"cn"}` | `jsonb` | 能插进去（PG 会转），但 `'null'` 与 `NULL` 语义不同 | `JSON_COLS` 保留字符串，由 psycopg 按 jsonb 收 |
| `INTEGER` 当金额 | `numeric(12,6)` | 浮点累加误差，对账时差几分钱找不到原因 | 建表即 `numeric`，迁移侧不改 |
| 无长度约束的 `TEXT` 主键 | `text PRIMARY KEY` | 无问题 | 保持一致，不引入 `varchar(n)` |

**为什么不把这些转换放进 `pg_init.sql` 的 `DEFAULT` 里**：`DEFAULT` 只管新插入的行，管不到存量数据。
转换必须发生在**读源库 → 写目标库**这一跳，也就是迁移脚本里。

**为什么 `BOOL_COLS` / `TIME_COLS` 要用「列名集合」而不是「按表写 if-else」**：
列名在整个 schema 里语义稳定（叫 `created_at` 的永远是时间，叫 `enabled` 的永远是布尔）。
按列名匹配，新增表时不用改转换逻辑；按表写则每加一张表都要复制一遍。

---

## 3. 表映射（20 张，逐表实测行数）

实测源库：`.data/webapp/app.db`，`python scripts/migrate_sqlite_to_pg.py --dry-run` → **合计 903 行**。

| # | 表 | 行数 | 主键 | 性质 | 迁移注意 |
|---|---|---:|---|---|---|
| 1 | `users` | 15 | `id` | 真值 | `password_salt` / `password_hash` 原样搬；**dry-run 抽样一律打 `***`** |
| 2 | `eval_tasks` | 3 | `id` | 真值 | — |
| 3 | `documents` | 75 | `id` | 真值 | `scope_json` → `jsonb`；`valid_from`/`expires_at`/`last_verified_at` → `timestamptz` |
| 4 | `chunks` | 119 | `id` | 真值 | 大文本；`idx` 是 `integer` |
| 5 | `chat_logs` | 106 | `id` | 真值（含脱敏后正文） | `degraded` → `boolean` |
| 6 | `chat_sources` | 52 | `id` | 真值（引用溯源） | `score` → `numeric(12,6)` |
| 7 | `badcases` | 26 | `id` | 真值 | — |
| 8 | `audit_logs` | 315 | `id` | 真值（审计，只增不改） | 迁完应比对 `COUNT(*)` 与 `MAX(created_at)` |
| 9 | `app_settings` | 9 | `key` | 真值（运行时配置） | 迁后要复核 `llm_provider` 等值没变 |
| 10 | `chunk_embeddings` | 129 | `(chunk_id, model)` | **派生，可重算** | 见 §5，**不要**只搬元数据了事 |
| 11 | `usage_events` | 24 | `id` | 真值（成本台账，**不回溯**） | `cost_cny` → `numeric`；`estimated` → `boolean` |
| 12 | `budgets` | 1 | `(scope, key)` | 真值 | 全局 `50` CNY / `warn` |
| 13 | `rate_rules` | 4 | `dimension` | 真值 | 见 §4 的种子值，代码里的实际默认是 1200/900/600/600 |
| 14 | `rate_hits` | 0 | `id` | 派生（限流命中台账） | 空表，迁不迁都行 |
| 15 | `eval_configs` | 6 | `key` | 真值 | — |
| 16 | `eval_runs` | 14 | `run_id` | 真值（评测台账） | `comparable` → `boolean` |
| 17 | `eval_logs` | 2 | `id` | 真值 | — |
| 18 | `safety_events` | 0 | `id` | 真值（PII 台账，**只存计数不存原文**） | `blocked` → `boolean` |
| 19 | `cache_entries` | 0 | `key` | **派生，可整表丢弃** | `is_negative` → `boolean`；`created_at`/`expires_at`/`last_access` → `timestamptz` |
| 20 | `runtime_state` | 3 | `key` | 半真值（`boot_count` 等） | 迁了会让重启计数延续，**通常故意不迁** |

**迁移顺序为什么这样排**：PG 侧没有建外键（演示规模不需要，且跨库外键会让迁移顺序变成硬约束），
但顺序仍按「被引用方在前」排，方便出问题时人工核对。
`cache_entries` / `runtime_state` 排**最后**：它们是最容易重建的派生数据，
前面真值表先落地，缓存即便迁失败也不影响业务正确性。

---

## 4. 实测：dry-run 到底跑了什么

```bash
$ python scripts/migrate_sqlite_to_pg.py --dry-run
源库：.data\webapp\app.db
表                         行数   样例（脱敏）
------------------------------------------------------------------------------
users                     15   id=ccf82b490d1b4f72b33a…, username=admin, email=admin@example.com, password_salt=***, password_hash=***
documents                 75   id=e68df5e7799e43abb18c…, owner_id=ccf82b490d1b4f72b33a…, filename=wifi.txt, ...
chunk_embeddings         129   chunk_id=a7ee2ddfb1664ac99d45…, model=text-embedding-bge-m3, dim=1024, vec=***, ...
runtime_state              3   key=boot_count, value=2, updated_at=2026-11-15T11:40:29Z
------------------------------------------------------------------------------
合计 903 行（dry-run，未写入目标库）
```

三个设计点值得讲：

- **默认只读**。不加 `--apply` 就一定是 dry-run；`--apply --dry-run` 同时给出时按只读处理并告警。
  迁移脚本的默认行为必须是最安全的那个 —— 想破坏数据得**显式**多打一个参数。
- **抽样脱敏在源码里写死**。`SENSITIVE_COLS = {password_hash, password_salt, vec, content, chunk_text, answer}`
  在抽样时一律输出 `***`。为什么不是「打印全量再遮蔽日志」——因为遮蔽漏一处就是一次泄露，
  而抽样本来就不需要看原文。
- **`--truncate` 默认拒绝**。要清空目标表得显式加参数，且脚本会先打印将被清空的表数。
  它只应该在确认目标库是空库或测试库时用。

---

## 5. 向量层：这才是真正难搬的部分

### 5.1 现状（别把替身说成 Milvus）

| 层 | 实现 | 状态 |
|---|---|---|
| `webapp/` | SQLite 表 `chunk_embeddings(chunk_id, model, dim, vec TEXT, updated_at)`，`vec` 是 **JSON 数组字符串** | **已实现**，是 Milvus 的替身 |
| `backend/` | `backend/app/integrations/milvus_client.py` 的 `MilvusVectorStore.ensure_collection()` | **骨架已实现，未连真实集群** |

`backend/` 侧的 collection schema（`milvus_client.py:34-53`）：

```
chunk_id     VARCHAR(64)  primary key
tenant_id    VARCHAR(64)  partition key          ← 历史命名，单组织改造后应换 ORG_NAMESPACE
kb_id        VARCHAR(64)  + Trie 索引
doc_id       VARCHAR(64)  + Trie 索引
dept_id      VARCHAR(64)  + Trie 索引
sensitivity  VARCHAR(16)  + Trie 索引
version_no   INT64
is_deleted   BOOL
embedding    FLOAT_VECTOR(dim=EMBEDDING_DIM)  HNSW / COSINE / M=16 / efConstruction=64
```

**`build_permission_expr()` 是唯一合法的 expr 构造器**：`tenant_id == "..."` 永远排第一且不可被调用方移除，
`extra` 只接受服务层白名单字符串，**不接受前端裸串**。这是「先按权限过滤再检索」红线的代码落点。

### 5.2 实测发现：两个嵌入命名空间混在同一张表

```
model                          dim   行数
external:BAAI/bge-m3          1024    83
text-embedding-bge-m3         1024    46
```

维度相同、模型名不同。这**不是**显示问题，是**数据正确性问题**：

- 如果两者其实是同一个模型（硅基流动 `BAAI/bge-m3` 与早期本地 LM Studio 的 `bge-m3`），
  向量可共用，迁移时统一改写成 `external:BAAI/bge-m3` 即可。
- 如果是**不同来源/不同权重**的两个模型，那 46 条向量与另外 83 条**不在同一向量空间**。
  余弦相似度照样能算出数字（维度都是 1024，`_cosine` 不会报错），但**分数没有意义**——
  检索看起来正常，实际全是噪声。这类问题不会抛异常，只会「答得怪」，最难查。

**所以迁移前必须先做这一步判定，而不是直接搬**：

```python
# 判定思路：同一条 chunk 若在两个命名空间都有向量，算余弦；
# 接近 1.0 ⇒ 同一模型；显著偏离 ⇒ 不同模型，必须重算。
# （本项目当前没有重复 chunk，所以判定要退化为：抽样若干文本，分别用两个
#   命名空间对应的模型 re-embed，比对向量距离。这一步未做。）
```

**处置结论（文档口径）**：迁移到 Milvus 时**只迁一个命名空间**，另一个标记为待重算。
`chunk_embeddings` 在 PG 里只留元数据（`chunk_id` / `model` / `dim` / `updated_at`），
`vec` 列保留仅为双写过渡期可回滚 —— **不是**长期存储方案。

### 5.3 向量迁移：四步 + 实测结果（**已跑通**）

配套脚本：`scripts/migrate_vectors_to_milvus.py`（默认只读，`--apply` 才写；复用
`backend/app/integrations/milvus_client.py` 的 `MilvusVectorStore`，**不另抄一份 schema**）。

1. **冻结写入**：停 worker，或把 `documents.status` 置 `frozen`，保证迁移期间不新增 chunk。
   （本次是演示库静态迁移，未做冻结。）
2. **建 collection**：`ensure_collection()`，分区数 `MILVUS_NUM_PARTITIONS=32` 建成后**不可改**。
3. **批量 upsert**：主键 `chunk_id`，批 200 条，写完 `flush()`。重跑只覆盖不新增存活行。
4. **对账**：`list_chunk_ids()` 与源库期望集合做差集；Milvus 有而源库无 = **幽灵**，反之 = **漏迁**。

**本机实测（Milvus 2.5.10 standalone，对象存储用 local）**：

```
覆盖率：chunks 共 119 条，其中本命名空间 83 条（69.7%），完全没向量 31 条
       其它命名空间（本次跳过）：{'text-embedding-bge-m3': 46}
待迁向量：83 条，维度 1024
  upsert   83/83
写入完成：83 条，耗时 3.2s
对账：Milvus 内 83 条 / 期望 83 条 | 幽灵 0 | 漏迁 0
回查断言（expr: tenant_id == "default" and is_deleted == false）
  OK   0440d55da70b4a3b… rank=1 score=1.0000
  OK   06eff01648a644bd… rank=3 并列 3 条同向量 score=1.0000
{"written": 83, "ghost": 0, "missing": 0, "checked": 20, "in_topk": 20,
 "top1_exact": 11, "verdict": "VECTORS_READY"}
```

**覆盖率 69.7% 不是 bug，是真实状态**：119 条 chunk 里 31 条根本没有向量（历史上传未跑嵌入），
另有 5 条只有 legacy 命名空间的向量。**迁 83 条并明确报出缺口，比「搬了 119 条但混了两个模型」正确得多。**

#### 三个必须知道的坑

**① `num_entities` 是近似值，且包含「已删未压实」的墓碑。**
Milvus 的 `upsert` 内部是 delete + insert，所以重跑一次后 `num_entities` 会**翻倍**（实测 83 → 166），
但 `query(count(*))` 的精确存活数仍是 83。用它做正确性断言会得到假告警；
**正确做法**：用 `count(*)`（本项目的 `verify_infra.py` 就是这么做的，并把墓碑数一并打印）。

**② 演示库里存在「逐元素完全相同」的向量。**
同一份样本文档被 e2e 反复上传，产生了同文本重复分块 → 同向量，实测 **5 组、最多 9 条一模一样**。
后果：`top1 == 自己` 这种断言会**假失败**（返回同分的另一条，`score` 也是 1.0000）。
**正确断言**：自己必须出现在 top-k 且 `score ≈ 1`；并列条数单独报出来。
向量库无法也不应该为相同向量定义先后。

**③ pymilvus 2.6 对 ORM 风格 API 全面告警**（`connections.connect` / `Collection` / `create_index` /
`col.upsert` / `col.search` 等），提示 PyMilvus 3.1 将移除。本项目 `milvus_client.py` 全用 ORM 风格，
**这是已知技术债**：迁移到 `MilvusClient` 属于独立改造，不在本次范围。
两个脚本里用 `warnings.filterwarnings` 压掉了逐行打印（否则会连源码行一起刷屏），
但**没有掩盖问题本身**——升级 pymilvus 3.x 前必须重写这个客户端。

---

## 6. Celery 映射：进程内队列换真队列

`webapp/queue.py` 是**进程内 `deque` + 单 worker 协程**，重启即丢（这是刻意的，见 `docs/async-upload.md`）。
`backend/app/tasks/ingest.py` 是 Celery 版本骨架，**13 个任务**：

| 阶段 | 任务名 | 作用 |
|---|---|---|
| 入口 | `enqueue_ingest` / `enqueue_richtext_ingest` / `retry_failed_job` | 投递（非 task，是 helper） |
| 1 | `parse_document` / `parse_richtext` | 整份解析 |
| 1b | `parse_page_range` → `merge_parsed` | 大文件分片解析再合并 |
| 2 | `chunk_document` | 切片 |
| 3 | `embed_and_index` | 向量化 + 写 Milvus |
| 4 | `finalize_document` / `finalize_batch` | 收尾（改状态、发进度） |
| 清理 | `cleanup_document` | 软删后的异步清理 |

**三条不能机械照搬的地方**：

1. **进度通道要换**。`webapp` 的进度是进程内 `dict` + SSE；多副本后必须落 Redis
   （key **只含** `ORG_NAMESPACE + batch_id`，**不按部门分片** —— 否则用户转岗后 SSE 断连空白）。
2. **幂等键要显式**。Celery 默认 `acks_late=False`，进程被杀会丢消息。
   开 `acks_late=True` 就必须让每个 task 幂等：本项目靠「先写 PG 文本、再写向量」+ 按 `chunk_id` upsert 保证重放安全。
3. **`_stage_guard` 是唯一异常出口**。它统一把异常转成 `stage` 字段 + 状态流转，
   不要在每个 task 里各写一套 `try/except`，否则失败态的字段形状会不一致。

---

## 7. 回滚：先影子读，再切写

**错误做法**：迁完 → 改 `DATABASE_URL` → 重启 → 出问题再改回来。
这时 SQLite 里已经是冷数据，PG 上新增的记录在回滚后**消失**，且没人知道丢了哪些。

**正确顺序（本项目建议，未演练）**：

| 阶段 | 读 | 写 | 验证点 |
|---|---|---|---|
| 0 基线 | SQLite | SQLite | 现有 33 项 e2e 全绿 |
| 1 影子写 | SQLite | SQLite + **双写 PG**（失败只记日志不阻断） | 比对两边行数差为 0 |
| 2 影子读 | SQLite 为主，**同时**读 PG 并比对结果 | 双写 | 抽样 50 条请求，两库结果逐字段一致 |
| 3 切读 | PG | 双写 | e2e 全绿；延迟、错误率无异常 |
| 4 切写 | PG | PG | 观察 1 个业务周期（≥1 天）；保留 SQLite 只读 |
| 5 退役 | PG | PG | 备份 SQLite 归档，**不删** |

**为什么必须先影子读**：读路径的问题（排序不稳定、时区偏移、`NULL` 排序位置、`numeric` 精度）
在**写入正确**的前提下依然会出现，而且只在真实查询里暴露。双读比对是唯一能在切写前发现它们的手段。
PG 的 `NULL` 默认排在 `ASC` 的**最后**（SQLite 排最前），这是最典型的会改变分页结果的差异。

**回滚判据（写死，别临场拍）**：任一项成立就回滚到上一阶段 ——
`P99 > 基线 1.5 倍` / `错误率 > 1%` / `双读不一致率 > 0.1%` / 任一 e2e 用例失败。

---

## 8. 操作教程（从零到能连）

### 8.1 起基础设施

```bash
# 只起数据层（不起应用）。默认端口即可时：
docker compose -f docker-compose.infra.yml up -d

# 本机实测：5432 / 6379 已被**别的项目**的容器占用（siteyeb-pg / siteyeb-redis），
# 且 Docker Hub 不可达。用环境变量覆盖宿主端口与镜像名，不动别人的容器：
export KB_PG_PORT=55432 KB_REDIS_PORT=56379 \
       KB_PG_IMAGE=docker.m.daocloud.io/library/postgres:16-alpine \
       KB_REDIS_IMAGE=docker.m.daocloud.io/library/redis:7-alpine
docker compose -f docker-compose.infra.yml up -d postgres redis

# 校验编排文件语法（不需要真起）
docker compose -f docker-compose.infra.yml config -q   # 已实测通过

# 看健康状态
docker compose -f docker-compose.infra.yml ps
# 或直接问
docker inspect -f '{{.State.Health.Status}}' kb-pg     # → healthy
docker exec kb-redis redis-cli ping                    # → PONG
```

端口全部绑 `127.0.0.1`（`5432`/`6379`/`19530`/`9091`），**不要改成 `0.0.0.0`** ——
这几个服务默认无鉴权或弱鉴权，暴露公网等于把库送人。
PG 密码 `kb_demo_only` 是明显可识别的占位值，上线必须换。

**为什么端口做成环境变量而不是写死成 55432**：写死就把「本机端口冲突」变成了项目约束。
默认值仍是标准端口，换机器不用改文件；只有冲突的机器才用变量覆盖。

#### 镜像从哪来（国内网络实拉记录，2026-11-15）

Docker Hub 直连不通（`dialing registry-1.docker.io` 超时）。逐个源实测的结果：

| 镜像 | 可用源 | 不可用源与原因 |
|---|---|---|
| `milvusdb/milvus:v2.5.10` | **`docker.m.daocloud.io/…`**（约 3.5 分钟） | Docker Hub 直连超时 |
| `coreos/etcd:v3.5.16` | **`quay.m.daocloud.io/…`** | Docker Hub 直连超时 |
| `postgres:16-alpine` / `redis:7-alpine` | `docker.m.daocloud.io/…`（本机已有） | — |
| `minio/minio` | **没找到可用源** | daocloud **denied**（该仓库被挡）；`1panel.live` manifest 可解析但 **blob 稳定 403**；`1ms.run` / `hub.rat.dev` / `dockerproxy.net` 均 `no such manifest`（镜像已迁 quay，旧 tag 不在 Docker Hub 上） |

**MinIO 拉不到怎么办**：不要为了一个对象存储把整条链路卡死。Milvus 2.5 standalone 支持
`common.storageType: local`，用本地磁盘代替对象存储。本项目把 minio 放进 `profiles: ["minio"]`，
并把 `depends_on` 标成 `required: false`，于是：

```bash
# 没有 minio 也能跑通 Milvus（standalone 单副本；生产不能用这个）
KB_MILVUS_STORAGE=local \
KB_ETCD_IMAGE=quay.m.daocloud.io/coreos/etcd:v3.5.16 \
KB_MILVUS_IMAGE=docker.m.daocloud.io/milvusdb/milvus:v2.5.10 \
  docker compose -f docker-compose.infra.yml up -d etcd milvus

# 有 minio 时（生产形态）：
docker compose -f docker-compose.infra.yml --profile minio up -d
```

**这是明知的妥协，不是设计**：`local` 存储只支持 standalone 单副本，没有对象存储的
共享与扩容能力。生产必须 minio 或云对象存储。写在这里是为了「本机能跑通」，
不是为了「这样就够了」。

### 8.2 建表（PG 首次启动自动执行）

`pg_init.sql` 挂在 `/docker-entrypoint-initdb.d/`，**只在数据卷为空时执行一次**。
改了 SQL 要重跑，必须先 `docker compose -f docker-compose.infra.yml down -v`
（`-v` 会删数据卷，**只在演示库上这么做**），或手工 `psql -f scripts/pg_init.sql`。

```bash
# 手工执行（幂等，全是 IF NOT EXISTS）
docker compose -f docker-compose.infra.yml exec -T postgres \
  psql -U kb -d kb -f /docker-entrypoint-initdb.d/pg_init.sql
```

### 8.3 迁数据

```bash
DSN="postgresql://kb:kb_demo_only@127.0.0.1:55432/kb"   # 默认端口时就写 5432

# 1) 先只看（永远先跑这步）
python scripts/migrate_sqlite_to_pg.py --dry-run

# 2) 只迁关键几张表试水
python scripts/migrate_sqlite_to_pg.py --apply --pg-dsn "$DSN" --tables users documents chunks

# 3) 全量
python scripts/migrate_sqlite_to_pg.py --apply --pg-dsn "$DSN"

# 4) 核对（脚本不替你核对，必须自己比）
docker exec kb-pg psql -U kb -d kb -c \
  "SELECT 'documents' t, count(*) FROM documents UNION ALL
   SELECT 'chunks', count(*) FROM chunks UNION ALL
   SELECT 'audit_logs', count(*) FROM audit_logs;"
```

`--apply` 需要 `psycopg`，它**不在项目 requirements 里**（只有真迁才需要）。
脚本对缺失依赖的处理是给一行明确提示，而不是抛 `ImportError` 栈：

```
[FAIL] 未安装 psycopg。安装：pip install 'psycopg[binary]'
       （它不在项目 requirements 里，只在真正迁移时需要）
```

**可重入性**：全部按主键 `ON CONFLICT DO NOTHING`，中断后直接重跑即可，不会重复插入。
注意这也意味着**改过的源数据不会被覆盖** —— 需要「以后来为准」时得先 `--truncate` 或手工删行。

脚本会分别报「尝试行数」与「实际写入行数」：

```
users                尝试   15 行 / 实际写入   15 行
...
完成：尝试 903 行，实际写入 903 行，跳过 0 行（主键已存在）。
```

**为什么必须是两个数**：第一版只报一个数（`len(chunk)`），重跑时会打印「写入 903 行」而实际写 0 行 ——
这会让「迁移成功」变成一句无法验证的话。改用 `cur.rowcount` 之后，重跑能明确看到 `跳过 903`，
这才是可重入的**可观测**证据，而不是靠读代码相信它。

### 8.4 本机实跑记录（2026-11-15，PG 16-alpine）

这一节就是「已实现」三个字的凭据，命令与输出都可复跑。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 起 PG | `docker compose -f docker-compose.infra.yml up -d postgres` | `kb-pg` → `healthy` |
| 建表 | init 脚本自动执行（`/docker-entrypoint-initdb.d/10-init.sql`） | **20 张表**，日志零 error |
| 种子核对 | `SELECT dimension,limit_count FROM rate_rules` | `user 1200 / endpoint 900 / model 600 / ip 600` ✓ |
| 首迁 | `--apply --pg-dsn …:55432` | 尝试 903 / 写入 903 / 跳过 0 |
| 重跑 | 同命令（不加 `--truncate`） | 尝试 903 / **写入 0 / 跳过 903** → 可重入证明 |
| 清空重迁 | `--apply --truncate` | 尝试 903 / 写入 903（`TRUNCATE` 20 表后完整重建） |
| 逐表比对 | 20 张表 `COUNT(*)` 双侧对比 | **不一致表数 = 0** |
| 尾部对齐 | `MAX(audit_logs.created_at)` 等 3 张表 | SQLite 与 PG **逐字节相同** |
| 类型抽检 | `chat_logs.degraded` / `documents.scope_json` / `usage_events.cost_cny` / `users.created_at` | `bool` / `dict` / `Decimal` / `datetime` |
| 真实查询 | `documents JOIN chunks` 按文件名聚合 | 返回真实分布（`全景设计…md` 48 块等） |
| 起 Milvus | `KB_MILVUS_STORAGE=local docker compose … up -d etcd milvus` | `kb-etcd` healthy → `kb-milvus` healthy |
| 建集合 | `MilvusVectorStore.ensure_collection()` | `kb_chunks`，1024 维 / HNSW COSINE / 32 分区 |
| 迁向量 | `migrate_vectors_to_milvus.py --apply` | 83 条写入，耗时 3.2s |
| 向量对账 | `list_chunk_ids()` vs 源库期望 | **幽灵 0 / 漏迁 0** |
| 回查自证 | 20 条真实向量回查 | **20/20 在 top-k 且 `score=1.0000`**（其中 9 条与同向量并列） |
| 权限 expr | 错 tenant 查 / 按 `doc_id` 收窄 | 错 tenant **0 条**；收窄后 **1 个文档** |

**一条自检脚本**（把上面这些断言变成可复跑的一条命令）：

```bash
python scripts/verify_infra.py \
  --pg-dsn "postgresql://kb:kb_demo_only@127.0.0.1:55432/kb" \
  --redis-url "redis://127.0.0.1:56379/0" \
  --milvus-uri "http://127.0.0.1:19530"
# == PostgreSQL ==  表 20 张 · documents 75 · chunks 119 · 无限流种子偏差 · created_at=timestamp with time zone
# == Redis（多副本窗口共享）==  limit=3：内存 6 次 vs Redis 3 次，放大倍数 2.0×
# == Milvus（集合与权限 expr）==  kb_chunks · 存活 83 条向量 · 覆盖 46 个文档 · 错 tenant 命中 0 条 · 收窄后 1 个文档
# {"verdict": "INFRA_READY"}
```

后两行不是顺带测的，它们分别是 `03` 和本节的核心论据：

- **Redis 那行**：同样的 `limit=3`，两个进程用内存实现放行 6 次、用 Redis 放行 3 次。
  这就是「多副本部署时进程内限流会把配额放大 N 倍」的实测数字，不是理论推演。
- **Milvus 那行**：`错 tenant 命中 0 条` 是「过滤真的发生了」的反证；
  `收窄后 1 个文档` 是「expr 不是摆设」的反证。只验「连上了」等于没验。

**时间戳「逐字节相同」这一条尤其重要**：它是 `Z → +00:00` 归一化真的生效的证据。
如果没做这层转换，PG 会把 `2026-11-12T13:48:56Z` 当本地时间，整体偏 8 小时 ——
而这种偏差在 `ORDER BY created_at` 的分页里才暴露，迁移当天的行数比对完全看不出来。

### 8.5 边界（必须知道）

- `pg_init.sql` 里的**种子值与 `webapp/db.py::_seed` 逐字对齐**（user 1200 / endpoint 900 / model 600 / ip 600）。
  这两处一旦不同，迁移后会出现「PG 里的限流配额比 SQLite 严 10 倍」这种诡异现象，
  且由于 `rate_rules` 是运行时读的，改代码不会自愈。
- **`department` 不建 `rate_rules` 行**。按部门限流是明确**不做**的，`department` 只作成本归因。
- **索引单独建，不写进 `SCHEMA` 字符串**。本项目在 SQLite 侧踩过一次：旧库 `executescript(SCHEMA)` 时
  新列还不存在，`CREATE INDEX` 先抛 `no such column`，导致整个脚本失败、迁移代码根本没跑到。
  PG 侧同样把 `CREATE INDEX` 与 `CREATE TABLE` 分开写，就是为了保持这个习惯。

---

## 9. 实测坑点表

| 坑 | 现象 | 原因 | 处置 |
|---|---|---|---|
| `pg_init.sql` 漏 `cache_entries` / `runtime_state` | `--apply` 迁这两张表时报 `relation does not exist` | 翻译时只看了 `webapp/db.py` 的 `SCHEMA`，漏了 `cache/store.py::_SCHEMA`（缓存包自己的表） | 已补；并加**列集合自动比对**（见下） |
| `rate_rules` 种子写错 | 迁移后配额比原来严 10 倍 | 凭印象写 120/600，实际 `db.py` 里是 1200/900/600/600 | 已改为对齐代码；本文档记录口径 |
| 两个嵌入命名空间 | 检索不报错但结果无意义 | 同维度不同模型的向量不在同一空间 | 见 §5.2；迁移只取一个，另一个待重算 |
| `--dry-run` 参数不存在 | `unrecognized arguments: --dry-run` | docstring 写了但 `argparse` 没注册 | 已补该 flag，且与 `--apply` 冲突时按只读处理 |
| 重跑报「写入 903 行」而实际 0 行 | 以为重复插入了 | 计数用的是 `len(chunk)`（尝试数），不是 `cur.rowcount`（实际影响行数） | 已改为同时报「尝试 / 实际写入 / 跳过」；重跑能明确看到 `跳过 903` |
| `up` 报 `port is already allocated` | 起不来 PG | 本机 5432 已被 `siteyeb-pg` 占用，6379 被 `siteyeb-redis` 占用（**别的项目**的容器） | compose 端口改成 `${KB_PG_PORT:-5432}` 等，默认值不变；本机用 `55432/56379` 起，不停别人的容器 |
| 拉镜像超时 `dialing registry-1.docker.io` | `up` 卡在 Pulling 后失败 | Docker Hub 不可达 | 镜像名同样参数化（`KB_PG_IMAGE` 等），用本机已有的 `docker.m.daocloud.io/library/postgres:16-alpine` |
| `minio/minio` 三个源都拿不到 | daocloud `denied`；1panel manifest 通了但 blob `403`；1ms/rat.dev/proxy.net `no such manifest` | MinIO 官方镜像已迁 quay，Docker Hub 上那些 tag 不存在；部分镜像站按仓库屏蔽 | Milvus 改 `KB_MILVUS_STORAGE=local`（standalone）；minio 放 `profiles: ["minio"]` + `depends_on.required: false`，不让可选依赖卡死启动 |
| 权限 expr 看着对但没生效 | 收窄后条数没变 | 只验「连上了」，没验「过滤发生了」 | 加两条反证：错 tenant 必须 0 条；按 `doc_id` 收窄后文档数必须变少 |
| `num_entities` 重跑后翻倍 | 以为 upsert 插了重复行 | 它是**近似值且含已删未压实墓碑**，upsert = delete + insert | 用 `query(count(*))` 取精确存活数，并把墓碑数一起打印 |

**列集合比对脚本**（每次改 `pg_init.sql` 都该跑一遍，比人眼看靠谱）：

```python
# 粗解析 CREATE TABLE 块 → 与 PRAGMA table_info 逐表比列名
# 输出「PG 缺表 / PG 缺列」，差异数必须为 0
# 当前实测：20 张表，列集合差异 0
```

这条规则的可复用版本：**任何「两处 schema 必须一致」的场景，都要有一个能自动比对的脚本，
而不是靠人工核对。** 人工核对的漏检率在 20 张表规模上已经很高了 —— 本文就漏了两张。

---

## 10. 自检清单（**已在本机逐项跑过**，2026-11-15）

- [x] `docker compose -f docker-compose.infra.yml config -q` 通过
- [x] `pg_init.sql` 与源 `SCHEMA` **列集合差异 = 0**（20 张表，含 `cache_entries` / `runtime_state`）
- [x] `pg_init.sql` 种子值 = `webapp/db.py::RATE_RULE_DEFAULTS`（1200/900/600/600 + 预算 + 6 项 eval_configs）
- [x] dry-run 行数 = 903
- [x] `--apply` 后逐表 `COUNT(*)` 与源库一致（**20/20**）
- [x] `audit_logs` / `chat_logs` / `usage_events` 的 `MAX(时间)` 与源库**逐字节相同**（无 8 小时偏移）
- [x] 重跑一次 `--apply`：实际写入 0、跳过 903（可重入性可观测）
- [x] `--apply --truncate` 清空 20 表后完整重建 903 行
- [ ] `chunk_embeddings` 只保留**一个**嵌入命名空间，另一个有明确处置 ← **部分做**：Milvus 只迁了 `external:BAAI/bge-m3`（83 条），`text-embedding-bge-m3`（46 条）显式跳过并报数；但**两个模型是否同一向量空间仍未判定**（需对同文本分别 re-embed 比对距离）
- [x] Milvus collection 已建、`num_partitions` 已定（32，建成后不可改）
- [x] 对账跑过：幽灵向量 = 0，漏迁 = 0
- [x] 权限 expr 反证：错 tenant 命中 0 条；按 `doc_id` 收窄后文档数从 46 → 1
- [ ] 影子读阶段的两库结果比对不一致率 < 0.1% ← **未做**（需应用侧双读改造）
- [ ] `webapp/rag.py` 真正改走 Milvus 召回 ← **未做**（本次只迁数据 + 验 expr，主链路仍用 SQLite 向量）
- [ ] 回滚判据写进值班文档 ← 判据已写在 §7，**尚未进值班文档**

---

## 11. 面试怎么讲这一段

**不要讲「我做了数据迁移」，要讲「我怎么判断迁移是安全的」。**

三层递进：

1. **类型层**：SQLite 是动态类型，PG 是静态类型。布尔、时间、JSON 三类的隐式转换
   在 SQLite 里「随便都能塞」，到 PG 就是硬报错或静默错时区。所以转换必须发生在
   「读源 → 写目标」这一跳，不能指望 `DEFAULT`。
2. **正确性层**：向量最危险的不是搬不过去，是**搬过去了但没意义**。两个同维度的嵌入模型
   混在一张表里，余弦照样能算，分数全是噪声。这类问题不抛异常，只在「答得怪」时暴露。
3. **过程层**：迁移的验收标准不是「迁完了」，是「**能安全回滚**」。所以先影子写、再影子读、
   再切读、再切写，每一步都有量化判据。只做「导出导入 + 改连接串」的人，
   在生产上出事时没有退路。

**一个能立刻区分配置「试过」和「没试过」的细节**：问他「你重跑一次迁移脚本，会打印什么？」
只报一个数（「写入 903 行」）说明没真跑过——因为真跑第二次会是 0 行。
把「尝试 / 实际写入 / 跳过」分开报，才是跑过的人才会做的设计。

**诚实边界**：`--apply` 已在真 PG 上跑通（903 行 / 20 表一致 / 时间逐字节对齐），
向量迁移也已在真 Milvus 上跑通（83 条 / 幽灵 0 / 漏迁 0 / 回查 20-20 命中 / expr 收窄生效）。
但 **Celery 映射只设计未实现**，**影子读双跑未做**（需应用侧改造），
**`webapp/rag.py` 主链路仍走 SQLite 向量、没有真正切到 Milvus 召回**。
**说清楚哪一步没做，比把设计说成实现更值钱。**

**再加一条能立刻分辨真假的**：问他「Milvus 里有多少条向量」。
答得出「存活 83 条、另有 N 条已删墓碑」的是真跑过；
只报 `num_entities` 的，八成没注意它是近似值。
