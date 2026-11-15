# 限流设计与多部门权限安全复核

> 交付：后端服务工程师 程不宕｜本轮**只写本文档**，未改任何源码、未动 `eval/` `demo/` `evalbench/` `docs/CHANGELOG.md`、未发起任何真实外部 LLM 调用。
> 适用定位：企业内部知识库「开发框架骨架 + 可运行演示切片」（A/B 档，学习与面试用，**非上线交付版**）。
> **口径声明**：用户新指令覆盖历史口径——系统为**多用户多部门架构，暂不实现多租户**；多租户仅作为**未来研发方向**写入文档（详见 §2.4）。主理人本轮正在把 webapp 演示切片的统计维度从 `tenant` 改为 `department`（代码在变更中，本文只给文档与设计建议）。
> **三态纪律**：全文严格区分「**已实现**（代码里真有）/ **本轮建议**（只有设计，未落地）/ **未做（Won't）**」。文中所有配额数值均为**本演示切片的建议默认值，不是压测结论，也不是供应商报价**。
> **事实基线**：本文全部结论来自实读代码。撰写**开始时** `git status --short` 为空（工作区干净），`webapp/observability.py` 的 `RATE_DIMENSIONS` / `DIMENSIONS` / `CONFIG_KEYS` **仍是 `tenant` 口径**。
> **撰写期间的并行变更（实查，必须知悉）**：本文写作过程中，其他成员已把 `webapp/db.py` 改为 `usage_events.department_id` 列 + `ix_usage_department` 索引 + 幂等迁移 `_migrate_tenant_to_department()`（把旧 `tenant` 列 `RENAME COLUMN` 为 `department_id`、把 `rate_rules.dimension='tenant'` 改为 `'department'`、`eval_configs.key='tenant'` 改为 `'department'`），`defaults` 由 `{"user","tenant","endpoint","model"}` 改为 `{"user","department","endpoint","model"}`，**仍保留 4 条限流规则**（`department` 3000/60s 参与计数）。`webapp/observability.py` 与 `webapp/main.py` **当时尚未同步改名**。
> **撰写期曾产生的两处差异（主理人已裁定，见下条回执）**：① **物理列名**：本文目标态表格以 `department` 指「部门归因维度」，落地代码物理列名为 `usage_events.department_id`，二者指同一归因字段；② **维度数量**：本文按用户指令建议「三维度计数 + 部门只做归因、不参与 429」，撰写期 `db.py` 一度是「四维改名、保留 `department` 计数规则」——**该四维分支未采纳，主理人裁定采用三维**（见 §1.6）。另：撰写期 `observability.py:83-85` 的 `INSERT INTO usage_events(..., tenant, ...)` 与已改列名不一致，属并行改名中间态，**已由主理人收口**（本文不直连、不代改）。以上均按「只出文档与建议」定位如实记录，不擅自改他人代码。
> **主理人裁定回执（本文档修订时补记）**：上述中间态已由主理人收口——`webapp/observability.py`（`DIMENSIONS` / `RATE_DIMENSIONS` / `record_usage` 的 INSERT / `usage_summary` / `rate_check` / `CONFIG_KEYS` / `run_offline_eval` / PLUGINS 文案）与 `webapp/db.py` 已改完，`main.py` / `routers_chat.py` / `routers_evaltest.py` / 前端与测试正在收口，C22-3 四处配置键同批改完后实跑全套回归 + `PRAGMA table_info` 验证，**不会留下「列名已改但 SQL 仍写 tenant」的状态**。维度数量裁定采用**三维计数 + 部门仅归因**（见 §1.6）。本文保留撰写期记录以说明证据来源，但**当前状态以本回执为准**。凡描述「目标态」处均显式标注，不把计划写成现状。

---

# 第一部分 · 限流设计（用户任务 3）

## 1.1 目标口径：三个隔离维度 + 一个部门归因标签

用户明确要求限流覆盖**用户 / 接口 / 模型调用**三个维度。项目现有实现是**四维**（`user / tenant / endpoint / model`，实查 `webapp/observability.py:37` 与 `webapp/db.py:317-318`），其中 `tenant` 在单组织切片下恒为 `"default"`（`webapp/main.py:67` 硬编码传入）。

| 维度 | 目标态定位 | 是否参与 429 计数 | 落点 |
|---|---|---|---|
| 用户 `user` | 隔离维度 | ✅ 参与 | `rate_rules.dimension='user'`，键 = JWT `sub`，未登录 `anonymous` |
| 接口 `endpoint` | 隔离维度 | ✅ 参与（按路径分组） | `rate_rules.dimension='endpoint'`，键 = 请求 `path` |
| 模型调用 `model` | 隔离维度 | ✅ 参与 | `rate_rules.dimension='model'`，键由 `main.py:62-66` 映射（`llm` / `settings` / `-`） |
| **部门 `department`**（原 `tenant`） | **归因素材，不是隔离维度** | ❌ **不参与** | 仅作为**事件发生时快照**写入 `usage_events.department_id`（落地代码物理列名；本文维度名简写 `department`），用于成本归集/归因 |

### 为什么把 `tenant` 降为「部门归因标签」而不再按部门做 429 隔离

1. **用户新指令要求的是三维度**。第四维 `tenant` 是历史多租户建模的残留；改为 `department` 后，它的用途是「谁花的钱」而不是「谁被限流」。
2. **部门是动态/层级/可变值，不适合做限流分片键**。`department_id` 会随转岗、部门移动、组织调整变化；按部门计数会导致：转岗瞬间旧计数清零、同一个人换个部门就绕过配额；部门子树合并/拆分时计数窗口语义不清。而 `tenant` 原本是静态、平级、互斥的。
3. **部门不构成安全边界，按部门 429 隔离没有安全收益**。真正的越权防护是 `decide()` 逐条复核（见 §2.2），限流只防资源打满。按部门做 429 断言会把「成本归因」和「访问控制」混为一谈。
4. **与既有验收口径一致**：`docs/org-model-change-plan.md:404 (A7)` 明确「四维 `user\|department\|endpoint\|model` + 响应头枚举；`usage_events.department` 映射正确；**不断言 429 按部门隔离生效**」；`C18` 两层语义规定「webapp 第二维 `tenant→department` 只用于核算成本/归因，不做 429 按部门隔离断言；backend 分级配额/全局并发闸仍 Won't，文档只改口径」。
5. **不打断演示自测**：`docs/enterprise-readiness.md:63` 明确默认阈值刻意宽松是为「不误伤 33 项端到端自测与 51 项 pytest」；多一个按部门计数维度只会增加自测偶然命中的概率。

> 因此目标态是：**`rate_rules` 表从 4 条规则变为 3 条参与计数的规则**；`department` 只出现在用量台账（`usage_events.department_id`）、用量聚合维度（`DIMENSIONS`）与预算报表里。响应头 `X-RateLimit-Dimension` 的枚举相应变为 `user|endpoint|model`（`department` 保留在文档枚举中但标注「不触发」）。
> **现状标注（主理人裁定后）**：本节主表与最终代码**一致，不需微调**——主理人已裁定采用「**三维计数 + 部门仅归因**」：`RATE_DIMENSIONS = ("user","endpoint","model")`、`rate_rules` 只插这三条规则、`rate_check(..., department="default", ...)` 保留参数但**不参与计数、只写日志归因**、`db.py::_migrate` 收敛旧 `tenant` / `department` 规则行、`usage_events.department_id` 仍是五维聚合之一。依据为用户任务 3「覆盖用户、接口、模型调用三个维度」与任务 1「按项目、部门和调用维度统计 token 消耗与成本」（部门是**统计**维度，不是限流维度）。撰写时的四维状态与并行差异保留于文首与 §1.6 作为证据记录；本文不代改代码。

## 1.2 配额表：建议默认值与重置周期

计数实现是**进程内滑动窗口**（`webapp/observability.py:170-229`：`_WINDOWS` + `deque` + `_rate_lock`），窗口按 `window_s` 滚动清理（`:218-219`），**不是固定自然分钟**；下表「重置周期」指单条计数窗口的长度，每维度独立。

