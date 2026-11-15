# 向量存储选型：pgvector 还是 Milvus

编制：2026-11-16 ｜ 状态：**已落地（选择 B：pgvector），Milvus 移出默认启动**
适用：回答「你们为什么用/不用 Milvus」，以及 `07-remaining-work.md` #1 的闭环记录。

> 三态口径：本文**实测数字**都可复跑（命令见各节）；
> **§7 的迁移步骤已于 2026-11-16 执行完毕**，实际与预案不同的两处记在 §7.0；
> **A13 对拍**只做了单次抽样（见 §6.5），200 条批量对拍仍是待办。

---

## 0. 一句话

选型的分水岭**不是「谁更快」**，而是两件事：**过滤与排序能不能在一次查询里完成**，
以及**向量和业务数据是否共享同一个事务边界**。百万级以内，pgvector 通常更合适，
而且它跟本项目「先按权限过滤再排序」的硬约束天然契合。

---

## 1. 先纠正一个事实：现在两个都没用

这一点必须先说清，否则后面全是空谈。

### 1.1 当前检索路径（`webapp/rag.py`）

```
_load_ready_chunks()            -- SELECT ... FROM chunks JOIN documents WHERE status='ready'（全表取文本）
  → 词法门禁 / 权限过滤          -- 缩候选
  → _get_or_compute_embeddings() -- SELECT chunk_id, vec FROM chunk_embeddings WHERE model=? AND chunk_id IN (...)
  → _cosine()                    -- 纯 Python 循环算余弦
  → 排序取 top-k
```

也就是说：**向量以 JSON 文本存在 `chunk_embeddings.vec`，余弦在 Python 里逐个算，没有任何 ANN 索引。**
Milvus 里确实有向量、也验过权限 expr，但**问答主链路一次都没查过它**。

### 1.2 现状常数与规模（本机实测）

| 项 | 实测值 | 复跑方式 |
|---|---|---|
| 单条向量文本长度 | **22,739 字符 ≈ 22.2 KB** | `select round(avg(length(vec))) from chunk_embeddings` |
| `json.loads` 单条 | **0.19 ms** | 40 条循环 20 轮取均值 |
| 纯 Python 单次 cosine(1024 维) | **0.071 ms** | 同上 |
| 当前数据量 | PG：143 chunks / 105 docs / 174 条向量（128 `external:` + 46 旧命名空间）；Milvus：`kb_chunks` **83 条** | `select count(*) ...` / `MilvusClient.query(count(*))` |

按这两个常数外推单次查询（不含 SQL 与网络）：

| 候选数 | JSON 反序列化 | 纯 Python 余弦 | 合计 |
|---|---|---|---|
| 1,000 | 0.2 s | 0.1 s | **0.3 s** |
| 10,000 | 1.9 s | 0.7 s | **2.6 s** |
| 100,000 | 18.5 s | 7.1 s | **25.6 s** |

对照：**同一份 10 万条数据，numpy 批量 GEMM 是 2.57 ms/query**（公开基准 `evalfmt/publicbench` 实测）。
同一份数据、同一个模型，**差 1 万倍**——差的不是算法，是「JSON 文本 + Python 循环」。

> 另外 `vector(1024)` 的二进制是 `4 B × 1024 = 4096 B`，**文本形态胖 5.5 倍**，
> 这 5.5 倍同时放大存储、I/O 和反序列化成本。当前 `chunk_embeddings` 表 2600 kB / 174 行，约 15 KB 一行。

**结论**：现在既不是 pgvector，也不是 Milvus，而是「演示级暴力余弦」。
说「我们用 PG 存向量」会被追问「建的什么索引」，答案是「没有」。

---

## 2. 候选其实是四个，不是两个

| 代号 | 方案 | 现状 |
|---|---|---|
| A | PG 文本列 + Python 余弦 | **当前实现**，143 块规模下可用 |
| B | pgvector（`vector(1024)` + HNSW） | 未做；镜像里没有 `vector` 扩展 |
| C | Milvus 独立服务 | 已起、已灌、expr 验过，**未进主链路** |
| D | 混合：PG 存真值 + Milvus 做索引 | 事实上的「未来计划」，但没定过 |

---

## 3. 对比表

