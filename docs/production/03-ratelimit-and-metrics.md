# 03 · 多副本限流与可观测指标（生产化建议）

> **定位**：本文是「生产化第 3 项」的实现说明 + 落地教程，**代码已合入、生产未部署**。
> **三态纪律**：逐条标注 `已实现` / `只设计` / `未做`，不把单机演示说成分布式生产。
> **配套文件**：`webapp/ratelimit_dist.py`（限流抽象与 Redis 实现）、`webapp/main.py::gateway_guard`（接入）、
> `webapp/tests/test_dist_ratelimit.py`（6 项验收）。
> **与 04 的分工**：本文负责「**限流怎么算、指标怎么产生**」；`docs/production/04-monitoring.md` 负责「**指标怎么抓取、告警怎么配**」。指标名以 04 的契约为准，本文不另起名字。

---

## 1. 为什么单机限流不能直接上线

现状 `observability._WINDOWS` 是**进程内滑动窗口**（`deque` + 锁）。单实例没问题，但一旦多副本：

| 问题 | 后果 |
|---|---|
| 各副本独立计数 | 部署 N 个副本，实际限额被放大 **N 倍**；自动扩缩容时限额随副本数漂移 |
| 重启清零 | 发布 / 崩溃重启瞬间计数归零，可被瞬时刷穿 |
| 无共享真值 | 无法回答「全局这个用户这一分钟调了多少次」 |

用户要上线，这一项必须先解决：限流计数要落到**副本共享的存储**（Redis），并且**原子**。

---

## 2. 两种后端（`Limiter` 抽象）

`ratelimit_dist.py` 定义 `Limiter.check(key, limit, window_s, cost=1) -> dict | None`，两套实现：

| 后端 | 适用 | 实现 | 状态 |
|---|---|---|---|
| `MemoryLimiter` | 单机 / 降级 | **复用** `observability._WINDOWS` 的进程内窗口（单一窗口，行为与 `rate_check` 一致） | **已实现** |
| `RedisLimiter` | 多副本 | Redis **zset 滑动窗口**，单次 `EVAL` 原子完成 | **已实现（演示级）** |

> 为什么 `MemoryLimiter` 复用 `_WINDOWS` 而不是自建：单机只应有一份窗口，否则「降级实现」和「单机实现」互相看不见，测试里 `obs._WINDOWS.clear()` 也清不干净，容易出现「两套计数」的错觉。

---

## 3. 为什么 Redis 侧必须用 Lua（单次 EVAL）

天真的多命令写法：

```text
ZREMRANGEBYSCORE key ...   # 清理过期
ZCARD key                  # 读当前数
# ← 并发窗口：另一个请求也读到了旧值
ZADD key ...               # 写入
EXPIRE key ...
```

「读 → 判断 → 写」不是原子的，两个并发请求会同时看到 `current < limit` 而都放行，**限额被超发**。正确做法是把整段放进一段 Lua，由 Redis 单线程原子执行：

```lua
-- KEYS[1]=key  ARGV: now, window, limit, cost, member
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local current = redis.call('ZCARD', key)
if current + cost > limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then retry = math.max(1, math.ceil(window - (now - tonumber(oldest[2])))) end
  return {0, current, retry}
end
for i = 1, cost do redis.call('ZADD', key, now, member .. ':' .. i) end
redis.call('EXPIRE', key, math.ceil(window) + 1)
return {1, current + cost, 0}
```

返回 `{allowed, current, retry_after}`；`retry_after` 由窗口内**最老一条**记录反推，和单机实现的口径一致。

---

## 4. 配置与降级链

| 环境变量 | 取值 | 说明 |
|---|---|---|
| `WEBAPP_REDIS_URL` | 空 / `redis://host:6379/0` | 空 = 单机内存；非空 = 走 Redis |
| `WEBAPP_RATE_BACKEND` | `auto`(默认) / `memory` / `redis` | `auto`：有 URL 用 Redis、否则内存；`memory` 强制单机；`redis` 强制 Redis（不可用则降级） |

**降级链**（`get_limiter()` + `RedisLimiter.check()`）：

```
redis 包未安装            → MemoryLimiter + WARN（available=False）
RedisLimiter 构造失败      → MemoryLimiter + WARN
ping 失败（建连探测）      → MemoryLimiter + WARN
运行中 EVAL 异常/超时      → 该次回退 MemoryLimiter + WARN（每条原因只报一次）
```

**失败降级而不是报错**：限流组件挂掉不能把业务一起打挂。代价是降级期间各副本回到「各算各的」，需要告警（见 04）提醒运维。