| 维度 | 计数键 | **建议默认限额** | **重置周期** | 现状值（实查） | 说明 |
|---|---|---|---|---|---|
| 用户 `user` | `user_id or "anonymous"` | **1200 次** | **60s** | 1200 / 60s | 防单用户刷接口；未登录统一落 `anonymous` 桶 |
| 接口 `endpoint` | 请求 `path`（按下表分组） | **见分组表** | **60s** | 900 / 60s（单一路径值） | 保护热点接口，尤其 `/api/chat/*` 长连接 |
| 模型调用 `model` | `llm` / `settings` / `-` | **600 次** | **60s** | 600 / 60s | 按「调用次数」近似 RPM；对应上游共享配额 |
| 部门 `department`（归因） | **不参与计数** | **无限额、不产生 429** | — | 撰写时 `tenant` 3000 / 60s | **主理人裁定采纳**：取消该维度计数规则；`rate_check()` 仅保留 `department` 参数写日志归因，`usage_events.department_id` 继续作为五维聚合之一 |

**接口维度按路径分组的建议值**（`main.py:53-66` 已给出路径分类的雏形，本轮建议把它落到 `rate_rules`）：

| 路径分组 | 建议限额 / 60s | 理由 |
|---|---|---|
| `/api/chat/*`（流式问答） | **300** | 单请求占用长连接与上游模型额度 |
| `/api/evaltest/*`（评估与治理） | **900** | admin 复现超限需连续发请求（见 `test_evaltest.py:144-167`） |
| `/api/auth/*`（登录/注册） | **600** | 演示防误伤；生产应显著收紧并加验证码 |
| 其余 `/api/*`（docs/ops/badcase 等） | **900** | 默认值 |
| `/` 及静态资源 | **不计数** | `main.py:54-55` 直通，本就不进限流 |

**模型维度 RPM / TPM 说明**：当前 `model` 维度按**调用次数**计数（`rate_check()` 每命中一次 `q.append(now)`），只能近似 RPM；**TPM 实时拦截未做**——`usage_events.total_tokens` 只在 `record_usage()`（`observability.py:65-94`）里做事后核算，不进 `rate_check()`。生产建议按供应商真实 RPM / TPM 的 70–80% 设阈值，TPM 需改成按 token 计数的令牌桶（属后续改造，非本演示切片）。

### 默认值为什么设得宽松

- `docs/enterprise-readiness.md:63` 原文：默认阈值刻意宽松，**不误伤 33 项端到端自测与 51 项 pytest**；admin 可在页面下调阈值复现超限。
- 回归基线（`docs/enterprise-readiness.md:192-193`、`docs/org-model-change-plan.md:17`）：`python -m pytest webapp/tests -q` → 51 passed、`python webapp/selftest.py` → 33/33 `DEMO_READY`。自测会连续打十几个 API，阈值稍紧即出现假红。
- 演示路径已内置「调低阈值 → 验证 429」：`webapp/tests/test_evaltest.py:144-167` 把 `user` 改成 `2/60s`，第 3 次请求断言 429 与 4 个响应头，随后恢复 1200。**宽松默认值 + admin 可调** 是这套演示的设计取舍，不是遗漏。

### 生产环境应如何收紧（示例，非压测结论）

| 维度 | 演示默认 | 生产收紧示例 | 配套必须补齐（现状未做） |
|---|---|---|---|
| 用户 | 1200 / 60s | **60 / 60s**（用户指令中给出的示例量级） | 未登录单独更严的桶、按实名账号而非 IP |
| 接口 | 900 / 60s | 按路径 30–120 / 60s | 网关层统一限流、热点接口独立队列 |
| 模型 | 600 / 60s | 供应商 RPM × 0.7~0.8，并加 TPM 上限 | 供应商账单对账、按 token 的令牌桶 |
| 全局 | 无 | 全局并发闸 | Redis 令牌桶 + 队列吸收突发 + 熔断 |

> `docs/enterprise-readiness.md:83/:231` 与 `docs/kb-capability-checklist.md` 已明确：进程内滑动窗口**单实例有效**，多副本必须换网关层 Redis 令牌桶；**全局并发闸、熔断、压测与容量基线均为「未做 / Won't」**，不得写成已落地。

## 1.3 统一错误格式（返回前端）

> **本轮落地范围（主理人裁定，先读这一段）**：本节是**部分落地，不是「已完成统一错误格式」**——
> - ✅ **本轮落地**：`webapp/main.py` 的 429 扁平体按 §1.3.2 主干补 `code` / `kind` / `message` / `trace_id` / `policy`，业务码用三个 `RATE_LIMIT_*_EXCEEDED`，`kind="rate_limit_exceeded"`；**保留原有 `dimension` / `limit` / `window_s` / `retry_after` / `current` 字段与 4 个响应头做向后兼容**。
> - ⏸ **已知不一致 / 下期对齐**：`backend/app/core/constants.py:173` 的 `QUOTA_EXCEEDED` 仍为 413，**本轮不改 backend 骨架**（用户明确不改 backend 数据模型与既有契约），原因见 §1.3.1。
> - ⏸ **已实现阻断 / 错误码契约未统一**：预算耗尽仍是 HTTP 200 + `{ok:false, reason:"budget_exceeded"}`，**本轮保留现状**（阻断语义已生效，只是状态码未统一；改动会波及既有演示契约与测试），见 §1.3.5。
>
> 因此「统一错误格式」本轮只覆盖**本地限流 429 这一层**，不得对外称全链路已统一。

### 1.3.1 业务码表（三个 `RATE_LIMIT_*` 本轮落地；`QUOTA_EXCEEDED` 为目标态）

| 业务码 | HTTP | `kind` | 触发条件 | 是否可重试 |
|---|---|---|---|---|
| `RATE_LIMIT_USER_EXCEEDED` | **429** | `rate_limit_exceeded` | `user` 维度命中 | ✅ 退避重试 |
| `RATE_LIMIT_ENDPOINT_EXCEEDED` | **429** | `rate_limit_exceeded` | `endpoint` 维度命中 | ✅ 退避重试 |
| `RATE_LIMIT_MODEL_EXCEEDED` | **429** | `rate_limit_exceeded` | `model` 维度命中 | ✅ 退避重试 |
| `QUOTA_EXCEEDED` | **429**（目标态，本轮不落地） | `quota_exceeded` | 预算 / 额度耗尽（`policy=block`） | ❌ **不重试** |
| `RATE_LIMITED` | 429 | `rate_limit_exceeded` | backend 既有统一码，兼容保留 | ✅ 退避重试 |

> **已知不一致 / 下期对齐（本轮不改）**：`backend/app/core/constants.py:172-173` 现为 `"RATE_LIMITED": 429` 与 **`"QUOTA_EXCEEDED": 413`**，即 backend 既有 `QUOTA_EXCEEDED` 映射到 **413 而非 429**，与本文「两类 429」口径不一致。**本轮不改 backend 骨架**——用户明确不改 backend 数据模型与既有契约，且改动会波及 backend 统一错误码契约与既有测试；该行保留为「已知不一致」，下期与预算耗尽状态码一并对齐，不得静默改码表，**也不得写成已统一**。三个 `RATE_LIMIT_*_EXCEEDED` 是 webapp 演示切片新增业务码，**本轮落地**，不触碰 backend `ERROR_CODES`。

### 1.3.2 响应体字段（本轮落地：扁平结构向后兼容 + 主干字段补齐）

主理人裁定采用**「在现有扁平体上补字段」**而非「把字段收进 `detail` 对象」，以保住 `webapp/tests/test_evaltest.py:144-167` 等既有断言。以 `backend/app/core/errors.py:51-62` 的 `{code, message, detail, trace_id}` 为**字段命名主干**，但 **`detail` 保持原有字符串文案**（不改成对象），原有扁平字段全部保留：

```json
{
  "code": "RATE_LIMIT_USER_EXCEEDED",
  "kind": "rate_limit_exceeded",
  "message": "请求过于频繁，请稍后重试",
  "detail": "请求过于频繁，请稍后重试",
  "dimension": "user",
  "key": "u_1024",
  "path": "/api/chat/stream",
  "limit": 1200,
  "window_s": 60,
  "retry_after": 43,
  "current": 1200,
  "trace_id": "0f3a...",
  "policy": "rate_limit_exceeded 可退避重试；quota_exceeded 不重试"
}
```

