# 04 · 监控与告警（生产化建议）

> **定位**：本文是「生产化第 4 项」的建议方案 + 落地教程，**不是已部署证明**。
> **三态纪律**：全文逐条标注 `已实现` / `未部署` / `未做`，不把设计说成落地。
> **配套文件**：告警规则在 [`monitoring/alerts.yml`](./monitoring/alerts.yml)（Prometheus rule 格式，8 条）。
> **指标契约**：后端本轮新增 `GET /metrics`（Prometheus 文本格式），输出下列 6 个指标；**本文不另起名字**，一律沿用：
> `kb_http_requests_total{path,status}`、`kb_http_request_duration_seconds_bucket{le}`、`kb_rate_limit_hits_total{dimension}`、`kb_queue_pending`、`kb_budget_spend_cny`、`kb_documents_by_status{status}`。
> **实测核对说明**：`GET /metrics` **已合入**（`webapp/main.py`），2026-11-15 实测返回 Prometheus 文本格式（`Content-Type: text/plain; version=0.0.4`），**指标名与本文清单一致**（`path` 为归一化后的路由，不是原始 URL）。首次核对时暂缺的 `kb_rate_limit_hits_total`、`kb_documents_by_status` 其后已补齐，**当前 6 个指标齐全**。校验方法：`curl -s http://127.0.0.1:<端口>/metrics | head`，**以实测输出为准**。

---

## 1. 为什么需要监控（先讲清楚再上工具）

监控不是为了「好看的大屏」，而是回答三个具体问题：

1. **现在坏没坏？** —— 用户是否已经受影响（错误率、延迟、可用性）。
2. **快坏了吗？** —— 有没有趋势性恶化（队列积压、预算见底、证书到期）。
3. **坏了怎么定位？** —— 是哪个接口、哪一层、什么原因（按 path/status 下钻、日志、追踪）。

没有监控时，故障通常由用户投诉发现；有了监控，才能在「用户感知前」收到告警。但也要记住 §6 的告警疲劳：**告警太多等于没有告警**。

---

## 2. 三层可观测：各自回答什么问题

| 层 | 回答什么问题 | 典型数据 | 本项目状态 |
|---|---|---|---|
| **指标 Metrics** | 「整体健康度如何？趋势如何？」——聚合数字，便宜、可长期存储、适合告警 | 请求数/错误率/延迟分位/队列长度/预算消耗 | <span>**已实现（后端本轮新增 `/metrics`）**</span>；Prometheus/Grafana **未部署** |
| **日志 Logs** | 「这一次请求到底发生了什么？」——离散事件，带上下文与堆栈 | 应用日志、访问日志、审计事件 | <span>**已实现到「结构化 + 可串联」**</span>：stdout 单行 JSON + 每请求 `trace_id` 贯穿 + 容器日志上限 10MB×3；`audit_logs` 审计只追加。**未做**集中采集/检索（未接 CLS/Loki）、日志留存策略、按 `user_id` 归因 |
| **追踪 Traces** | 「一次调用跨了哪些服务、慢在哪一跳？」——请求级因果链 | trace_id + span 树 | <span>**未做**</span>：只有 `trace_id` 贯穿与日志关联，**没有 span 采集与可视化** |

**怎么配合用**：指标告警先响（便宜、全局）→ 用 `trace_id` 或时间窗去日志里找那条请求 → 需要跨服务定位时才有必要上追踪。**顺序不要反过来**：先上追踪再补指标，通常投入大、收益慢。

> 本项目的连接点：`webapp/main.py::access_log` 中间件给**每个响应**回 `X-Trace-Id`
> （客户端传来的合法 id 会沿用），同一个 id 同时出现在响应头、错误体和访问日志里。
> 这已经能支撑「用户截图 → 按 trace_id 捞日志 → 看到 path/status/latency」的人工串联，
> 是上追踪之前的最低成本方案。细节见 §2.1。

---

## 2.1 日志落地记录（2026-11-16）

上线第一件该做的不是「上采集平台」，是**把日志写成能被机器读的形式**。顺序反了的话，
采集平台收到的还是纯文本，白干。所以先做四件事，全部已实现：

