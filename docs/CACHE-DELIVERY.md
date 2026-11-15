# 缓存优化与重启稳定性 · 交付说明（可直接拿去讲）

> 用户诉求：「缓存具体想怎么做也得优化好，还有关于服务器重启以后状态各方面还算正常少 bug 稳定情况」。
> 本轮交付：通用双层缓存 + 版本感知失效 + 启动自检与恢复 + 真·进程重启验证。
> 定位：开发框架骨架 + 可运行演示切片，**不是上线交付版**。

---

## 1. 一页地图

| 路径 | 一句话 | 考官问到就打开 |
|---|---|---|
| `cache/README.md` | **先打开这个**：key 逐段表、两层缓存、启动自检 7 项、诚实边界 | 「缓存你怎么设计的？」 |
| `cache/keys.py` | key 13 段、授权快照、文档指纹、命中复核 | 「缓存 key 为什么要带模型名/身份？」 |
| `cache/store.py` | L1 内存 LRU + L2 SQLite，TTL / 负缓存 / 抖动 / 容量 / 失效统计 | 「重启后缓存还在吗？」 |
| `cache/invalidation.py` | 懒失效：版本递增让旧 key 自然不命中 | 「文档更新后旧答案怎么办？」 |
| `cache/recovery.py` | 7 项启动自检 + 状态三分类 + 优雅关闭标记 | 「重启会丢什么？」 |
| `webapp/recovery.py` | 启动/关闭桥接、策略持久化、模拟重启 | 「策略重启会不会回默认？」 |
| `webapp/routers_runtime.py` | `/api/runtime/status / cache-demo / simulate-restart / invalidate / cache-policy / recovery` | 「前后端怎么接？」 |
| `webapp/main.py` lifespan | 启动调 `recovery.startup()`、关闭调 `shutdown()` | 「自检挂在哪？」 |
| `webapp/routers_docs.py` | 上传/删除递增数据版本；**删文档前先删向量**（源头治理幽灵向量） | 「幽灵向量怎么治？」 |
| `webapp/static/`「缓存与恢复」 | key 拆解、命中率、失效原因、恢复报告、演示按钮 | 现场演示主界面 |
| `docs/cache-recovery/01-research.md` | 工业实践调研（27 条来源）+ 主理人最终状态回填 | 「你怎么研究的？」 |
| `docs/cache-recovery/02-design.md` | 分层设计、key 表、7 项检查、12+ 验收、面试问答 | 「设计取舍是什么？」 |
| `docs/CACHE-DELIVERY.md` | 本文：三段话术、真重启记录、验证、边界 | 直接照着讲 |

---

## 2. 三段话术

**开场（30 秒）**
> 缓存我只敢放在派生层。真值是文档、分块、用量和状态表，缓存里放的是「可以重算出来的东西」。
> 所以重启后 L1 内存缓存全丢也没关系，L2 和 runtime_state 还在，而且每次命中都会复核版本。

**技术深度（90 秒）**
> key 不是随便拼个 query 哈希，它有 13 段：查询、embedding 模型、生成模型、top_k、检索模式、
> rerank、新鲜度开关、**授权快照**、数据版本、策略版本、组织命名空间等。每一段都对应一类事故——
> 缺模型名会复用旧向量导致余弦静默归零，缺授权快照会跨用户串权限，缺数据版本会在文档删除后
> 继续返回旧片段。授权快照里含 grant_version，转岗或 ACLGrant 变更递增它，旧 key 自然不命中。
>
> 失效我用**懒失效**：文档/授权/策略变化只递增版本，不批量删缓存；TTL 到期再回收空间。
> 命中时仍比对 `data_version` / `authz_snapshot` / `policy_version`——**TTL 不是权限控制**。
> 写缓存前先按权限过滤，普通用户只保留 owner_id 匹配的命中，缓存 value 连正文都不存。
>
> 重启分三类状态：限流窗口和轨迹 deque 可安全丢弃；队列活跃态和未完成任务从 `documents.status`
> 真值重建；配置、策略、向量缓存、会话必须持久化。启动跑 7 项自检：孤儿任务幂等重投、幽灵向量
> 对账、过期缓存清理、文档指纹校验、策略恢复。

**诚实边界（30 秒）**
> 检索结果缓存目前只在演示接口跑，主问答链路默认不接；答案缓存和语义缓存没做，因为依赖 prompt、
> 生成模型和语义相似度，误命中风险更高。演示切片是 SQLite + 进程内队列，不是生产 Redis/Celery。
> 阈值是经验起始值，没做压测或 SLA 验证。强停进程不会写优雅关闭标记，所以真值层必须先落盘——
> 这一点我在真实重启演练里专门验证过。

---

## 3. 真·进程重启演练（最有说服力的一段）

隔离库 `.data/restart-check/app.db`，**两次真实 uvicorn 进程启停**：