| 字段 | 类型 | 状态 / 来源 |
|---|---|---|
| `code` | str | **本轮落地新增**；取值 `RATE_LIMIT_USER_EXCEEDED` / `RATE_LIMIT_ENDPOINT_EXCEEDED` / `RATE_LIMIT_MODEL_EXCEEDED` |
| `kind` | str | **本轮落地新增**；本地限流恒为 `rate_limit_exceeded`（显式区分两类 429，避免前端靠文案猜） |
| `message` | str | **本轮落地新增**；与 `detail` 同文案，命名对齐 `errors.py:58` |
| `detail` | str | **原样保留**（字符串文案），不改成对象 |
| `dimension` / `key` / `path` / `limit` / `window_s` / `retry_after` / `current` | — | **原样保留**，由 `rate_check():222-227` 返回；`X-RateLimit-Dimension` 目标态枚举 `user\|endpoint\|model` |
| `trace_id` | str \| null | **本轮落地新增**；命名对齐 `errors.py:60`。演示切片暂无跨服务 trace 上下文，可先置 `null` 或取请求 id，**不伪造链路** |
| `policy` | str | **原样保留**（`main.py:74` 已有说明串） |

> 4 个响应头（§1.3.3）与上述原有扁平字段共同构成**向后兼容面**：`test_evaltest.py` 的 `body["dimension"] == "user"`、`body["limit"] == 2`、`"retry_after" in body`、`body["policy"]` 断言继续成立。

### 1.3.3 响应头（已实现）

`webapp/main.py:76-81` 已返回 4 个头，**本轮不改、不减**：

| 响应头 | 值来源 |
|---|---|
| `Retry-After` | `hit["retry_after"]`（`rate_check():225`） |
| `X-RateLimit-Dimension` | `hit["dimension"]` |
| `X-RateLimit-Limit` | `hit["limit"]` |
| `X-RateLimit-Window` | `hit["window_s"]` |

> 目标态枚举 `user|endpoint|model`；`department` 不出现在 `X-RateLimit-Dimension`（§1.1）。

### 1.3.4 前端提示文案与重试指引

| `kind` | 前端提示文案（建议） | 重试指引 |
|---|---|---|
| `rate_limit_exceeded` | 「请求太频繁，约 **{retry_after}** 秒后可继续；系统会自动重试（最多 3 次）」 | 自动退避：尊重 `Retry-After`，否则指数 1/2/4s + 抖动；**流式已开始吐字后不重试**（`providers.py:282` 注释「仅限『还没开始流式』的阶段」） |
| `quota_exceeded` | 「**额度或预算已用尽，不会自动重试**。请联系管理员调整预算，或更换模型 / 供应商」 | 不重试，直接失败；`providers.py:296-298` 与 `:334-335` 抛 `RuntimeError("quota_exceeded: ...")` |

### 1.3.5 与 `providers.py` 既有实现的对齐（三层 429 必须分清）

实查发现项目里存在**三种不同来源**的 429，写前端逻辑时不能混：

| 层 | 来源 | 现状行为 | 与本文关系 |
|---|---|---|---|
| ① **本地限流 429** | `webapp/main.py:47-82` 中间件 → `rate_check()` | 返回本文 §1.3.2 的 429 体 + 4 头；命中写 `rate_hits` + `eval_logs` WARN（`observability.py:232-246`） | 本文统一格式的主对象，`kind=rate_limit_exceeded` |
| ② **上游供应商 429** | `webapp/providers.py:277-298`（`_stream_raw`）、`:334-335`（`complete`） | `classify_429()`（`:78-92`）解析**上游响应正文**：含 `quota_exceeded` / `quota...insufficient` → `quota`，否则 `rate`；`quota` 直接抛错不重试，`rate` 走 `backoff_seconds()` 最多 3 次 | 服务端内部调用上游，**不直接把 429 透传前端**；本文的 `kind`/码表供将来透传时复用 |
| ③ **预算耗尽** | `webapp/observability.py:421-426` `run_offline_eval()` | `check_budget()`（`observability.py:97-125`）全局累计 `SUM(cost_cny)`，`policy=block` 且超额时返回 **HTTP 200 + `{ok:false, reason:"budget_exceeded"}`**，不是 429 | **本轮保留现状：已实现阻断 / 错误码契约未统一**。阻断语义已生效（拒绝运行 + ERROR 日志留痕），只是状态码未统一；改动会波及既有演示契约与测试，**不写成已完成统一错误格式**；下期与 `constants.py:173` 的 413 一并对齐 |

`classify_429()` 的判定式（实查 `:92`）：`"quota" if "quota_exceeded" in blob or "quota" in blob and "insufficient" in blob else "rate"`；`backoff_seconds()`（`:95-102`）：服务端给 `Retry-After` 则 `min(retry_after, 30.0)`，否则 `min(2 ** attempt, 8) + random.uniform(0, 0.5)`。**这两处本轮均不改**（`docs/org-model-change-plan.md:385` C22-1 已裁决「本次都不改」）。

## 1.4 触发条件与执行顺序

### 1.4.1 现状顺序（实查，逐行可核）

```
1. 请求进入 webapp/main.py:47-55
   └─ path 不以 "/api/" 开头 → 直接 call_next（静态资源直通，不计数）
2. 身份解析 main.py:56-61
   └─ Authorization: Bearer → security.verify_token() → payload.get("sub")
   └─ 无令牌 / 无效 → uid = ""（落 "anonymous" 桶）
   └─ 注意：中间件只取 sub，不查 DB、不取 role、不取 department
3. 模型标签 main.py:62-66
   └─ /api/settings → "settings"；/api/chat/ → "llm"；其余 → ""
4. 计数 observability.py:197-229 rate_check(...)
   └─ keys = {user, tenant, endpoint, model}（:202-207，现状含 tenant）
   └─ for dim in RATE_DIMENSIONS（:211）依次检查 rate_rules 表 enabled（:212-214）
   └─ 每个维度：清过期（:218-219）→ len(q) >= lim ?
        ├─ 命中：_persist_hit() 落台账 + WARN 日志 → 【立即 return 命中信息，短路】
        └─ 未命中：q.append(now)（:228），继续下一维度
5. 命中 → main.py:68-81 返回 429 + 4 响应头 + 扁平 body
   未命中 → main.py:82 call_next(request)
6. 预算检查【不在中间件】：仅 POST /api/evaltest/run → run_offline_eval()
   开头调用 check_budget()（observability.py:421-426），且只拦离线评估运行
```

**短路规则**：三维按 `RATE_DIMENSIONS` 元组顺序检查，**第一个命中即返回**，`rate_hits` 只记这一条，后续维度不再计数（`observability.py:220-227`）。这保证错误体里的 `dimension` 唯一、可解释；副作用是「同时超两个维度」时只报第一个。

### 1.4.2 目标态顺序（本轮建议）

```
请求进入 → 静态直通判断
  → 身份解析（服务端派生 principal：sub + DB 实时 role / department_path / 授权版本）
  → 三维计数（user → endpoint → model，逐维短路；department 不参与）
  → 预算 / 额度检查（目标态：429 QUOTA_EXCEEDED / kind=quota_exceeded / 不重试；本轮保留现状 HTTP 200 + budget_exceeded，见 §1.3.5）
  → 放行
  → 业务处理：列表 / 检索返回前逐条 decide() 复核（见 §2.2 V-01/V-02）
  → 旁路 record_usage() 记 department 事件快照
```

把预算检查前移到放行之前，是为了让「配额耗尽」与「频率超限」在同一层返回统一的 429 契约；**该段属目标态设计，本轮不落地**——主理人裁定预算耗尽保留现状（已实现阻断 / 错误码契约未统一）。现状放在业务函数内、且只覆盖离线评估，是演示切片的已知边界（`docs/enterprise-readiness.md:142/:233`）。

### 1.4.3 `Retry-After` 的计算方式

- 服务端：`webapp/observability.py:225` → `max(1, int(win - (now - q[0]))) if q else win`。命中时 `q` 必非空（已满足 `len(q) >= lim`），故取**窗口内最早一次请求到当前的剩余秒数**，下限 1 秒、上限即 `window_s`（60s）。
- 响应头：`webapp/main.py:77` → `Retry-After: str(hit["retry_after"])`。
- 客户端：`webapp/providers.py:100-102` → 有 `Retry-After` 就听它并封顶 `min(retry_after, 30.0)`；没有则 `min(2 ** attempt, 8) + random.uniform(0, 0.5)`（1/2/4/8s + 抖动，避免惊群）。
- 次数上限：`providers.py:282` `for attempt in range(3)`，且注释明示**仅限「还没开始流式」的阶段**；`quota` 分支（`:296-298`）直接抛错**不重试**。
- 命中同时落 `rate_hits` 台账与 `eval_logs` WARN（`_persist_hit()`，`:232-246`），可回放、可核对。

