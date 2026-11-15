# 02 · 压测与容量计划

> 配套脚本：`scripts/loadtest.py`（纯标准库，已实现）。
> 适用对象：`webapp/` 演示切片（FastAPI + SQLite + 进程内队列 + 限流：**默认进程内，配 Redis 可多副本共享**）。
> **实现版本说明**：`webapp/main.py` 与 `webapp/ratelimit_dist.py` 在本轮有**在途改动**（网关指标 `/metrics` + 分布式限流）。本文按**当前工作区实现**描述，一律引用**常量名 / 函数名**而非行号（行号会随改动漂移）；§8 的实测是在**改动前的运行进程**上完成的 —— 改造后维度顺序与 key 构造已逐条核对一致，故结论不变，但**阈值标定须在最终版本复测**。
> **三态**：脚本 **已实现**；本机实测数据仅为**演示机连通性抽样**；**真实容量结论未做**——没有压测环境、没有真实模型链路、没有生产硬件，本文不编造任何 QPS / 延迟数字。

---

## 1. 压测目标：回答三个问题

刷高 QPS 没有意义。压测只为回答：

1. **单机能扛多少并发**——在 P99 不失控的前提下，最多同时服务多少请求。
2. **瓶颈在哪个组件**——应用层（限流/并发闸）、SQLite、模型调用、还是上传队列。
3. **阈值该定多少**——`MAX_INFLIGHT`、IP 规则、三维配额分别设成多少才既挡得住刷、又不误伤正常用户。

回答完这三个问题，才谈「需要几台机器」。

---

## 2. 指标口径

### 2.1 五类指标

| 指标 | 定义 | 为什么看它 |
|---|---|---|
| QPS | 单位时间完成的请求数。**必须区分「总 QPS」与「成功 QPS」** | 总 QPS 含被 429 拒绝的请求，虚高；容量只看成功 QPS |
| P50 / P95 / P99 | 延迟分位数（nearest-rank） | 决定体验的是长尾，不是平均 |
| 错误率 | `(4xx其他 + 5xx + 连接错误) / 总请求` | 注意 429/503 单列，不混进错误率 |
| 429 率 | 限流命中占比 | 429 是**限流生效**，不是故障 |
| 503 率 | 并发闸打满占比 | 503 才是**容量真正到顶** |

### 2.2 为什么平均延迟没有意义

假设 100 个请求里 95 个 10ms、5 个 5 秒：平均值 ≈ 260ms，看起来「还行」，但每 20 个用户就有 1 个卡 5 秒。
平均值把长尾摊平了，而长尾正是用户投诉的来源。**看 P95 / P99，并盯着 P99 随并发上升的斜率**：斜率陡 = 有排队或锁竞争。

### 2.3 SSE 的「延迟」要拆成两个数

`POST /api/chat/stream` 返回的是流（事件 `meta` → 多个 `data:` → `done`，中间还有 `: ping` 心跳）。对一个流式请求：

- **TTFT（Time To First Token）**：从发请求到收到**第一个 `data:`**。用户感知的「反应快不快」由它决定。
- **总时长**：到 `done` 为止。决定并发槽占用多久（`gateway_guard` 要等响应体发完才释放令牌）。

两者会差一两个数量级。把 SSE 的「延迟」当成一个数，等于把「首字延迟」和「整段生成完」混为一谈，两边的优化手段完全不同。
脚本用 `--sse` 分别记录：`latency_ms` 记总时长，`ttft_ms` 记首 token。

---

## 3. 场景设计

### 场景 ①：只读接口（`GET /api/docs`）

- **命令**：`python scripts/loadtest.py --concurrency 10 --duration 70`
- **预期瓶颈**：应用层限流。未登录返回 401，但**限流在鉴权之前**，请求已走完网关，压测有效。
- **看什么**：成功 QPS 的上限被哪条规则卡住（看 `429 命中维度`）。

### 场景 ②：登录 + 问答（SSE）

- **命令**：`python scripts/loadtest.py --method POST --path /api/chat/stream --body '{"question":"配网步骤"}' --sse --token "$TK" --concurrency 4 --duration 70`
- **预期瓶颈**：并发闸（每个 SSE 长时间占一个令牌）+ 模型调用耗时。默认 `MAX_INFLIGHT=32`、等令牌超时 2s。
- **看什么**：TTFT 的 P95；503 是否出现；总量受模型链路影响远大于受 CPU 影响。