| # | 做了什么 | 为什么必须 |
|---|---|---|
| 1 | **容器日志上限** `max-size=10m` / `max-file=3` | `docker-compose.yml` 里的 `x-logging` 锚点只对 compose 起的服务生效；`run-demo.sh` 用 `docker run`，实测 `docker inspect` 的 `LogConfig.Config` 是空的 = **json-file 默认无上限**。这条路径上的日志会一直长到把磁盘写满 |
| 2 | **每请求 `trace_id`** | 原来只在错误分支 `uuid4()`。成功请求没有 id → 用户说「刚才那题答错了」时无从定位 |
| 3 | **stdout 单行 JSON**（`webapp/logging_setup.py`） | 字段固定 `ts/level/logger/msg/trace_id` + 白名单扩展（`method/path/status/latency_ms/client/user_id/...`）。堆栈压成单行，否则「一行一 JSON」的约定就破了 |
| 4 | **访问日志接管 + 降噪** | 关掉 uvicorn 自带的纯文本访问日志，由 `access_log` 中间件自己发。**path 不含 query string**；静默路径（`/`、`/health/live`、`/metrics`）<400 时降 DEBUG |

**为什么第 4 条里「不含 query string」是关键**：`/api/search/preview` 是 GET，
搜索词走 query（`routers_ops.py:89`）。uvicorn 默认访问日志打印**完整 URL**，
于是用户搜的原文会以明文进容器日志。审计表里存原文是设计（可追溯、有权限过滤），
运行日志里留痕是泄漏——两者不是一个东西，别混。

**实测（容器内真实输出，2026-11-16）**

```
$ curl -s -D - -o /dev/null http://127.0.0.1:8765/api/definitely-missing
HTTP/1.1 404 Not Found
x-trace-id: e3c0cb9a2aae41b2

$ docker logs kb-webapp | grep e3c0cb9a
{"ts":"...","level":"INFO","logger":"webapp.access","msg":"request",
 "trace_id":"e3c0cb9a2aae41b2","method":"GET","path":"/api/definitely-missing",
 "status":404,"latency_ms":4.91,"client":"172.25.0.1"}
```

响应头与日志里的 id **同一个值**。健康检查 `GET /` 的日志行数：**2880 行/天 → 0**。

**已知边界（别当成做完了）**

- `latency_ms` 是**到响应头返回**的耗时，不是流式响应的总时长。流式问答的生成长度差异极大，
  混进同一字段会误导，所以这条日志不承担它。
- 日志里**没有 `user_id`**：中间件在鉴权之前执行，拿不到用户。要归因得等业务层自己打，
  或改成在拿到 token 后再补一条。**这是缺口，不是设计。**
- **没有集中采集**：`docker logs` 只能在本机看，多副本时各看各的。上 Loki / CLS 的触发条件是
  「多副本 + 需要按字段检索 + 需要留存 30 天以上」，本项目一条都不满足，故不做。
- **没有留存与轮转策略**（除容器 10MB×3 外）：日志随容器删除消失，不满足合规留存。
- 日志脱敏只做到「不主动把 query string 写进去」；业务日志里如果主动打了用户原文，这里拦不住。

---



## 3. 指标清单（含义 / 正常范围 / 异常时先看什么）