## 1.5 已实现 / 本轮建议 / 未做 对照

| 能力 | 状态 | 代码路径与函数名 |
|---|---|---|
| 进程内滑动窗口多维计数 | **已实现（演示级）** | `webapp/observability.py::rate_check()`、`:170-171 _WINDOWS/_rate_lock`；`webapp/db.py::rate_rules` |
| 429 + 4 响应头 + 命中台账 + WARN 日志 | **已实现** | `webapp/main.py:68-81`、`webapp/observability.py::_persist_hit()` |
| `rate_rules` 规则与 admin 可调 | **已实现**（撰写时 4 条；**主理人裁定本轮收敛为 3 条**） | `webapp/db.py:317-323`、`db.py::_migrate`（收敛旧规则行）、`webapp/routers_evaltest.py::put_ratelimit()` |
| 两类 429 区分（上游侧）、退避与不重试 | **已实现** | `webapp/providers.py::classify_429()` / `::backoff_seconds()` / `_stream_raw():282-298` / `:334-335` |
| 预算 `warn` / `block` 与超限留痕 | **已实现阻断 / 错误码契约未统一**（仅离线评估、全局累计；本轮保留现状 200 + `budget_exceeded`） | `webapp/observability.py::check_budget()` / `run_offline_eval():421-426` |
| **三维度 + department 归因标签**（取消第四维计数） | **主理人裁定采纳，本轮落地** | `RATE_DIMENSIONS=("user","endpoint","model")`、`rate_rules` 只插 3 条、`rate_check(..., department=...)` 保留参数但不计数只写日志归因 |
| **统一 `code` / `kind` / `message` / `trace_id` / `policy` 错误体** | **本轮落地修改**（扁平结构向后兼容，原字段与 4 响应头全保留） | `webapp/main.py:69-75` 补字段；命名主干 `backend/app/core/errors.py:54-62` |
| **预算耗尽返回 429 `QUOTA_EXCEEDED`** | **下期对齐（本轮不改）** | 现状 200 + `budget_exceeded`；`constants.py:173` 的 413 属**已知不一致**，本轮不动 backend 骨架（用户明确不改 backend 契约） |
| **接口维度按路径分组配额** | **本轮建议** | 现状 `rate_check()` 以原始 `path` 单值计数 |
| 实时 TPM 计数 | **未做** | `usage_events` 仅事后核算 |
| Redis 令牌桶 / 网关层 / 全局并发闸 / 熔断 / 压测基线 | **未做（Won't）** | `docs/enterprise-readiness.md:83/:231`；`backend` 分级配额亦属 Won't |

---

## 1.6 与并行落地代码的口径差异（撰写期实查 + 主理人裁定回执）

| 项 | 本文按用户指令给的**设计建议** | 撰写期间 `webapp/db.py` 已落地的形态 | **主理人裁定 / 处置** |
|---|---|---|---|
| 限流维度数 | **3 个参与计数**：`user / endpoint / model` | **4 个**：`user / department / endpoint / model`（`department` 3000/60s） | ✅ **采纳三维**：`RATE_DIMENSIONS=("user","endpoint","model")`、`rate_rules` 只插 3 条、`db.py::_migrate` 收敛旧 `tenant` / `department` 规则行。**「保留四维、仅作宽松归因兜底」的分支未采纳，勿按此理解** |
| 归因物理列名 | 维度名 `department` | `usage_events.department_id` + `ix_usage_department` | 统一指同一归因字段，文档表述以落地列名为准；`usage_events.department_id` 仍是五维聚合之一 |
| 存量数据 | 事件快照不回溯重写 | `_migrate_tenant_to_department()` 做幂等 `RENAME COLUMN` + 规则/配置键改名 | 迁移只改列名与键名，**不改写历史 department 值**，与「不回溯」一致 |
| `CONFIG_KEYS` 四处同步 | 本轮建议同步改名 | 撰写时 `db.py` 已改、`observability.py:252` / `routers_chat.py:129` / `routers_evaltest.py:72` 未见同步 | 主理人已收口 `observability.py`，正在收口 `main.py` / `routers_chat.py` / `routers_evaltest.py` / 前端与测试，**C22-3 四处配置键同批改完**，随后实跑全套回归 + `PRAGMA table_info` 验证 |
| 代码一致性 | — | 撰写时 `observability.py:83-85` 仍 `INSERT ... (tenant)`，`db.py` 已无 `tenant` 列 | **中间态风险已由主理人收口**（`observability.py` 的 `record_usage` INSERT / `usage_summary` / `rate_check` / `CONFIG_KEYS` / `run_offline_eval` / PLUGINS 文案已改），不会留下「列名已改但 SQL 仍写 tenant」的状态；本文不代改代码 |

> 本节是**撰写期实查记录 + 主理人裁定回执**，不是对他人改动的评价或回滚要求：本文全程只读他人改动、未修改任何源码。**「若采用落地代码的四维方案，则本文 §1.1 第 5 条理由仍成立、但需把『取消部门计数规则』改为『保留规则仅作归因兜底』并同步修订 §1.2 / §1.3」这一分支未采纳**——主理人已裁定采用「**三维计数 + 部门仅归因**」，本文 §1 主表（部门「不参与计数、无限额、不产生 429」）**与最终代码一致，不需微调**；`rate_check(..., department="default", ...)` 保留参数但不参与计数、只写日志归因。依据：用户任务 3「覆盖用户、接口、模型调用三个维度」与任务 1「按项目、部门和调用维度统计 token 消耗与成本」（部门是**统计**维度，不是限流维度）。

---

# 第二部分 · 多部门与权限设计综合复核（用户任务 4）

## 2.1 统一口径：五项逐项对应文件与函数

| 项 | 现状落点（文件:行号 / 函数名） | 现状状态 | 目标态落点 | 依据 |
|---|---|---|---|---|
| **部门隔离** | backend `models/identity.py:24-37 Department(path, parent_id, level)`；`services/permission.py:166-168` 子树判定；`services/document.py:25-36 apply_filters()`；**webapp/db.py 的 `users` 表无 `department_id` 列**（`:29-37`） | backend 演示级 / webapp **未做** | 单一 `core/visibility.py::visible_department_ids()` 纯拓扑查询 + `ScopedRepository` 候选下推 | `docs/org-model-change-plan.md` M1/M2/M6 |
| **角色层级** | backend `core/constants.py:5-10 Role`（5 值）、`:135-148` 天花板与写角色集；`services/permission.py::decide()`；webapp `deps.py:39-42 require_admin()`（仅 `admin`/`user` 两级） | 演示级 | 4 角色 `admin / dept_admin / dept_user / viewer`，`decide()` 唯一真值 | M2 / A4 |
| **资源归属** | `models/knowledge.py:81 Document.department_id`、`Document.owner_id`；`api/v1/documents.py:69/116` 构造 `Resource`；webapp `routers_docs.py:83/98 owner_id` 过滤、`routers_badcase.py:85 created_by` | **已落地（owner 级）** | 叠加部门维度 + `decide()` 逐条复核 | M2 / A13 |
| **数据可见范围** | `services/permission.py:227-238 visible_document_clause()`、`:247-258 milvus_expr()`、`:260-280 filter_candidates()`；`integrations/milvus_client.py:63-82 build_permission_expr()`；**第四处** `services/document.py:25-36 apply_filters()` | 演示级（产出**超集候选**） | 四处收敛到同一集合 + 列表侧逐条复核 + 召回后二次复核 | M2 / A13 / C28-1 |
| **越权边界** | webapp `deps.py:39-42 require_admin()`、`routers_badcase.py` 采集/查看分离；backend `api/v1/knowledge_bases.py:52-72` **无写门禁**、`api/v1/documents.py:56-61 download_local()` 只校验 key 前缀 | **部分落地，有实查缺口**（V-07） | `decide()` 唯一真值 + 全接口 `check()` | M1/M2/M8 |

