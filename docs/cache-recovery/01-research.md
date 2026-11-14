# 缓存设计与重启状态稳定性调研报告（01-research）

> 文档状态：**调研报告（只写入本文，不改任何源码）**
> 检索日期：**2026-11-14**（所有外部来源均于当日通过 WebSearch 定向检索获得，共 6 次，中英文各半）
> 适用项目：企业内部知识库 RAG（检索增强生成）演示切片，定位「开发框架骨架 + 可运行演示切片」，不是上线交付版
> 配合阅读：`docs/async-upload.md`（异步上传与进度三层通道）、`docs/org-model-change-plan.md`（去多租户、改多用户多部门）、`docs/knowledge/01-research.md`（知识治理调研，同期姊妹篇）
> 用户新需求原话：「缓存具体想怎么做也得优化好，还有关于服务器重启以后状态各方面还算正常少 bug 稳定情况」

---

## 0.0 主理人最终状态回填（2026-11-14 实现落地后，优先级高于下文）

> 本文是**调研报告**，编制时按「仓库现状锚点」写；同日主理人已完成实现，
> 请以本节为准。下文 C1–C7 的「落地口径」保留不回改，用于追溯「调研 → 落地」过程。

| 报告里的建议 | 最终状态 |
|---|---|
| C1 `chunk_embeddings` 失效三件套 | **已补齐**：TTL/容量/幽灵向量对账 + 删文档时源头先删向量；`cache_key` 仍只含 provider + 模型名，未改 |
| C2 进程内策略持久化 | **已实现**：`webapp/recovery.py` 把新鲜度 / 解析 / 缓存策略落 `runtime_state`，启动恢复 |
| C3 启动 recovery 报告 + 孤儿任务重投 | **已实现**：`cache/recovery.py::run_startup_checks()` 7 项检查接入 `webapp/main.py` lifespan；`documents.status IN ('queued','analyzing')` 幂等重投 |
| C4 限流窗口 | 保持演示替身，未改维度；报告结论仍成立 |
| **C5 缓存层** | **口径调整，见下方 §0.0.1** |
| C6 `redis_client.py` 命名空间 | 仍为**只设计**，未改源码；建议保留 |
| C7 分层缓存与真值口径 | 直接沿用，新增 L2 SQLite 只作演示替身，未新增真值源 |

**§0.0.1 C5 调整说明**：编制时判定「检索结果缓存 / 答案缓存 / 语义缓存本期不建议做」的前提是
「`grant_version` 与『先过滤后写 + 命中复核』尚未就位」。同日主理人已落地 `cache/` 通用缓存包：
`authz_snapshot()` 含 `grant_version`、`VersionController` 提供版本递增、命中时 `key_matches()`
复核三重版本字段、`webapp/routers_runtime.py::cache_demo` 在写缓存前调用
`_filter_for_user()` 做权限过滤。因此最终裁决细化为：

| 能力 | 最终状态 |
|---|---|
| 通用双层缓存（L1 内存 LRU + L2 SQLite）、key 逐段构造、版本感知懒失效、TTL/负缓存/抖动/容量 | **已实现**（`cache/`，19 项离线单测） |
| 检索结果缓存 | **已实现（演示级）**：只在 `/api/runtime/cache-demo` 使用，`rag.retrieve()` 主链路**默认不接**，避免打断 33 项端到端自测；接主链路是下一步 |
| 答案缓存 / 语义缓存 | **仍未做**，理由不变：依赖 prompt + 生成模型 + 语义相似度，误命中风险高，演示切片不引入 |

**真实进程重启验证**：隔离库启停两次，策略（half_life=45 / blend=0.5）、L2 缓存条目、
孤儿任务、幽灵向量全部按预期恢复（healthy=True、boot 1→2、重投 1、清理 1、无 500）。
强停导致 `last_shutdown_at` 未写入，实证「SIGKILL 不保证优雅关闭，真值层必须先行」。

**仍然成立的诚实边界**：合成演练是功能连通性证明，不是压测或 SLA 结论；阈值是演示经验起始值；
SQLite + 进程内队列不是生产 Redis/Celery；`eval/` 冻结口径不受缓存影响。

---

## 0. 阅读约定与现状锚点

### 0.1 术语首现中文解释（按出现顺序）

- RAG：检索增强生成（Retrieval-Augmented Generation），先检索证据片段再让大模型作答。
- TTL：存活时间（Time To Live），缓存条目到期自动失效的时长。
- LRU：最近最少使用（Least Recently Used），一种容量满时淘汰最久未用条目的策略。
- ACL：访问控制列表（Access Control List），决定「谁能看哪些文档」。
- RBAC：基于角色的访问控制（Role-Based Access Control）。
- CDC：变更数据捕获（Change Data Capture），把源系统的数据/权限变动作为事件流输出。
- SSE：服务端推送事件（Server-Sent Events），单向长连接把进度推给浏览器。
- lease：租约，任务持有者带有效期的「临时所有权」，超时未续约即视为失联可回收。
- checkpoint：检查点，长任务把中间进度阶段性落盘，重跑时从断点续传而非从头开始。
- 幂等：同一操作执行一次与重复执行多次，对系统状态的影响相同。
- 孤儿任务：进程崩溃后留在「排队/处理中」状态、但已无人负责推进的任务。
- 幽灵向量：文档已删除或重解析，但向量库里仍残留、可被检索命中的旧分块向量。
- 对账（reconcile）：比对两个数据源的差集并补齐/清理，使二者重新一致。

### 0.2 三态标注约定

本项目纪律要求严格区分完成度，本报告统一使用：

- **【已实现】**：仓库里已有可运行代码，可实查验证。
- **【只设计】**：已有明确方案/文档/字段设计，但未落地为可运行代码。
- **【未做】**：既无代码也无设计，仅作为候选或明确不做。

第 C 节「落地建议」额外使用一套落地口径（按团队要求）：**已具备基础 / 需补齐 / 本期不建议做**，与上面三态一一对应说明，不混用。

### 0.3 本项目现状锚点（写报告以团队已实查结论为准）

下表是「缓存」与「重启状态」两块的事实底稿，均来自实查，标注三态。

