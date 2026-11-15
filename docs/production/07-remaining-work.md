# 剩余工作清单 · 哪些是真没做

编制：2026-11-16 ｜ 适用：`demo/v1` 分支（`71fa09d`）
用途：**切新环境做测试前先看这一页**，避免把「设计」当「实现」去验，也避免漏掉已知陷阱。

> **口径**：本文只列**还没做完的事**。已实跑得出的数字见 `01-data-migration.md` §10–§12、
> `03-ratelimit-and-metrics.md` §8、`interview/index.html` §06.1。
> 三态标记：**未做** = 一行代码都没有；**只设计** = 有文档/骨架/接口契约，跑不起来；
> **半成品** = 能跑但只在演示级条件下成立。
>
> **2026-11-16 复核**：20 项里 **4 项已闭环**（#1 向量层切到 pgvector、#2 嵌入命名空间已实测判定、
> #18 单文件版已重建校验、§2.4 连接池已接 SQLAlchemy Engine），
> 编号保持不变以便历史引用；真正没做的仍有 **16 项**。末尾 §11 给出「值得做 / 只值得说」的分档。

---

## 0. 一句话结论

真基础设施（PG / Redis / Milvus）这条线**已经通了、有实测数字**；剩下的缺口集中在三类：

1. **替身基本换干净了**——向量召回已走 `embedding vector(1024)` + HNSW（v1.3.0），Milvus 退成对照实验；
   剩下的替身是「演示级阈值」与「进程内队列 / 并发闸」；
2. **横向扩展只验了一半**——限流修了多副本，但并发闸 / 指标 / 台账还是各副本各算各的；
3. **「证明它扛得住」这件事完全没做**——没压测、没阈值标定、没恢复演练、没标注集。

这三类都不是「写点代码」能补的，前两类是改造，第三类要真跑几小时。

---

## 1. 总览

| # | 缺口 | 状态 | 阻塞它的东西 | 做完的判断标准 |
|---|---|---|---|---|
| 1 | ~~向量召回仍读 PG 文本列~~ | **已完成**（2026-11-16） | — | 主链路走 pgvector（`embedding vector(1024)` + 一条 SQL 过滤排序）；Milvus 移出默认启动，见 `08-vector-store-decision.md` §6.5 |
| 2 | ~~两个嵌入命名空间是否同一向量空间未判定~~ | **已验**（2026-11-16） | — | 已给出结论：**同文本余弦 0.977，不是同一空间**，见 §2.2 |
| 3 | 影子读双跑（两库结果比对） | **未做** | 依赖 #1 | 不一致率 < 0.1% 有实测值 |
| 4 | 连接池 | **已完成** | — | 接 SQLAlchemy Engine，实测 18.59ms → 3.77ms（§2.4） |
| 5 | 全局并发闸 `asyncio.Semaphore` | **半成品** | 进程内，N 副本上限 ×N | 多副本下总并发仍等于 `MAX_INFLIGHT` |
| 6 | `/metrics` 计数 | **半成品** | 进程内，`--workers N` 各算各的 | 多 worker 下计数不丢 |
| 7 | 按 token 的 TPM 限流 | **只设计** | — | 按 token 而非次数计窗口 |
| 8 | `rate_hits` 台账多副本 | **半成品** | 各副本各写各库 | 汇总口径明确（或改为只写中心库） |
| 9 | Prometheus / Grafana 真接 | **未做** | 只写了 `alerts.yml` 8 条规则 | 规则能真触发，不是文本 |
| 10 | 真实容量结论 + 阈值标定 | **未做** | `02` 只做过连通性抽样 | 有 RPS / P95 / 拐点数字 |
| 11 | 备份恢复演练 | **只设计** | `05` 有脚本没跑 | 真删库真恢复，RTO 有数字 |
| 12 | Celery 映射 | **只设计** | `backend/app/tasks/ingest.py` 13 个骨架，`webapp/` 仍是进程内 `deque` | 至少一条链路走 broker |
| 13 | `backend/` 13 步去 tenant | **未做** | 见 `org-model-change-plan.md` | 与 webapp 口径一致 |
| 14 | 逐行 / 逐段落 ACL | **只设计** | 只有开关预留（默认关闭） | 段落级判定进 `decide()` |
| 15 | SSO / OIDC / SAML / SCIM | **未做** | — | — |
| 16 | 评测标注集与质量数值 | **未做** | 无标注集 | 表格 F1 / 校准曲线有真值 |
| 17 | 无障碍（屏幕阅读器）实测 | **未做** | — | 有读屏走查记录 |
| 18 | ~~`index-standalone.html` 同步~~ | **已完成**（2026-11-16） | — | 两版正文逐字比对一致，仅差「自引用链接」这一处有意改写 |
| 19 | 备案 / 等保 | **未做** | 需本人实名，AI 代办不了 | — |
| 20 | pymilvus ORM API 技术债 | **已知债** | 2.6 告警 3.1 移除 | 升级前重写 `milvus_client.py` |