| 维度 | B · pgvector | C · Milvus |
|---|---|---|
| 形态 | PG 扩展，向量是表的一列 | 独立服务，需 etcd + 对象存储 + milvus |
| 事务边界 | 与文档表**同库同事务**，删文档可级联删向量 | 跨系统，需双写 + 补偿 + 对账 |
| 过滤与排序 | `WHERE ... ORDER BY embedding <=> $1 LIMIT k` 一条 SQL，先过滤后排序 | expr 过滤搜索，过滤后可能不足 k 条 |
| 权限 | 直接吃 PG 角色 / RLS；部门可见集可作 `ANY($2)` | 要在应用层构造 expr 并复核 |
| 索引能力 | HNSW / IVFFlat；`halfvec`(2B) / `bit` / `sparsevec` | 更全：IVF 系列、DiskANN、GPU、稀疏向量、原生 BM25（2.5+） |
| 规模主场 | 百万级舒适，千万级要分区 + 调参 | 亿级，分片副本横向扩 |
| 运维新增 | **0 个组件** | 3 个容器、GB 级内存、独立备份策略 |
| 本项目实测状态 | 未验证（PG 16.16 Alpine 无该扩展） | `kb_chunks` 实际 **83 条**、expr 收窄生效、`verify_infra` 通过 |

---

## 4. 三个真正的判据（按重要性排）

### 4.1 过滤与排序能否一次完成

本项目的硬约束是 **「先权限过滤 → 再重排 → 再进模型」**，且列表侧要逐条 `decide()` 复核（A13 双向对拍）。

- pgvector：`WHERE c.department_id = ANY($2) ORDER BY e.embedding <=> $1 LIMIT $3`，
  过滤和排序在同一个执行计划里，**语义天然满足**，且能用 SQL 直接把「现状 top-k」和「新方案 top-k」对拍。
- Milvus：过滤靠 expr，属于「搜索时附带标量条件」，**过滤后召回不足 k 条**是已知行为
  （新版有迭代式索引扫描缓解，参数名以实际版本为准，本项目未验证）。
  要保证语义，就得靠 partition key 按部门分片，或者搜 k×N 再截断——两者都会把复杂度搬回应用层。

对「权限必须严格」的系统，这一条的分量比吞吐高得多。

### 4.2 事务边界（已经交过学费）

本项目已有代码：**删文档前先删 `chunk_embeddings`**，就是为了治理「文档删了、向量还在」的幽灵向量。

- pgvector：`DELETE FROM chunks` 级联即可，**一个事务**，不存在中间态。
- Milvus：两个系统，要么双写要么异步清理 + 每日对账（现有实现正是后者）。

这不是理论问题，是**已经发生过的故障模式**。

### 4.3 运维成本

Milvus standalone 的最小形态是 **etcd + 对象存储 + milvus 三件套**（本项目 `docker-compose.infra.yml` 就是这套）。
一个 20 人的公司，为了 10 万条向量维护三个有状态组件、独立备份、版本升级（README 里记着
`milvus_client.py` 的 ORM API 3.1 将移除），**收益和成本不成比例**。

---

## 5. 规模阈值：给算术，不给感觉

向量原始体积 = `4 B × 维度 × 条数`。1024 维：

| 条数 | float32 原始 | `halfvec` 半精度 | HNSW 索引额外（约 1.3×） |
|---|---|---|---|
| 10 万 | **410 MB** | 205 MB | ~530 MB |
| 100 万 | **4.1 GB** | 2.05 GB | ~5.3 GB |
| 1000 万 | **41 GB** | 20.5 GB | ~53 GB |

经验分界（**经验值，非实测**）：

- **< 100 万条、QPS < 50、过滤是结构化字段** → pgvector，别折腾
- **100 万 ~ 1000 万** → pgvector + `halfvec` + 分区，配好 `shared_buffers` 与 `hnsw.ef_search`
- **> 1000 万，或 QPS 上百，或要 GPU / DiskANN / 多机房** → 才轮到 Milvus

注意本机 `shared_buffers` 目前是 **128 MB**（默认值）、`work_mem` **4 MB**——真要上 HNSW，
至少先按内存的 25% 调 `shared_buffers`，否则索引反复换入换出，测出来的数字没有意义。

---

## 6. 推荐：走 pgvector（B），Milvus 降为备选（C）

理由按权重：

1. **权限过滤语义**直接由 SQL 保证，不用在应用层模拟（4.1）；
2. **同事务**，幽灵向量问题从根上消失（4.2）；
3. **零新增有状态组件**（4.3）；
4. 改动面小：一条查询 + 一张表的列 + 一个索引，**半天量级**，而切 Milvus 是 2–3 天且有 e2e 回归风险；
5. 面试叙事更强：「评估了两种方案，选 pgvector，因为过滤语义和事务一致性比 ANN 峰值性能更重要」——
   这比「我们计划迁移」扎实得多。

---

## 6.5 落地记录（2026-11-16 执行完毕）

### 6.5.1 改造前后同一查询的实测对比

同一份数据（143 候选、1024 维）、同一条 query、同一个模型：

