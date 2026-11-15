# 今天修的 14 个真实缺陷 · 案例集

> 每一条都是**可复现**的：现象 → 根因 → 修法 → 面试问法 → 证据路径。
> 不含「假想风险」，全部来自本轮实跑或 QA 独立验收。
> 配套图文页：`interview/cases.html`；分支与 PR 记录：`docs/git-pr-log.md`。

## 怎么用

- **面试前**：只过每条的「现象 + 面试问法」，能在 30 秒内讲清就够。
- **复盘时**：看「根因 + 修法」，重点是想「同类缺陷还会藏在哪」。
- **代码审查**：把「根因」当 checklist，比读一遍代码更快。

---

## 一、安全类（5 条）

### 案例 1 · API_KEY 正则漏了连字符，真实密钥全部漏检

**现象（P0，QA 独立验收报出）**
`sk-proj-…`、`sk-or-v1-…`、`sk-<TENANT>-…` 这些真实供应商形态的密钥，扫描结果 `hits=0`；
更糟的是 `/api/guardrails/scan` 会把输入**原样回显**，上传 `.txt` 后密钥明文进响应并落盘。

**根因**
正则字符集写成 `[A-Za-z0-9]`，不含 `-`。密钥在第一个连字符处被截断，整条匹配失败。
而我的演示样本用的是自己编的 `sk-abcdefghijklmnop1234567890`（无连字符），所以自测全绿。

**修法**
1. 字符集纳入 `-` 与 `_`：`sk-[A-Za-z0-9][A-Za-z0-9_-]{15,}`；
2. A9 不再回显输入（`results[].text` 置空 + `echo_suppressed: true`）；
3. A5 落盘前对整份 ingest JSON 再扫一遍，命中即 422 且**不写任何文件**（defense in depth）。

**面试问法**
> 「你的脱敏规则怎么测的？会不会漏？」——标准答法：**用真实凭证形态测，不要用自己编的样例**。
> 我最初就是拿自己编的无连字符样例测的，全绿，但真实形态全漏。这是「测试数据比测试代码更容易骗人」。

**证据**：`guardrails/detectors.py:79`、`guardrails/tests/test_guardrails.py`（6 项形态矩阵）、
`docs/showcase/04-acceptance.md` D-01。

---

### 案例 2 · 流式输出把身份证整段发了出去

**现象**
逐字符 SSE 输出一段长文本时，脱敏后的回答里仍然出现完整 18 位身份证。

**根因**
旧实现只保留尾部 `HOLD=24` 个字符。滑动窗口每轮只扫前 2 个字符，
**永远看不到完整 18 位**，于是原文被逐字符发出去。

**修法**
找出横跨「可下发边界」的连续候选串（数字/字母/邮箱分隔符，中文天然是边界），
把下发边界收缩到该串起点，**整串扣住**，等它完整进入缓冲区再扫描脱敏。
无 PII 文本的流式拼接必须与原文**逐字相同**（有专门用例锁定）。

**面试问法**
> 「流式脱敏怎么做？」——关键不是正则，是**边界**：一个标识符可能被拆在多个 chunk，
> 任何单块扫描都会漏。要么保留足够长的重叠窗口，要么按候选串扣留。

**证据**：`guardrails/redact.py::StreamRedactor`、`test_stream_chunk_sizes_never_leak`（7 种 chunk 大小）。

---

### 案例 3 · 伪造 `X-Forwarded-For` 就能绕过 IP 限流

**现象**
按 IP 限流后，攻击者每次请求带不同的 `X-Forwarded-For`，限流完全不生效。

**根因**
无条件采信 `X-Forwarded-For` 的第一个值。这个头是客户端可任意伪造的。

**修法**
只有**直连对端**属于可信代理（本机反代 / 云负载均衡回源段）时，才采信 XFF 最左值；
否则一律用 `request.client.host`。host 缺失返回 `"unknown"`，避免空串成为共享桶。

**面试问法**
> 「挂了 CLB/WAF 之后，你怎么拿到真实客户端 IP？」——**信任链**：只信自家代理的转发头，
> 白名单配置回源段；不要无脑取 XFF 最左值，那等于把限流键交给攻击者。

**证据**：`webapp/security.py::client_ip`（7 个边界用例全过）、`test_gateway_hardening.py`。