| 位置 | 变量 / 结构 | 性质 | 重启后 | 三态 |
|---|---|---|---|---|
| `webapp/db.py:152-159` | `chunk_embeddings(chunk_id, model, dim, vec, updated_at)` | 唯一真正的持久化缓存，按 provider（供应商）+ 模型名做 `cache_key`（缓存键）隔离 | 保留 | 【已实现】无 TTL、无容量上限、无失效机制 |
| `webapp/rag.py:133,140,159` | `effective_embedding()["cache_key"]` 读写 `chunk_embeddings` | 向量缓存的读路径与写路径 | 保留 | 【已实现】 |
| `webapp/db.py:55` | `documents.status IN ('queued','analyzing')` | 任务状态机 | 保留但无人重跑 → **永久卡住** | 【已实现】有缺陷 |
| — | 检索结果缓存 / 答案缓存 | 不存在 | — | 【未做】 |
| `webapp/observability.py:180` | `_WINDOWS`（限流滑动窗口 `deque`） | 进程内 | **丢失** | 【已实现】演示替身 |
| `webapp/observability.py:40` | `RATE_DIMENSIONS = ("user","endpoint","model")` | 三维限流维度 | 规则落库、窗口内存 | 【已实现】 |
| `webapp/queue.py:16-19` | `_sem` / `_active` / `_queued` | 进程内信号量与活跃/排队任务 | **丢失** | 【已实现】演示替身 |
| `webapp/routers_knowledge.py:28` | `_POLICY`（新鲜度策略，模块级变量） | 进程内 | **丢失**，回默认 | 【已实现】 |
| `webapp/routers_parse.py:28` | `_TRACE_HISTORY`（解析轨迹 `deque`） | 进程内 | **丢失** | 【已实现】 |
| `parsing/server.py:22` | `_HISTORY`（探测轨迹 `deque`） | 进程内 | **丢失** | 【已实现】 |
| `parsing/config.py:129-134` | `set_strategy()` 直接写 `os.environ` | 进程内策略 | **丢失**，回默认 | 【已实现】 |
| `webapp/db.py:144-148` | `app_settings`（运行时配置落 SQLite） | 持久化 | 保留 | 【已实现】切换即时生效 |
| `backend/app/core/config.py:45-47` | `REDIS_CACHE_DB=0 / BROKER_DB=1 / RESULT_DB=2` | 设计中的 Redis 三分库 | — | 【只设计】 |
| `docker-compose.yml:43` | `--maxmemory-policy noeviction` + AOF | Redis 不淘汰、只追加日志 | — | 【已实现】（编排层） |
| `docs/async-upload.md:117,127` | `build_snapshot()` 从 PG 重算；PG 真值 / Redis 实时通道 | 权威口径 | — | 【只设计】 |
| `docs/org-model-change-plan.md:347` | `grant_version`/`authz_version` 全仓零命中 | 新增能力 | — | 【未做】 |

> 持久化层：演示切片是 SQLite（`webapp/db.py`），生产对应 PostgreSQL（下称 PG）。`docs/async-upload.md` 的既有口径是「**PG 是状态真值、Redis 只是实时通道，进度可从 PG 重建**」——本报告 B 节的全部建议都必须服从这一口径，不另立真值。

### 0.4 本报告不做什么

- 不改任何 `.py` / `.html` / `.js` / `.yml`，不动冻结区 `eval/` `demo/` `evalbench/`，不碰 `docs/knowledge/`、`docs/parsing/`、`knowledge/`、`parsing/`（另有成员在改）。
- 不编造实测数字、价格、接口地址、压测结论。
- 不把公开资料里的经验阈值当作本项目结论（见 D 节统一免责声明）。

---

## A. 缓存设计

### A1. 缓存什么、不缓存什么：四类缓存的收益与风险

| 缓存层 | 缓存对象 | 主要收益 | 主要风险 | 本项目对应 | 三态 |
|---|---|---|---|---|---|
| 向量缓存 | chunk（分块）文本对应的 embedding（向量） | 避免重复向量化，省 embedding（向量化）调用与延迟 | 换 embedding 模型后旧向量与新向量空间不可比；内容改了向量没改 → 旧证据 | `webapp/db.py:152` `chunk_embeddings` | 【已实现】无失效 |
| 检索结果缓存 | 某 query（查询）命中的 chunk id（分块标识）/score（得分）/排序 | 省掉召回、融合、rerank（重排）整段链路 | **跨用户串权限**、知识库更新后旧片段复现、模型/检索配置变了仍在用 | 无 | 【未做】 |
| 答案缓存 | 最终生成的回答文本与引用 | 感知延迟最低、省 LLM（大语言模型）调用 | 依赖最多（query + 权限 + 语料 + prompt（提示词）+ 生成模型），任一变化即串答案；语义缓存还有误命中 | 无 | 【未做】 |
| 元数据缓存 | 文档标题、版本、权限标签、集合指纹 | 便宜、命中率高 | 权限元数据滞后 → 用旧规则过滤；「正文已更新、权限仍用旧规则」 | `webapp/db.py` 各表直查 | 【只设计】 |

**为什么检索结果缓存必须先按权限过滤再写（本项目硬约束）**：
`docs/org-model-change-plan.md` 的 D5 决策明确把缓存归为「**先按 `decide()`（授权判定）过滤再写**」，检索缓存 key 必须携带 principal（请求主体）的授权快照，命中后再轻量复核 `decide()`。原因是 tenant（租户）前缀被删除后，若只按部门分片缓存，A 部门可能命中 B 部门缓存造成越权（`:498-499`）。若先缓存全库结果、再在读取时裁剪，缓存里就落了未授权内容，任何 key 碰撞、日志外泄、后台导出都会泄露；且「缓存命中」常被当作可信快速路径，复核容易被省略。**写入前过滤 + 命中后复核**是双保险。这一步是【只设计】，`grant_version` 未就位前不得切换缓存 key（`:347`）。

### A2. 缓存 Key 组成：每一项都是一道事故防线

一个检索结果/答案缓存的 key，应当是「影响输出的全部输入」的确定性摘要（如 `query_hash`，查询文本的归一化哈希）。下表逐项列出**缺了会出什么事**。