| 指标 | 类型 | 含义 | 正常范围（演示/小规模，**需按基线校准**） | 异常时先看什么 |
|---|---|---|---|---|
| `kb_http_requests_total{path,status}` | Counter | 按路径与状态码累计的请求数；用 `rate()` 得 QPS 与错误率 | 5xx 占比 < 1%；演示期 QPS 个位数~数十 | `sum by (path,status)` 下钻：是单接口还是全站；是否与发布/配置变更同时发生 |
| `kb_http_request_duration_seconds_bucket{le}` | Histogram | 请求耗时分布；`histogram_quantile()` 得 P50/P95/P99 | P99 < 1s（演示）；外部模型调用可能更高 | 先分「本地处理慢」还是「外部调用慢」；同窗口 `kb_queue_pending` 是否升高；是否有冷启动长尾 |
| `kb_rate_limit_hits_total{dimension}` | Counter | 各限流维度（`user`/`endpoint`/`model`/`ip`）命中次数 | 平稳或为 0；突增需解释 | 是正常防刷、真实增长、还是阈值过紧；按 `dimension` 看是哪一维在拒 |
| `kb_queue_pending` | Gauge | 待处理队列长度 | 接近 0 或低位波动 | 对比入队/消费速率：突增 or 消费停摆；worker 心跳与失败重试 |
| `kb_budget_spend_cny` | Gauge | 累计花费（人民币） | 低于上限的 80% | 按用户/项目/模型看用量归因；是否有异常放量或重试风暴 |
| `kb_documents_by_status{status}` | Gauge | 各状态文档数（如 `ready`/`processing`/`failed`） | `failed` 占比低且不持续增长 | `processing` 长期不降 → 入库卡住；`failed` 上升 → 解析/下游依赖 |

**派生查询（面板与告警都用这些，不要另造指标）**：

```promql
# 全站 QPS
sum(rate(kb_http_requests_total[5m]))
# 5xx 错误率
sum(rate(kb_http_requests_total{status=~"5.."}[5m]))
  / clamp_min(sum(rate(kb_http_requests_total[5m])), 1e-9)
# P99 延迟
histogram_quantile(0.99, sum by (le) (rate(kb_http_request_duration_seconds_bucket[5m])))
# 按维度看限流
sum by (dimension) (rate(kb_rate_limit_hits_total[5m]))
```

> **多副本部署注意（必读）**：多实例部署时 `kb_http_requests_total` / `kb_rate_limit_hits_total` 会带 `instance` 标签，**必须先 `sum by (path,status)` / `sum by (dimension)` 聚合，再算 QPS 与错误率**，不要直接取单个实例的值（上方 P99 查询的 `sum by (le)` 已跨实例聚合）。另：**限流计数由 Redis 共享，多副本下限额不会被放大 N 倍**（本地内存计数仅在 Redis 故障时作降级兜底）。

---

## 4. Prometheus 抓取配置（含安全约束）

`prometheus.yml` 片段：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 30s

rule_files:
  - /etc/prometheus/rules/alerts.yml