### 场景 ③：上传 + 解析（并发 2 会排队）

- **命令**：并发上传 `POST /api/docs/upload`（脚本用 `--method POST --path /api/docs/upload` 只能压「接口本身」，multipart 需另写或用页面操作）
- **预期瓶颈**：`webapp/queue.py::MAX_CONCURRENT_INGEST = 2` —— **只有 2 个分析线程**，第 3 个起**排队而不是报错**。
- **看什么**：不是看 429/503，而是看**延迟随并发线性上升**，以及 `GET /api/system/busy` 的 `queue_count` 增长。这是「容量到顶但没报错」的典型形态。

### 场景 ④：混合比例

- 按真实流量配比（例如 70% 只读 / 20% 问答 / 10% 上传）跑，脚本不支持多路径混合，需串行多轮或写上层编排。
- **预期瓶颈**：往往先卡在限流（因为各维度共享同一个 60s 窗口），再卡在队列。

### 场景对照

| 场景 | 主要压力点 | 首个瓶颈信号 | 降级形态 |
|---|---|---|---|
| ① 只读 | 网关限流 | 429（`model` 或 `ip` 维度） | 快速拒绝，无排队 |
| ② SSE 问答 | 并发闸 + 模型 | 503 `SERVER_BUSY` | 2s 后快速失败 |
| ③ 上传解析 | 队列并发 2 | 延迟线性上升、`queue_count` 涨 | 排队 |
| ④ 混合 | 三者叠加 | 先 429 后 503 | 混合 |

---

## 4. 怎么读结果

| 现象 | 含义 | 该做什么 |
|---|---|---|
| 出现 **429** | 限流生效，**不是故障** | 记录「在 X 并发触发」，算该维度上限 = `limit / window` |
| 出现 **503** | 并发闸打满（`MAX_INFLIGHT` 全占，等令牌超 2s） | 提高 `MAX_INFLIGHT` 或降低单请求耗时；看 P99 是否同步恶化 |
| **P99 远高于 P50**（>5×） | 有排队、慢查询或锁竞争 | 查 SQLite 锁、`_rate_lock` 争用、队列排队 |
| **错误率上升但成功 QPS 不涨** | 已到瓶颈 | 停止加压，这就是当前容量 |
| **401 很多但 0 个 429/503** | 链路完全正常，只是没登录 | 可继续加压 |
| 5xx 出现 | 应用 bug 或依赖故障 | 先修 bug，再谈容量 |
| **客户端统计与 `/metrics` 不一致** | 客户端只看到自己这一侧；服务端可能在网关层已拒绝或仍在排队 | **压测时同时抓 `GET /metrics` 对拍**（见下） |

> **压测时同时抓 `/metrics`，比只看客户端统计更准**。后端已加 Prometheus 文本端点（`main.py` 的 `@app.get("/metrics", include_in_schema=False)`），暴露：
> - `kb_http_requests_total{path,status}` —— **服务端真实**的请求数与状态分布（客户端可能漏计被网关直接拒绝的请求）；
> - `kb_http_request_duration_seconds` 直方图（bucket / sum / count）；
> - `kb_rate_limit_hits_total{dimension}` —— 按维度统计的 429 命中；
> - `kb_queue_pending` —— 上传分析排队数（判断 `MAX_CONCURRENT_INGEST=2` 是否成为瓶颈）；
> - `kb_budget_spend_cny`、`kb_documents_by_status`。
>
> **用法**：压测前抓一次、结束后再抓一次，两次相减即为本次压测的服务端增量；把 `kb_http_requests_total` 里的 429 与脚本的 429 数对拍，能立刻发现「客户端漏计」。
> ⚠️ **现状**：端点代码已就绪（`test_dist_ratelimit.py` 已有 `/metrics` 与 path 归一化测试），但**当前 8765 运行进程是改造前版本，实测 `/metrics` 返回 404**，需重启服务后生效。另注意 `/metrics` 本身直通（不限流、不占并发），**不要拿它当业务路径压测**。

### 4.1 怎么定位「修了但没生效」

压测里遇到「明明改了策略、行为却没变」，按这个顺序查：