---

## 2. 存储与检索（最该先做的一组）

### 2.1 向量召回仍读 PG 文本列 —— **已完成（2026-11-16）**

> 已经按 `08-vector-store-decision.md` 的方案落地：`chunk_embeddings` 加 `embedding vector(1024)`，
> 回填 174 条，建 HNSW 索引，`rag.py` 改为一条 SQL 做「权限过滤 + 余弦排序」，
> 旧路径保留为 `WEBAPP_VECTOR_BACKEND=python` 回滚开关。
> **本节下方关于 Milvus 的细节不再有效**，保留只为记录当时的判断依据。
> 实测：143 候选下 69.45ms → median 2.18ms（只算 SQL，约 32×），top-10 集合重合 10/10、分数最大差 8e-08。
> **接上连接池后（v1.3.1）端到端是 3.77ms，对旧路径约 18×**——两个倍数的口径不同，别混着说：
> 32× 是「算得少」，18× 是「连得省」。

`webapp/rag.py:154` 现在是：

```python
q = ("SELECT chunk_id, vec FROM chunk_embeddings WHERE model=? AND chunk_id IN (")
```

也就是说：**Milvus 里 83 条向量是真的、可查的、权限 expr 也验过，但线上问答一次都没用它。**
演示能跑是因为 `chunk_embeddings` 这张 PG 表同时存了向量文本，余弦在 Python 里算。

改造要点（别踩的坑都在这儿）：
- Milvus 返回的是 `chunk_id` + 距离，PG 那份 `vec` 列不能马上删——要留一个版本做回滚；
- 权限过滤必须**继续双保险**：Milvus expr 先缩，返回后再逐条 `decide()` 复核（A13 对拍）；
- 演示库有**逐元素完全相同的向量**（5 组、最多 9 条），改成 Milvus 召回后 `top1` 依然可能不是「自己」，
  e2e 断言不能写死 top1；
- 缓存 key 里 `embedding` 段是命名空间，切库后要确认 key 不串。

> **2026-11-16 补充·已经改掉的两处（别按旧描述讲）**
> ① **PDF 已接入主链路**：`rag.extract_text()` 现在对 `.pdf` 走 pypdf 文本层，
>    有文本层的 PDF 能上传 → 分块 → 检索。仍然不支持：扫描版 PDF（无文本层）、图片、docx、xlsx。
>    扫描件是**有意**报错而不是塞占位分块——OCR 没接主链路，宁可明确失败也不要污染检索。
> ② **词法门禁职责改了**：原实现「先按词法筛候选，再在候选里向量排序」，中文问英文 PDF 零词法重叠，
>    文档连进入排序的机会都没有（多格式评估实测 0 召回）。现在门禁只判定「有没有相关信号」，
>    不挑候选；词法完全无命中时仍返回空，`retrieval_empty` 降级语义不变。
>    这一条是 `evalfmt/` 评估抓出来的，改完 Recall@1 77.8% → 100%。
>    但**向量仍存在 PG 的 `chunk_embeddings` 文本列**，Milvus 依旧没进主链路 —— 上面这条仍然成立。