| 步骤 | 操作 / 结果 |
|---|---|
| 第一次启动 | 改新鲜度策略 `half_life_days=45, blend=0.5`；跑 `/api/runtime/cache-demo` 写入 1 条缓存 |
| 埋故障 | 直接写真值层：1 个 `status='analyzing'` 的孤儿任务 + 1 条 `chunk_id` 不存在的幽灵向量 |
| 强停 | 进程结束。停服后直接读库：策略、缓存、孤儿任务、幽灵向量都在；**`last_shutdown_at` 未写入**（lifespan `finally` 未执行）→ 实证 SIGKILL 不保证优雅关闭 |
| 第二次启动 | 恢复报告 `healthy=True`、`boot_count 1→2`、`requeued=1`、`ghost_vectors=1` 已清理、`data_version` 因文档指纹变化递增 |
| 策略核对 | `/api/knowledge/status` 返回 `half_life_days=45.0, blend=0.5` —— 完整恢复，没有回默认 |
| 缓存核对 | L2 条目仍在；`/api/runtime/cache-demo` 清 L1 后从 L2 命中；版本变化后新 key 未命中 |
| 结论 | 无 500，状态按预期恢复。这是**功能连通性实证，不是压测或 SLA 结论** |

前端「缓存与恢复」页可现场点「模拟重启」（只清 L1 并重跑检查，不杀进程）复现这条链路。

---

## 4. 稳定性别的实质修复（不只是文档）

1. **孤儿任务**：`documents.status IN ('queued','analyzing')` 以前重启后永久卡住；现在启动扫描 + 幂等重投（只处理这两态，重投前复查真值，重启两次不会重复分析）。
2. **幽灵向量**：`routers_docs.py::delete_doc` 以前只删 `chunks`、不删 `chunk_embeddings`；现在删文档时先删对应向量行，启动再对账兜底。
3. **策略丢失**：`_POLICY`、`set_strategy()` 写 `os.environ`，重启回默认；现在落 `runtime_state`，启动恢复新鲜度 / 解析 / 缓存三套策略。
4. **缓存无治理**：`chunk_embeddings` 以前无 TTL / 容量 / 失效；现在通用缓存层有 TTL、抖动、负缓存、L1/L2 容量、LRU、版本感知失效与失效原因统计。
5. **一个真实 500 隐患**：`CacheStore.get()` L1 命中复核时曾把 `CacheEntry` 对象当 dict 传给失效判定，会抛 `AttributeError`；单测抓到并修复。
6. **可观测性**：恢复报告原来丢字段（`restored_policies` / `graceful_last_shutdown`），单测与手工验证后改为返回完整增强字典。

---

## 5. 验证结果

| 项 | 结果 |
|---|---|
| `pytest webapp/tests parsing/tests knowledge/tests cache/tests` | **154 passed**（webapp 90 + parsing 22 + knowledge 23 + cache 19） |
| `python webapp/selftest.py` | 33/33 `DEMO_READY`（隔离库 + 离线 provider，原报告按字节还原） |
| backend / eval / evalbench / demo | 16 passed / 14 OK / 16 OK / 16 与 16（run_id 未变） |
| `node --check` ×3 + `npm run build` | 通过 |
| `docker compose config -q` | 通过；默认 11 服务、`--profile parsing` 12 服务 |
| 冻结区 / 密钥 / `diff --check` / 行尾 | 零 diff / 零命中 / 通过 / 零异常 |
| 真实进程重启 | 见 §3 |

---

## 6. Git 路径与提交

按逻辑版本分开提交：`cache/`（通用缓存与恢复包）、`webapp/`（接入层）、`docs/cache-recovery/`（调研与设计）、`docs/CACHE-DELIVERY.md`（本文）、`interview/index.html`（演示页）。具体 hash 以 `git log --oneline -6` 为准；分支 `demo/v1`，工作区干净。静态资源 `?v=15`。

---

## 7. 还差什么（别被问住）

- 检索结果缓存**默认不接 `rag.retrieve()` 主链路**（演示级，避免打断 33 项端到端自测）；答案缓存 / 语义缓存**未做**。
- 缓存层不负责授权，调用方必须「先过滤后写」；命中复核是第二道防线，不是 `decide()` 的替代。
- L2 落 SQLite 单文件，无跨进程/跨机共享；生产应换 PG 真值 + Redis，且严禁 `FLUSHALL`。
- 启动自检不是持续对账，没有看门狗 / 死信队列 / 心跳 lease；限流窗口重启清零属已知取舍。
- 未做真实压测、监控告警 SLO、SLA / 故障注入演练、权威注册表审批流、质量基线冻结。
- `backend/` 的 13 步去 `tenant_id` 数据模型改造仍是**方案已定、代码尚未改造**。