| 维度（英文键名） | 中文说明 | 缺失后果 | 事故类型 |
|---|---|---|---|
| `query_hash` | 归一化后查询文本的哈希 | 不同问题互串 | 旧答案 |
| `llm_model` + `llm_version` | 生成模型名与版本 | 旧模型答案喂给新模型口径 | 串模型 |
| `embedding_model` + `embedding_version` | 向量模型名与版本 | 旧向量空间与新空间不可比，召回错乱 | 串模型 |
| `top_k` | 取回的候选条数 | 换条数仍复用旧排序结果 | 串版本 |
| `retrieval_mode` | 检索模式（稠密/词法/混合） | 换召回方式仍用旧候选 | 串版本 |
| `rerank_tier` | rerank 档位/模型版本 | 旧排序结果不失效 | 串版本 |
| `authz_snapshot` | 授权快照 | **A 用户命中 B 用户结果** | **串权限** |
| `data_version` | 文档集合指纹/语料版本 | 文档增删改后旧片段仍在 | 旧答案 |
| `policy_version` | 策略版本（新鲜度/冲突/提示词） | 策略改了仍用旧判定结果 | 旧答案 |
| `org_namespace` | 静态组织命名空间 | 跨组织/跨部署串数据 | 串权限 |

`authz_snapshot`（授权快照）的字段构成，逐字沿用 `docs/org-model-change-plan.md:269` 的既有设计，不在本文重新定义：

```
authz_snapshot = hash(role, department_id, department_path,
                      sorted(visible_department_ids()), grant_version, ORG_NAMESPACE)
```

即：角色（role）、部门 id（department_id）、部门物化路径（department_path）、**排序后的可见部门集合**、授权版本（grant_version）、静态组织命名空间（ORG_NAMESPACE）。排序是为了让集合与顺序无关、保证哈希稳定；`grant_version` 是组织级单键计数器，授权变更时 `INCR`（自增），使旧 key 自然不命中。

> 两类 key 按数据性质**分治**（`:269-272`）：检索结果缓存挂授权快照、授权变更后旧 key 不命中；进度快照 key 恒定（不挂部门/授权），订阅资格由端点 `decide()` 把关，PG 为真值、Redis 失效可重建。`chunk_embeddings.cache_key` **不改**（只含 provider + 模型名，与权限正交），改的是候选集来源（先按部门过滤再查/写）。**明确禁止**把进度 key 按部门分片或挂授权快照。

### A3. 三种失效机制对比：为什么「只靠 TTL」不够

| 机制 | 触发方式 | 优点 | 局限 | 适用 |
|---|---|---|---|---|
| TTL 失效（时间失效） | 到期自动删 | 实现简单、有兜底上限 | 「十分钟 TTL 不表示十秒前没人删过文档」；窗口内一定存在陈旧读 | 兜底，不当主机制 |
| 版本感知失效 | key 里带版本号，版本变了旧 key 自动不命中 | 精确、无需扫描全库、天然灰度 | 依赖版本号可靠递增与传播 | 主机制 |
| 显式失效（事件驱动） | 变更事件定向删 key / 前缀 | 即时、可定向 | 事件可能丢；`noeviction` 下严禁 `FLUSHALL`（全库清空） | 补充 |

三条联动链（务必写进设计）：

1. **文档增删改 → `data_version`（数据版本）递增**：每次写入提交后递增集合指纹，检索缓存 key 里带 `data_version`，旧 key 自然不命中。`docs/async-upload.md:194` 已有幂等键 `chunk.id = uuid5(doc_id, version_no, seq)`（用命名空间 UUID 由文档 id、版本号、序号派生）可作版本载体。
2. **授权变更 → `authz_snapshot` 变化**：转岗、部门移动、ACLGrant（显式授权）变更 → `grant_version` 自增 → 旧授权快照 key 不命中；命中后仍要复核 `decide()`。公开资料（Truto、CloudsPress、TianPan）一致强调：**「稍旧的内容可以接受，稍旧的授权绝不可接受」**，因此授权决策要么每次实时重算，要么把权限变更当作一等 CDC 事件驱动失效，绝不缓存一个「已放行」的断言。
3. **模型切换 → 命名空间变化**：换 embedding 模型/版本、换生成模型/版本、换 rerank 档，用 `CACHE_VERSION`（缓存版本前缀）或命名空间切换，旧向量/旧答案「不可达」、自然过期；**旧向量与新向量不可比较**，必须全量重算，不能混用（ExplainX、Adarsh M、BitTide 均指出此点）。

**为什么只靠 TTL 不够**（DEV Community、CloudsPress、面试大师、吴师兄文均反复强调）：文档删除十秒后，十分钟 TTL 的检索缓存仍会把已删片段喂进上下文；权限 revoked（撤销）后，TTL 窗口内用户仍能看到无权限资料；且「检索缓存更新了、答案缓存没更新」会更隐蔽地继续返回旧答案。TTL 只能作为**陈旧度上限兜底**，正确性边界必须由版本 + 显式失效 + 命中后复核共同承担。

### A4. 分层缓存与三大经典故障

分层（与 `docs/async-upload.md:121-131` 的三层进度通道同构，但职责不同）：

- **L1 进程内 LRU（最近最少使用）**：最快、零网络；**重启即清空**，只放可重建的派生数据；必须设容量上限与 LRU 淘汰，不能无限增长。
- **L2 Redis / 持久化缓存**：跨进程共享、可设 TTL；本项目 `REDIS_CACHE_DB=0`（`config.py:45`）为缓存库，compose 配 `noeviction`（`docker-compose.yml:43`）→ **内存满时写入失败而非静默淘汰**，因此容量上限与淘汰策略要落在应用侧，不能指望 Redis 自动腾挪。
- **真值层 PG**：文档、分块、任务、审计、用量的唯一权威；缓存永远不写真值，只写派生；「PG 真值、Redis 实时通道、进度可从 PG 重建」口径不变。

三大故障与手段（中文面试高频三件套，来源见 D 节）：

| 故障 | 定义 | 手段 |
|---|---|---|
| 缓存穿透 | 查**库里也没有**的数据，请求穿过缓存直打 DB | 参数校验 + 布隆过滤器（Bloom Filter，位图概率判「一定不存在」）+ **负缓存**（缓存空结果，短 TTL，区分「未找到」与「暂时不可用」） |
| 缓存击穿 | **单个热点 key** 过期瞬间大量并发同时回源 | **互斥重建**（`SET NX EX` 原子锁，只放一个线程重建，双重检查）+ 逻辑过期（value 内带逻辑过期时间，后台异步刷新、期间返回旧值） |
| 缓存雪崩 | **大量 key 同时过期**或缓存整体宕机 | **TTL 抖动**（基础 TTL 叠加随机偏移打散）+ 多级缓存（L1 挡一层）+ 高可用 + 限流/熔断/降级兜底 |

