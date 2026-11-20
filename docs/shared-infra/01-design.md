# P5 接入共享基础设施设计

编制：2026-11-20 ｜ 状态：**设计 + 本机模拟已跑通，真接入未做**

> **先说清楚现状**：P1（SlimTrack）/ P2（灵境 CoreAI）/ P4（Agent 商城）已经开始共享
> 同一个 PostgreSQL 实例和同一个 Redis。用户要求 P5 也加进去：「记住你是 P5，
> 然后给自己加独立表，Redis 也要共享，加自己的用户字段 key 标识就行」。
>
> **本机没有那台共享实例的连接串**，所以本文的方案在本机用「三个邻居 schema + 一个
> P5 schema」模拟验证。这是**模拟，不是真的接入了 P1/P2/P4 的库**——面试时不能说成真接入。

---

## 1. 结论先摆出来

| 维度 | P5 的做法 | 不做的事 |
|---|---|---|
| PostgreSQL | 共享实例里用 **P5 专属 schema `p5_kb`** | 不另起 database、不碰 public 里的业务表 |
| DDL | 仍是 `scripts/pg_init.sql` 一份事实源，靠 `search_path` 切到 p5_kb | 不写第二份 P5 专用 DDL |
| Redis | 共用实例，key = `p5kb:{user_id}:{业务段}:...` | 不用全局 key、不靠「先查再写」判重 |
| 权限 | `permission.decide()` 仍是唯一 allow/deny 权威 | 不拿 key 前缀当权限控制 |
| 身份 | P5 用独立 DB role（设计值） | 不共用 superuser 账号上生产 |

---

## 2. 为什么是 schema，不是 database

| 方案 | 收益 | 代价 | 结论 |
|---|---|---|---|
| **独立 schema**（推荐） | 复用同一连接池、同一份 pgvector 扩展、同一套备份；`pgkit.py` 已有 `WEBAPP_DB_SCHEMA` 机制，改环境变量即可 | 隔离强度弱于 database（同一 role 能跨 schema 读） | **选它**：共享实例的价值就是共用基础设施，另起 database 等于放弃这个价值 |
| 独立 database | 天然隔离，role 权限边界清晰 | 每库都要装扩展、单独备份、连接池按库分、跨项目只读表要走 FDW | 放弃：运维成本翻倍，且用户明确说「共享库」 |
| 同 schema 加表前缀 | 最省事 | 表名全变、20 张表的 138 处 SQL 全要改 | 直接排除 |

**关键事实**：pgvector 的 `vector` 类型与 `<=>` 操作符是**库级扩展**，只能装在 public，
装不进业务 schema。所以 P5 的 `search_path` 必须是 `p5_kb,public`——末尾的 public
不是为了「能读别人的表」，是为了能解析扩展类型。这条写进 `shared_infra.py` 的
`search_path_is_valid()`，有单测钉住。

**已经存在的坑（本轮验证时确认）**：池化之后会话级 `SET` 会跨借用人存活。
`pgkit.connect()` 已经做了两件事——建连时 `connect_args={"options": "-c search_path=..."}`
+ 每次 checkout 补一条 `SET` 复核。P5 接共享实例时直接复用这套机制，**不要再自己 `SET` 一次**。

---

## 3. DDL 唯一事实源怎么保持

P5 现有 20 张表全在 public（`users` / `documents` / `chunks` / `chunk_embeddings` /
`audit_logs` / `budgets` / `rate_rules` / `rate_hits` / `cache_entries` / `runtime_state` /
`usage_events` / `eval_*` / `chat_*` / `badcases` / `app_settings` / `safety_events`）。

共享实例里不能让 P5 继续在 public 建表——会撞 P1/P2/P4 的同名表。

**做法**：`scripts/pg_init.sql` 仍是唯一事实源，执行前先切 `search_path`：

```sql
CREATE SCHEMA IF NOT EXISTS p5_kb;
SET search_path TO p5_kb, public;
-- 下面原样执行 scripts/pg_init.sql 的全部 20 张 CREATE TABLE / CREATE INDEX / 种子 INSERT
RESET search_path;
```

演示脚本 `scripts/shared_infra_demo.py` 就是这么做的：它读 `DDL_PATH.read_text()`
然后原样 `cur.execute(ddl)`，**没有复制一份建表语句**。实测在 `p5_kb` 下建出 20 张表，
与 public 同构。

> **未做**：真接入时，`scripts/pg_init.sql` 需要支持「按环境变量选择目标 schema」
> 而不只是靠执行前 SET。目前演示脚本能跑通，但生产化的参数化还没做。

---

## 4. Redis key 命名空间

四个项目共用一个 Redis 实例（甚至同一个 db），只靠 TTL 或「先查再写」判重一定会撞车。

### 4.1 key 模板

```
p5kb:{user_id}:{business}:{rest...}
```

- `p5kb`：**项目前缀**，与邻居的 `p1slim` / `p2core` / `p4mall` 区分
- `{user_id}`：**用户字段**——用户明确要求「加自己的用户字段 key 标识」
- `{business}`：业务段白名单 `rl` / `cache` / `state` / `queue` / `budget`
- 示例：`p5kb:alice:rl:user`、`p5kb:alice:cache:q1`

### 4.2 为什么用户段不能省

只写 `p5kb:rl:user` 看起来也是三段，但它把业务段 `rl` 当成了用户——
**alice 和 bob 会写进同一个 key**，限流窗口互相串。

所以 `shared_infra.is_p5_key()` 不是只数段数，而是要求**第三段必须命中业务白名单**。
实测：`p5kb:alice:rl:user` → True；`p5kb:rl:user` → False。

### 4.3 现有 key 怎么办

