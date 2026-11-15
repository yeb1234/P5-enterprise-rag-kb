# `cache/` · 双层缓存、版本感知失效与重启恢复

> 一句话：**L1 加速、L2 兜底、真值层说话**。重启后 L1 必然清空，但策略、
> 缓存条目、任务状态都能从 SQLite 恢复，并且每一次缓存命中都复核三重版本字段。

## 为什么单独一个包

`parsing/` 管「字节 → 结构化文本」，`knowledge/` 管「哪些证据适用、是否互斥」，
`cache/` 管「派生结果怎么复用、怎么失效、重启后怎么恢复」。三者都是纯函数/纯存储包，
可脱离 FastAPI 单独 import 与单测；下游 `ChunkSpec` / citation 契约不动。

## 目录地图

| 文件 | 职责 |
|---|---|
| `contracts.py` | `CachePolicy` / `CacheEntry` / `CacheStats` / `CheckResult` / `RecoveryReport` |
| `keys.py` | key 构造与逐段拆解、授权快照、文档指纹、策略版本、命中复核 |
| `store.py` | L1 `OrderedDict` LRU + L2 SQLite `cache_entries` / `runtime_state`，TTL/LRU/容量/失效 |
| `invalidation.py` | `VersionController`：数据与授权版本递增、策略版本同步、演练用主动清空 |
| `recovery.py` | `run_startup_checks()` 启动自检、`note_shutdown()` / `was_graceful()` |
| `tests/` | 19 项离线单测，含**真·重启模拟**（清 L1 + 新建 store 实例） |

配套适配层：`webapp/recovery.py`（启动/关闭桥接、策略持久化、模拟重启）、
`webapp/routers_runtime.py`（`/api/runtime/*`）、`webapp/static/` 的「缓存与恢复」视图、
`docs/cache-recovery/01-research.md`（调研 27 条来源）、`02-design.md`（设计与面试问答）、
`docs/CACHE-DELIVERY.md`（交付说明）。

## 缓存 key：每一段对应一类事故

```
kb:<sha256 of sorted parts>    parts =
  schema | org | kind | query | embedding | model | top_k |
  retrieval_mode | rerank | freshness | authz | data_version | policy_version
```

| 段 | 缺了会怎样 |
|---|---|
| `embedding` | 切模型后复用旧向量，余弦静默归零，检索看似正常实则全错 |
| `model` | 换生成模型仍用旧答案 |
| `authz` | **跨用户串权限**（最严重） |
| `data_version` | 文档增删改后旧片段/旧答案复现 |
| `policy_version` | 改了新鲜度策略仍用旧排序结果 |
| `freshness` / `rerank` / `top_k` | 不同检索配置共用一份结果 |
| `org` / `schema` | 结构升级后旧条目形状不兼容 |

`authz_snapshot = sha256(org, role, department_id, sorted(visible_department_ids), grant_version)`。
转岗、角色变更、ACLGrant 增改（递增 `grant_version`）都会让旧 key 不可命中。

## 两层缓存与失效顺序

```
请求 → 构造 key → L1 命中? → L2 命中? → miss → 检索
  → 先按权限过滤 → 写 L1+L2 → 返回
（命中路径）仍复核 data_version / authz_snapshot / policy_version
```

- **L1**：进程内 LRU，重启必然清空，属「可安全丢弃」。
- **L2**：SQLite `cache_entries`，与业务库同文件，重启后仍在；命中后复核三重版本字段。
- **真值层**：`documents` / `chunks` / `runtime_state`；进度与队列活跃态可从真值重建。
- **懒失效**：版本递增后旧 key 自然不命中，不做批量删除；TTL 到期由 `purge_expired()` 回收。
  日常只递增版本，`invalidate_all()` 仅用于演练或立即腾空间。
- 负缓存（空结果）短 TTL 防穿透；TTL 带抖动防雪崩；L1/L2 都有容量上限与 LRU 淘汰。

## 重启状态三分类

| 类别 | 本项目例子 | 处置 |
|---|---|---|
| 可安全丢弃 | 限流滑动窗口 `_WINDOWS`、解析轨迹 deque、SSE 连接 | 重启清零，不产生错误数据 |
| 可从真值重建 | `queue.py` 的 `_active` / `_queued`、进度快照 | 启动扫描 `documents.status IN ('queued','analyzing')` 幂等重投 |
| 必须持久化 | 新鲜度/解析/缓存策略、预算、用量台账、文档、`chunk_embeddings`、`cache_entries`、`runtime_state` | 落 SQLite，启动从 `runtime_state` 恢复 |

## 启动自检 7 项（`run_startup_checks`）

| 检查 | 分类 | 动作 |
|---|---|---|
| `schema_ready` | 必须持久化 | 幂等建 `cache_entries` / `runtime_state` |
| `volatile_state_discarded` | 可安全丢弃 | 声明限流窗口/轨迹清零 |
| `orphan_tasks_recovered` | 可从真值重建 | `queued/analyzing` 任务重投（幂等） |
| `ghost_vectors_reconciled` | 必须持久化 | 删 `chunk_embeddings` 中已无对应 `chunks` 的行；删除文档时源头先删向量 |
| `data_version_consistent` | 必须持久化 | 文档指纹变化则递增 `data_version` |
| `policies_persisted` | 必须持久化 | 打印 grant/policy 版本，策略由适配层恢复 |
| `expired_cache_purged` | 必须持久化 | 清理过期条目 + 容量淘汰 |

`boot_count` 每次启动递增，`last_shutdown_at` 仅在优雅关闭时写入——**强停（SIGKILL）不会写**，
这正是「真值层必须先行、不能依赖关闭钩子」的实证。

## 三条命令

```bash
# 离线单测（含重启模拟）
python -m pytest cache/tests -q

# 运行时 API（需先起服务）
python -m uvicorn webapp.main:app --port 8765
#   admin / admin123 → 侧栏「缓存与恢复」→ 运行缓存演示 / 模拟重启 / 主动失效

# 真·进程重启演练：隔离库启停两次，见 docs/CACHE-DELIVERY.md §3
```

## 诚实边界

- 演示切片用 SQLite + 进程内队列，不是生产 PG / Redis / Celery；L2 落在同一个 SQLite 文件里是演示替身。
- **检索结果缓存只在 `/api/runtime/cache-demo` 演示，`rag.retrieve()` 主链路默认不接缓存；
  答案缓存与语义缓存未做**（依赖 prompt / 生成模型 / 语义相似度，误命中风险更高）。
- 缓存不负责授权：调用方必须**先按权限过滤再写**；命中后仍复核快照，TTL 不是权限控制。
- 阈值（TTL 900s、负缓存 60s、容量 256/5000、抖动 0.10）是演示默认经验起始值，
  **非本项目实测、非推荐配置**，需用真实流量与故障演练校准。
- 未做真实压测、监控告警 SLO、跨缓存失效链路、CDC 近实时增量、权威注册表审批流、质量基线冻结。
- `eval/` 的 `__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径不受缓存影响，缓存命中不得绕过。