补充工程纪律（来自检索资料）：互斥锁必须带超时，否则持锁线程挂了全系统陪葬；空值缓存 TTL 要短，否则数据新增了仍返回 null；布隆过滤器有误判率且不便删除，频繁增删要配空值缓存；**别把所有 key 设成永不过期**，会让内存膨胀并触发淘汰把热点挤掉。容量上限 + 淘汰策略（LRU/LFU）+ `noeviction` 下的应用侧写入失败降级，三者要一起设计。

### A5. 可观测：缓存要能「算得清」

必须能按层、按原因聚合，且缓存收益要能算（不是拍脑袋命中率）：

- 命中/未命中/旁路（`hit` / `miss` / `bypass` / `stale` / `error`）——**缓存报错不能记成 miss**（Stanley Yang 明确：把 error 当 miss 会隐藏可靠性事故并造成成本尖峰）。
- 失效原因分布：TTL 到期 / `data_version` 变化 / `authz_snapshot` 变化 / 模型命名空间切换 / 显式删除 / 容量淘汰。
- 条目数、容量水位、TTL 分布（各剩余时长桶的条目数），用于发现「集中过期」风险。
- 延迟与成本节省：按层统计 TTFT（首字响应）、完整耗时 P50/P95/P99，以及省下的 embedding / rerank / LLM 调用次数与 token；口径对齐 `webapp/observability.py` 的 `usage_events` 逐调用台账（`webapp/db.py:166`），`estimated=1` 表示按字符估算、不是供应商 usage（供应商用量回执）。
- 安全指标：越权命中数（应为 0）、语义缓存误命中率、陈旧命中率（命中条目是否与当前 `data_version` 一致）。

> 公开资料强调「不要只凭合成命中率宣称节省」（Stanley Yang、吴师兄文）：要用真实重放语料对比 cached / uncached 两跑，同时看正确性、引用新鲜度、越权命中、误命中、延迟、成本，并固定模型/prompt/数据快照/缓存配置。**本项目无实测数字，相关结论一律留白。**

### A6. 禁止项（硬红线）

1. **禁止缓存未授权内容**：检索结果必须先按权限过滤再写；候选集之外的内容不得进入共享缓存边界。
2. **禁止 key 不含身份快照**：用户 id 不够（角色会变），必须带 `authz_snapshot` 或命中后实时复核 `decide()`；「把 ACL 缓存掉」是公认的 RAG 安全漏洞（TianPan）。
3. **禁止把 `comparable=false` 的离线评估结果当质量基线**：这是本项目 `eval/` 的阻断口径（见 C8），缓存命中不能绕过质量闸门，也不得用一次缓存加速的演示结果冒充可比较的质量结论。
4. **禁止把 TTL 当权限控制**：TTL 是陈旧度上限，不是授权边界；权限变更必须即时失效。
5. 禁止缓存被中止的流、拒绝/鉴权失败、临时错误、个性化敏感数据为共享条目；不要把「未找到」和「暂时不可用」混为同一负缓存。
6. 禁止在 `noeviction` 三分库上用 `FLUSHALL` 做失效（`docs/org-model-change-plan.md:349`），只做按前缀的定向失效；否则会打断 Celery 在途任务。

---

## B. 重启与状态稳定性

### B1. 进程内状态分类法（先分类，再决定要不要救）

把「重启会丢的东西」先归档，是 recovery（恢复）设计的第一步。三类各自给本项目实例：

| 类别 | 判定标准 | 本项目实例 | 处置 |
|---|---|---|---|
| **可安全丢弃** | 丢了不影响正确性，最多损失体验/统计精度 | `webapp/observability.py:180` 的 `_WINDOWS`（限流滑动窗口）、`webapp/routers_parse.py:28` 的 `_TRACE_HISTORY`、`parsing/server.py:22` 的 `_HISTORY` | 重启清零，不重建；可记一条「窗口重置」事件 |
| **可从真值重建** | 真值在 PG/SQLite，内存只是投影 | `webapp/queue.py:16-19` 的 `_sem`/`_active`/`_queued`（队列活跃态）、进度快照、SSE（服务端推送事件）连接 | 启动时从 `documents.status` 与 `ingest_jobs` 重算，不信任旧内存 |
| **必须持久化** | 丢了会产生错误或用户可感知的「改了又回退」 | 配置与策略（`_POLICY`、`PARSING_STRATEGY`）、预算（`budgets`）、用量台账（`usage_events`）、文档与向量缓存（`chunk_embeddings`）、会话（`chat_logs`） | 落库/落配置中心，重启后原样恢复 |

**注意一个真实反差**：`_POLICY`（`routers_knowledge.py:28`）与 `PARSING_STRATEGY`（`parsing/config.py:133`）目前都在「必须持久化」类里却是进程内实现 → 用户在前端改的策略**重启回默认**，这是可复现的体验 bug。反过来 `app_settings`（`db.py:144`）、`budgets`（`db.py:190`）、`usage_events`（`db.py:166`）已经落 SQLite，是正确示范，可作为策略持久化的落点参照。

### B2. 启动自检 / Recovery（恢复）清单

启动顺序建议：**先连真值层 → 跑自检 → 出报告 → 再放行流量**。自检项：

1. **孤儿任务检测与重新入队**：扫 `documents.status IN ('queued','analyzing')`（`db.py:55`）且心跳/lease（租约）超时的记录，用**幂等键**重新入队；这是本项目最明确的真实风险——这类任务重启后无人重跑会**永久卡住**。`docs/async-upload.md:186` 已有口径：`acks_late`（晚确认）+ beat 每 5 分钟扫 `heartbeat_at < 15min`（心跳停更）重投。
2. **软删与向量对账**：删文档先 PG 软删 → 异步清 Milvus → 标记 chunks 软删 → 每日/启动时按 `chunk_id` 差集清孤儿（`async-upload.md:192`）；目标是清理**幽灵向量**（文档已删但向量仍可命中）。
3. **缓存过期条目清理**：启动时按 `data_version` / 模型命名空间 / `authz_snapshot` 做惰性或批量失效；`chunk_embeddings` 目前无 TTL、无容量上限（`db.py:152`），需补「模型命名空间变更即失效」与容量上限，否则换模型后旧向量永久残留。
4. **配置默认值补齐**：`_seed()`（`db.py:358`）已有幂等种入（只在缺失时插入、不覆盖 admin 修改），策略/开关的默认值补齐照此模式；**绝不覆盖用户已改值**。
5. **上次异常退出标记**：用「干净关闭旗标」区分 `SIGTERM`（可捕获的终止信号，走优雅关闭）与崩溃/`SIGKILL`（不可捕获的强制终止信号）；无干净旗标即进入 recovery 分支并上报。
6. **启动报告**：把上面 1–5 的结果汇总成结构化报告（字段见 B6），写日志/审计（可复用 `audit_logs`，`db.py:130`）。