**红线（现状已在代码中体现，但只覆盖了检索侧）**：`backend/app/agent/orchestrator.py:117-129 permission_filter_node()` 在召回后逐片段过 `filter_candidates()`；`:185 DEFAULT_ORDER` 固定七节点顺序；`:72-73 NodeRegistry.run()` 遇 `state.blocked` 即 `break`；`:36-44 INJECTION_PATTERNS / PII_PATTERNS` 与 `:152-161 verify_node()` 构成前置安全门禁与后置 PII 复述扫描。**缺口在列表侧**（V-01/V-02）。

## 2.2 安全漏洞逐条复核

> 全部条目均来自实读代码；「本轮是否修」统一按主理人纪律：**本轮只交付本文档、不改任何源码**，故代码类漏洞本轮均不修，每条明确指向 `docs/org-model-change-plan.md` 的**具体里程碑 / 验收编号**，不使用「后续优化」这类含糊表述。
> 严重级：高 = 可越权读取/改写他人数据；中 = 当前不可直接利用但构成设计债务或讲解失真；低 = 表述问题。

### V-01 ｜ 高 ｜ webapp 演示切片无部门树、无逐条 `decide()` 复核（列表侧越权缺口，C23-2）

- **涉及文件**：`webapp/db.py:29-37`（`users` 表）、`webapp/deps.py:39-42`、`webapp/routers_docs.py:75-99`、`webapp/routers_evaltest.py:225-236`（权限矩阵自述）、`backend/app/api/v1/documents.py:26-45`、`backend/app/services/document.py:25-36`、`docs/org-model-change-plan.md:547`（C23-2）
- **现状证据**：`users` 表 `role` 注释为 `'admin' | 'user'`（`db.py:35`），**无 `department_id` 列、无部门表**；`routers_docs.py:77-83` 非 admin 直接用 `SELECT * FROM documents WHERE owner_id=?` 返回；`document.py:27-36` 以 `Document.tenant_id == user.tenant_id` + `or_(department_id == 本部门, is_(None))` + sensitivity 下推 SQL 后**直接返回**，无逐条判定；`routers_evaltest.py:230-231` 自述「仅 admin / user 两级」「行级 ACL 未实现」。
- **攻击 / 误用路径**：候选集是**超集**而非精确集。显式 deny、敏感度超角色上限、owner 不符的文档只要 SQL 条件命中，就会直接进列表响应；目标态改为「同部门 + 子树可见」后，owner 过滤与部门可见会互相冲突（该挡的没挡、该放的没放）。删掉 `tenant_id` 硬过滤后这个缺口完全暴露。
- **修正方案**：① M2 在列表接口取到候选后逐条 `PermissionService(db, user).check(Action.READ, resource, raise_on_deny=False)`，**分页与 total 必须在过滤之后计算**；② M6 webapp 补 `users.department_id` + 部门树 + 按部门裁剪候选（`webapp/rag.py::_load_ready_chunks()` 之后、算 embedding 之前）；③ 四处口径收敛到 `visible_department_ids()`。
- **修正后验证**：M8 **A13 一致性对拍**（文档列表最终集合 vs 检索召回最终集合差集为空；「候选集内但 `decide()` 应拒绝」负例；双向覆盖）；`pytest backend/tests/test_permission.py -q`；`python webapp/selftest.py` 33/33 + 新增部门越权用例。
- **本轮是否修**：**不修**。本轮交付物仅本文档且明令禁止改源码；落点为 **M2（列表侧逐条复核）、M6（webapp 部门树/成员视图）、M8（A13 第一验收项）**，C23-2 已被 plan 升为 Must。

### V-02 ｜ 高 ｜ backend 列表与详情接口只靠 SQL 下推，缺召回后二次复核（与检索侧不对称）

- **涉及文件**：`backend/app/services/permission.py:227-238 visible_document_clause()`、`:260-280 filter_candidates()`；`backend/app/api/v1/documents.py:26-45 list_documents()`；`backend/app/services/document.py:25-36 apply_filters()`；`backend/app/services/search.py:159-174 hybrid_search()`（`filter_candidates()` 在融合后又过一次）
- **现状证据**：检索侧 `search.py:174` 与 `orchestrator.py:123` 都调 `filter_candidates()`；列表侧 `documents.py:44` 只调 `doc_service.list_documents()` → `apply_filters()` 后 `repo.paginate()` 即返回。**`apply_filters()` 是 D8 未点名的第四处独立口径**（本部门 or NULL + sensitivity），与 `visible_document_clause()`、`build_permission_expr()` 三者口径不一致。
- **攻击 / 误用路径**：同一用户、同批文档，列表与检索可能给出不同集合；攻击者挑「SQL 条件命中但 `decide()` 应拒绝」的文档，从列表接口拿到标题/元数据/下载链接，绕过检索侧的二次校验。
- **修正方案**：M2 把 `apply_filters()` 一并收敛到 `visible_department_ids()`，并在列表接口补逐条 `check()`；A13 列表集合对拍要求 **`apply_filters()` 与 `visible_document_clause()` 两个入口都测**。
- **修正后验证**：A13 第 1/3/4 条（集合相等、判定收敛证明、双向覆盖）；`pytest backend/tests -q`；新增负例（本部门挂显式 deny / 敏感度超限 / owner 不符）。
- **本轮是否修**：**不修**。落点 **M2（第四处口径收敛 + 列表侧复核）、M8（A13 两入口对拍）**，依据 C28-1 与 C23-2。

### V-03 ｜ 高 ｜ `filter_candidates()` 构造 `Resource` 缺 `owner_id`、`visibility` 兜底、PG 回查不足（C28-2「失败即越权」）

- **涉及文件**：`backend/app/services/permission.py:260-280 filter_candidates()`、`:64-81 _grant_applies()`、`:84-107 evaluate_fine_grained()`；`backend/app/services/search.py:100-107`、`:146-153`（召回 dict）；`backend/app/integrations/milvus_client.py:112 output_fields`
- **现状证据**：`Resource(...)` 构造（`:266-276`）**无 `owner_id` 字段**，`visibility=hit.get("visibility") or "dept"` 兜底；两处召回 dict 与 `output_fields` 只含 `chunk_id/doc_id/kb_id/dept_id/sensitivity/.../acl`，**不含 `owner_id` / `visibility`**；`docs/permission-model.md` 的 `chunks.acl` 逐段落预留依赖 `acl` 正确传入。
- **攻击 / 误用路径**：`decide()` 升为唯一权威后，owner 分支（`:169-170`）、`organization` 可见分支、显式 deny、逐段落 ACL 钩子可能拿不到正确输入 → 要么把无权片段判为 allow（越权进 LLM 上下文），要么把有权片段判为 deny（漏召回）。`visibility` 兜底 `"dept"` 会把「全组织可见」文档误按部门处理。
- **修正方案**：M2 在二次校验前按 `chunk_id` **一次批量回查 PG** 补齐 `owner_id`（`documents.owner_id`）、`visibility`（`knowledge_bases.visibility`，唯一物理落点）、`acl`（`chunks.acl`）、`sensitivity`；**回查异常 / 孤儿向量 / 必需字段缺失一律 deny 该条，不静默放行**；不新增 Milvus 标量。
- **修正后验证**：A13 第 2/3 条；`test_permission.py` 补 hydrate 成功 / 回查异常 / 孤儿 / 字段缺失四类输入，断言失败条不进 `kept`；`logger.warning("hydrate-failed")` 计数。
- **本轮是否修**：**不修**。落点 **M2（Resource 属性补齐）、M8（A13）**，依据 C28-2。

### V-04 ｜ 高 ｜ 进度 / 对象 / 缓存 key 与身份快照的关系：SSE 订阅无 `decide()` 把关