scrape_configs:
  - job_name: kb-app
    metrics_path: /metrics
    static_configs:
      - targets: ["app:8000"]        # 换成实际暴露 /metrics 的服务名:端口
        labels:
          env: demo

  # 证书到期探测（KbCertExpiringSoon 依赖它）
  - job_name: blackbox-tls
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets: ["https://your-domain.example"]   # 换成你的域名
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115
```

> **安全红线（必须做到）**：`/metrics` **只对内网 / 抓取白名单开放**，**绝不暴露公网**。
> 理由：`/metrics` 会暴露接口路径、状态码分布、限流维度等运维信息，是攻击者的地图。
> 做法三选一：① 只绑 `127.0.0.1` 或容器内网，由 Prometheus 在同网络抓取；② 反向代理加 IP 白名单；③ 网关层加鉴权。**不要**图省事把它挂到公网域名下。
> 与既有实践一致：`run-demo.sh` 起服务时端口就只绑 `127.0.0.1`，`/metrics` 应沿用同一原则。

---

## 5. 告警规则（8 条，详见 `alerts.yml`）

| 告警 | 触发条件 | `for` | 级别 | 一句话判读 |
|---|---|---|---|---|
| `KbTargetDown` | `up{job="kb-app"} == 0` | 2m | **P1** | 抓不到指标 = 最先要确认的静默故障 |
| `KbHigh5xxRate` | 5xx 占比 > 5% | 5m | **P1** | 用户已在受影响；按 path 下钻 |
| `KbHighP99Latency` | P99 > 2s | 5m | P2 | 先分清外部调用慢还是本地慢 |
| `KbServerBusySpike` | 503 速率 > 0.2/s | 5m | P2 | 并发闸在拒；别用调大阈值来「消警」 |
| `KbQueueBacklog` | 队列 > 100 | 10m | P2 | 突增 or 消费停摆，二选一先判定 |
| `KbRateLimitSurge` | 某维度限流 > 1 次/s | 10m | P3 | 不一定是故障，先解释再决定 |
| `KbBudgetUsageHigh` | 花费 > 40（默认上限 50 的 80%） | 10m | P3 | 核对归因，决定是否切 block |
| `KbCertExpiringSoon` | 证书剩余 < 14 天 | 30m | P2 | 先查自动续期；< 3 天提级 P1 |

**为什么每条都要 `for`**：没有 `for` 的规则会被一次抖动触发，是告警疲劳的头号来源。`for` 让「持续 N 分钟」才响，过滤瞬时毛刺。

**阈值全部是起步建议**：先按观察期（1~2 周）的真实分位数定基线，再收紧；`KbBudgetUsageHigh` 的 40 依赖默认上限 50，改上限时必须同步改规则。

---

## 6. 告警分级与路由（含「告警疲劳」）

| 级别 | 定义 | 响应要求 | 通知渠道（建议） |
|---|---|---|---|
| **P1** | 影响可用性或数据正确性（全站 5xx、抓取中断、证书即将过期） | **立即**人工介入，非工作时间也要叫醒 | 电话 / 强提醒 IM @值班；15 分钟未确认自动升级 |
| **P2** | 影响体验但服务仍可用（延迟升高、并发拒绝、队列积压） | **工作时间**处理；可先观察是否自愈 | IM 群 / 工单；避免夜间叫醒 |
| **P3** | 趋势性、需复盘（预算接近上限、限流上升） | **周会/日报**统一看，不单独打扰 | 日报汇总 / 看板；不进夜间通道 |

**告警疲劳（必须正视）**：
- 症状：值班人开始忽略告警、把告警群静音、出现「狼来了」。
- 成因：阈值太敏感、`for` 太短、同一个根因发多条、无分级全走电话。
- 治理：① 每条告警必须能**对应一个明确动作**，否则删掉；② 同根因合并（如 5xx 与 503 同现只保留一条主告警，另一条降级或抑制）；③ 定期统计「误报率」，高于 ~30% 就该调；④ P3 永不进电话通道。

---

## 7. Grafana 面板建议（4 个图，够用就好）

| 面板 | 类型 | 核心查询 | 看什么 |
|---|---|---|---|
| **① 流量与错误率** | 时间序列（双轴） | `sum(rate(kb_http_requests_total[5m]))` + 5xx 占比 | 是否有「错误率台阶式上升」，通常对应一次发布或依赖故障 |
| **② 延迟分位** | 时间序列 | `histogram_quantile(0.5/0.95/0.99, ...)` | P50 稳而 P99 飙 = 长尾（个别慢请求）；P50 也升 = 整体变慢 |
| **③ 限流与并发** | 时间序列 / 堆叠 | `sum by (dimension)(rate(kb_rate_limit_hits_total[5m]))` + 503 速率 | 是哪个维度在拒、是否与 503 同现（容量问题） |
| **④ 队列与预算** | 双图（Gauge 曲线） | `kb_queue_pending` + `kb_budget_spend_cny` | 队列是否持续爬升（积压）；预算斜率是否异常（放量/重试风暴） |

> 建议给每个面板加一条「注释线」（annotation）记录发布/配置变更时间，用于把「指标突变」与「人为变更」对齐——这是最省力的根因线索。

---

## 8. 落地步骤（教程：从零到能响）

> 全部容器**只绑 `127.0.0.1`**，不暴露公网；演示环境不需要高可用。

```bash
# 1) 起 Prometheus + Grafana（数据卷持久化，端口只绑本机）
mkdir -p /opt/mon && cd /opt/mon
cat > prometheus.yml <<'YAML'
global: { scrape_interval: 15s, evaluation_interval: 30s }
rule_files: ["/etc/prometheus/rules/alerts.yml"]
scrape_configs:
  - job_name: kb-app
    metrics_path: /metrics
    static_configs: [{ targets: ["host.docker.internal:8000"] }]
YAML