### B3. 幂等与重复处理

恢复必然带来「重跑」，因此所有写入都要幂等：

- **任务重复入队**：`ingest_jobs.idempotency_key`（幂等键）保证同一上传不重复建任务（`async-upload.md:195`）；`chunk.id = uuid5(doc_id, version_no, seq)` 同时是 Milvus 主键，重复执行只覆盖不新增（`:194`）。
- **写入去重**：`upload_chunks` 的 `uq_upload_chunk_idx` + 先查后写，重复 PUT 幂等返回成功（`:184`）；`chunk_embeddings` 主键 `(chunk_id, model)` 天然去重（`db.py:158`）。
- **SSE 重放**：`Last-Event-ID`（最后事件标识）跳过已收事件，建连先拉权威快照（`async-upload.md:137-139`）；「刷新页面进度不归零」由 PG 快照保证，不靠内存。
- **checkpoint / 断点续传**：长任务阶段性落进度，重投后跳过已完成部分；公开资料（Task Queues、Autotomy）强调「宽限期要大于 p99 任务时长，否则每次发布都在制造重投」，超长任务应做成可续传而非无限拉长优雅关闭窗口。

### B4. 优雅关闭：为什么真值层必须先行

`SIGTERM`（可捕获）时的正确顺序（systemd / Task Queues / Celery 部署文一致）：

1. 记录「已请求关闭」；2. 停止接收新请求/新任务；3. 通知 worker 循环停止取新消息；4. 在途任务**排空**（完成或 checkpoint 后确认，未完成的释放以便重投）；5. 状态落盘、只 flush（刷写）真正必须持久的状态；6. 关闭连接/句柄、注销、退出。

**为什么 `SIGKILL` 无法优雅关闭**：`SIGKILL`（以及掉电、OOM Killer 内存溢出杀进程、未捕获异常）进程**无法捕获、无法拦截**，没有信号处理器、清理代码不执行、内存里的工作直接丢失（systemd 文、Visakh Unni 文、Autotomy 文一致）。因此**不能把正确性押在优雅关闭上**：Autotomy 的「crash-only」主张——把不可预测的 shutdown bug 转成可预测的 recovery latency（恢复延迟），修复的是**恢复路径**而不是 shutdown handler；测试要在请求处理到一半时直接 `SIGKILL`、重启、断言系统仍一致，而不是只测优雅关闭那条「虚构故事」。结论：**真值层（PG）先行、状态随时可重建、写入幂等**，才是 `SIGKILL` 下不丢状态的根本。

### B5. 对账与自愈

| 机制 | 启动时 | 定时（beat / 看门狗） |
|---|---|---|
| 触发 | 每次进程拉起、异常退出标记存在 | 固定周期或事件 |
| 任务对账 | 扫 `queued/analyzing` + lease 超时 → 重投 | 心跳 `heartbeat_at < 阈值` 扫卡死任务重投（`async-upload.md:186`） |
| 向量对账 | chunk id 差集补齐/清孤儿 | 每日 `reconcile_vectors` 报 `missing_fixed` / `orphans_removed`（`:188,228`） |
| 会话/分片清理 | 清 `expires_at < now` 的残留 | beat 每小时清过期会话与分片对象（`:191`） |

- **失败重试与死信**：指数退避 + 上限（本项目 `INGEST_MAX_RETRIES=3`，`config.py:92`/`async-upload.md:215` 为既有口径）；超过上限进死信队列（dead letter，无法再自动处理、留待人工/告警），不能无限重试把队列打满。
- **卡死看门狗（心跳 / lease）**：worker 定期续约，超时未续约即判定失联并回收任务；systemd `WatchdogSec` + `sd_notify` 心跳、K8s Liveness/Readiness（存活/就绪探针）是同类机制。注意「看门狗的看门狗」、超时太短会在正常重负载下误重启、心跳线程优先级不能低于失控线程，否则失效（Inferensys、CSDN、人人代码）。
- **定时对账 vs 启动时对账**：启动对账保证「拉起即自愈」，定时对账兜住「运行中漂移」（消息丢失、Milvus 写失败、软删未清理），二者都要有，且都不能变向制造重复写入（依赖 B3 幂等）。

### B6. 可观测与验收

**重启恢复报告建议字段**（可落 `audit_logs` / 独立 `recovery_reports`）：

- `recovery_id`、`started_at`、`clean_shutdown_flag`（上次是否干净关闭）、`trigger`（SIGTERM / 崩溃 / SIGKILL / 首次启动）。
- `orphan_tasks_found` / `orphan_tasks_requeued`（孤儿任务发现数 / 重新入队数）。
- `ghost_vectors_found` / `ghost_vectors_removed`（幽灵向量发现数 / 清理数）。
- `cache_entries_expired` / `cache_namespaces_invalidated`（缓存过期清理数 / 命名空间失效数）。
- `config_defaults_backfilled`（默认值补齐数，且不含被覆盖的用户值）。
- `recovery_duration_ms`、各阶段耗时、告警列表。

**验收判据**（都要可复现、可写进 QA 用例）：

1. **结果一致性**：重启前后用同一 `query`（同一用户、同一授权快照、同一 `data_version`）提问，**答案与来源（citation 引用链）不变**；对齐 `chat_sources`（`db.py:91`）的引用真值。
2. **幂等验证**：连续重启两次，第二次的孤儿回收数应为 0、结果与第一次一致，不产生重复分块/重复任务/重复用量台账行。
3. **故障演练**：上传中途 `SIGKILL`，重启后进度从 PG 快照恢复不归零、任务最终 `ready`、无幽灵向量；`docker compose stop redis` 时进度降级轮询 PG、入库不受影响（`async-upload.md:222-223`）。
4. **策略不回退**：改新鲜度策略/解析策略后重启，值保持（当前未满足，见 B1/C）。