- **涉及文件**：`backend/app/integrations/redis_client.py:25-34 tenant_key() / progress_channel() / progress_snapshot_key()`、`backend/app/integrations/object_storage.py:166-168 object_key()`、`backend/app/api/v1/uploads.py:78-90 batch_stream()`、`backend/app/services/progress.py:93-107`、`docs/org-model-change-plan.md:346`
- **现状证据**：key 形如 `kb:{tenant_id}:{domain}:...`（`redis_client.py:26`）；`uploads.py:78` 注释自述「越权保护靠 channel / 快照查询里强制携带的 `tenant_id`，跨租户订阅只会拿到空快照」；`batch_stream()` 建连时**没有 `PermissionService.check()`**；`docs/org-model-change-plan.md:346` 已明确「进度 key 改为静态 `ORG_NAMESPACE + batch_id` 后，现状隐式隔离消失……**端点 `decide()` 订阅校验与进度 key 切换必须同一批次发布**，列为发布一票否决门禁」。
- **攻击 / 误用路径**：单租户下 `tenant_id` 恒为 `default`，任何登录用户只要知道 / 猜到 `batch_id`（UUID）即可订阅任意批次的进度与快照，读到他人文件名与处理状态；改成静态命名空间后若不同批发布订阅校验，窗口直接放大为全库。另：`object_key()` 历史前缀是 `tenant_id`，转岗后若按当前部门重算 key，历史对象会 404（`plan:348`）。
- **修正方案**：M5 进度 key = `ORG_NAMESPACE + "progress-snapshot" + batch_id`（**不含 `department_id`、不含授权快照**，保证转岗后 SSE 断连重连仍读到旧快照）；订阅资格由端点 `decide()` 把关；检索缓存 key 另带授权快照（见 V-05）；对象 key 一级前缀改静态 `ORG_NAMESPACE`，**库存 key 字符串直读、不重算**；键切换与订阅校验**同批发布**。
- **修正后验证**：A7（转岗后历史 `usage_events.department` 不变、旧授权快照不命中）、SSE 断连重连 + `Last-Event-ID` 续传、跨部门订阅返回 403、历史对象下载 200。
- **本轮是否修**：**不修**。落点 **M5（任务与 key）、`plan §7.1.1` 发布一票否决门禁、M8（A7）**。

### V-05 ｜ 中高 ｜ 检索结果缓存可能串权限（key 无授权版本，命中直接返回不过 `decide()`）

- **涉及文件**：`backend/app/services/search.py:51-59 _cache_key()`、`:161-164 hybrid_search()` 缓存命中分支、`:174` 融合后 `filter_candidates()`、`:206 set_json()`；`backend/app/integrations/redis_client.py:45-49 set_json()`
- **现状证据**：`_cache_key()` 的 raw = `{"tenant", "user", "role", "dept", **req}` 的 `sha256[:24]`，**不含 `grants` / 授权版本 / `department_path`**；命中时 `:163-164` 直接 `return SearchResponse(**cached, cache_hit=True)`，**不再过 `decide()`**；TTL 300s（`:206`）。写入前确实过了 `filter_candidates()`（先过滤后写，这一点是正确的）。
- **攻击 / 误用路径**：`ACLGrant` 增删、部门移动（子树可见范围变化）、角色变更后，`user/role/dept` 三元组可能不变，旧缓存在 300s 内继续返回旧可见集合 → **授权收回后仍能看到已无权片段**（越权），或授权新增后 300s 内看不到（可用性）。`plan:347` 实查 `grant_version|authz_version` 全仓零命中，即当前根本没有让缓存失效的版本号。
- **修正方案**：M5 缓存 key 改 `ORG_NAMESPACE + authz_snapshot + user + query 摘要`，`authz_snapshot = hash(role, department_id, department_path, sorted(visible_department_ids()), grant_version, ORG_NAMESPACE)`；命中后仍**轻量复核 `decide()`**；`grant_version / authz_version` 三触发点（部门树变更、`ACLGrant` 写入/撤销、转岗/改角色）统一 `INCR`，**确认就位后才允许切换缓存 key**（`plan:347`）。
- **修正后验证**：A13 第 7 条（部门树变动即时重算）、A7（旧授权快照不可命中）、缓存串部门用例；`pytest backend/tests -q`。
- **本轮是否修**：**不修**。落点 **M5（缓存授权快照）、`plan §7.1.1`（版本计数器就位门禁）、M8（A7 / A13 第 7 条）**。

### V-06 ｜ 高 ｜ `department_id` 来自请求体时缺少授权复核，`setattr` 无字段白名单

- **涉及文件**：`backend/app/schemas/knowledge.py:10-13 KbCreate.department_id`、`backend/app/api/v1/knowledge_bases.py:30-44 create_kb()`、`:52-61 patch_kb()`；`backend/app/schemas/auth.py:42-46 UserPatch.department_id`、`backend/app/api/v1/users.py:47-57 patch_user()`、`backend/app/api/v1/users.py:30-44 create_user()`
- **现状证据**：`create_kb():34` 直接 `department_id=payload.department_id or user.department_id`，只判 `user.is_admin`（`:31-32`），**未校验目标部门是否在授权范围内**；`patch_kb():55-57` 与 `patch_user():52-54` 用 `for key, value in payload.model_dump(...): setattr(obj, key, value)` 遍历写入全部字段，**无逐字段白名单、无 `decide()` 目标复核**。
- **攻击 / 误用路径**：任何能调到这些接口的主体可把知识库 / 用户挂到任意部门，绕过部门树边界；`setattr` 还能写入 `role` / `status` 以及将来新增的敏感字段（字段一旦加进 schema 就自动可写）。目标态红线要求「请求体 `department_id` 只能作为资源筛选目标或授权客体，经 `decide()` 复核后生效」，现状未落实。
- **修正方案**：M1 全部查询收敛 `ScopedRepository`；M2 主体归属 `department_id` 只由 DI 从 DB 产生，请求体的 `department_id` 一律视作目标/客体并过 `PermissionService.check()`；`setattr` 改**显式字段白名单**；保留七类合法入参（`POST /users`、`PATCH /users/{id}`、`POST /departments`、`move`、`members`、`GET /users?department_id=`、`POST /acl-grants`）。
- **修正后验证**：A13 第 5 条（请求体伪造 `department_id` 指向兄弟部门，两入口返回集合与不传时一致）、20+ 条越权用例集、`pytest backend/tests -q`。
- **本轮是否修**：**不修**。落点 **M1（收口点）、M2（判定与字段白名单）、M8（A13 第 5 条）**。

### V-07 ｜ 高 ｜ 写接口 admin 门禁与「采集/查看权限分离」：webapp 正确，backend 存在绕过

- **涉及文件**：`webapp/deps.py:39-42 require_admin()`、`webapp/routers_badcase.py:70-93 / :96-105 / :120-121 / :156`、`webapp/routers_evaltest.py:61 / :69 / :127 / :165 / :198`、`webapp/routers_chat.py:244`；**缺口** `backend/app/api/v1/knowledge_bases.py:47-72 get_kb/patch_kb/delete_kb`、`backend/app/api/v1/documents.py:56-61 download_local()`
- **现状证据（正确的一半）**：webapp 侧 `create_badcase()` 普通用户可写且 `created_by=user["id"]` 由服务端取（`routers_badcase.py:85`），查看 / 导出 / 更新均 `Depends(require_admin)`（`:105/:156/:121`）；评估测试全部写接口 `require_admin`；`docs/enterprise-readiness.md:112-113` 的「采集与查看权限分离」约定在 webapp 侧**成立、无绕过**。
- **现状证据（缺口的一半）**：backend `get_kb()` / `patch_kb()` / `delete_kb()` **既无 `is_admin` 判断，也无 `PermissionService.check()`**，仅靠 `TenantRepository(db, user.tenant_id).get(...)`；`download_local()` 只校验 `key.startswith(f"{user.tenant_id}/")`（`documents.py:59`），不查该 key 是否属于用户可见文档。
- **攻击 / 误用路径**：单租户下 `tenant_id` 恒为 `default`，**任意登录用户可 PATCH / DELETE 任意知识库**（改 `visibility`、`department_id`、`default_sensitivity`，或软删）；只要拿到对象 key 字符串（如从列表响应、日志、分享链接）即可下载同租户任意原文件，跳过 `decide()`。
- **修正方案**：M1 全部列表/详情查询收敛 `ScopedRepository`；M2 所有写接口与详情接口补 `PermissionService.check()`；`download_local()` 改为按 `doc_id` 查 `Document` 并过 `decide()` 后流式返回，**不再靠 key 前缀当越权防线**（前缀校验保留作目录穿越防护，但不再承担授权）。
- **修正后验证**：M8 20+ 条越权用例集；`pytest backend/tests -q`；`webapp/selftest.py` 的 C24/C31/C32 保持通过；新增「普通用户 PATCH 他人 KB → 403」「知道 key 但无文档权限 → 403」。
- **本轮是否修**：**不修**。落点 **M1（ScopedRepository）、M2（全接口 check）、M8（越权用例集）**。

