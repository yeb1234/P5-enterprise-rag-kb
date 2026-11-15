# 01 · 数据迁移：从 SQLite 演示库到 PostgreSQL + Milvus

> 配套文件（均**已实现**）：
> - `scripts/pg_init.sql` — SQLite `SCHEMA` 的 PG 方言翻译 + 必要种子
> - `scripts/migrate_sqlite_to_pg.py` — 迁移脚本（默认只读，`--apply` 才写）
> - `docker-compose.infra.yml` — PG 16 / Redis 7 / Milvus（etcd + minio + standalone）
>
> **三态**：PG 建表与迁移脚本 **已实现且实跑过 dry-run**；infra 编排 **已实现并通过 `compose config` 校验**；
> **`--apply` 真写 PG 尚未在本机实跑**（没有跑起来的 PG 实例）；Milvus 向量迁移 **只设计**；
> Celery 映射 **只设计**（`backend/` 有 13 个任务骨架，`webapp/` 仍是进程内队列）。
> 本文不编造任何「迁移耗时」「QPS 提升」数字。**凡是没跑过的，一律标「未验证」。**

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

### 5.3 向量迁移四步（只设计，未做）

1. **冻结写入**：停 worker，或把 `documents.status` 全部置 `frozen`，保证迁移期间不新增 chunk。
2. **建 collection**：`ensure_collection()`，注意分区数 `MILVUS_NUM_PARTITIONS` 建成后**不可改**。
3. **批量 upsert**：按 `chunk_id` 幂等（`upsert` 主键是 `chunk_id`，重跑只覆盖不新增），
   批次建议 500–1000 条；**每批 `flush()`** 否则数据在内存里查不到。
4. **对账**：`list_chunk_ids()` 分页遍历 Milvus 主键，与 PG `chunks` 做差集。
   - Milvus 有、PG 无 → **幽灵向量**，删。
   - PG 有、Milvus 无 → **漏迁**，补。
   该对账逻辑在 `webapp` 侧已有等价的启动自检（`cache/recovery.py::run_startup_checks()` 里的 ghost 检查），
   Milvus 版本**未实现**。

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
# 只起 PG + Redis + Milvus，不起业务容器
docker compose -f docker-compose.infra.yml up -d

# 校验编排文件语法（不需要真起）
docker compose -f docker-compose.infra.yml config -q   # 已实测通过

# 看健康状态
docker compose -f docker-compose.infra.yml ps
```

端口全部绑 `127.0.0.1`（`5432` / `6379` / `19530` / `9000` / `9001`），
**不要改成 `0.0.0.0`** —— 这几个服务默认无鉴权或弱鉴权，暴露公网等于把库送人。
PG 密码 `kb_demo_only` 是明显可识别的占位值，上线必须换。

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
# 1) 先只看
python scripts/migrate_sqlite_to_pg.py --dry-run

# 2) 只迁关键几张表试水
python scripts/migrate_sqlite_to_pg.py --apply --tables users documents chunks

# 3) 全量
python scripts/migrate_sqlite_to_pg.py --apply

# 4) 核对（脚本不替你核对，必须手工比）
docker compose -f docker-compose.infra.yml exec -T postgres psql -U kb -d kb -c \
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

### 8.4 边界（必须知道）

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

**列集合比对脚本**（每次改 `pg_init.sql` 都该跑一遍，比人眼看靠谱）：

```python
# 粗解析 CREATE TABLE 块 → 与 PRAGMA table_info 逐表比列名
# 输出「PG 缺表 / PG 缺列」，差异数必须为 0
# 当前实测：20 张表，列集合差异 0
```

这条规则的可复用版本：**任何「两处 schema 必须一致」的场景，都要有一个能自动比对的脚本，
而不是靠人工核对。** 人工核对的漏检率在 20 张表规模上已经很高了 —— 本文就漏了两张。

---

## 10. 自检清单（上线前逐项打勾）

- [ ] `docker compose -f docker-compose.infra.yml config -q` 通过
- [ ] `pg_init.sql` 与源 `SCHEMA` **列集合差异 = 0**（跑 §9 的比对脚本）
- [ ] `pg_init.sql` 种子值 = `webapp/db.py::_seed` 的值（限流四维 + 预算 + eval_configs 六项）
- [ ] dry-run 行数 = 预期（当前 903）
- [ ] `--apply` 后逐表 `COUNT(*)` 与源库一致
- [ ] `audit_logs` 的 `MAX(created_at)` 与源库一致（证明没漏尾部）
- [ ] `chunk_embeddings` 只保留**一个**嵌入命名空间，另一个有明确处置
- [ ] Milvus collection 的 `num_partitions` 已定（建成后不可改）
- [ ] 对账跑过：幽灵向量 = 0，漏迁 = 0
- [ ] 影子读阶段的两库结果比对不一致率 < 0.1%
- [ ] 回滚判据写进值班文档，不是口头约定

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

**诚实边界**：本项目的 `--apply` 没在真 PG 上跑过，Milvus 迁移只设计未实现，
Celery 映射只设计未实现。**说清楚哪一步没做，比把设计说成实现更值钱。**