### B7. 面试考点（≥6 条，含四条指定题）

1. **「重启后限流窗口清零会不会被打爆？」**——会，但要分层答：`_WINDOWS`（`observability.py:180`）是进程内滑动窗口，重启清零意味着短时间内计数从头开始，理论上存在突发流量穿透窗口的风险；但本项目 `rate_rules`（`db.py:201`）规则落库、且这是**演示替身**，生产应换 Redis 令牌桶/滑动窗口 + 网关层限流，使计数跨重启连续。要点是承认风险、给出「规则持久化 + 计数外置」的方向，而不是说「没事」。
2. **「缓存 key 为什么要带模型名？」**——不同 embedding/生成模型对应不同向量空间与输出口径，旧向量与新向量**不可比较**；不带模型名会在模型升级后复用旧向量/旧答案，产生「串模型」与静默质量下降。换模型要走命名空间切换 + 全量重算，不能混用（A2/A3）。
3. **「进度放 Redis 挂了怎么办？」**——`docs/async-upload.md` 的口径：PG 是状态真值、Redis 只是实时通道（pub/sub 推送 + 快速快照，`progress-snapshot` TTL 24h）；Redis 挂了 SSE 降级 2s 轮询 PG、入库任务不受影响，进度可从 `ingest_jobs` / `batch_uploads` 重建，绝不把 Redis 当真值。
4. **「怎么证明重启没有丢状态？」**——不能靠口头，要靠 B6 的恢复报告 + 三组可复现对比：同一 query 答案与引用不变、孤儿/幽灵向量计数归零、连重启两次幂等；再加 `SIGKILL` 中途故障演练，测恢复路径而非只测优雅关闭。
5. **「缓存击穿、穿透、雪崩区别与解法？」**——见 A4 表：穿透（查不存在）用布隆过滤器 + 空值短 TTL；击穿（单热点过期）用互斥重建 / 逻辑过期；雪崩（大面积同时过期）用 TTL 抖动 + 多级缓存 + 限流熔断。
6. **「只靠 TTL 做失效够不够？」**——不够（A3）：TTL 是陈旧度上限而非正确性边界，删除/授权撤销/模型切换都要求即时或版本感知失效。
7. **「`acks_late` 与 `visibility_timeout` 为什么重要？」**——worker 被 kill -9 时任务未确认，靠晚确认 + 可见性超时重投；宽限期要大于 p99 任务时长，长任务做 checkpoint。
8. **「缓存命中要不要复核权限？」**——要：授权是廉价但关乎安全的决策，绝不缓存「已放行」断言；命中后轻量复核 `decide()` 是本项目硬约束。

---

## C. 对本项目的落地建议（7 条，逐条标落地口径）

> 口径：**已具备基础**（有可复用代码/字段，补一层即可）/ **需补齐**（缺机制，建议本期做）/ **本期不建议做**（超出演示切片定位，明确留白）。指到具体文件与字段。

**C1【需补齐】给 `chunk_embeddings` 补失效三件套。**
`webapp/db.py:152-159` 目前无 TTL、无容量上限、无失效机制。最小补齐：加 `cache_namespace`（缓存命名空间，随 embedding 模型/版本变化）、`data_version`（数据版本）或至少 `updated_at` 驱动的惰性校验，以及容量上限 + LRU 淘汰；删文档时按 `doc_id` 联动清对应 `chunk_id` 的向量行（治理幽灵向量）。**不改** `cache_key` 的 provider + 模型名口径（`org-model-change-plan.md:272` 明确不改），只补失效与候选集来源。

**C2【已具备基础 → 需补齐】把进程内策略搬进 `app_settings` 模式。**
`_POLICY`（`routers_knowledge.py:28`）与 `set_strategy()` 写 `os.environ`（`parsing/config.py:133`）重启回默认。落点参照已落库的 `app_settings`（`db.py:144-148`，切换即时生效）与幂等 `_seed()`（`db.py:358`）。保持 `WEBAPP_` / `PARSING_` 前缀隔离配置命名空间不变（`parsing/config.py:3-4` 既有纪律）；策略变更写审计（`audit_logs`）。

**C3【需补齐】启动 recovery 报告 + 孤儿任务重投。**
针对 `documents.status IN ('queued','analyzing')`（`db.py:55`）永久卡住：启动时扫描 + 幂等键重投（复用 `async-upload.md:194-195` 的 `uuid5` / `idempotency_key` 口径），产出 B6 字段的恢复报告。这是「重启后状态正常」最该先做的一条。

**C4【已具备基础】限流窗口：保留演示替身，明确生产替换路径。**
`_WINDOWS`（`observability.py:180`）属「可安全丢弃」；`RATE_DIMENSIONS = ("user","endpoint","model")`（`observability.py:40`）与 `rate_rules`（`db.py:201`）已落库。本期**只补文档说明与面试话术**（B7-1），不改限流维度、不引 Redis 令牌桶；生产替换方向写入注释即可。

**C5【本期不建议做 → 口径已按实现细化，见 §0.0.1】检索结果缓存 / 答案缓存 / 语义缓存。**
调研时理由：依赖 `grant_version`（`org-model-change-plan.md:347` 全仓零命中）与「先过滤后写 + 命中复核」尚未就位；在授权快照能力确认前切换缓存 key 是明确禁区（`:512`）。同日主理人补齐了授权快照、版本递增与命中复核三件前置能力，因此最终裁决改为：**通用双层缓存与演示级检索结果缓存已实现（主链路默认不接）；答案缓存 / 语义缓存仍不做**。语义缓存误命中风险更高的判断始终成立。

**C6【需补齐】`redis_client.py` 命名空间与既有口径对齐。**
`backend/app/integrations/redis_client.py:3-4,25-26` 的 `tenant_key(tenant_id, ...)` 仍是旧 tenant（租户）前缀，而 `org-model-change-plan.md:162,269` 已改为**静态 `ORG_NAMESPACE`（组织命名空间）+ 授权快照**。该文件是【只设计】骨架，本期不落地缓存，但建议在设计层标注「切缓存 key 前先替换前缀口径、严禁 `FLUSHALL`、只做定向失效」（`:349`），避免两套命名空间并存。