docker network create mon >/dev/null 2>&1 || true
docker run -d --name prom --network mon \
  -p 127.0.0.1:9090:9090 \
  -v /opt/mon/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  -v /opt/mon/rules:/etc/prometheus/rules:ro \
  prom/prometheus:latest

docker run -d --name grafana --network mon \
  -p 127.0.0.1:3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD=change-me \
  -v grafana-data:/var/lib/grafana \
  grafana/grafana:latest

# 2) 放告警规则（把仓库里的 alerts.yml 拷进去）
mkdir -p /opt/mon/rules
cp docs/production/monitoring/alerts.yml /opt/mon/rules/alerts.yml
# 校验语法（有 promtool 时）：
docker run --rm -v /opt/mon/rules:/r --entrypoint promtool prom/prometheus:latest \
  check rules /r/alerts.yml
docker restart prom

# 3) 验证抓取与规则已加载
curl -s http://127.0.0.1:9090/api/v1/targets | head -c 400
curl -s http://127.0.0.1:9090/api/v1/rules | grep -o '"name":"Kb[^"]*"' | sort -u
```

**告警通知**二选一：
- **轻量**：直接用 Grafana 内置 Alerting（配 IM/邮件 contact point），不用 Alertmanager；
- **标准**：加 Alertmanager，在 `prometheus.yml` 配 `alerting.alertmanagers`，用路由树把 `severity=P1` 走电话、`P2` 走 IM、`P3` 只进日报。

**制造一次假故障，验证「告警真的会响」**（最关键的一步，不做等于没配）：

```bash
# 方式 A：把并发闸上限调到 1，压 2 个并发 → 触发 503（KbServerBusySpike）
#   仅演示环境，改完立刻改回
# 方式 B：停服务 → up==0 → KbTargetDown（2 分钟内必响）
docker stop kb-app
sleep 150 && curl -s http://127.0.0.1:9090/api/v1/alerts | grep -o '"name":"KbTargetDown"'
docker start kb-app
# 方式 C：把预算上限设为很小值并产生用量 → KbBudgetUsageHigh
```
> 验证完记得把阈值改回，并记录「告警从触发到收到通知」的实际耗时——这是比阈值更重要的可用性数据。

---

## 9. 三态与边界

| 项 | 状态 |
|---|---|
| `GET /metrics`（6 个指标） | **已实现（后端本轮新增，已合入）**；2026-11-15 实测指标名与本文清单一致，6 个指标齐全 |
| 结构化运行日志（stdout JSON） | **已实现**（`webapp/logging_setup.py`）；单行 JSON + 固定字段 + 白名单扩展，堆栈压成单行 |
| 每请求 `trace_id` 贯穿 | **已实现**（`access_log` 中间件）；响应头 / 错误体 / 访问日志三处同值，客户端合法 id 会沿用 |
| 容器日志上限 | **已实现**（`run-demo.sh` 的 `--log-opt max-size=10m --log-opt max-file=3`）；实测 `LogConfig.Config` 生效 |
| 访问日志脱敏（query string） | **部分**：path 不含 query，故 GET 的用户原文不进日志；**业务日志主动打原文拦不住** |
| 日志里按 `user_id` 归因 | **未做**：中间件在鉴权前执行，拿不到用户 |
| Prometheus / Grafana / Alertmanager | **未部署**（本文给配置与步骤，未实际执行） |
| 集中日志采集（CLS / Loki） | **未做**（单副本阶段判定为不划算，理由见 §2.1） |
| 日志留存与轮转策略（合规） | **未做**：除容器 10MB×3 外无留存，容器删除即丢 |
| 分布式追踪（span / trace 树） | **未做**（仅有 `trace_id` 关联） |
| 告警通知渠道、值班表、SLO 定义 | **未做**（本文只给分级建议） |
| 压测基线（用于校准阈值） | **未做**：本文所有「正常范围」都是演示量级**估算**，非实测 |

**边界声明**：本文不构成 SLA 承诺；没有压测与观察期基线，阈值只能起步用。真实生产还应补：SLO/SLI 定义、错误预算、值班轮换与升级路径、告警抑制规则、容量规划。