---

### 案例 4 · 未登录用户共用一个 `anonymous` 限流桶

**现象**
所有未登录请求的 `user` 维度 key 都是 `"anonymous"`，等于全站匿名流量共用一个桶：
要么一视同仁地被限死，要么为了放行而把阈值调得形同虚设。

**根因**
限流维度只有 `user / endpoint / model`，而没有稳定身份时 `user_id` 为空，退化成同一个 key。

**修法**
增加 **IP 附加维度**（`IP_DIMENSION = "ip"`，默认 600 次/60 秒），
`RATE_DIMENSIONS` 仍保持三维（业务配额口径不变），IP 是接入层防刷。
命中时返回专用码 `RATE_LIMIT_IP_EXCEEDED`，前端能区分「你的账号被限流」和「这个网络出口请求过多」。

**面试问法**
> 「多维限流怎么设计？」——先分「业务配额」和「接入层防刷」两类：
> 前者按用户/接口/模型，后者按 IP；两者阈值、错误码、提示语都应该不同。

**证据**：`webapp/observability.py:43`、`webapp/main.py::RATE_CODES`。

---

### 案例 5 · `/api/system/busy` 不需要登录就能看到正在处理的文件名

**现象**
未登录 `GET /api/system/busy` 返回 200，且 `active[].filename` 是真实文档名。

**根因**
该端点写的时候没加 `Depends(get_current_user)`，只当它是"状态查询"。

**修法**
加登录依赖；普通用户只拿计数（`active/queued` 置空 + `redacted: true`），
文件名明细仅 admin 可见。自测 C07/C26 原本不带 token 调它，已改为带 admin token。

**面试问法**
> 「哪些接口需要鉴权？」——判断标准不是"是不是写操作"，而是**响应里有没有别人的信息**。
> 一个只读的"状态查询"也可能泄漏「谁在上传什么文件」。

**证据**：`webapp/routers_docs.py::system_busy`、`webapp/tests/test_queue_overview.py`。

---

## 二、确定性与状态（3 条）

### 案例 6 · 版本号被"瞬时计数"污染，同参双跑 `run_id` 必变

**现象**
同一份文件、同样参数连续跑两次，`snapshot_version` 从 `7bd0a3fa…` 变成 `cb26b89a…`，
连带 `run_id`、manifest 摘要全变，"同参双跑逐字节一致"直接不成立。

**根因**
版本哈希字段集里包含了 `reason`（含"连续命中 1/2"文本）和 `hysteresis`（滞回计数）。
这两个是**决策过程观测**，不是**配置快照**。

**修法**
把字段集拆成两份：
- `_CONFIG_HASH_FIELDS`（给 `snapshot_version`）：tier/model/prompt/params/fallback_chain/trigger/…
- `_DECISION_HASH_FIELDS`（给 `route_id`）：全字段，含 reason/hysteresis。

语义变成：**版本号 = 配置版本；route_id = 这一次决策实例**。

**面试问法**
> 「版本号里该放什么？」——放**配置**，不放**运行时计数**。
> 否则每次调用都产生新版本，缓存、快照、对拍全部失效。

**证据**：`showcase/llm_router.py:108-180`、`test_gateway_hardening` 之外的 `test_contracts.py`。

---

### 案例 7 · 单次复杂档永远升不到 Pro（滞回状态每次新建）

**现象**
复杂文档难度 `0.735`（阈值 0.60），但每次解析结果都是 `standard`，Pro 档永远不触发。

**根因**
滞回要求"连续 2 次命中"，但 A5 每次请求都 `TierState(tier="standard")` **新建状态**，
计数永远是 1/2，升档条件永不满足。

**修法**
保留 `k=2`（滞回防抖动是专业卖点，不降阈值），改状态管理：
按 `(source_sha, params_hash)` 做进程内 LRU（≤32 条）缓存 `TierState`。
同一份复杂档连跑两次 → 第 2 次真实升 pro；不同文件/参数不同 key，不串用户；重启清零。

**面试问法**
> 「有状态的东西怎么设计？」——三个问题必须答得出：**隔离维度**（按什么分桶）、
> **生命周期**（重启丢不丢）、**并发安全**（加不加锁）。