| 位置 | 现状 | 接共享实例时 |
|---|---|---|
| `webapp/ratelimit_dist.py:214/228` | `kb:rl:{dim}:{key}` | 改为 `p5kb:{user_id}:rl:{dim}`，并保留 dim 作为末段 |
| `cache/keys.py` 的 13 段 key | 首段 `schema=v1`，`org=org` | 前置 `p5kb:{user_id}`，**原有 13 段全部保留** |
| `cache/contracts.py:15` | `ORG_NAMESPACE="org"`（单组织静态） | 不建议改它——它是业务语义；项目隔离放在 key 外层 |

**硬约束提醒**：缓存 key 里已有 authz / data_version / policy 段，
**TTL 不是权限控制**。加项目前缀不能替代 `permission.decide()`，也不能替代
「先过滤→再重排→再进模型」。

### 4.4 为什么不能「先查再写」

共享 Redis 下，`if key exists: skip else: write` 是典型的 TOCTOU：
两个 P5 进程同时判断「不存在」，然后都写，后写的覆盖先写的。
项目前缀 + 用户段必须在**构造 key 的那一刻**就带上，不能事后补。

---

## 5. 身份与越界（只设计，未实施）

演示脚本用的还是本机 `kb` 这个 superuser role，所以「跨 schema 读不到」
靠的是 **search_path 命名空间隔离**，不是数据库权限隔离——
显式写 `SELECT * FROM p1_slimtrack.demo_docs` 依然能读到。

真接入共享实例必须补 DB role 最小权限（以下是设计值，**未执行**）：

```sql
-- P5 专用角色：只能连库、只能用 p5_kb、只能读 public 的扩展对象
CREATE ROLE p5_app LOGIN PASSWORD '<由密钥管理注入，不进仓库>';
GRANT CONNECT ON DATABASE kb TO p5_app;
GRANT USAGE ON SCHEMA p5_kb TO p5_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA p5_kb TO p5_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA p5_kb TO p5_app;
-- public 只给扩展的 USAGE，不给业务表
GRANT USAGE ON SCHEMA public TO p5_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM p5_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA p5_kb
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO p5_app;
```

> 注意：这段 SQL 里有 `REVOKE ALL ON ALL TABLES IN SCHEMA public`——
> 在共享实例上对 public 动手会影响别的项目的验证脚本，**真接入前要先和
> P1/P2/P4 的负责人确认 public 里有没有他们的共享只读表**。这是开放问题。

---

## 6. 可运行演示

### 6.1 跑法与实测输出

```bash
python scripts/shared_infra_demo.py
```

2026-11-20 本机实测（**模拟**，非真接入）：

| 指标 | 结果 |
|---|---|
| `p1_slimtrack` / `p2_coreai` / `p4_agentmall` 演示行数 | 2 / 3 / 4（故意不同） |
| P5 在 `p5_kb` 下可见表数 | **20**（与 public 同构，共用一份 DDL） |
| 裸表名 `SELECT ... FROM demo_docs` | **被挡住**，错误 `UndefinedTable` |
| Redis 各项目 key 数 | 每项目 2（alice + bob），共 8 |
| P5 key 与邻居 key 交集 | **0** |
| 弱 key `p5kb:rl:user` | **拒绝** |
| verdict | `SHARED_INFRA_DEMO_OK` |

报告落在 `scripts/out/shared_infra_demo.json`，脚本幂等：开头与结尾各清一次自己的
schema 和 key，**不碰 public 业务表、不碰别人的 key**。

### 6.2 验收判据（每条都能复跑）

1. P5 的 search_path 首位是 `p5_kb`、末位是 `public` → 单测 `test_search_path_has_own_schema_then_public`
2. P5 key 必须含项目前缀 + 真实用户段 + 已知业务段 → `test_weak_key_without_real_user_is_rejected`
3. 邻居 key 不被识别为 P5 key → `test_neighbor_key_is_not_p5_key`
4. 裸表名读邻居表失败 → 演示脚本 `foreign_bare_lookup.blocked = true`
5. P5 与邻居 key 交集为 0 → 演示脚本 `p5_neighbor_intersection = 0`
6. 全量回归不破：新增 17 项单测，**原有 271 项仍全绿**

---

## 7. 三态表

| 状态 | 项 |
|---|---|
| **已实现** | `shared_infra.py` 的 key 构造 / 弱 key 拒绝 / search_path 校验 / 越界 schema 判定；`scripts/shared_infra_demo.py` 本机多 schema + 多 key 前缀模拟；17 项契约单测 |
| **只设计** | DB role 最小权限 SQL、真接入时的 DDL 参数化、缓存 13 段 key 前置 `p5kb:{user_id}`、限流 key 迁移 |
| **未做** | **真接入 P1/P2/P4 那台共享实例**（没有连接串）、邻居项目配合改 key 前缀、跨项目只读表清单确认、共享实例的备份与恢复演练 |

---

## 8. 面试口径

> 「我的项目里 P1/P2/P4 共用一个 PG 和 Redis，P5 要加进去。我选 schema 隔离而不是
> 另起 database——共享实例的价值就是共用连接池、扩展和备份。Redis 那边所有 key
> 统一加 `p5kb:{user_id}:` 前缀，项目段和用户段缺一不可：只写项目段会跨用户串
> 限流窗口，只写用户段会撞别的项目。我踩过一个具体的坑——`p5kb:rl:user`
> 看着是三段，其实是把业务段当用户了，所以校验必须要求第三段命中业务白名单。
> 本机我用三个邻居 schema 模拟跑通了，但**真接入那台共享实例还没做**，
> 因为我没有那边的连接串；DB role 最小权限也只写了设计没执行。」

**不要说**：「我们已经接入了共享集群」——本机演示是模拟，说了被追问连接串就穿帮。