### 2.2 两个嵌入命名空间是否同一向量空间 —— **已验：不是**

库里并存两套向量：`external:BAAI/bge-m3`（128 条）与 `text-embedding-bge-m3`（46 条，早期本地 LM Studio 产出）。
名字里都写着 bge-m3，但**名字相同不代表向量相同**。

2026-11-16 实测（抽 20 条旧向量，取同一 `chunks.text` 用外部命名空间重新嵌入，均 1024 维）：

| 指标 | 结果 |
|---|---|
| 同文本跨命名空间余弦 | min **0.9557** / median **0.9774** / max **0.9816** |
| 20 条中 < 0.98 的 | **14 条** |
| 逐元素最大绝对差 | **0.0239** |

**结论：不是同一向量空间，不能混检。** 同一份文本在两种推理实现下（本地 GGUF/llama.cpp 与硅基流动服务端）
会偏离轴线 2–4 个百分点；0.977 的余弦意味着「同一段文本」与「另一段高度相关文本」的可分性被压缩，
混在一起排序会系统性扭曲名次。

一个必要的对照：**同一端点重复调用**的余弦是 **0.99996**（逐元素最大差 1.3e-3），
即服务端只有浮点/批处理级抖动。两个数字差三个数量级，说明 0.977 是真实的空间差异，不是噪声。

所以现有实现是对的：`chunk_embeddings` 按 `(chunk_id, model)` 做主键，`rag.py` 查询带 `WHERE model=?`，
两套向量并存但**永不混算**；`settings.effective_embedding()` 把缓存 key 定成 `external:{model}` / `local:{model}`，
正是为了让这两种实现天然隔离。

> 这条可以直接当面试答案：**「同名模型在不同推理实现下不是同一向量空间——我抽 20 条实测，同文本余弦只有 0.977，
> 而同一端点重复调用是 0.99996，所以缓存 key 必须带实现前缀，不能按模型名认亲。」**
> 比「我考虑过这个问题」硬得多，而且这个数字是自己跑出来的。

### 2.3 影子读双跑 —— **未做**

两库并行查、比对 top-k 重合率。这是 2.1 上线前的安全网，也是面试里最好讲的一段
（「我怎么知道换引擎没换坏」）。目标不一致率 < 0.1%，**这个数字目前不存在**。

### 2.4 连接池 —— **已完成（2026-11-16）**

原判断是「`pgkit.connect()` 每次新建连接（实测 2–5ms），单机演示无所谓，多副本 + 高频接口就是瓶颈」。

实际落地走的是 **SQLAlchemy Engine + 连接池**（不是 `psycopg_pool`，因为 `backend/` 已经在用
SQLAlchemy，统一一套依赖比多引一个库更干净）。`connect()` 从 `psycopg.connect()` 改为
`engine.raw_connection()`，`PgConn.close()` 从「断 TCP」改为**还池**——全仓 138 处
`finally: conn.close()` 零改动。

**实测（143 候选 pgvector 查询，15 次取中位）**：改前直连 **18.59ms** → 改后 **3.77ms**（约 4.9×）；
`connect()+close()` 从「新建 TCP + PG 后端认证」降到 **0.42ms**。

**没有做的部分**：仍是「每次操作取一次连接」，没有做成 FastAPI 依赖注入的请求级连接
（一个请求内多次取连接会多次 checkout，虽便宜但非最优）。这一层留着不动的原因是
138 处调用点各自 `connect()/close()`，改成请求级注入要穿透全部 service 函数签名，
风险与收益不成比例。**池化解决的是「连接建立太贵」，请求级复用解决的是「checkout 次数」，
后者的收益是 0.4ms 量级，不值得现在动。**