### V-08 ｜ 中 ｜ JWT 载荷仍带 `department_id` / `role` 而服务端不读（权限语义陈旧）

- **涉及文件**：`backend/app/core/security.py:41-51 create_access_token()`（extra = `{tenant_id, department_id, role}`）、`:54-60 create_refresh_token()`、`backend/app/core/deps.py:44-68 get_current_user()`；`webapp/security.py:45-53 make_token()`（payload 带 `role`）、`webapp/deps.py:20-36 get_current_user()`
- **现状证据**：`create_access_token():46-50` 明确把 `tenant_id / department_id / role` 写进 JWT；`deps.py:51` 只解 `payload["tenant_id"]`（用于构造 `TenantRepository`），`Principal` 的 `role / department_id / department_path` 全部从 `user` ORM 与 `user.department.path` 实时读（`:61-68`，其中 `:67` 是必须保留的实时读）；webapp 侧 `make_token():49` 带 `role`，但 `webapp/deps.py:22-35` 用 `SELECT ... FROM users WHERE id=?` 从 DB 取 `row["role"]` 返回，**JWT 里的 role 从未被读取**。
- **攻击 / 误用路径**：**当前不可直接利用**（服务端正确地从 DB 派生，这是好设计）。风险是陈旧语义误导后续维护：一旦有人图省事改成信任 JWT 的 `role` / `department_id`，转岗或改角色后旧令牌在有效期内仍带旧身份，直接破坏「转岗即时生效」与「旧授权快照不可命中」；同时把与判定无关的身份字段长期放进可解码的 JWT payload 属于不必要的信息暴露。
- **修正方案**：M1 JWT 收敛为**只带 `sub` + 标准字段**（`typ / iat / exp / jti`），三个身份字段全删；`deps.py:67` 从 DB 实时读 `user.department.path` **必须保留**；`security.py` 与 `deps.py` **同批改、同批回退**（`docs/org-model-change-plan.md:362` 已列「JWT 载荷语义不可逆，须一起回」）；`docs/org-model-change-plan.md:350` 明确本阶段**不轮换 `JWT_SECRET_KEY`**。
- **修正后验证**：A13 第 5 条（JWT 注入旧 `role` / `department_id` 负例，返回集合与正常令牌一致）、转岗即时生效用例、`pytest backend/tests -q`。
- **本轮是否修**：**不修**。落点 **M1（`constants`/`security`/`deps` 同批）、`plan §7.2`（成组回滚）、M8（A13 第 5 条）**。

### V-09 ｜ 中 ｜ 文档与模型的口径失真 / 技术债未标注

- **涉及文件**：`docs/permission-model.md:8-13 / :23-31 / :50 / :75 / :95-96`（仍写共享 Schema + `tenant_id` 行级隔离、`tenants` 表、`tenant_admin` 示例）、`docs/enterprise-readiness.md:7 / :43 / :59 / :93-94 / :234`（仍写「单租户 default」「未建部门树」「多租户/多部门…未实现隔离」）、`backend/app/db/repository.py:18 TenantRepository`、`backend/app/models/identity.py:14-21 Tenant`、`webapp/routers_evaltest.py:225-229` 规则集
- **现状证据**：如上；`git status --short` 为空说明本轮代码尚未改名；`webapp/observability.py:36-37` 仍是 `tenant` 维度。
- **攻击 / 误用路径**：不是运行时漏洞，是**评审 / 面试讲解失真**：对外称「已实现多租户隔离」或「已实现完整多部门」都与代码不符；把 `TenantRepository` 当成真有租户隔离，会掩盖 V-07 的越权（单租户下它其实什么都没隔离）。
- **修正方案**：M7 按 `docs/org-model-change-plan.md:292-296` 的**六类标注失真**逐条纠偏；`docs/CHANGELOG.md` §7 新增一条修订记录（历史条目不回改）；`enterprise-readiness.md §3.1` 与 checklist F1 的「演示切片四维限流 / 生产骨架架构预留」混写一并校准。
- **修正后验证**：文档三态抽查（已实现 / 只设计 / 未做）、A6 清零检索式（代码层，属后续阶段）、面试讲解问答。
- **本轮是否修**：**文档层面已修**——本文档 §1.5、§2.1、§2.3、§2.4 已给出统一口径与三态标注；**既有文档改标与源码改名不修**，落点 **M7（六类失真 + CHANGELOG §7）** 与 **M1–M3（若恢复移除计划）**。

> **漏洞条数：9 条**（高 6 条：V-01/02/03/04/06/07；中高 1 条：V-05；中 2 条：V-08/09）。7 项指定覆盖全部命中：①→V-01、②→V-03、③→V-04、④→V-05、⑤→V-06、⑥→V-07、⑦→V-08；V-02 与 V-09 为实查补充。

## 2.3 修正路线汇总（不含糊指向）

| 漏洞 | 本轮 | 实施里程碑 | 验收编号 / 门禁 |
|---|---|---|---|
| V-01 | 不修（仅文档） | **M2** 列表侧复核、**M6** webapp 部门树 | M8 **A13 第一验收项** |
| V-02 | 不修（仅文档） | **M2** 第四处口径收敛 + 列表复核 | M8 A13 两入口对拍 |
| V-03 | 不修（仅文档） | **M2** Resource 属性补齐（C28-2） | M8 A13 第 2/3 条 |
| V-04 | 不修（仅文档） | **M5** 进度 key + SSE 订阅把关 | `plan §7.1.1` 发布一票否决门禁、M8 A7 |
| V-05 | 不修（仅文档） | **M5** 缓存授权快照 + 版本计数器 | `plan §7.1.1`、M8 A7 / A13 第 7 条 |
| V-06 | 不修（仅文档） | **M1** ScopedRepository、**M2** 字段白名单 + 复核 | M8 A13 第 5 条 |
| V-07 | 不修（仅文档） | **M1** 查询收口、**M2** 全接口 check | M8 20+ 越权用例集 |
| V-08 | 不修（仅文档） | **M1** `security.py`/`deps.py` 同批 | `plan §7.2` 成组回滚、M8 A13 第 5 条 |
| V-09 | **文档层面已修**（本文） | **M7** 六类失真 + CHANGELOG §7 | 文档三态抽查 |

> 里程碑定义见 `docs/org-model-change-plan.md:318-328`（M0 冻结声明 → M1 五个收口点 → M2 模型与契约 → M3 数据迁移 → M4 向量重建 → M5 任务与 key → M6 演示切片 → M7 文档纠偏 → M8 回归与准出）；回归三分区与 A1–A13 编号见 `plan:400-407 / :444-446`。

## 2.4 明确声明：多租户是未来研发方向，当前是已知技术债

**用户新指令（覆盖历史口径）**：系统为**多用户多部门架构，暂不实现多租户**；多租户仅作为**未来研发方向**写入文档。

**当前代码事实（实查）**：

- backend 仍完整保留多租户时代的命名与结构：`db/base.py:36-38 TenantMixin`、`models/identity.py:14-21 Tenant` 表、全表 `tenant_id` 列、`db/repository.py:18 TenantRepository`、`core/security.py:47` JWT 里的 `tenant_id`、`integrations/redis_client.py:25-34` 的 `kb:{tenant_id}:...` key、`integrations/milvus_client.py:36` 的 `tenant_id is_partition_key`。
- webapp 演示切片是 `SQLite + 进程内队列` 替身，`observability.py:36-37` 仍有 `tenant` 维度、`main.py:67` 仍硬编码 `tenant="default"`。
- **本轮不改数据模型**：主理人明确「代码在变更中，你只出文档与建议，不改代码」；本文所有 `department` 相关表述均为**目标态设计**。

**定性（三句， must 对齐）**：

1. 这**不是**「已实现多租户」——单租户 `default`、无租户注册 / 路由 / 隔离策略 / 分级配额，`TenantRepository` 在恒值 `tenant_id` 下不产生任何隔离效果（这也是 V-07 能越权的原因）。
2. 这也**不是**「已实现完整多部门」——backend 有 `Department(path, parent_id)` 树但判定口径分散在四处、列表侧无逐条复核（V-01/V-02），webapp 连 `department_id` 列都没有。
3. 这是**已知技术债**：命名与文档停留在多租户时代，与「多用户多部门」的目标架构不一致；本轮把它显式登记，不隐藏、不美化。