**C7【已具备基础】分层缓存与真值口径直接沿用，不另立。**
`docs/async-upload.md:121-131` 的三层进度通道（PG 真值 / Redis 快照 / pub/sub 实时）、`config.py:45-47` 三分库、`docker-compose.yml:43` 的 `noeviction` + AOF 已把「缓存不写真值、可从 PG 重建、内存满写失败有降级」讲清楚。本报告 A4/B 的建议都建立在这套口径之上，**不新增真值源、不改编排**。

### C8 与既有口径如何共存（逐项对齐，不重新定义）

| 既有口径 | 本报告如何共存 |
|---|---|
| `parsing/` 三级可插拔解析策略 + 90:10 预算闸门（`parsing/config.py:61` `vision_escalation_ratio=0.10`，是「最贵多模态兜底最多占多少页」的**上限/熔断线**，非随机分流） | 缓存只作用于**检索/答案派生层**，不碰解析策略选择与升级判定；策略版本若进缓存 key，只作为 `policy_version` 维度，不改闸门语义 |
| `knowledge/` 入库新鲜度 + 事实冲突检测（`routers_knowledge.py` 的 `_POLICY` 为进程内） | 新鲜度策略属「必须持久化」，纳入 C2；冲突检测是纯函数、不写业务库，不进缓存；`comparable=false` 评估口径见下 |
| 三维限流 `("user","endpoint","model")`（`observability.py:40`） | 维度不改、不新增部门限流断言；`_WINDOWS` 归「可安全丢弃」，不因缓存改造而持久化 |
| C33 的 10 具名 view + 6 具名 loader **存在性检查**（`selftest.py:369-377`，断言 `nav_ok==10 and loaders_ok==6`） | 本报告不改任何前端/静态资源；新增缓存视图若发生，须不删改这 10 个 view 名与 6 个 loader 名、不改断言数字 |
| 冻结区 `eval/` `demo/` `evalbench/` 零改动 | 全程只读，不写入；A6-3 明确禁止把 `comparable=false` 的离线评估结果当质量基线 |
| `eval/` 的 `__TBC__`（待确认占位符）/ `NOT_COMPARABLE`（不可比较）/ `real_api`（真实接口档位）阻断口径（`eval/config/objects/adapter_contracts.json`：`real_api` 档下任一 `blocking=true` 字段仍为 `__TBC__` 必须快速失败） | 缓存命中**不得绕过**这些阻断；缓存加速的演示结果不得冒充可比较质量结论；任何「命中率/节省」数字在 `real_api` 未就位前一律标「未实测」 |
| `WEBAPP_` / `PARSING_` 前缀隔离配置命名空间 | C2/C6 的新增键沿用前缀，不引入扁平同名键污染两边启动校验 |

---

## D. 来源清单

**检索说明**：2026-11-14 通过 WebSearch 做 6 次定向检索（3 英 + 3 中），查询词覆盖「RAG retrieval cache version-aware invalidation authorization snapshot」「LLM semantic cache key design embedding model version TTL negative caching」「service restart state recovery idempotent orphan task reconciliation graceful shutdown」「RAG 缓存 失效 版本 权限过滤 面试」「服务重启 状态恢复 幂等 孤儿任务 对账 心跳」「缓存击穿 穿透 雪崩 面试」。以下 URL 与标题均取自当日检索结果，未改写、未编造；每条标注关联章节。

### D.1 英文来源（检索日 2026-11-14）

1. Truto — *Document-Level RBAC for RAG Pipelines: The 2026 Enterprise Architecture Guide* — https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines — 关联 A2/A3（ACL 版本、权限变更 webhook、审计行 `(user_id, query_hash, returned_chunk_ids, denied_chunk_ids, acl_version)`）
2. Interviews Vector — *Design a Production RAG System in 2026* — https://www.interviewsvector.com/blog/design-production-rag-system-2026 — 关联 A1/A2（分层缓存 key 组成表、ACL fingerprint、负缓存、TTL 抖动、勿缓存 denials/errors）
3. CloudsPress — *How to prevent AI agents from using stale or conflicting context* — https://www.cloudspress.com/how-to-prevent-ai-agents-from-using-stale-or-conflicting-context — 关联 A3（派生工件依赖 canonical id + version、删除/权限标签传播、安全裁剪）
4. DEV Community — *2026 Privacy-Focused Personal Knowledge Manager Search: Cache Invalidation Under Load* — https://dev.to/jamesanderson3589/2026-privacy-focused-personal-knowledge-manager-search-cache-invalidation-under-load-1bno — 关联 A2/A3/A4（查询不是纯文本、快照不可混用、重启不假设暖缓存有效、指纹校验）
5. Stanley Yang — *Caching AI Applications: Prompts, Prefixes, Retrieval, and Tools* — https://stanleycyang.com/writing/caching-ai-applications — 关联 A5/A6（hit/miss/bypass/stale/error、重放语料对比、ACL 不可缓存掉、stampede 防护）
6. ExplainX — *Semantic Caching for LLM Apps: Exact Hash, Embeddings and Safe Rollouts* — https://explainx.ai/blog/semantic-caching-llm-apps-cost-latency-guide-2026 — 关联 A2/A3（key 含模型名+版本+prompt hash+工具 schema、换 embedder 需重索引、负缓存「永不命中」提示）
7. Adarsh M — *Semantic Caching for LLM Apps* — https://adarshm.com/blog/semantic-caching-for-llm-apps — 关联 A3（阈值不跨模型迁移、版本化 key、知识库版本 bump）
8. Neptune Infotech — *How to Build a Versioned LLM Cache in TypeScript for Reliable AI Apps* — https://neptuneinfotech.in/blogs/how-to-build-a-versioned-llm-cache-in-typescript-for-reliable-ai-apps — 关联 A2（model/user/prompt/temperature 组成 key、漏 user id 会串数据）
9. Visakh Unni — *Disposability in Software Development* — http://www.visakhunni.com/blogs/disposability-in-software-development — 关联 B3/B4（`acks_late`、`task_reject_on_worker_lost`、幂等键、`SIGKILL` 不可捕获、关键状态不放进程内）
10. gr8software — *Writing Production-Grade Systemd Services: Readiness, Restart, and Shutdown* — https://gr8software.pl/writing-production-grade-systemd-services-readiness-restart-and-shutdown — 关联 B4/B5（`SIGTERM`→`SIGKILL` 顺序、`TimeoutStopSec`、重启限速、crash-safe 状态转移）
11. Task Queues — *Graceful Shutdown & Worker Deployments* — http://www.task-queues.com/backend-frameworks-worker-scaling/graceful-shutdown-and-deployments — 关联 B3/B4（排空窗口 > p99、Celery warm/cold shutdown、长任务 checkpoint）
12. Yasha Solutions — *Deploying a Django app with Celery background tasks* — https://yasha.solutions/posts/2025-09-15-deploying-a-django-app-with-celery-background-tasks — 关联 B4（`stop_grace_period`、`worker_prefetch_multiplier=1`、`acks_late`、新旧 worker 并存）
13. Autotomy — *你的 Web Service 有一條 Graceful Shutdown 路徑。那就是 Bug。* — https://autotomy.dev/zh-hant/blog/crash-only-software-web-services — 关联 B4（crash-only、测 SIGKILL 恢复路径、idempotency key、内存结构逐项决定重建/接受丢失）