**池化带来的一个真隐患（已修，别删）**：会话级 `SET` 会跨借用人存活，导致串 schema。
修法与验收见 `08-vector-store-decision.md` §6.5.2 坑三。

---

## 3. 限流与可观测

### 3.1 全局并发闸是进程内的 —— **半成品**

`webapp/main.py:67` 的 `MAX_INFLIGHT` 配的是 `asyncio.Semaphore`，只在本进程有效。
**N 个副本 = 实际并发上限 ×N**，和限流器修好之前的毛病一模一样。
限流已经用 Redis 共享窗口修了，并发闸没修——这是**已知的不一致**，别在面试里说「并发也控住了」。

### 3.2 `/metrics` 是进程内计数 —— **半成品**

`--workers N` 时每 worker 各算各的，Prometheus 抓哪个 worker 就看哪份。
要么单 worker，要么改用 `prometheus_client` 的 multiprocess 模式，要么把计数推到 Redis。

### 3.3 其余三项
- **TPM 限流**：只设计。现在是按「次数」限，不按 token。
- **`rate_hits` 台账**：多副本各写各库，汇总口径没定。
- **Prometheus / Grafana**：`docs/production/monitoring/alerts.yml` 8 条规则**只写不跑**，
  没接真 Prometheus，没验证过任何一条能触发。

---

## 4. 异步任务与 backend

- **Celery 只设计**：`backend/app/tasks/ingest.py` 有 13 个任务骨架，`webapp/queue.py` 仍是
  进程内 `deque` + `threading.Semaphore`。重启时靠 `documents.status IN ('queued','analyzing')`
  幂等重投（这条验证过了），但**没有 broker、没有 worker、没有重试策略**。
- **`backend/` 13 步去 tenant 未做**：`tenant_id` / `TenantRepository` 还在，是历史命名技术债。
- **pymilvus ORM 风格技术债**：`backend/app/integrations/milvus_client.py` 全用 ORM 风格 API，
  pymilvus 2.6 已经告警 3.1 移除，升级前必须重写成 MilvusClient 风格。

---

## 5. 压测与灾备（这组是「证明」而非「实现」）

| 项 | 现状 | 缺口 |
|---|---|---|
| 容量结论 | `02-load-testing.md` 只有本机连通性抽样 | 没有 RPS / P95 / 拐点数字 |
| 阈值标定 | 所有阈值都是**演示经验起始值** | 没被压测校准过（10% 视觉升级、0.75 置信度、`floor=0.5` 衰减） |
| 恢复演练 | `05-backup-dr.md` 有脚本 | **一次没跑过**，RTO / RPO 无数字 |

面试口径：这三项要主动说「没做」，说成「做了」一问数字就露。

---

## 6. 权限与合规

- **逐行 / 逐段落 ACL**：只有开关预留，默认关闭。当前粒度是文档级。
- **SSO / OIDC / SAML / SCIM**：未做，现在只有本地账号。
- **备案 / 等保 / 数据出境**：`06-compliance.md` 全是流程说明，**必须本人实名办理**，AI 代办不了。

---

## 7. 评测口径