**证据**：`webapp/api.py::decide_with_lru`、`docs/showcase/03-phase2-spec.md` §1.1(7)。

---

### 案例 8 · 双向摘要互引在数学上无解

**现象**
manifest 要指向 ingest 的 sha256，ingest 又要指向 manifest 的 sha256，写入时怎么算都不对。

**根因**
`digest(A) = sha256(A 完整字节)`，而 A 里含 B 的摘要、B 里含 A 的摘要——循环依赖。

**修法**
用**规范化投影**打破循环：ingest 摘要只对「剔除 `manifest.sha256` 后的投影」计算，
manifest 摘要对完整 manifest 计算（manifest 不含自指字段）。
两侧都可重算，篡改任一侧都能被拦。

**面试问法**
> 「两个文件互相校验完整性怎么做？」——**不可能对完整字节互签**。
> 要么一方只存对方的摘要（单向），要么签规范化投影（剔除互引字段）。

**证据**：`showcase/snapshot.py` docstring、`test_contracts.py` 的四类篡改用例。

---

## 三、配额与并发（3 条）

### 案例 9 · 全局预算：一个人烧光，所有人被拦

**现象**
预算只查 `scope='global', key='default'`。用户 A 跑量把额度用完后，
**用户 B 的问答也被 `policy=block` 拦住**。

**根因**
配额没有按用户隔离，`budgets` 表虽然已有 `scope/key` 结构，但只用了 global 一行。

**修法**
`check_budget(user_id=...)`：优先 `budgets(scope='user', key=<uid>)`，没有则回退 global；
用户级花费按 `usage_events.user_id` 聚合。返回体加 `scope` / `overridden`。
实测：A `scope=user allowed=False`，B `scope=global allowed=True`。

**面试问法**
> 「多用户配额怎么防互相影响？」——**配额是每用户的，账是每用户的**；
> 全局额度只作总闸，不能当成唯一判定。否则一个用户就能把系统打成只读。

**证据**：`webapp/observability.py::check_budget`、`routers_chat.py` 预算拦截。

---

### 案例 10 · 预算闸门只在离线评估里生效，问答链路根本没拦

**现象**
`policy=block` 时，离线评估会被拒，但**问答接口照跑**——配额形同虚设。

**根因**
`check_budget` 只在 `run_offline_eval` 里调用；`chat_stream` 从未调用过。

**修法**
在 `chat_stream` 进模型前查一次预算；超支时以 **SSE 首个事件**告知
（`QUOTA_EXCEEDED` + `scope` + `policy`），并写审计。离线评估端点保持原来的
`200 + {ok:false}` 形态——两种入口语义一致、形态不同，文档要区分。

**面试问法**
> 「配额在哪一层拦？」——**在真正花钱的那一层拦**。只在报表里统计、或者在别的入口拦，
> 都不算配额生效。

**证据**：`webapp/routers_chat.py:60-85`、`test_gateway_hardening.py` 的预算用例。

---

### 案例 11 · 并发闸把流式响应的令牌提前释放了

**现象**
并发上限设 1 时，两个 SSE 请求同时进来，第二个没有被拒——并发数被低估。

**根因**
中间件在 `return response` 时就 `sem.release()`，而 `StreamingResponse` 的响应体是**之后**才逐步发送的。

**修法**
用 `response.body_iterator` 包装：`try/finally` 里 `async for chunk in inner: yield chunk`，
`finally: sem.release()`。正常、异常、客户端断连三条路径都恰好释放一次。

**面试问法**
> 「限流/信号量什么时候释放？」——**等响应真正结束**。
> 流式响应的 `return` 不等于结束，提前释放会让并发控制失效。

**证据**：`webapp/main.py::gateway_guard` 的 `_release_after_stream`、串行流式用例。

---

## 四、工程与演示（3 条）

### 案例 12 · Docker Compose v5 的 bake 在中文路径下直接失败

**现象**
`docker compose up -d --build` 报
`failed to dial gRPC: header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters`。

**根因**
Docker 29 + Compose v5 的 bake 构建器在含非 ASCII 字符的项目路径下，把路径塞进 gRPC 头，编码失败。

**修法**
本机用 `docker build` + `docker run` 绕开（`run-demo.sh`）；确实要用 compose 时加 `COMPOSE_BAKE=false`。
镜像本身没问题——同一镜像 `docker run` 完全正常。