1. **先确认默认路径调的是哪个实现**：`grep -n "rate_check\|check_rate" webapp/main.py`。本次事故里 `main.py` 已切到 `ratelimit_dist.check_rate`，而修复只加在 `observability.rate_check`。
2. **确认进程加载的是新代码**：改完不重启，新端点会 404、行为仍是旧的（本轮实测就踩到：`/metrics` 代码就绪但进程 404）。
3. **覆盖真实调用链再断言**：函数级单测只能证明「函数对」，证明不了「默认路径调的是这个函数」。必须有一条走 HTTP 的回归。
4. **改完立刻复跑同参数压测**：看 429 的 `dimension` 是否真的从 `model` 消失。

> 真实案例：`model` 空跳过判断先在 `observability.rate_check` 修，函数单测全绿，但 `main.gateway_guard` 调的是 `ratelimit_dist.check_rate`，默认路径仍共桶；补上第二处 + HTTP 级回归 `test_http_path_does_not_share_model_bucket` 后才真正生效（7 passed）。**结论：限流这类「多实现并存」的逻辑，修完必须确认默认路径 + 加 HTTP 断言 + 重启后复测。**

---

## 5. 容量估算方法

### 5.1 公式

```
所需实例数 ≈ 峰值 QPS ÷ 单实例安全 QPS ，向上取整
单实例安全 QPS = 实测稳态成功 QPS × (50% ~ 60%)
```

取 50–60% 是因为压测是理想条件（单机、无网络抖动、无其它业务），生产要留余量。

### 5.2 假设算例（**假设，不是实测**）

| 输入 | 值 | 说明 |
|---|---|---|
| 日活 | 500 | 假设 |
| 峰值 QPS | 20 | 假设（按日活 × 人均请求数 / 峰值集中系数估） |
| 单实例实测安全 QPS | 30 | **假设值**，须用你的机器实测替换 |
| 计算 | 20 ÷ 30 = 0.67 → 1 | 向上取整 |
| 结论 | 1 实例 + 1 冗余 = **2 台** | 冗余用于滚动发布与故障切换 |

> ⚠️ 这个算例**不能直接用**，有两层原因：
> ① **「单实例安全 QPS 30」是假设值**，必须用你自己机器 `--duration 70` 的实测值替换；
> ② **model 共桶已修**（见 §6.3，两条路径均覆盖 + HTTP 级回归）：非 chat 接口不再受 `model` 维度约束。但**同 IP 压测时 `ip` 600/60s = 10 QPS 仍是最先到顶的一条**——容量换算要按实际出口 IP 数量折算，单 IP 单机跑不出更高。

### 5.3 容量表模板（留给实测填写）

| 场景 | 并发 | 成功 QPS | P95 | P99 | 429 率 | 503 率 | 结论 |
|---|---|---|---|---|---|---|---|
| ① 只读 | — | — | — | — | — | — | 待填 |
| ② SSE | — | — | — | — | — | — | 待填 |
| ③ 上传 | — | — | — | — | — | — | 待填 |
| ④ 混合 | — | — | — | — | — | — | 待填 |

---

## 6. 怎么调阈值

### 6.1 当前阈值清单（实读代码 + 实读 DB，非推测）

| 项 | 值 | 来源 | 性质 |
|---|---|---|---|
| `user` 维度 | 1200 次 / 60s | `rate_rules` 表（`webapp/db.py` 默认种子） | 演示起始值 |
| `endpoint` 维度 | 900 次 / 60s | 同上 | 演示起始值 |
| `model` 维度 | 600 次 / 60s | 同上 | 演示起始值 |
| `ip` 维度（附加） | 600 次 / 60s | 同上 | 演示起始值 |
| `MAX_INFLIGHT` | 32 | `webapp/main.py` 常量 `MAX_INFLIGHT`（env `WEBAPP_MAX_INFLIGHT`） | 演示起始值 |
| 等令牌超时 | 2.0s | `main.py` 常量 `INFLIGHT_ACQUIRE_TIMEOUT` | 常量（写死） |
| 请求体上限 | 10 MiB | `main.py` 常量 `MAX_BODY_BYTES`（env `WEBAPP_MAX_BODY_BYTES`） | 演示起始值 |
| 分析并发 | 2 | `webapp/queue.py` 常量 `MAX_CONCURRENT_INGEST` | 演示起始值 |
| 可信代理 | `127.0.0.1,::1` | `main.py` 常量 `TRUSTED_PROXY_IPS`（env `WEBAPP_TRUSTED_PROXY_IPS`） | 部署相关 |
| 限流后端 | 默认 `memory`（进程内窗口） | `webapp/ratelimit_dist.py::get_limiter`（env `WEBAPP_RATE_BACKEND` = `auto`/`memory`/`redis`；`WEBAPP_REDIS_URL` 为空即内存） | 演示级；配 Redis 走 zset + 单次 Lua 原子，故障自动降级内存并 WARN 一次 |