- **无标注集**：表格结构 F1、置信度校准曲线、视觉升级 precision/recall **只有定义没有数值**。
- **evalbench 12 条自建样本 `comparable=false`**：这是诚实标记，不是 bug，别改成 true。
- 冻结区 `eval/` 的 `__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径**不得改动**。

---

## 8. 前端与文档

- **无障碍实测未做**：`prefers-reduced-motion`、`focus-visible` 都写了，但没做过读屏走查。
- ~~`index-standalone.html` 未同步~~ → **2026-11-16 已重建并校验**：两版正文逐字一致，
  只差「自引用链接改写成本段文字」这一处有意为之的差异。重建规则见 `interview/index.html` 顶部注释。

---

## 9. 建议顺序（三档）

**A. 本轮只做「便宜且能保护已有数字」的**
1. ~~§2.2 命名空间判定~~ —— 已完成，结论见 §2.2（同文本余弦 0.977，不能混检）
2. §2.1 向量召回切 Milvus + §2.3 影子读 —— 见 §11，**降级为「只讲」**
3. §3.1 并发闸改 Redis —— 单副本演示里验不出差别，**降级为「只讲」**

**B. 面试前（不写代码，只补证据）**
4. `python scripts/verify_infra.py` 截图存档
5. §5 三项明确标「未做」，准备好被问时的答法
6. ~~§8 同步 `index-standalone.html`~~ —— 已完成

**C. 真上生产前（这轮不做）**
7. §3.3 Prometheus 真接、§4 Celery、§6 SSO、§1 #10–#11 压测与演练

---

## 10. 新环境怎么起（SQLite 已移除，比之前多一步）

### 10.1 起基础设施

```bash
# 新机器端口不冲突时不需要 KB_*_PORT 覆盖
KB_MILVUS_STORAGE=minio \
KB_MINIO_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/minio/minio:RELEASE.2025-04-22T22-12-26Z \
KB_ETCD_IMAGE=quay.m.daocloud.io/coreos/etcd:v3.5.16 \
KB_MILVUS_IMAGE=docker.m.daocloud.io/milvusdb/milvus:v2.5.10 \
  docker compose -f docker-compose.infra.yml --profile minio up -d
