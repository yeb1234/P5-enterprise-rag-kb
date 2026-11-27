# 企业级就绪改造 · 模块化改动清单、限流 / 权限 / 成本规则与验收标准

编制：2026-11-12 ｜ 适用：`webapp/` 可运行演示切片（A/B 档骨架）

> **边界声明**：本文记录的是**本次改造真实落地的内容**，不是上线承诺。
> 项目仍是「可运行开发框架骨架 + 工程实现版」：单实例、SQLite、进程内队列、
> 单租户（`default`）。文中凡标「演示级 / 架构预留 / 未做」的，不得对外称已实现。
> `eval/` 冻结的离线占位体系（`__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断）**未被改动**。

---

## 1. 模块化改动清单

| # | 模块 / 文件 | 类型 | 改动内容 | 验证 |
|---|---|---|---|---|
| 1 | `webapp/static/i18n.js` | 新增 | 632 条「中文原文即 key」字典；`t()` 精确→片段替换→原文兜底；TreeWalker 文本节点扫描；`data-i18n` / 富文本 / placeholder / title / aria-label；无 value `<option>` 固化中文提交值；MutationObserver 覆盖动态 DOM；`setLang()` 无刷新可逆切换 | `node --check`；覆盖校验：HTML 中文片段 277 条，仅「中文」语言选项与双语 aria-label 刻意保留 |
| 2 | `webapp/static/theme.js` | 新增 | 6 套主题色 × 明/暗两模式**正交**；三份独立 localStorage（`kb_lang` / `kb_theme` / `kb_mode`）互不覆盖；head 同步执行 + `kb-boot` 首帧禁用过渡防闪烁；`color-scheme` 适配原生控件 | 同上 |
| 3 | `webapp/static/style.css` | 重写 | `:root` 尺寸/字体常量；`[data-theme]` 主题色（含 `--accent-rgb` 派生 soft/focus）；`[data-mode]` 中性色与状态色、`--on-primary` 保证暗色对比度；全部硬编码颜色改变量；间距/层级/状态/过渡；`focus-visible`、`prefers-reduced-motion`；760px 移动端布局 | 浏览器预览 + 双模式对比检查 |
| 4 | `webapp/static/index.html` | 改 | head 引入两引擎；顶栏语言/主题色/模式切换器；新增 `data-view="evaltest"` 独立评估测试子页（6 面板）；保留原有 10 个 view 与「企业级知识库」文案（C01/C33 约束） | `selftest` C01/C33 |
| 5 | `webapp/static/app.js` | 改 | 外观控件初始化与事件；`VIEW_META` / `LOADERS` 增加 `evaltest`；插值型文案与 toast 接 `t()`；动态按钮/提示加 `data-i18n` 保证切换可逆；新增评估页 13 个函数 | `node --check`；C33 六个 loader 名保留 |
| 6 | `webapp/db.py` | 改 | 新增 7 张表：`usage_events` / `budgets` / `rate_rules` / `rate_hits` / `eval_configs` / `eval_runs` / `eval_logs`；幂等种子（默认预算、4 条限流规则、6 项评估配置） | pytest 表结构断言 |
| 7 | `webapp/observability.py` | 新增 | 成本单价表与 `cost_of()` / `estimate_tokens()`；`record_usage()` 逐调用台账；`usage_summary()` 五维聚合；`check_budget()` 与超限留痕；`rate_check()` 四维滑动窗口；评估配置 / 运行 / 日志 / 插件注册表 / 离线评估运行 | 51 项 pytest |
| 8 | `webapp/routers_evaltest.py` | 新增 | 12 个 API：配置读写、运行记录、指标、日志、用量、预算、限流、429 探针、插件、权限矩阵 | 端到端 12 个 200 |
| 9 | `webapp/main.py` | 改 | 挂载新路由；`@app.middleware("http")` 四维限流闸门，命中返回统一 429 + `Retry-After` 等 4 个响应头 | 429 单测 |
| 10 | `webapp/routers_chat.py` | 改 | 流正常收尾旁路记录 token 用量（估算口径标注，异常不影响问答） | selftest 33/33 |
| 11 | `webapp/routers_admin.py` | 改 | 完成度 / 整体完成率钳制在 `[0,1]`（累积 badcase 超目标数时不再显示 >100%） | C16 + 新增单测 |
| 12 | `webapp/tests/test_evaltest.py` | 新增 | 10 项验收：表结构 / 只读接口 / 写接口 403 / 成本规则 / 用量范围隔离 / 预算 block 拒绝 / 离线运行留痕 / 429 头与台账 / 静态资源 / 完成率钳制 | 10/10 |
| 13 | `webapp/selftest.py` | 改 | C16 断言与服务端 `pending` 钳制口径一致，并校验完成率上界 | 33/33 |
| 14 | `docs/kb-capability-checklist.md` | 新增 | 中型公司知识库能力清单：6 个可核对来源、50 条能力对照、框架要求、差距三组、Top10 建议 | 见 §9 |

---

## 2. 独立评估测试子页（可观测能力）

入口：侧栏「质量与治理 / 评估测试」，`view-evaltest`，6 个面板。

| 面板 | 能力 | 事实源（只读真实数据，不造假） |
|---|---|---|
| 评估配置 | 档位 / 提示词版本 / 参数组 / 样本范围可配；保存即时生效；一键发起离线评估运行 | `eval_configs` 表 + `evalbench/prompts|configs` 资产 |
| 运行记录 | run 列表、可比标记、Git 分支/commit、状态分布、明细快照弹窗 | `evalbench/runs/<run_id>/config_snapshot.json` + `eval_runs` 台账 |
| 指标展示 | 按 run / 模型筛选，按任务类别 × 维度给均值与记录数、进度条 | `evalbench/runs/<run_id>/summary.json` |
| 日志追踪 | 时间线（时间 / 级别 / 事件 / 详情），按 run 与级别过滤 | `eval_logs` 表 |
| 成本与预算 | 按项目 / 租户 / 调用类型 / 模型聚合 token 与成本；预算上限、使用率、剩余、超限策略 | `usage_events` + `budgets` |
| 限流与插件 | 四维配额与命中次数、统一 429 反馈说明；11 项插件的接入模块与边界 | `rate_rules` / `rate_hits` / `observability.PLUGINS` |

**离线评估运行**：`POST /api/evaltest/run` 基于 `evalbench/datasets/samples_v1.jsonl` 做确定性评分，
零外部 API 调用、逐样本落用量台账、写追踪日志、生成带 Git 分支/commit 的 run 快照，
结果固定 `comparable=false`，**不写入质量基线**。

---

## 3. 限流规则

### 3.1 四个维度与默认阈值

| 维度 | 计数键 | 默认限额 / 窗口 | 说明 |
|---|---|---|---|
| 用户 `user` | JWT `sub`（未登录为 `anonymous`） | 1200 / 60s | 防止单用户刷接口 |
| 租户 `tenant` | `default`（切片单租户） | 3000 / 60s | 生产按 tenant_id 隔离配额 |
| 接口 `endpoint` | 请求 path | 900 / 60s | 保护热点接口（如 `/api/chat/stream`） |
| 模型调用 `model` | `llm` / `settings` / `-` | 600 / 60s | 对应上游 RPM/TPM 共享配额 |

默认阈值刻意宽松，**不误伤 33 项端到端自测与 51 项 pytest**；admin 可在页面下调阈值复现超限。

### 3.2 超限反馈（统一契约）

```
HTTP 429
Retry-After: <seconds>
X-RateLimit-Dimension: user|tenant|endpoint|model
X-RateLimit-Limit: <n>
X-RateLimit-Window: <seconds>
{
  "detail": "请求过于频繁，请稍后重试",
  "dimension": "...", "key": "...", "path": "...",
  "limit": 2, "window_s": 60, "retry_after": 58, "current": 2,
  "policy": "rate_limit_exceeded 可退避重试；quota_exceeded 不重试"
}
```

- 命中写 `rate_hits` 台账 + `eval_logs` WARN 事件，可观测、可回放。
- 与项目既有 429 分类一致：`rate_limit_exceeded` 走指数退避 1/2/4s + 抖动（尊重 `Retry-After`，上限 30s，最多 3 次）；`quota_exceeded`（额度耗尽）**不重试**。
- **实现边界**：进程内滑动窗口，单实例有效；生产必须换网关层 Redis 令牌桶 + 队列吸收突发，且需全局并发闸（当前未做）。

---

## 4. 权限规则（多部门 / 多租户 / 越权边界复核）

### 4.1 六层隔离

| 层 | 规则 | 本切片现状 | 生产落点 | 状态 |
|---|---|---|---|---|
| 组织隔离 | `tenant_id` **只由依赖注入产生**，禁止来自请求体；所有查询经 `TenantRepository` 下推 SQL | 单租户 `default`；`users/documents/chat_logs` 带 owner/user 字段 | `backend/app/db/repository.py` | 架构预留 |
| 部门 | `department_id` 挂资源与用户；跨部门默认不可见，显式授权才放行 | 未建部门树；用量台账有 project/tenant 维度 | 待建 org 树 | 未做 |
| 角色层级 | `owner > admin > editor > viewer`，权限只继承不放宽；判定收敛到 `decide()` 一个纯函数 | `admin` / `user` 两级 | `backend/app/services/permission.py` | 演示级 |
| 资源归属 | 文档/会话/badcase 归属 owner；删除与导出校验归属或 admin | `documents.owner_id` / `chat_logs.user_id` / `badcases.created_by` 已落库并参与过滤 | 同上 | 已落地 |
| 数据可见范围 | **先按权限过滤，再重排、再进模型**；统计同理（**不查而非隐藏**） | `/api/overview` 普通用户强制带 `owner_id/user_id/created_by` 条件；用量/日志同理 | Milvus expr + `filter_candidates` 双过滤 | 演示级 |
| 越权边界 | 前端隐藏 ≠ 后端放行；所有 admin 接口独立 `require_admin` 返回 403 | selftest C24/C31/C32 与新增 10 项测试覆盖 | 行级 ACL + 审计留存 | 演示级 |

### 4.2 权限矩阵

| 资源 | 普通用户 | admin | 匿名 |
|---|---|---|---|
| 文档列表 / 删除 | 仅本人 `owner_id` | 全局 | 401 |
| 智能问答 / 历史会话 | 仅本人会话 | 全局 | 401 |
| badcase **采集** | 可写 | 可写 | 401 |
| badcase **查看 / 导出 / 完成度** | **403** | 可读写 | 401 |
| 审计日志 / 成员 / 模型设置 | **403** | 可读写 | 401 |
| 评估测试（只读：运行/指标/用量/日志） | 本人范围 | 全局 | 401 |
| 评估配置 / 预算 / 限流规则 / 触发运行 / 429 探针 | **403** | 可读写 | 401 |

> 「采集与查看权限分离」是本项目既有约定，本次新增能力**没有破坏它**：
> 普通用户可产生用量与日志，但看不到别人的数据；所有写接口后端独立鉴权。

**明确未做**：多租户行级隔离、部门树、逐行/逐段落 ACL、跨租户检索 expr 过滤、审计留存合规与脱敏检索。

---

## 5. 成本规则

### 5.1 计量与聚合

- **逐调用台账** `usage_events`：`project` / `tenant` / `user_id` / `call_type` / `model` /
  `prompt_tokens` / `completion_tokens` / `cost_cny` / `estimated` / `run_id`。
- **token 口径**：未拿到供应商 usage 时按字符估算（中文按字符、英文 4 字符/token），
  逐条标 `estimated=1`；**绝不冒充真实计费**。
- **成本公式**：`(prompt/1000) × price_in + (completion/1000) × price_out`，输入/输出分别计价。
- **单价表是演示估算值，非任何供应商报价**；API 响应固定带 `price_note`：
  上线前必须用真实账单校准。
- **聚合维度**：`project` / `tenant` / `call_type` / `model` / `user_id` 五选一，
  普通用户强制 `user_id` 条件（不查而非隐藏）。

### 5.2 预算上限与超限处理

| 项 | 默认 | 说明 |
|---|---|---|
| `limit_cny` | 50.00 | 累计周期（演示切片不重置周期） |
| `policy` | `warn` | 超额只告警 + 写 `eval_logs` WARN + 审计台账，**不拒绝** |
| `policy=block` | admin 显式切换 | 超额时 `check_budget().allowed=false`，`POST /api/evaltest/run` 返回 `budget_exceeded` 并写 ERROR 日志 |

使用率 = `spend / limit`；剩余 `max(0, limit - spend)`；前端进度条 80% 转琥珀、超额转红。
**边界**：预算只拦本页发起的离线评估；真实外部模型调用的成本拦截需接供应商 usage 与网关层配额（未做）。

---

## 6. 插件能力清单（高频场景 · 接入模块与边界）

来源：`GET /api/evaltest/plugins`（`webapp/observability.py::PLUGINS`），共 11 项。

| 插件 | 接入模块 | 状态 | 边界 | 工程要点 |
|---|---|---|---|---|
| 全链路可观测 trace | `observability.log_event()` / `eval_logs` | 演示级 | 只有事件级日志与 run 追踪；未接 OTel / Langfuse，无跨服务 trace | 一次 RAG 回答慢/错在哪一段 |
| Token 计量与预算闸门 | `usage_events` / `budgets` | 已落地（演示级） | token 估算、单价演示值；无账单对账、无多币种 | 怎么防止租户打爆预算 |
| 多维限流 | `rate_check()` + `main.py` 中间件 | 已落地（演示级） | 进程内窗口；生产换 Redis 令牌桶 + 网关层 | RPM/TPM 共享配额与隔离 |
| 退避重试 / 配额不重试 | `providers.classify_429()` / `backoff_seconds()` | 已落地 | 仅单次请求级；无熔断与全局并发闸 | rate 可重试、quota 不可重试 |
| 语义 / 向量缓存 | `chunk_embeddings`（按 provider:模型 隔离） | 已落地 | 只缓存分块向量；无答案级语义缓存 | 换 embedding 模型后旧向量怎么办 |
| Rerank 插件 | `providers.rerank()` / `rerank_with_llm()` | 已落地 | 三档；不可用时优雅跳过并留痕 | 双塔召回 vs cross-encoder 精排 |
| 引用溯源 | `routers_chat.py` / `chat_sources` | 已落地 | 只到分块级；无逐句 grounding 高亮 | 怎么证明答案不是编的 |
| 评测 CI 闸门 | `evalbench/compare.py` / `iterations/` | 演示级 | 有版本化对象与前后对照；闸门接 CI、阈值冻结未做 | 改提示词怎么证明没退化 |
| 权限与多租户隔离 | `backend/app/services/permission.py::decide()` | 架构预留 | 切片只有 admin 门禁 + owner 过滤 | 先过滤还是检索后过滤 |
| Agent 编排 / MCP | `backend/app/agent/` / `docs/agent-extension.md` | 架构预留 | 只有状态机与工具注册表接口 | 单轮问答如何演进到工具 Agent |
| 内容安全 / PII 脱敏 | 未接入 | **未做** | 输入输出审核、审计脱敏检索均未完成 | 企业知识库合规红线 |

筛选依据：优先「与本项目已有 seam 对齐、可验证、高频场景」的插件；每一项都写明状态与边界，
**不把架构预留说成已落地**。完整调研见 `docs/kb-capability-checklist.md`。

---

## 7. i18n / 主题 / 展示优化

- **语言**：中文原文即 key，zh 模式零替换；en 模式精确 + 片段替换 + 原文兜底（不产生空白/丢字）。
  覆盖导航、按钮、提示、表单占位符、空状态、表头、弹窗、toast、降级提示。
  用户数据（问题、答案、文件名、引用片段）在 `SKIP_SELECTORS` 中排除，不被误翻。
  无 value 的 `<option>`（如 badcase 判定「错误/正确」）先固化中文提交值再翻译显示，
  **表单提交值不变**。切换即时生效、无刷新、可逆。
- **主题色**：6 套（靛蓝/青碧/紫罗兰/琥珀/玫红/石墨）× 明/暗 2 模式 = 12 种组合，
  各自独立持久化、互不覆盖；主题色影响 primary/按钮/激活态/进度/焦点环，
  明暗影响背景/表面/文字/边框/侧栏/状态色；`--on-primary` 保证暗色主按钮文字对比度。
- **防闪烁**：`theme.js` 在 `<head>` 同步执行，CSS 应用前写好 `data-*`；`kb-boot` 首帧禁用过渡。
- **布局适配**：英文侧栏加宽至 258px；`page-head` / `filter-bar` / `head-actions` 可换行；
  统计卡 `auto-fit minmax`；表格横向滚动；按钮不写死宽度；`kv-line` 允许换行。
- **展示与可访问性**：统一间距/字号层级 token、组件 hover/active/disabled 状态与 0.14–0.28s 过渡；
  `:focus-visible` 描边、`prefers-reduced-motion` 关闭动画、状态色在明暗两套各自调优对比度；
  760px 以下侧栏转横向滚动、顶栏 sticky、卡片单列。

---

## 8. 验收标准

| # | 验收项 | 命令 / 判定 | 结果 |
|---|---|---|---|
| V1 | 后端单元测试 | `python -m pytest webapp/tests -q` → 51 passed（含新增 10 项） | ✅ |
| V2 | 端到端自测 | `python webapp/selftest.py` → 33/33 `DEMO_READY` | ✅ |
| V3 | 演示物料 | `python demo/selftest.py` → 16/16，`run_id=RUN-095a2a4a40c6` 不变 | ✅ |
| V4 | 评测 harness | `python -m unittest discover -s eval/tests -t .` → 14 OK | ✅ |
| V5 | 评估工作台 | `python -m unittest discover -s evalbench/tests -t .` → 16 OK | ✅ |
| V6 | 前端语法 | `node --check app.js && node --check i18n.js && node --check theme.js` | ✅ |
| V7 | i18n 覆盖 | 脚本校验 `t()` 字典零缺口；HTML 中文片段 277 条仅 2 条刻意保留 | ✅ |
| V8 | 新 API | 12 个 `/api/evaltest/*` 端点全部 200；写接口普通用户 403 | ✅ |
| V9 | 限流 429 | 阈值调至 2/60s 后第 3 次返回 429 + 4 个响应头 + 命中台账 | ✅ |
| V10 | 预算 block | `limit=0, policy=block` → 运行被拒 + ERROR 日志留痕 | ✅ |
| V11 | 用量范围隔离 | 普通用户 `usage` 只返回本人调用（0 条而非隐藏全局数字） | ✅ |
| V12 | 静态资源 | `/`、`/i18n.js`、`/theme.js`、`/style.css`、`/app.js` 均 200；C01 保留中文 | ✅ |
| V13 | 冻结边界 | `git diff main..HEAD -- eval/` 为空；`eval/` 的 `NOT_COMPARABLE` 口径未破 | ✅ |
| V14 | 密钥 | `git grep -E 'sk-[A-Za-z0-9]{20,}'` 无命中；`.env` 未入库 | ✅ |

---

## 9. 中型公司知识库能力清单（可纳入实现范围）

完整调研见 **`docs/kb-capability-checklist.md`**：6 个可核对来源（Microsoft Learn / Azure AI Search
安全与 RAG 文档等）、50 条能力对照（能力域 / 常见要求 / 本项目状态 / 落点 / 是否纳入 / 优先级 / 验收口径）、
框架与工程要求、差距三组归纳、Top10 建议。

本轮与该清单的对应关系：

- **本轮已纳入并落地**：可观测事件与 run 追踪、token 计量与预算、四维限流、成本归因、
  i18n / 主题（清单中「多语言与可访问性」）、权限矩阵可视复核。
- **清单中仍是架构预留**：多租户行级隔离、部门树、增量索引、混合检索 + RRF、Agent/MCP、
  评测 CI 闸门、审计留存合规。
- **明确未做**：内容安全 / PII 脱敏、SSO/OIDC、压测与容量基线、容灾演练、监控告警、灰度回滚。

> 工程说明口径：**A 档（当前）= 离线可跑、闭环可演示、降级可见；
> B 档（评审期）= 真实 embedding / 中文分词 / Rerank / 压测 / 越权用例 / trace 全链路；
> C 档（团队质量工作）= 容灾演练、容量基线、审计留存合规、监控告警、灰度回滚。**

---

## 10. 已知限制与诚实边界

1. 限流是进程内滑动窗口，**单实例有效**；多副本必须换 Redis/网关层，当前无全局并发闸与令牌桶。
2. token 与单价均为**演示估算**，不是供应商 usage / 报价；成本结论只可用于相对比较与预算演练。
3. 预算只拦本页离线评估运行，不拦真实外部模型调用。
4. 多租户/多部门/行级 ACL 只做到「维度建模 + 规则文档 + 后端 403 门禁」，**未实现隔离**。
5. 本轮前置条件：离线评估与评估配置仅 admin；`real_api` 档位仍受 `OBJ-ADAPTER-CONTRACTS` 阻断。
6. i18n 采用「中文原文即 key + DOM 扫描」，适合存量零构建页面；若引入前端框架应改用语义 key + 资源包。
7. 暗色对比度按 WCAG 目标调优，但**未做自动化对比度扫描与屏幕阅读器实测**。