| 路径 | 耗时 | 说明 |
|---|---|---|
| 旧：取 JSON 文本 + 纯 Python 余弦 | **69.45 ms**（取向量 56.98 + 余弦 12.47） | 每条 `json.loads` 22.2KB，再逐条算 |
| 新：pgvector 一条 SQL（复用连接，10 次） | **median 2.18 ms**（min 1.73 / max 3.92） | 过滤 + 排序 + 打分都在库里 |
| 加速比 | **~32×** | 候选集越大差距越大（旧路径是线性，新路径可走下推） |

> 注意口径：143 个候选时 planner 仍选 Seq Scan + Sort（数据太少，索引反而更慢），
> **这 32 倍不是 HNSW 的功劳，是「把计算搬进 PG + 二进制列」的功劳**。
> HNSW 只有在数据量上去、且过滤后剩余候选仍然很多时才显效——那时才需要调 `hnsw.ef_search`。

**排序一致性（A13 对拍的单次抽样结果）**：

| 指标 | 结果 |
|---|---|
| 分数最大绝对差 | **8e-08**（浮点级，两条路径算的是同一个余弦） |
| top-10 集合重合 | **10/10** |
| top-4 集合重合 | **3/4** |
| 顺序完全一致 | **否** |

顺序不一致不是 bug：库里存在**逐元素完全相同的向量**（5 组，最多 9 条），
第 2~6 名分数都是 `0.674249`，8e-08 的差异足以让并列项换位。
所以**对拍判据只能是「top-k 集合重合率」，不能是「顺序逐条相等」**——
按顺序断言会得到一个永远绿的假测试，或者一个永远红的真测试。

### 6.5.2 实际执行时踩到的两个坑

**坑一：Alpine 里 `apk add postgresql-pgvector` 是个陷阱。**
`postgres:16-alpine` 的源里确实有 `postgresql-pgvector-0.8.1-r0`，看起来很美。
但官方 postgres 镜像的 PG 是**源码编译在 `/usr/local`**，而 apk 包把扩展装到
`/usr/share/postgresql18/extension/`——它顺手把 PG18 当依赖装了进来。
也就是说：装完能得到一个「PG 18 的扩展 + PG 16 的服务端」，`CREATE EXTENSION` 照样失败，
失败信息还很有迷惑性。**结论：官方 postgres 镜像加扩展只能换镜像，不能靠 apk。**

**坑二：`search_path` 只含业务 schema 时，`vector` 类型和 `<=>` 操作符都解析不到。**
扩展是**库级**的，装不进业务 schema。测试用独立 schema 隔离，`SET search_path TO "kb_test_x"`
之后 DDL 在 `embedding vector(1024)` 处直接报 `type "vector" does not exist`；
SQL 里 `a <=> b` 则报 `operator does not exist: vector <=> vector`。
修法是 `SET search_path TO "<schema>", public`（`pgkit.connect` 与 `cache/tests/conftest.py` 两处）。
代价要认：DDL 漏建某张表时，查询会静默落到 public 的同名表上——所以**不能让 DDL 悄悄失败**。
（先试过把类型写成 `public.vector`，能过类型那关，但操作符仍然解析不到，只能修 search_path。）

## 7. 迁移步骤（2026-11-16 已执行；含实际路线）

### 7.1 换镜像（最大的一个坑）

当前是 `PostgreSQL 16.16 on x86_64-pc-linux-musl`（Alpine），`pg_available_extensions` 里**没有 `vector`**。
官方 `postgres` 镜像不带 pgvector，需要换成带扩展的镜像（示例 `pgvector/pgvector:pg16`；
Alpine 变体是否存在以实际可拉取为准，本项目拉镜像要走加速站，见 `07-remaining-work.md` §10.1）。

**不要直接把新镜像挂到旧数据目录上赌兼容**。稳妥路径：`pg_dump` → 新卷 + 新镜像 → `restore`。
数据量很小（143 chunks / 105 docs），20 分钟内能完成，且顺带验证了一次备份可用性。

### 7.2 DDL

```sql
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE chunk_embeddings ADD COLUMN embedding vector(1024);

-- 回填：复用已有文本列，不重新调 API
UPDATE chunk_embeddings
   SET embedding = vec::vector
 WHERE embedding IS NULL AND vec IS NOT NULL;
```

> `vec` 文本列**先不删**：既是回滚路径，也是 §8 对拍的基准。

### 7.3 索引