### D.2 中文来源（检索日 2026-11-14）

14. TechDataFuture — *RAG 知识库更新后为什么会变差：切分、版本和缓存的连锁反应* — https://techdatafuture.com/interview/article/rag-knowledge-update-versioning — 关联 A3（文档/段落/索引带版本、权限规则变化旧缓存未过滤、回答缓存没跟着更新）
15. 面试大师 — *RAG 知识库如何做不停服更新，并保证检索结果一致性？* — https://mianshidashi.cn/interview-questions/tencent/backend-development/tencent-backend-rag-hot-update-consistency — 关联 A3（缓存 key 带知识库版本和权限上下文、索引 alias 切换、删除/降权优先传播）
16. BitTide — *Agent 高频题 01. 讲讲你的 RAG 链路* — http://bittide.aicompass.dev/article/e596247e-3c19-44b9-983e-f85862d19d56 — 关联 A3（块记文档 id/版本号/内容哈希、换 embedding 全量重算、删后缓存一起失效、权限在检索阶段带上）
17. 吴师兄（微信公众号）— *面试官：RAG 首字响应慢，应该先优化哪一段？* — http://mp.weixin.qq.com/s?__biz=MzkzMDIwMzg1Mw==&mid=2247490943&idx=1&sn=45a08184f35884cc07aaa9986e79f843 — 关联 A1/A4/A5（三层缓存位置与失效边界、生产缓存键至少区分模型版本与租户、Trace 分段计时、P95/P99）
18. TianPan — *你的向量索引是一个没有失效策略的缓存* — https://tianpan.co/zh/blog/2026/05/17/vector-index-cache-no-invalidation-strategy — 关联 A3/A6（缓存昂贵且缓变的嵌入、绝不缓存廉价但关乎安全的授权决策、CDC 驱动、集合对账 diff、内容哈希+源版本）
19. 孟斌的小站 — *大模型缓存完全指南：从 KV Cache 到语义缓存* — https://mengbin.top/2026-08-26-大模型缓存完全指南从-kv-cache-到语义缓存 — 关联 A2/A5（精确缓存 key = hash(model+system+messages+temperature...)、隐私泄漏、命中率/节省 token/误命中投诉率）
20. 好代码 — *Redis面试题：Redis缓存如何回收？击穿、穿透、雪崩、预热解决方案？* — https://m.haodaima.com/question/content-262-132339-1.htm — 关联 A4（穿透/击穿/雪崩定义、空结果短 TTL、随机过期、互斥锁、逻辑过期、预热）
21. 中培伟业 — *Redis缓存架构设计：穿透、击穿与雪崩的解决方案* — https://m.zpedu.com/it/ityw/38690.html — 关联 A4（布隆过滤器、SETNX 互斥锁、随机偏移、多级缓存、Cache Aside 先更库再删缓存）
22. 人人代码 — *接口一到高峰就打崩数据库？缓存穿透、击穿、雪崩的分场景解法* — https://www.renrendaima.com/article/3316.html — 关联 A4（三者诊断区分、互斥锁必须带超时、空值 TTL 要短、布隆过滤器误判率）
23. CSDN — *面试官问我缓存击穿是啥，我反手就是一个比喻把他整笑了* — https://blog.csdn.net/2402_86612729/article/details/161500249 — 关联 A4/B7（三者对比表、双重检查、锁超时、别全设永不过期）
24. 人人代码 — *Linux 某服务老是意外退出、业务空窗？systemd Restart 与看门狗* — https://www.renrendaima.com/article/7202.html — 关联 B5（`Restart=on-failure`、`WatchdogSec` + `sd_notify` 心跳、治本查日志）
25. CSDN DevPress — *看门狗架构演进与设计实战：从硬件到云原生的系统守护哲学* — https://devpress.csdn.net/v1/article/detail/163868929 — 关联 B5（systemd 看门狗、K8s Liveness/Readiness 探针、应用内线程级看门狗）
26. Inferensys — *Watchdog Timer* — https://inferensys.com/glossary/recursive-error-correction/fault-tolerant-agent-design/watchdog-timer — 关联 B5（心跳/喂狗、超时过短误重启、心跳任务不可被饿死、重启后从 checkpoint 恢复）
27. 腾讯云开发者社区 — *服务总是莫名其妙就死了？写个看门狗，让它自己爬起来* — https://cloud.tencent.com.cn/developer/article/2769519 — 关联 B5（巡检拉起、环境变量传递坑、优雅退出旗标）

> 来源合计 **27 条**（英文 13 + 中文 14），均为 2026-11-14 检索所得，标题与 URL 逐字取自检索结果，未编造。

### D.3 统一免责声明（重要）

- 上述公开资料中出现的**相似度阈值、TTL 时长、随机偏移区间、心跳/宽限期秒数、命中率、成本节省比例、QPS 压测值、误判率**等一切数字，一律为**公开经验值，非本项目实测，需用真实语料与故障演练校准**；本报告不将其作为本项目结论引用，正文也刻意不转述具体数值。
- 本项目**未做任何缓存命中率、延迟、成本节省的实测**，A5 只定义「应当能算哪些指标」，不给出任何数字。
- 本报告的现状锚点来自团队实查（见 0.3），落地建议标了三态；凡标【只设计】/【未做】者，均不得对外表述为「已实现」。