---

## 5. 限流维度口径（**不变**）

`RATE_DIMENSIONS = ("user", "endpoint", "model")` 三维是**业务配额本体**；`ip` 是**接入层防刷附加维度**（不进 `RATE_DIMENSIONS`）。`check_rate()` 返回结构与 `observability.rate_check` **完全一致**：

```json
{"dimension","key","path","limit","window_s","retry_after","current","department"}
```

因此 `gateway_guard` 只换一行调用，429 错误体与响应头（`Retry-After` / `X-RateLimit-*`）**一个字段都没变**。

---

## 6. `/metrics` 指标字典

端点：`GET /metrics`，`Content-Type: text/plain; version=0.0.4`（手写 Prometheus 文本，**未引入 `prometheus_client`**）。

| 指标 | 类型 | 含义 |
|---|---|---|
| `kb_http_requests_total{path,status}` | counter | 按归一化路由 + 状态码计数 |
| `kb_http_request_duration_seconds_bucket{le}` | histogram | 延迟分桶 0.05/0.1/0.25/0.5/1/2.5/5/+Inf |
| `kb_rate_limit_hits_total{dimension}` | counter | 各维度限流命中次数 |
| `kb_queue_pending` | gauge | 队列积压（进程内队列替身） |
| `kb_budget_spend_cny` | gauge | 累计成本（演示估算，非账单） |
| `kb_documents_by_status{status}` | gauge | 文档按状态分组 |

**安全与标签治理**：

- **不登录可读**，但支持 `WEBAPP_METRICS_ALLOW_IPS` 白名单（逗号分隔，非白名单 403）。生产应只让 Prometheus 抓，在网关/安全组层面限来源，**不要直接对公网开放**。
- **绝不输出密钥 / 邮箱 / 文件名 / 用户 ID**：指标只有聚合计数与枚举。
- **path 动态段归一**：`/api/docs/abc123 → /api/docs/{id}`（纯 hex≥6 / 纯数字 / UUID），否则每个文档 id 都会变成一个标签，直接打爆 Prometheus。
- `/metrics` 在 `gateway_guard` 里**直通**：不被限流、不占并发令牌、也不记自身指标，避免「抓取越频繁、限流越严重」的反噬。

---

## 7. 落地步骤（教程）

```bash
# 1) 起 Redis（数据卷持久化；生产用托管 Redis / Cluster）
#    走 infra 编排而不是 docker run，是为了和 PG / Milvus 用同一份定义。
#    本机 6379 已被别的项目占用，所以宿主端口用变量覆盖（默认仍是 6379）：
export KB_REDIS_PORT=56379 KB_REDIS_IMAGE=docker.m.daocloud.io/library/redis:7-alpine
docker compose -f docker-compose.infra.yml up -d redis
docker exec kb-redis redis-cli ping        # → PONG

# 2) 让 webapp 指向 Redis（多副本部署时每个副本都配同一个 URL）
export WEBAPP_REDIS_URL=redis://127.0.0.1:56379/0
export WEBAPP_RATE_BACKEND=auto

# 3) 验证「多副本共享同一窗口」——不用起两个副本，用两个独立进程直接打 Limiter：
python scripts/verify_infra.py --skip-pg --redis-url "$WEBAPP_REDIS_URL"
# → limit=3：内存 6 次 vs Redis 3 次，放大倍数 2.0×
# 这一行就是本节的核心结论，详见 §8.1

# 4) 抓指标（实测已通，不再是「代码就绪」）
curl -s http://127.0.0.1:8765/metrics | head
# 期望：Content-Type: text/plain; version=0.0.4，含 6 个指标名

# 5) 降级验证：停掉 Redis，再打接口 —— 业务仍正常，日志出现
#    webapp.ratelimit WARNING "rate limiter degraded: redis unavailable (...)"
docker stop kb-redis
```

---

## 8. 测试证据

```bash
python -m pytest -q webapp/tests/test_dist_ratelimit.py   # 7 passed
python -m pytest -q webapp/tests                          # 134 passed（含既有项零回归）
python webapp/selftest.py                                 # 33/33 DEMO_READY
```

覆盖：默认内存后端 / 窗口内超限与窗口外放行 / Redis 不可用回退 + WARN / `check_rate` 结构兼容 / `/metrics` 形状与无敏感值 / 动态 path 归一 / **`model` 空值不共桶的 HTTP 级回归**。

### 8.1 真 Redis 上的窗口共享实测（2026-11-15）

这一条之前只有内存模拟，现在有真 Redis 的数字。