**检查顺序**（`gateway_guard` / `_apply_guards`）：请求体 413 → 限流 429 → 并发闸 503。限流内部顺序：`user` → `endpoint` → `model` → `ip`，命中即返回。
**`/metrics` 直通**：`/metrics` 与静态资源不经过限流与并发闸、也不记自身指标（避免抓取被反噬）——因此**不能拿 `/metrics` 的结果当容量指标**，也不要用它压业务路径。

### 6.2 `MAX_INFLIGHT`：从 32 往上试

- 从 32 起，每次 ×2（32 → 64 → 128），同时观察 **P99** 与 **503 率**。
- 若 503 消失但 P99 暴涨 → 瓶颈已转移到下游（SQLite / 模型），**再提高只会让队列更长**，不是扩容解。
- 若 P99 平稳且 503 为 0 → 可继续加，直到 P99 出现拐点，取拐点前的 50–60%。

### 6.3 IP 规则与 `model` 维度共桶（实测发现 → 已修，但**未落到默认路径**）

- **`ip` 600/60s 对办公网 NAT 偏紧**：一个办公室出口可能几百人共享一个公网 IP，600/min 会被正常用户打满。上线前应确认出口 IP 数量，必要时按「登录用户维度」为主、IP 只做匿名兜底并放宽。
- **`model` 维度共桶：为什么原来会出问题**（这段保留，教学价值最高）：
  `gateway_guard` 只给 `/api/settings` 和 `/api/chat/*` 填 `model`，其余接口 `model=""` → `rate_check` 的 key 退化为 `"-"`；而 `model` 维度在 `endpoint` **之前**判定。结果：**全站所有非 chat 请求共享同一个 `600/60s` 桶 = 10 QPS 全局上限**。本机实测 4 并发 3 秒即在 `model` 维度触发 429（见 §8）。这不是「阈值调小一点」能解决的，因为语义上这些请求根本不该进 model 维度。
- **已修（2026-11-15）**：`webapp/observability.py::rate_check` 增加 `if dim == "model" and not model: continue`（该函数内第 265–266 行），**model 为空时跳过该维度**——修的是语义，不是阈值。主理人实测：model 规则设 1 次/60s 后，非 chat `/api/docs` 连打 4 次全部放行；chat `/api/chat/stream` 第 2 次命中 model（语义正确）；回归 133 passed。
- **修复的两次迭代（过程本身就是最好的素材）**：
  1. **第一次修（只修了一处 → 没生效）**：`webapp/observability.py::rate_check` 加了跳过判断，但 `main.py` 已改为调用 `webapp/ratelimit_dist.py::check_rate`（**默认路径**），那条路径没有该判断。独立复核（预填 model 窗口 700 条后调用，不改 DB、不改文件）：
     ```
     dist.check_rate(model='') -> {'dimension': 'model', 'key': '-', 'current': 700, ...}   # 仍共桶
     obs.rate_check(model='')  -> None                                                     # 已跳过
     ```
     即「修了但没生效」——**只在一个实现里修**是最常见的漏法。
  2. **第二次修（两条路径均覆盖）**：`ratelimit_dist.check_rate` 的维度循环补上同一判断（注释写明「与单机实现保持一致，否则非 chat 请求挤进同一个 10 QPS 桶」），并新增 **HTTP 级回归** `webapp/tests/test_dist_ratelimit.py::test_http_path_does_not_share_model_bucket`：把 model 规则调成 `1/60s` 后，`GET /api/docs` 连打 3 次断言无 429（经 `gateway_guard` → distributed），再 `POST /api/chat/stream` 两次断言第二次 `429 dimension=model`。实跑 **7 passed**。