```sql
CREATE INDEX CONCURRENTLY idx_chunk_emb_hnsw
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

`m=16 / ef_construction=64` 是 pgvector 默认值，够用；查询期用 `SET hnsw.ef_search = 100`
（默认 40，过滤重时调大）。**这两个参数要实测调，别照抄。**

### 7.4 查询改写（`rag.py`）

```sql
SELECT e.chunk_id, 1 - (e.embedding <=> $1) AS score
  FROM chunk_embeddings e
  JOIN chunks c    ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.doc_id
 WHERE e.model = $2                 -- 命名空间隔离，别丢
   AND d.status = 'ready'
   AND c.department_id = ANY($3)    -- 可见部门由服务端从 DB 构造，不信请求体
 ORDER BY e.embedding <=> $1
 LIMIT $4;
```

三处不能丢：`model` 条件（否则两套命名空间混检，实测同文本余弦只有 0.977，见 §2.2 相关结论）、
`status='ready'`、`department_id = ANY(...)`。

### 7.5 回滚

`vec` 列还在 → 把查询切回旧路径即可，**不需要恢复数据库**。
确认新路径稳定一段时间后，再考虑删列（删列不可逆，放到最后）。

---

## 8. 验证方案：A13 双向对拍改用 SQL 做

上线判据（**目标值，未实测**）：

1. 抽 200 条真实 query，同时跑「现状 Python 余弦 top-10」和「pgvector top-10」；
2. 计算 top-10 重合率，**目标 ≥ 99%**（不是 100%——ANN 是近似的，允许边界抖动）；
3. 任一条重合率异常的，把两者 top-10 打出来人工看：通常是**并列分数**或**向量本身逐元素相同**
   （库里存在 5 组完全相同的向量，这是已知坑，别把「top1 不是自己」当 bug）；
4. 再跑 `webapp/selftest.py` 33 项，确认召回不退化、引用准确率不变。

这套对拍用 SQL 就能做，不需要双写、不需要新进程——这是 pgvector 相对 Milvus 的额外好处。

---

## 9. 什么情况下才真的该上 Milvus

诚实边界，别把 Milvus 说成一无是处：

- 向量 **> 1000 万**，或单机内存放不下索引；
- QPS 上百且要**水平扩副本**（Milvus 的 query node 可以直接加）；
- 需要 **DiskANN / GPU 索引**这种单机 PG 给不了的索引类型；
- 需要**原生 BM25 + 稠密向量混合检索**（Milvus 2.5+ 内置），且不想上 ParadeDB/tsvector 那套；
- 多机房、多租户大规模隔离（partition key 比 SQL 的 `WHERE` 更适合超多租户）。

到那时再迁，前面 §8 的对拍方案可以直接复用。

---

## 10. 已有的 Milvus 工作怎么定位

不是白做，但**不能说成「我们用了 Milvus」**：

- 已经证明的：Milvus 能起、能灌、权限 expr 生效（错 tenant 命中 0）、`verify_infra` 每条都过；
- 已经暴露的：三个有状态组件、跨系统一致性、`load()` 在元数据与数据不一致时会**永久挂住**（真踩过）；
- 正确说法：**「Milvus 作为备选方案验证过链路，评估后选择 pgvector 路线，Milvus 留给千万级之后的场景。」**

---

## 11. 面试口径

**能说**

- 「向量检索的选型判据不是 QPS，是**过滤语义能不能和排序在一次查询里完成**，以及**向量和业务数据是否同事务**。
  我们的权限是部门级的，pgvector 一条 SQL 就能先过滤再排序。」
- 「我先算过账：1024 维、100 万条是 4.1GB，HNSW 再加 1.3 倍，一台 16GB 机器够；到 1000 万才需要独立向量库。」
- 「我实测过现状的暴力实现：JSON 文本 22 KB 一条、纯 Python 余弦 0.071 ms，
  10 万候选要 25.6 秒；而 numpy 批量矩阵乘同一份数据是 2.57 ms。**差 1 万倍不是算法问题，是工程实现问题。**」

**不能说**

- ❌「我们用 Milvus 做向量检索」——主链路读的是 PG。
- ❌「我们用了 pgvector」——扩展还没装。
- ❌「向量检索 2.57 ms」——那是公开基准里 in-process numpy 的数字，不是线上接口延迟。

---

## 12. 未决事项

| 事项 | 状态 |
|---|---|
| 选 B（pgvector）还是继续 D（混合） | **未拍板**，本文推荐 B |
| 换镜像路线：dump/restore 还是自建 Alpine 镜像 | 未定，推荐 dump/restore |
| `hnsw.ef_search` / `m` / `ef_construction` 的实测调参 | 未做，需装完扩展后跑 |
| 删 `vec` 文本列的时间点 | 未定，建议对拍通过并稳定一周后 |
| `07-remaining-work.md` #1 的目标改写 | 待拍板后同步 |
