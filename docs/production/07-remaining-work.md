# 剩余工作清单 · 哪些是真没做

编制：2026-11-16 ｜ 适用：`demo/v1` 分支（`71fa09d`）
用途：**切新环境做测试前先看这一页**，避免把「设计」当「实现」去验，也避免漏掉已知陷阱。

> **口径**：本文只列**还没做完的事**。已实跑得出的数字见 `01-data-migration.md` §10–§12、
> `03-ratelimit-and-metrics.md` §8、`interview/index.html` §06.1。
> 三态标记：**未做** = 一行代码都没有；**只设计** = 有文档/骨架/接口契约，跑不起来；
> **半成品** = 能跑但只在演示级条件下成立。

---

## 0. 一句话结论

真基础设施（PG / Redis / Milvus）这条线**已经通了、有实测数字**；剩下的缺口集中在三类：

1. **替身还没换干净**——向量召回仍读 PG 文本列，Milvus 只被迁移脚本用过，没进主链路；
2. **横向扩展没验过**——限流修了多副本，但并发闸 / 指标 / 台账还是各副本各算各的；
3. **「证明它扛得住」这件事完全没做**——没压测、没阈值标定、没恢复演练、没标注集。

这三类都不是「写点代码」能补的，前两类是改造，第三类要真跑几小时。

---

## 1. 总览

| # | 缺口 | 状态 | 阻塞它的东西 | 做完的判断标准 |
|---|---|---|---|---|
| 1 | 向量召回仍读 PG `chunk_embeddings` | **未做**（最关键） | 需改 `rag.retrieve()` + 影子读比对 | 主链路走 Milvus 且 33 项 e2e 召回不退化 |
| 2 | 两个嵌入命名空间是否同一向量空间未判定 | **未做** | 需对同文本分别 re-embed 比距离 | 给出「同空间/不同空间」的结论 |
| 3 | 影子读双跑（两库结果比对） | **未做** | 依赖 #1 | 不一致率 < 0.1% 有实测值 |
| 4 | 连接池 | **未做** | — | 换 `psycopg_pool`，P95 建连开销消失 |
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
| 18 | `index-standalone.html` 同步 | **待办** | 改 `interview/index.html` 后未重跑 `make_video.py` | 两文件内容一致 |
| 19 | 备案 / 等保 | **未做** | 需本人实名，AI 代办不了 | — |
| 20 | pymilvus ORM API 技术债 | **已知债** | 2.6 告警 3.1 移除 | 升级前重写 `milvus_client.py` |

---

## 2. 存储与检索（最该先做的一组）

### 2.1 向量召回仍读 PG 文本列 —— **未做，这是「换掉替身」的最后一步**

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

### 2.2 两个嵌入命名空间是否同一向量空间 —— **未做**

实测库里并存两套：`external:BAAI/bge-m3`（83 条）和 `text-embedding-bge-m3`（46 条）。
名字不同不代表向量空间不同，也不代表相同。**没验过就不能假设能混检。**
验证方法：取 10 条相同文本分别用两个命名空间 re-embed，比余弦距离是否 ≈1.0。

### 2.3 影子读双跑 —— **未做**

两库并行查、比对 top-k 重合率。这是 2.1 上线前的安全网，也是面试里最好讲的一段
（「我怎么知道换引擎没换坏」）。目标不一致率 < 0.1%，**这个数字目前不存在**。

### 2.4 连接池 —— **未做**

`pgkit.connect()` 每次新建连接（实测 2–5ms）。单机演示无所谓，多副本 + 高频接口就是瓶颈。
生产换 `psycopg_pool.ConnectionPool` 或 FastAPI 依赖注入，改动面在 `webapp/db.py::get_conn()` 一处。

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
- **`index-standalone.html` 未同步**：改完 `interview/index.html` 要重跑 `make_video.py`，
  否则离线单文件版和主版内容不一致。

---

## 9. 建议顺序（三档）

**A. 本周（有代码产出、能写进简历）**
1. §2.2 命名空间判定（半天，纯分析）
2. §2.1 向量召回切 Milvus + §2.3 影子读（最值钱的一项，也是唯一能讲「迁移工程」的经验）
3. §3.1 并发闸改 Redis（和限流同构，改起来快）

**B. 面试前（不写代码，只补证据）**
4. `python scripts/verify_infra.py` 截图存档
5. §5 三项明确标「未做」，准备好被问时的答法
6. §8 同步 `index-standalone.html`

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
PARSING_OFFLINE=1 python -m pytest webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests showcase/tests -q   # 期望 231 passed
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