- **为什么必须有 HTTP 级回归**：直接调 `rate_check()` 只覆盖函数本身，覆盖不到「`main.py` 实际调的是哪一个实现」；这次就是**函数单测全绿、默认路径仍错**的例子。改限流逻辑后必须沿真实调用链（HTTP）断言一次。

### 6.4 正确顺序

```
先压测（拿到真实拐点）→ 再定阈值（留 40–50% 余量）→ 再写进配置/DB → 再复测确认
```

不要「先拍一个阈值再压测验证」——那只是验证自己的猜测。

---

## 7. 教程：从起服务到填容量表

```bash
# 0) 起服务（项目根）
bash run-demo.sh          # 或 python -m uvicorn webapp.main:app --port 8765

# 1) 确认服务活着
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/api/docs   # 401 属正常

# 2) 小并发基线（限速，确保不触发限流）
python scripts/loadtest.py --concurrency 4 --duration 10 --qps 5

# 3) 不限速，逐步加并发，记录拐点
python scripts/loadtest.py --concurrency 4  --duration 70 --json .data/lt-c4.json
python scripts/loadtest.py --concurrency 16 --duration 70 --json .data/lt-c16.json
python scripts/loadtest.py --concurrency 64 --duration 70 --json .data/lt-c64.json

# 4) 带登录态压问答（SSE，看 TTFT 与 503）
TK=$(curl -s -X POST http://127.0.0.1:8765/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"admin123"}' | python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
python scripts/loadtest.py --method POST --path /api/chat/stream \
  --body '{"question":"配网步骤"}' --sse --token "$TK" \
  --concurrency 4 --duration 70 --json .data/lt-sse.json

# 5) 两次结果对比（读 JSON里的 qps.ok 与 latency_ms.p99）
```

**硬性建议：测持续容量必须 `--duration ≥ 70`（覆盖一个完整限流窗口）。** 限流窗口是 60s，`--duration` 小于 60s 时窗口允许你把限额**一次性打完**，得到的是「爆发吞吐」而不是「持续容量」，会明显高估——本轮实测就踩到了：3 秒跑出 189.8 成功 QPS，而持续上限只有 10 QPS。脚本在 `duration < 60` 且出现 429 时会自动打印该提示。**任何写进容量表（§5.3）的数字，都必须来自 `--duration ≥ 70` 的运行。**

**记录拐点**：把每次的 `并发 / 成功 QPS / P95 / P99 / 429 率 / 503 率` 填进 §5.3 表。成功 QPS 不再随并发增长的那一点，就是单实例容量。

---

## 8. 本机实测记录（演示机抽样，**不是容量结论**）

环境：Windows / Python 3.13 / uvicorn 单进程 / `PARSING_OFFLINE=1` / 目标 `GET /api/docs`（匿名 401）/ 服务与本机同机。
**实测时点**：服务进程运行的是本轮改造**之前**的 `main.py`（`observability.rate_check` 单机窗口）。改造后的 `ratelimit_dist.check_rate` 的维度顺序、`model` key 退化规则（`model or "-"`）、命中返回结构已逐条实读核对一致，故下面关于 `model` 维度退化的结论不变；但正式阈值标定请在最终版本上复测。

**A. 限速基线（`--concurrency 4 --duration 3 --qps 5`）**

```
总请求 16  成功(含 401/403) 16  429 0  503 0  4xx(其他) 0  5xx 0  连接错误 0
实际总 QPS 4.4   成功 QPS 4.4   P50/P95/P99 = 2.7 / 20.8 / 20.8 ms
结论 [NO_LIMIT_HIT]: 未触发限流，可继续加压
```

**B. 不限速（`--concurrency 4 --duration 3`）**

```
总请求 687  成功(含 401/403) 584  429 103  503 0  4xx(其他) 0  5xx 0  连接错误 0
实际总 QPS 223.3   成功 QPS 189.8   P50/P95/P99 = 4.3 / 77.5 / 88.4 ms
429 命中维度 [{'dimension': 'model', 'limit': '600', 'window_s': '60', 'count': 103}]
结论 [RATE_LIMITED]: 在 4 并发触发 429（model 维度 600 次 / 60s，该维度单 key 上限约 10.0 QPS）
```