**未来研发方向（只设计 / 未做）**：若将来真要引入多租户，需要独立 Schema / 独立库 + 连接池按租户路由（`docs/permission-model.md:100` 已列）、租户级配额与全局并发闸、全链路 `tenant_id` DI、跨租户 expr 过滤，并**重新评估本文 V-01～V-09 的全部修正**（因为隔离边界会从「部门超集」变回「静态唯一值」）。该项不在本轮与当前排期。

**范围张力（诚实标注，需主理人知悉）**：`docs/org-model-change-plan.md` 的目标 **M1–M3 是「彻底移除 `tenants` 表与所有 `tenant_id` 列」**（`:145 D1`、`:180 0002 七阶段`），与本轮用户新指令「暂不实现多租户、**不改数据模型**」并不相同。本文档按**用户新指令（覆盖历史口径）**执行：保留历史命名、只产出文档与设计建议；上表引用 plan 的 M 阶段，仅作为「漏洞将来实施时的落点与验收编号」，**不代表本轮执行移除计划**。若主理人决定恢复移除计划，需重新确认本 §2.4 的技术债表述，以及 V-01～V-09 的修正优先级与 §1.1 的维度改名节奏。

---

## 附录 A · 实查证据索引（文件:行号 → 结论）

| 证据 | 结论 |
|---|---|
| `git status --short`（空） | 本轮撰写时代码未改名，`tenant` 口径仍在 |
| `webapp/observability.py:36-37` | `DIMENSIONS` / `RATE_DIMENSIONS` 仍含 `tenant` |
| `webapp/observability.py:197-229` | `rate_check()` 四维计数、短路、`Retry-After` 公式 |
| `webapp/observability.py:97-125 / :421-426` | 预算全局累计、只拦离线评估、返回 200 而非 429 |
| `webapp/observability.py:232-246` | `_persist_hit()` 落 `rate_hits` + WARN |
| `webapp/main.py:47-82` | 中间件顺序、身份只取 `sub`、model 标签、4 响应头、429 体 |
| `webapp/providers.py:78-102 / :282-298 / :334-335` | `classify_429` / `backoff_seconds` / 3 次上限 / quota 不重试 |
| `webapp/db.py:29-37 / :157-195 / :317-325` | `users` 无 department 列、`usage_events.tenant`、4 条默认规则、`CONFIG_KEYS` 种子 |
| `webapp/deps.py:20-42` | `get_current_user()` 从 DB 取 role；`require_admin()` |
| `webapp/routers_badcase.py:70-105 / :120-121 / :156` | 采集可写 / 查看导出更新需 admin，分离成立 |
| `webapp/routers_docs.py:75-99` | 非 admin 仅 `WHERE owner_id=?` |
| `webapp/security.py:45-53` | JWT payload 带 `role` |
| `backend/app/core/security.py:41-60` | JWT payload 带 `tenant_id/department_id/role` |
| `backend/app/core/deps.py:44-68` | 只读 `payload["tenant_id"]`，Principal 从 DB 构造，`:67` 实时读 path |
| `backend/app/core/constants.py:164-179` | `RATE_LIMITED=429`、`QUOTA_EXCEEDED=413` |
| `backend/app/core/errors.py:51-62` | 统一错误体 `{code,message,detail,trace_id}` |
| `backend/app/db/repository.py:18-45` | `TenantRepository` 强过滤（恒值下无隔离效果） |
| `backend/app/services/permission.py:64-107 / :227-280` | `_grant_applies` / `evaluate_fine_grained` / 四处口径 / `filter_candidates` 缺 owner_id |
| `backend/app/services/document.py:25-36` | 第四处独立口径 `apply_filters()` |
| `backend/app/services/search.py:51-59 / :161-174 / :206` | 缓存 key 无授权版本、命中直接返回、先过滤后写 |
| `backend/app/integrations/milvus_client.py:36 / :63-82 / :112` | partition key、expr 首条件、`output_fields` 无 owner/visibility |
| `backend/app/integrations/redis_client.py:25-34` | `kb:{tenant_id}:...` key 规范 |
| `backend/app/api/v1/uploads.py:78-90` | SSE 订阅仅靠 key 内 tenant_id，无 `decide()` |
| `backend/app/api/v1/knowledge_bases.py:30-72` | create 有 admin 门禁；get/patch/delete **无门禁** |
| `backend/app/api/v1/documents.py:56-61` | `download_local` 只校验 key 前缀 |
| `backend/app/agent/orchestrator.py:36-44 / :117-129 / :152-161 / :185` | 七节点、前置守卫、中置权限过滤、后置 PII、block 即 break |
| `backend/app/integrations/llm.py:63 / :74 / :89-114` | 模型层无重试、抽取式降级 `ExtractiveFallbackLLM` |
| `docs/enterprise-readiness.md:52-83 / :87-115 / :192-204 / :229-238` | 四维默认值、429 契约、六层隔离、验收基线、已知限制 |
| `docs/permission-model.md:8-13 / :23-31 / :95-104` | 判定顺序、共享 Schema 行级隔离表述 |
| `docs/org-model-change-plan.md:17 / :40 / :292-296 / :318-328 / :346-350 / :400-407 / :444-446 / :547` | 回归基线、失真六类、里程碑、发布门禁、A13、C23-2 |

## 附录 B · 三态标注与术语

| 术语 | 含义 |
|---|---|
| **已实现** | 本仓库有可运行代码；演示级 = 可跑但为替身（进程内窗口 / SQLite / 无真实外部依赖） |
| **本轮建议** | 只有设计与文档，未落地；不得对外称已实现 |
| **未做（Won't）** | 明确不在当前范围（Redis 令牌桶、网关层、全局并发闸、熔断、压测、容灾、监控告警、SSO、内容安全 PII） |
| **候选集超集** | `visible_document_clause()` / `build_permission_expr()` / `apply_filters()` 只做 SQL / expr 下推，产出的是「可能有权」的超集，**不是**精确授权结论 |
| **最终授权** | 只由 `backend/app/services/permission.py::decide()` 逐条判定；列表侧当前缺这一步（V-01/V-02） |
| **事件快照** | `department_id` 在事件发生时写入台账，转岗不回溯改写历史记录 |

## 附录 C · 本轮边界

- ✅ 只新增本文件 `docs/rate-limit-and-security-review.md`；未修改任何 `.py` / `.js` / `.html` / 其它 `.md`。
- ✅ 未触碰 `eval/`（含 `__TBC__` / `NOT_COMPARABLE` / `real_api`）、`demo/`（`run_id=RUN-095a2a4a40c6` 与 `config_snapshot`）、`evalbench/`、`docs/CHANGELOG.md`。
- ✅ 未运行任何真实外部 LLM 调用；全部结论来自静态实读代码与文档。
- ✅ 未直连其他成员沟通，跨成员事项（M 阶段归属、frontend / data / qa 分工）由主理人中转。
- ✅ 撰写期间出现的 `webapp/db.py` / `webapp/providers.py` / 静态资源等改动**均非本轮产出**（本轮只 `Write` 了本文档一次，其余操作全是只读 `Read` / `Grep` / `git diff` / `git status`）；已在文首与 §1.6 如实标注并行变更与中间态一致性风险，未回滚、未覆盖、未代改。
- ✅ 附录 A 的行号基于撰写开始时的代码快照；并行改动可能使个别行号偏移，结论与函数名仍可核（§1.6 已列受影响项）。
- ✅ **修订记录（主理人裁定后）**：按裁定仅做小标注与状态措辞修订——文首补裁定回执、§1.1/§1.2 标「三维已裁定采纳」、§1.3 新增「本轮落地范围」说明、§1.3.1 改为「已知不一致 / 下期对齐（本轮不改 backend 骨架）」、§1.3.2 示例改为**扁平结构向后兼容 + 补 5 字段**、§1.3.5 / §1.4.2 / §1.5 标「预算耗尽本轮保留现状：已实现阻断 / 错误码契约未统一」、§1.6 标「四维分支未采纳，主理人裁定三维」。**未改任何源码**；V-01..V-09 九条漏洞、M1/M2/M5/M6/M7/M8 落点与 §2.4 范围张力说明**原样保留**。