```

镜像源实拉结论（本机验过）：`milvusdb/milvus` → `docker.m.daocloud.io` ✅、
`coreos/etcd` → `quay.m.daocloud.io` ✅、`postgres/redis` → daocloud ✅、
**`minio` 四个加速站全失败**，只有华为云 SWR 的 `ddn-k8s` 镜像能拉。

### 10.2 写 `.env`（已 gitignore，新机器必须自己建）

```
WEBAPP_DATABASE_URL=postgresql://kb:kb_demo_only@127.0.0.1:5432/kb
WEBAPP_REDIS_URL=redis://127.0.0.1:6379/0
MILVUS_URI=http://127.0.0.1:19530
```

模板见 `.env.example`。**本机因为 5432/6379 被别的项目占用，用的是 55432/56379。**

### 10.3 起服务 + 验证

```bash
bash run-demo.sh                          # 内部 docker run --network kb-infra_default
python scripts/verify_infra.py            # 期望 INFRA_READY
PARSING_OFFLINE=1 python -m pytest webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests showcase/tests evalfmt/tests -q   # 期望 255 passed
python webapp/selftest.py                 # 期望 33/33 DEMO_READY（需真实外部模型）
```

### 10.4 两个新环境陷阱（本轮真踩过）

1. **`pytest` 现在要求 PG 起着** —— 不再有「零依赖 clone 就能跑单测」。
   `webapp` 每进程建一个 `kb_test_<uuid8>` schema，`cache` 每用例一个 schema，跑完自动 DROP。
2. **换对象存储 / 清空 bucket 后必须 `--reset-collection`** ——
   Milvus 元数据在 etcd、数据在对象存储。换 `storageType` 后 etcd 里 collection 还在、
   segment 已不存在，`has_collection` 为真 → `ensure_collection()` 直接 return → `load()` **永久挂住**。
   看起来像「Milvus 挂了」，实际是元数据与数据不一致。

### 10.5 一条命令自检覆盖什么

`verify_infra.py` 验的是「起来之后对业务可用且正确」，不是端口通：
PG 20 张表 + 限流种子无偏差 + `created_at` 真为 `timestamptz`；
Redis 两独立子进程打同一 key（内存 6 次 vs Redis 3 次，放大 2.0×）；
Milvus 用 `query(count(*))` 取精确存活数 + 错 tenant 命中 0 条 + 按 doc 收窄 46→1。

---

## 11. 分档：16 项里哪些真值得做，哪些只值得说

> 判断标准只有一条：**做完之后，会不会多出一个可核对的数字、或一个行为差异**。
> 绝大多数项做完只是「有代码」，不改变任何结论——**那就是只值得说，不值得做**。
> 把边界讲清楚比把功能堆起来更值钱：这一页的价值在于「我知道自己没做什么，以及为什么」。

### 11.1 真值得做：1 项

| 项 | 成本 | 做完多出什么 |
|---|---|---|
| **#11 备份恢复演练** | 半天 | 一个真实的 **RTO 数字** + 一次「删库 → 恢复 → `verify_infra.py` 回到 `INFRA_READY`」的可复述记录 |

**为什么只有它**：这是 16 项里唯一能**不写业务代码**、却产出一个「别人简历上没有的数字」的项。
脚本在 `05` 里已有，只差真跑一遍；做完之后「我做过恢复演练，RTO 是 X 分钟」是可核对的，
而「我设计了备份方案」谁都能说。

### 11.2 只值得说：15 项

按「被问到时怎么答」分五类（不重叠，合计 15）：

| 类别 | 包含 | 一句话答法 |
|---|---|---|
| **架构选择，不是欠账** | #3 影子读（#1 已落地） | 「评估了 pgvector 与 Milvus：判据是过滤语义和事务边界，不是 QPS；选 pgvector 因为权限过滤和排序能一条 SQL 做完，且向量与文档同事务。论证见 `08-vector-store-decision.md`」 |
| **要多副本才成立** | #5 并发闸、#6 `/metrics`、#7 TPM、#8 台账 | 「这些在单副本演示里做完也验不出差别，所以我只出设计并写清触发条件——什么时候必须改」 |
| **要预算或环境** | #9 Prometheus、#10 压测、#16 标注集、#19 备案 | 「没环境硬造数字就是编，我宁可写未做」 |
| **生产化改造，不是这个阶段** | #12 Celery、#13 去 tenant、#14 段落 ACL、#15 SSO、#20 pymilvus 债 | 「在生产化路线图上，边界写清楚了，没假装做了」 |
| **小项，做了也没人问** | #17 无障碍走查 | 「知道该怎么做，收益 < 半天，所以排在后面」 |

### 11.3 唯一「不能只说、必须说准」的一项

**#3 影子读 / 向量层口径** 牵动一句会被直接追问的话：*「你们到底用没用向量库？」*

现在的事实（v1.3.1）：**主链路是 PG + pgvector**——`chunk_embeddings.embedding vector(1024)` 上
一条 SQL 同时做「权限过滤 + 余弦排序 + 打分」，不再取文本列、不再在 Python 里逐条算。
Milvus 已起过、已灌过、权限 expr 验过，但**已移出默认启动**，退成对照实验。
所以不能说「我们用 Milvus 做检索」，也不能说「我们上了专业向量数据库」。三种答法选一种，别含糊：

- **A（推荐）**：「向量存在 PG 里、用 pgvector 的 `vector` 类型 + HNSW 索引，检索是一条 SQL——
  权限过滤和排序在同一个查询里完成，向量和文档同事务，删文档不会留幽灵向量。
  Milvus 验证过链路（权限 expr、灌数、对拍），但评估后不选它：中小规模下多一个有状态组件不划算，
  判据是过滤语义和事务边界，不是 QPS。千万级、或需要多副本分片时再换。」
  —— 现状 + 判据 + 边界，三句话都在。**论证与实测在 `08-vector-store-decision.md`。**
- **B（更保守）**：「这一版向量检索在 PG 里做（pgvector），Milvus 只验证了链路，没进主链路。」
- **C（只讲算法不讲存储）**：只在被问到存储时才展开，否则专注讲「先按权限过滤再进模型」这条主线。

**不要用第四种**：含糊带过，让对方以为已经上了独立向量数据库。这类问题一旦被追细节
（用了什么 metric、索引参数、为什么 recall 没变、过滤怎么下推），含糊的代价是整场可信度。