**面试问法**
> 「构建失败怎么定位是环境还是代码？」——看**同一产物换一条路径是否可用**。
> 本例里镜像能跑、只有 compose 构建失败，说明是构建器兼容问题，不是 Dockerfile 写错。

**证据**：`run-demo.sh` 顶部注释、`docker-compose.showcase.yml`。

---

### 案例 13 · 内嵌预览面板拦截 `window.open` 与 `target="_blank"`

**现象**
页面里「解析工作台」按钮点了没反应，两个方向都点不动；但直接访问 URL 是 200。

**根因**
内嵌 WebView / 预览面板默认禁止弹窗与新标签页，`window.open` 与 `target="_blank"` 都被拦。

**修法**
改用 `<a href>` **同标签跳转**，不依赖弹窗 API；同时把地址写进 `title` 并在页面里直接列出，
点不动时用户可复制到地址栏。环境变量可在部署时覆盖地址。

**面试问法**
> 「为什么不用 `window.open`？」——它依赖弹窗权限，在 WebView、iframe、企业微信内置浏览器里
> 都可能被拦。普通链接是更稳的降级路径。

**证据**：`webapp/static/index.html` 的 `#nav-showcase`、`showcase/static/app.js::bindMainSiteLink`。

---

### 案例 14 · 演示种子被「启动自检」逐条消费

**现象**
造了 130 条排队文档用于演示，重启几次后只剩 50 条、再重启继续减少。

**根因**
启动自检会把所有 `queued` 任务幂等重投（这是**正确**的恢复行为），
而种子没有真实上传文件，重投后逐条转成 `error`。

**修法**
不是改恢复逻辑，而是**改演示策略**：种子定位为「演示前生成、演示后清理」的临时数据，
在脚本 docstring 里写明会被消费，需要时重新 `--seed`。

**面试问法**
> 「演示数据和恢复逻辑冲突怎么办？」——不要为了演示去改正确性逻辑。
> 把演示数据降级为"临时资产"，写清生命周期，比让系统行为迁就演示更专业。

**证据**：`webapp/demo_seed_queue.py` 顶部说明、`recovery.py::startup`。

---

## 五、一张表看完

| # | 案例 | 类型 | 一句话教训 |
|---|---|---|---|
| 1 | 密钥正则漏连字符 | 安全 | 用真实形态测，别用自己编的样例 |
| 2 | 流式脱敏跨界 | 安全 | 关键在边界，不在正则 |
| 3 | 伪造 XFF | 安全 | 只信自家代理，白名单回源段 |
| 4 | anonymous 共桶 | 限流 | 业务配额与接入层防刷要分开 |
| 5 | busy 无鉴权 | 安全 | 只读接口也可能泄漏别人的信息 |
| 6 | 版本号被计数污染 | 确定性 | 版本放配置，不放运行时计数 |
| 7 | Pro 永不触发 | 状态机 | 隔离维度 / 生命周期 / 并发安全 |
| 8 | 双向摘要互引 | 一致性 | 完整字节互签无解，签投影 |
| 9 | 全局预算 | 配额 | 配额与账都要按用户 |
| 10 | 预算闸门错层 | 配额 | 在花钱的那一层拦 |
| 11 | 并发闸提前释放 | 并发 | 等响应真正结束再释放 |
| 12 | Compose bake | 工程 | 换路径验证是环境还是代码 |
| 13 | 预览面板拦截 | 前端 | 别依赖弹窗 API |
| 14 | 种子被消费 | 演示 | 演示数据别改正确性逻辑 |

---

## 六、还值得继续挖的方向

- **限流是单机内存窗口**：多副本时各副本独立计数，应换 Redis 滑动窗口或云网关统一配额；
- **并发闸没有排队**：当前快速失败，生产可加短队列 + 公平调度；
- **PII 只有规则级**：图片/表格内 PII、模型级 NER 未做；
- **预算没有预警**：只有 warn/block 两档，缺少 50%/80% 阈值告警；
- **WAF/网关规则未标定**：云侧 CC 阈值需要真实流量。

这些写在 `docs/multi-user-hardening.md` §6 与 `docs/showcase/04-acceptance.md` §12.3，不重复展开。