**怎么读这两组数（重要）**：

1. **429 命中的是 `model` 维度而不是 `ip`**：`/api/docs` 不填 `model` → key 退化为 `"-"`，与全站非 chat 请求共享 `600/60s`。这印证了 §6.3。
2. **「成功 QPS 189.8」是 3 秒爆发值，不代表持续容量**：3 秒内把 60s 窗口的 600 个额度一口气打完，属爆发吞吐；持续容量受 `600/60s = 10 QPS` 约束。**测持续容量必须 `--duration ≥ 70`**（见 §7 硬性建议），不要把 189.8 写进容量表。
3. **503 为 0**：4 并发远未触及 `MAX_INFLIGHT=32`；要压出 503 需要更高并发或长耗时请求（SSE / 上传）。
4. **5xx 为 0、连接错误 0**：单进程 uvicorn 在 200+ QPS 的短时 401 请求下稳定。

> 以上是**同一台演示机、3 秒短时**的抽样，只证明「脚本可用 + 网关行为符合设计」，**不构成任何容量承诺**。真实结论请在你的目标机器上用 `--duration 70` 复测并填 §5.3。

---

## 9. 三态与边界

| 项 | 状态 |
|---|---|
| `scripts/loadtest.py`（本压测器） | **已实现**：并发/时长/限速/Token/JSON/SSE-TTFT，纯标准库 |
| 网关限流、并发闸、请求体上限的**代码行为** | **已实现**（`webapp/main.py::gateway_guard`） |
| 阈值**标定** | **未做**：上表所有阈值都是演示起始值，没有压测标定过 |
| `model` 空跳过（防共桶）修复 | **已完成**（2026-11-15）：`observability.rate_check` 与 `ratelimit_dist.check_rate` **两条路径均已覆盖**，并有 HTTP 级回归 `test_http_path_does_not_share_model_bucket`（7 passed） |
| 真实容量结论 / QPS / P99 基线 | **未做**：无压测环境、无真实模型链路、无生产硬件；本文不写任何编造数字 |
| Redis 分布式限流（多副本共享计数） | **已实现（演示级）**：`webapp/ratelimit_dist.py`，默认内存；配 `WEBAPP_REDIS_URL` 走 zset + 单次 Lua 原子；Redis 故障自动降级内存并 WARN 一次 |
| 网关层限流 / 令牌桶 / 全局限流 | **未做**（属生产化后续项） |
| `/metrics`（Prometheus 文本） | **在途**：`main.py` 已加路由与直通逻辑，但当前运行进程为改造前版本（实测 `/metrics` 返回 404）；重启后生效，压测勿把它当业务路径 |
| 数据库（SQLite）并发写压测 | **未做**：本项目 SQLite 是演示替身，生产为 PG |
| 真实模型调用耗时 | **未做**：本轮离线，`external_request_count=0` |

**边界提醒**：
- 本脚本压的是**应用层网关 + 目标端点**，不压数据库、不压模型、不压网络。
- 压测会在 `rate_hits` 表留下命中记录（可观测用途），属预期行为。
- 长时间高并发可能耗尽本机临时端口；出现大量「连接错误」时先降并发，别误判为服务故障。

---

## 附录 A：脚本参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--url` | `http://127.0.0.1:8765` | 目标根地址（支持 https） |
| `--path` | `/api/docs` | 压测路径（可含 query） |
| `--method` | `GET` | HTTP 方法 |
| `--body` | 无 | 请求体字符串（POST 用） |
| `--token` | 无 | Bearer token；不带则匿名（401 仍计入成功） |
| `--concurrency` | 10 | 并发线程数 |
| `--duration` | 10 | 持续秒数（测稳态建议 ≥70） |
| `--qps` | 0（不限速） | 全局限速，用于测「不触发限流」的基线 |
| `--timeout` | 10 | 单请求超时秒数 |
| `--sse` | 关 | 按 SSE 读流，另记 TTFT |
| `--json` | 无 | 输出结构化 JSON，便于两次对比 |
| `--label` | 空 | 本次标签 |

**退出码**：`0` 正常；`2` 参数错误；`3` 全部连接失败（服务没起）。