```bash
$ python scripts/verify_infra.py --skip-pg --redis-url redis://127.0.0.1:56379/0
== Redis（多副本窗口共享）==
  limit=3：内存 6 次 vs Redis 3 次，放大倍数 2.0×
```

**测法**：起**两个独立子进程**，各用同一个 key 打 `limit=3 / window=60s`，每个进程最多试 5 次。

| 后端 | 进程 A 放行 | 进程 B 放行 | 合计 | 说明 |
|---|---:|---:|---:|---|
| `MemoryLimiter` | 3 | 3 | **6** | 各算各的 → 配额被放大 2 倍（两副本） |
| `RedisLimiter` | 3 | 0 | **3** | 共享同一 zset 窗口 → 配额正确 |

**为什么必须用两个独立进程**：同一进程内 `MemoryLimiter` 刻意复用 `observability._WINDOWS`，
两个实例共享同一份窗口，测出来必然是 3 —— 那会得出「内存实现也没问题」的**错误结论**。
真正的放大只发生在**进程之间**，所以测试必须付出进程隔离的代价。

这条断言已经写进脚本的失败判据里：如果内存侧不等于 `limit × 2`，脚本会报
「说明测试没真正隔离进程，结论不可信」而不是「通过」——**防止测试自己变成假绿**。

### 8.2 真实 `/metrics` 输出（逐字节摘自运行中的容器）

```
# TYPE kb_http_requests_total counter
kb_http_requests_total{path="/api/chat/stream",status="200"} 4
kb_http_requests_total{path="/api/docs",status="200"} 14
kb_http_requests_total{path="/api/docs/seed-sample",status="200"} 1
kb_http_requests_total{path="/api/docs/upload",status="200"} 4
kb_http_requests_total{path="/api/docs/{id}",status="404"} 1

# TYPE kb_rate_limit_hits_total counter
kb_rate_limit_hits_total{dimension="user"} 0
kb_rate_limit_hits_total{dimension="endpoint"} 0
kb_rate_limit_hits_total{dimension="model"} 0
kb_rate_limit_hits_total{dimension="ip"} 0

# TYPE kb_documents_by_status gauge
kb_documents_by_status{status="queued"} 0
kb_documents_by_status{status="analyzing"} 0
kb_documents_by_status{status="ready"} 16
kb_documents_by_status{status="error"} 134
```

三个细节值得看：

- **零值样本确实在**：`rate_limit_hits_total` 四个维度全 0 也照样输出。
  如果只在有命中时才输出，Prometheus 里这个指标就是「不存在」，
  `rate(...)` 永远 no data，对应告警**永远不会触发**——这是最典型的一种「装了监控但等于没装」。
- **path 已归一**：我故意打了一次 `GET /api/docs/abc123def`，拿到 404，
  指标里出现的是 `path="/api/docs/{id}",status="404"`，**不是** `abc123def`。
  不归一会让标签基数随文档数增长，打爆 Prometheus。
  顺带说明：归一按「纯 hex ≥6 / 纯数字 / UUID」判定，所以 `seed-sample`、`upload` 这类**字面量段没被误伤**。
- **`error` 134 不是异常**：那是演示库里历史失败文档的累积值（`ready 16 + error 134 = 150`），
  与本次限流改造无关；写文档时不要把它读成「服务在报错」。

---

## 9. 已知边界（**未做 / 只设计**）

1. **`/metrics` 计数是进程内的**：uvicorn 多 worker（`--workers N`）时每个 worker 各自计数，Prometheus 会抓到 N 份不同数字。生产需 `prometheus_client` 的 multiprocess 模式或「单 worker + 前置网关」；当前演示建议 `--workers 1`。
2. **全局并发闸仍是进程内**：`asyncio.Semaphore(MAX_INFLIGHT)` 每副本各一份，N 副本时实际并发上限 ×N。跨副本并发闸需要 Redis 计数或网关层，**未做**。
3. **Redis 部署形态**：只验证了单节点；Cluster / 哨兵 / 连接池调优、`EVAL` 的大 key 治理均**未做**。
4. **令牌桶 / 精确 TPM**：当前是「次数滑动窗口」，模型维度按调用次数近似 RPM；按 token 的 TPM 限流**只设计**。
5. **限流命中台账**仍写本地 SQLite（`rate_hits`），多副本各写各库；生产应汇总到日志/数仓。

> 对外口径：**单机 → 多副本的关键不是「换成 Redis」，而是「把读-改-写变成一次原子操作」，并想清楚降级时会发生什么**。降级回单机是可用性优先的取舍，必须配告警。
