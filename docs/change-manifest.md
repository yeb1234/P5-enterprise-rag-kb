# 本轮模块化改动清单总纲

> 编制：系统架构师 梁承宇 ｜ 配套文档：`docs/architecture-review.md`
> **性质**：本轮**模块化改动清单总纲**。本次未修改任何项目源码，仅新增本文档与 `docs/architecture-review.md`。
> 核心要求：「改动清单需标明**涉及文件路径**与**改动范围**」——每条均给这两列。
> 事实源：`README.md`、`docs/org-model-change-plan.md`、`docs/enterprise-readiness.md`、`backend/app/**`、`webapp/**`、`frontend/**`、`.env.example` 的本次实查，以及主理人本轮派发口径与任务列表。

---

## 0.0 主理人最终状态回填（2026-11-14，代码冻结后实查）

> 下面各表是**编制时快照**：当时 A / B 两项尚未落地，故 CFG-06~08、OBS-01~09、RATE-01~05、EVT-01 等条目的状态列写「未做（本轮落地项 · 进行中）」。
> **这些状态现已失效，以本节为准**；原表保留不回改，用于追溯「方案 → 落地」的过程。

| 编制时标签 | 最终状态 | 实际落点 |
|---|---|---|
| A 项 · `tenant → department` 统计维度 | **已实现** | `webapp/db.py`（列名 `department_id`、幂等迁移 `_migrate_tenant_to_department()`、索引改为迁移后建）、`webapp/observability.py`（`DIMENSIONS` / `record_usage()` / `usage_summary()` / `CONFIG_KEYS` / `run_offline_eval()` / PLUGINS 文案）、`webapp/main.py`、`webapp/routers_chat.py`、`webapp/routers_evaltest.py`、`webapp/rag.py`、`webapp/static/{index.html,app.js,i18n.js}`、`webapp/tests/` |
| B 项 · LLM 低思考 / 快速档 | **已实现** | `webapp/settings.py`（`llm_thinking` 枚举 + `thinking_disabled()`）、`webapp/providers.py`（`thinking_fields()` / `_extras()` / `get_thinking_mode()` / 三条请求路径）、`webapp/routers_settings.py`、设置页控件、`.env.example` 的 webapp 段、`webapp/tests/test_thinking.py` |
| 统一 429 错误契约 | **已实现（本轮新增，原表未列）** | `webapp/main.py`（`RATE_CODES` + `code/kind/message/trace_id/policy` + `X-RateLimit-Code` / `X-Trace-Id`）、`webapp/static/app.js` `api()` 的 429 分支、`webapp/routers_evaltest.py` 契约示例、`webapp/tests/test_evaltest.py` 断言 |
| 迁移三态与索引构建顺序 | **已实现（本轮新增，原表未列）** | `webapp/db.py::_migrate_tenant_to_department()` 支持全新库 / `tenant` 旧库 / 半迁移态；`webapp/tests/test_department_migration.py` 4 项锁定。修正了「索引写进 SCHEMA 会让旧库 `executescript` 先失败」的真实缺陷 |

**三条主理人裁定（以代码为最终口径，规格文档已同步）**：
1. 限流采用**三维计数** `("user","endpoint","model")` —— 依据用户任务 3 原文；`department` 只作成本归因，不参与计数、不产生按部门 429。原表 RATE-01 描述的四维方案**未采纳**。
2. `?dim=` 对外用物理列名 `department_id`（`DIMENSIONS` 白名单直接拼 SQL），前端下拉 value 同步。
3. 预算耗尽仍是 HTTP 200 + `{ok:false}`（阻断生效但错误码未统一）；`backend/app/core/constants.py` 的 `QUOTA_EXCEEDED→413` 本轮不动，登记为下期对齐。

**文档落盘实查**（本节回填时逐个 `ls` 核实）：`docs/frontend-audit.md`、`docs/acceptance-report.md`、`docs/deployment-guide.md`、`docs/git-conventions.md`、`docs/rate-limit-and-security-review.md`、`docs/plugin-selection.md`、`docs/capability-priority.md`、`docs/llm-safety-positions.md`、`docs/cost-metrics-spec.md`、`docs/change-manifest.md`、`docs/architecture-review.md` 共 11 份，另有独立演示页 **`interview/index.html`**（单文件、零外部依赖）。`docker-compose.yml` 有 62 行保守补齐，`docker compose config -q` 通过。

**回归结论**：`pytest webapp/tests` **66 passed**、`backend` 16 passed、`eval` 14 OK、`evalbench` 16 OK、`demo/selftest.py` 16/16（`run_id=RUN-095a2a4a40c6` 未变）、`webapp/selftest.py` **33/33 `DEMO_READY`**（离线隔离补跑、原报告按字节还原）、`node --check` ×3 与 `npm run build` 通过、`git diff --exit-code -- eval/ demo/` 返回 0。详见 `docs/acceptance-report.md` §5.2。

**§9「明确不动表」仍然全部有效**：`eval/` / `demo/` / `evalbench/` / `docs/evaluation-plan.md` 的 `__TBC__` 行 / `docs/CHANGELOG.md` 历史条目 / backend 的 `tenant_id` 数据模型与 `0001_initial.py` 本轮一律未改。

---

## 0. 阅读方式

**改动范围（本轮动作，四态）**

| 值 | 含义 |
|---|---|
| 新增 | 本轮新建的文件 / 文档 / 能力 |
| 修改 | 本轮改动既有文件的内容或命名 |
| 仅复核 | 本轮不改，但需确认其未被误伤或需同步 |
| 明确不动 | 冻结区、技术债或范围外，写入清单即表示「有意识地不改」 |

**状态（三态，禁止把「只设计」写成「已实现」）**

| 值 | 含义 |
|---|---|
| 已实现 | 代码 / 文档已在工作区就位（含**现状已存在**、本轮无需改动） |
| 只设计 | 只有方案或规格，无实现 |
| 未做 | 本轮范围内但编制时尚未落地 |

**「本轮落地项」标签**：A（成本 / 限流统计维度 `tenant → department`）与 B（LLM 低思考 / 快速档）是主理人本轮**工作范围**，不代表每一条都已提交。状态列以本清单编制时的实查快照为准；主理人提交后应按实际结果更新状态列。

**口径优先级**：用户本轮新指令「暂不实现多租户；多租户仅作为未来研发方向写入文档」覆盖历史口径。`docs/org-model-change-plan.md` 的 13 步改造是**未来路线**，本轮不执行；backend 的 `tenant_id` / `TenantRepository` 历史命名保留为技术债。

**统计口径**：主清单 §1–§8 共 **62 条**；§9「明确不动表」8 条（与主清单部分重叠，为便于核对单列）；§10「可量化验收标准索引」4 条。合计 **74 条登记记录**。逐模块编号区间见 §11。

---

## 1. 模型配置（CFG）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| CFG-01 | Discovery 网关默认模型 | `webapp/settings.py:25` | 修改 | `discovery_model` 默认 `deepseek-v4.1-flash-1017` | 模型设置页、成本单价表、调用标签 | 已实现（现状） | B 项；实查已就位，本轮增量为低思考档与 `.env.example` |
| CFG-02 | `llm_provider` 枚举含 `discovery` | `webapp/settings.py:20,43` | 修改 | 四档 `offline / sensenova / discovery / local` 白名单 | 设置页下拉、Provider 装配 | 已实现（现状） | B 项；`set_many()` 按 `ENUMS` 校验，未知值拒绝 |
| CFG-03 | Discovery 适配器分支 | `webapp/providers.py:426-432` | 修改 | `get_llm()` 的 `discovery` 分支，`DISCOVERY_BASE_URL` / `DISCOVERY_API_KEY` | 外部模型调用链路 | 已实现（现状） | B 项；OpenAI 兼容 `/chat/completions` |
| CFG-04 | Discovery 模型标签 | `webapp/providers.py:443-445` | 仅复核 | `get_model_label()` 返回 `deepseek-v4.1-flash-1017` | 用量台账、`run_id` 展示 | 已实现（现状） | 与 CFG-01 默认值一致，改名需同步 |
| CFG-05 | 单价表含该模型 | `webapp/observability.py:30` | 仅复核 | `PRICES["deepseek-v4.1-flash-1017"]`，标注「演示估算值」 | 成本计算、预算 | 已实现（现状） | `PRICE_NOTE` 明确非供应商报价，不得当真实单价 |
| CFG-06 | **低思考 / 快速档** | `webapp/settings.py`、`webapp/providers.py` | 修改 | 新增显式档位与思考参数控制（低思考 / 快速） | 请求体、Provider 装配、设置页 | 未做（本轮落地项 · 进行中） | B 项核心增量；编制时实查未见显式档位 |
| CFG-07 | 设置页低思考档控件 | `webapp/static/index.html`（设置页）、`webapp/static/app.js` | 修改 | 新增档位选择控件并接 `set_many()` 白名单 | 前端交互、运行时配置 | 未做（本轮落地项 · 进行中） | B 项；`ALLOWED = set(DEFAULTS)`，新键须先进 `DEFAULTS` |
| CFG-08 | `.env.example` 的 webapp 配置段 | `.env.example` | 修改 | 补 `WEBAPP_LLM_PROVIDER` / `DISCOVERY_BASE_URL` / `DISCOVERY_API_KEY` / `DISCOVERY_MODEL` 等 | 首次默认值、离线自测 | 未做（本轮落地项 · 进行中） | B 项；实查 `.env.example` 当前只有 backend 的 `DEEPSEEK_*` 段；`webapp/providers.py` 的 webapp 键须用 `WEBAPP_` 前缀避免与 backend 枚举冲突 |

---

## 2. 可观测与成本（OBS）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| OBS-01 | 用量台账维度列 | `webapp/db.py:157-173` | 修改 | `usage_events.tenant` → `department` | 建表 DDL、既有库迁移、所有 INSERT / SELECT | 未做（本轮落地项 · 进行中） | A 项核心；SQLite 演示库，需考虑既有库加列或重建 |
| OBS-02 | 用量台账索引 | `webapp/db.py:176` | 修改 | `ix_usage_tenant` → `ix_usage_department` | 按维度聚合查询 | 未做（本轮落地项 · 进行中） | 与 OBS-01 同批，缺一即索引失效 |
| OBS-03 | 预算 scope 注释 | `webapp/db.py:180` | 修改 | `global / tenant / project` → `global / department / project` | 注释口径、`budgets` 读侧 | 未做（本轮落地项 · 进行中） | 仅注释与枚举说明；预算只拦离线评估运行，不拦所有真实外部调用 |
| OBS-04 | 聚合维度集合 | `webapp/observability.py:36` | 修改 | `DIMENSIONS` 中 `tenant` → `department` | `/api/evaltest/usage?dim=` 与成本看板 | 未做（本轮落地项 · 进行中） | A 项；`project / department / call_type / model / user_id` |
| OBS-05 | `record_usage()` 签名与 SQL | `webapp/observability.py:65-94` | 修改 | 形参、row 字典、INSERT 列名与占位符四处同步 | 全部调用方（`routers_chat.py`、`routers_evaltest.py`、`run_offline_eval()` 等） | 未做（本轮落地项 · 进行中） | 漏改任一处即 `sqlite3` 报列不存在 |
| OBS-06 | 评估配置键白名单 | `webapp/observability.py:252` | 修改 | `CONFIG_KEYS` 中 `tenant` → `department` | `set_config()` 白名单、评估测试页 | 未做（本轮落地项 · 进行中） | C22-3 四处联动之一；与冻结的 `config_snapshot_hash` 是两条独立链路 |
| OBS-07 | `eval_configs` 默认值 | `webapp/db.py:324-328` | 修改 | 默认配置字典中 `tenant: default` → `department: default` | 首次初始化、评估测试页展示 | 未做（本轮落地项 · 进行中） | C22-3 四处联动之一 |
| OBS-08 | 离线评估入口参数 | `webapp/observability.py:415-455` | 修改 | `run_offline_eval(tenant=...)` → `department=...`，内部 `record_usage()` 调用同步 | 离线评估运行、用量留痕 | 未做（本轮落地项 · 进行中） | 结果仍 `comparable=false`，不进质量基线 |
| OBS-09 | 配置读取调用点 | `webapp/routers_chat.py:129`、`webapp/routers_evaltest.py:72` | 修改 | `cfg.get("tenant", "default")` → `cfg.get("department", "default")` | 聊天与评估测试子页 | 未做（本轮落地项 · 进行中） | C22-3 四处联动之一；与 OBS-06/07 必须同批，否则旧键读不到或白名单拒绝 |

---

## 3. 限流（RATE）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| RATE-01 | 四维限流维度元组 | `webapp/observability.py:37` | 修改 | `RATE_DIMENSIONS` → `("user", "department", "endpoint", "model")` | 计数窗口、429 响应头 | 未做（本轮落地项 · 进行中） | A 项；四维仅存在于 webapp 演示切片，backend 是按 IP 令牌桶 |
| RATE-02 | `rate_check()` 签名与键 | `webapp/observability.py:197-229` | 修改 | 形参 `tenant` → `department`，`keys` 字典键同步 | 限流中间件、命中台账 | 未做（本轮落地项 · 进行中） | 漏改 `keys[dim]` 即 `KeyError` |
| RATE-03 | 限流规则表注释 | `webapp/db.py:190` | 修改 | `dimension -- user / tenant / endpoint / model` → `department` | DDL 注释、规则表读侧 | 未做（本轮落地项 · 进行中） | 注释级，但属文档失真纠偏范围 |
| RATE-04 | 限流规则默认值 | `webapp/db.py:317-323` | 修改 | `defaults` 中 `tenant: (3000, 60)` → `department` | 首次初始化的 `rate_rules` 表 | 未做（本轮落地项 · 进行中） | 幂等插入：只在键缺失时写入，既有库不会自动改键 |
| RATE-05 | 限流中间件 | `webapp/main.py:49,67` | 修改 | docstring 四维注释与 `obs.rate_check(... tenant="default")` 调用 | 所有 `/api/` 读写请求 | 未做（本轮落地项 · 进行中） | A 项；静态资源直通逻辑不变 |
| RATE-06 | 429 响应头 | `webapp/main.py:76-80` | 仅复核 | `X-RateLimit-Dimension` 取值随维度名变化，`Retry-After` 等不变 | 前端 429 退避、命中台账 | 已实现（现状） | 本轮不改字段结构，只随 RATE-01 变化；`rate_limit_exceeded` 可退避、`quota_exceeded` 不重试口径不变 |

---

## 4. 评估测试子页（EVT）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| EVT-01 | 权限矩阵与规则集文案 | `webapp/routers_evaltest.py`（`/permissions` 等） | 修改 | 维度枚举、`tenant` 相关规则文本、响应头枚举说明改用部门口径 | 评估测试子页展示、对齐检查 | 未做（本轮落地项 · 进行中） | 该文件 `/permissions` 规则集是运行时返回的演示 JSON，**不属于 `eval/` 冻结对象** |
| EVT-02 | 评估测试子页单测断言 | `webapp/tests/test_evaltest.py` | 修改 | `{"user","tenant","endpoint","model"}` 断言与 `dim=tenant` 用例改用 department | `pytest webapp/tests` 计数 | 未做（本轮落地项 · 进行中） | 断言与用例数会变，属「可变基线」，新计数冻结见 §10 |
| EVT-03 | `/api/evaltest/*` 端点路径 | `webapp/routers_evaltest.py` | 仅复核 | 12 个端点路径不变，只改响应体维度字段 | 前端调用、429 / 403 契约 | 已实现（现状） | 写接口普通用户 403 门禁不变 |
| EVT-04 | webapp 其余单测 | `webapp/tests/test_api.py`、`test_providers.py`、`test_rag.py`、`test_settings.py`、`conftest.py` | 仅复核 | 确认维度改名与低思考档不破坏离线强制配置 | `pytest webapp/tests` | 已实现（现状） | `conftest.py` 强制 provider 离线，本轮不触发真实外部模型调用 |
| EVT-05 | 演示自测 | `webapp/selftest.py` | 仅复核 | 33 项 `DEMO_READY` 中涉及维度文案与静态资源的用例 | `python webapp/selftest.py` | 已实现（现状） | C33 冻结的是 10 个具名 view + 6 个具名 loader 的**存在性检查**；本轮不新增视图 |

---

## 5. 权限与安全（SEC）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| SEC-01 | backend `tenant_id` 历史命名（技术债） | `backend/app/models/identity.py`、`backend/app/db/base.py`、`backend/app/db/repository.py`、`backend/app/core/deps.py` | 明确不动 | 保留 `Tenant` / `TenantMixin` / `TenantRepository` / `Principal.tenant_id` | 无（本轮不动 backend） | 已实现（现状保留） | 用户指令「暂不实现多租户」；**不代表已实现多租户隔离**，见 `architecture-review.md` §3.4 |
| SEC-02 | 权限判定函数 | `backend/app/services/permission.py` | 明确不动 | `decide()` / `filter_candidates()` / `visible_document_clause()` 不改 | 无 | 已实现（现状保留） | 完整改造的列表侧 `decide()` 复核缺口属未来 Must，本轮不涉及 |
| SEC-03 | 基线迁移 | `backend/migrations/versions/0001_initial.py` | 明确不动 | `metadata.create_all` 基线冻结 | 无 | 已实现（现状保留） | 项目约定「从 0002 起必须写显式迁移」；本轮不新增 0002 |
| SEC-04 | webapp 权限模型 | `webapp/security.py`、`webapp/deps.py`、`webapp/routers_admin.py` | 仅复核 | `admin`/`user` 两级 + owner 过滤 + 写接口 403 门禁不变 | 演示切片全部接口 | 已实现（现状） | **本轮不给 webapp 增加部门级权限**，A 项只是统计维度改名 |
| SEC-05 | 部门树下沉 webapp | `docs/org-model-change-plan.md` §2.4（C5）、§5.8 | 明确不动 | 最小可演示部门能力（部门表 / 成员视图 / 按部门过滤）为完整改造范围 | 未来 webapp 改造 | 只设计 | 方案已定、未落地；本轮不动，避免把演示替身说成生产实现 |
| SEC-06 | 密钥与离线纪律 | `.env.example`、`webapp/tests/conftest.py`、`webapp/live_smoke.py` | 仅复核 | 密钥只进 `.env` 且 gitignore；webapp 键用 `WEBAPP_` 前缀 | 启动校验、离线测试 | 已实现（现状） | `git grep -E 'sk-[A-Za-z0-9]{20,}'` 零命中；本轮不轮换密钥 |

---

## 6. 前端展示（UI）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| UI-01 | 「租户」文案 → 「部门」 | `webapp/static/index.html:416,423,519,533` | 修改 | 评估测试页说明、聚合维度 `<option value="tenant">`、限流配额标题 | 演示页中文展示、下拉提交值 | 未做（本轮落地项 · 进行中） | `<option>` 的 value 与后端维度枚举须同批改，否则提交旧键 |
| UI-02 | i18n 字典成对同步 | `webapp/static/i18n.js` | 修改 | 中文原文即 key，需**成对**改中文 key 与英文 value 并删旧 key | 中英切换、切回中文逐字一致 | 未做（本轮落地项 · 进行中） | 仓库**无 i18n 生成器**；禁止只改英文值留下旧中文 key；改后 `node --check` |
| UI-03 | 维度键名与调用 | `webapp/static/app.js` | 修改 | 维度映射字典、成本看板与限流页的 `tenant` 键 | 前端渲染与请求参数 | 未做（本轮落地项 · 进行中） | 与 OBS-04 / RATE-01 同批 |
| UI-04 | 设置页低思考档 UI | `webapp/static/index.html:555-563`、`webapp/static/app.js` | 修改 | 低思考 / 快速档控件、Discovery 模型下拉 | 模型设置页 | 未做（本轮落地项 · 进行中） | B 项；Discovery 选项与 `#st-discovery-box` 已存在（现状） |
| UI-05 | `frontend/` React 骨架 | `frontend/src/**`、`frontend/package.json`、`frontend/vite.config.ts` | 明确不动 | 本轮不改 React 工程 | 无 | 已实现（现状保留） | `frontend/` 依赖 backend API，与 `webapp/` 静态页是两套东西；本轮不联动 |

---

## 7. 文档（DOC）

> 并行产出文档的**归属**列按任务列表的派发记录填写，不编造归属；用途按文档名与派发口径描述。
> **状态复核**：编制初稿时这些文档均未落盘；随后全目录 Glob 复核发现 `docs/acceptance-report.md`、`docs/deployment-guide.md`、`docs/git-conventions.md`、`docs/rate-limit-and-security-review.md`、`docs/plugin-selection.md`、`docs/capability-priority.md`、`docs/llm-safety-positions.md`、`docs/cost-metrics-spec.md` **8 份已落盘**，故状态列已由「未做（进行中）」更新为「已实现（已落盘）」。`docs/frontend-audit.md` 与 `interview/index.html` 仍未落盘，保持「未做（进行中）」。

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注（归属 / 用途） |
|---|---|---|---|---|---|---|---|
| DOC-01 | 架构复核 | `docs/architecture-review.md` | 新增 | 分层图、口径统一、差距、多租户未来方向、§8 门禁引用 | 本轮交付 | 已实现 | 归属：system-architect；用途：本轮架构复核 |
| DOC-02 | 改动清单总纲 | `docs/change-manifest.md` | 新增 | 文件路径级、改动范围四态、三态、明确不动表、验收索引 | 本轮交付 | 已实现 | 归属：system-architect；用途：本文件 |
| DOC-03 | 前端四级问题清单 | `docs/frontend-audit.md` | 新增 | 前端检查与问题分级 | 并行产出 | 未做（进行中） | 归属：frontend-engineer；用途：前端审计；实查文件尚未落盘 |
| DOC-04 | 测试与验收报告 | `docs/acceptance-report.md` | 新增 | 可量化验收标准、三分区回归、放行门禁 | 并行产出 / §10 索引指向 | 已实现（已落盘） | 归属：qa-engineer；用途：验收标准单一事实源 |
| DOC-05 | 部署指南 | `docs/deployment-guide.md` | 新增 | 部署 / 迁移 / 回滚交付保障 | 并行产出 | 已实现（已落盘） | 归属：devops-engineer；用途：部署说明 |
| DOC-06 | Git 规范 | `docs/git-conventions.md` | 新增 | 分支、提交、逐版本提交约定 | 并行产出 | 已实现（已落盘） | 归属：devops-engineer；用途：版本管理约定 |
| DOC-07 | 限流与安全复核 | `docs/rate-limit-and-security-review.md` | 新增 | 限流维度、越权边界、安全规则复核 | 并行产出 | 已实现（文件已落盘；任务复核中） | 归属：backend-engineer；用途：安全复核 |
| DOC-08 | 插件选型 | `docs/plugin-selection.md` | 新增 | 插件 / 扩展点选型 | 并行产出 | 已实现（已落盘） | 归属：scenario-product-manager；用途：选型记录 |
| DOC-09 | 能力优先级 | `docs/capability-priority.md` | 新增 | 能力清单与优先级排序 | 并行产出 | 已实现（已落盘） | 归属：scenario-product-manager；用途：范围纪律 |
| DOC-10 | 安全前中后置 | `docs/llm-safety-positions.md` | 新增 | 模型调用前 / 中 / 后的安全位置 | 并行产出 | 已实现（已落盘） | 归属：agent-app-engineer；用途：安全口径 |
| DOC-11 | 成本指标规范 | `docs/cost-metrics-spec.md` | 新增 | token / 成本 / 聚合维度口径 | 并行产出 | 已实现（已落盘） | 归属：data-engineer；用途：A 项维度口径固化 |
| DOC-12 | 独立 HTML 演示页 | `interview/index.html` | 新增 | 独立可打开的工程说明页 | 并行产出 | 未做（进行中） | 归属：frontend-engineer；用途：评审演示。**实查 `interview/` 目录尚不存在** |
| DOC-13 | 上一轮完整改造方案 | `docs/org-model-change-plan.md` | 仅复核 | 601 行，C1–C35、13 步、0002、Milvus 蓝绿、A13 | 引用源 | 已实现（现状） | 归属：上一轮；用途：未来路线与 §8 门禁出处，**本轮不改写** |
| DOC-14 | 企业级就绪改造 | `docs/enterprise-readiness.md` §8 | 仅复核 | V1–V14 验收标准表 | §10 索引指向 | 已实现（现状） | 归属：上一轮；用途：现状验收标准；本轮不改 |
| DOC-15 | 整体改进日志 | `docs/CHANGELOG.md` | 明确不动 | 历史日志与 §7 修订记录 | 无 | 已实现（现状保留） | 纪律：历史条目不回改；本轮新计数冻结也不写入该文件（见 DEP-05） |
| DOC-16 | 评估计划冻结行 | `docs/evaluation-plan.md`（含 `__TBC__` 的第 706 / 733 行） | 明确不动 | `EMBEDDING_SERVICE_ACCESS__TBC` / `EMBEDDING_CACHE__TBC` 字面量及周边文字 | 无 | 已实现（现状保留） | 冻结口径；`__TBC__` 不可改 |
| DOC-17 | 项目总说明 | `README.md` | 仅复核 | 分层结构、四条硬约束、快速启动、已知限制 | 讲解口径 | 已实现（现状） | 其中「`tenant_id` 只由依赖注入产生」等表述属历史命名技术债，见 `architecture-review.md` §3.3 |

---

## 8. 部署与版本管理（DEP）

| 编号 | 改动项名称 | 涉及文件路径 | 改动范围 | 改动说明 | 影响范围 | 状态 | 备注 |
|---|---|---|---|---|---|---|---|
| DEP-01 | 编排与反代 | `docker-compose.yml`、`nginx/`、`frontend/Dockerfile`、`frontend/nginx.conf` | 明确不动 | 本轮不改 compose 服务、nginx SSE 关缓冲与安全响应头 | 无 | 已实现（现状保留） | `backend/` 与 `webapp/` 是独立发布单元，webapp 不在 compose 编排内 |
| DEP-02 | 环境变量与密钥纪律 | `.env.example`、`webapp/envfile.py`、`webapp/tests/conftest.py`、`webapp/live_smoke.py` | 仅复核 | 密钥只进 `.env` 且 gitignore；webapp 键用 `WEBAPP_` 前缀；改键名须同步 conftest / live_smoke | 首次启动、离线自测 | 已实现（现状） | B 项的 `.env.example` 实际改动登记在 CFG-08，此处仅复核纪律 |
| DEP-03 | 静态资源版本号 | `webapp/static/index.html`（四处 `?v=11`：`theme.js` / `i18n.js` / `style.css` / `app.js`） | 仅复核 | 若本轮改了静态资源，四处版本参数须统一升 `?v=12` | 浏览器缓存 | 已实现（现状） | 版本不改会导致用户拿到旧 JS / i18n；与 UI-02 / UI-03 联动 |
| DEP-04 | 逐版本提交与全量回归 | 任务列表 #16（全量回归、逐版本提交与交付） | 明确不动 | 本轮不做全量回归与发版 | 后续任务 | 未做（范围外） | 本轮交付为文档与演示切片维度对齐，提交与回归由后续任务承接 |
| DEP-05 | 新计数冻结写入 | `docs/CHANGELOG.md`（§7 修订记录） | 明确不动 | 完整改造要求「新计数单独冻结写入 CHANGELOG」；本轮不改该文件 | 无 | 未做（范围外） | 本轮维度改名若改变 pytest 计数，计数留痕由后续任务处理；见 DOC-15 |
| DEP-06 | 部署与 Git 规范文档 | `docs/deployment-guide.md`、`docs/git-conventions.md` | 新增 | 交叉引用：实体条目见 §7 DOC-05 / DOC-06 | 并行产出 | 已实现（已落盘） | 本行是模块索引，不计入唯一文件数；与 DOC-05/06 指向同一文件 |

---

## 9. 明确不动表（FIX）

> 单列便于核对。「对应主清单」指出该类别在主清单中的登记位置；本表与主清单部分重叠。

| 编号 | 类别 | 涉及文件路径 | 原因 | 对应主清单 |
|---|---|---|---|---|
| FIX-01 | 冻结评测对象 | `eval/`（14 个版本化对象 `eval/config/objects/*.json` + `eval/config/registry.json`） | `__TBC__` / `NOT_COMPARABLE` / `comparable=false` / `real_api` 阻断口径冻结 | SEC-06 / DOC-16 |
| FIX-02 | 演示物料 | `demo/` | `run_id=RUN-095a2a4a40c6`、`config_snapshot`、`demo/selftest.py` 16/16 冻结 | DOC-16 |
| FIX-03 | 真实 API 评估工作台 | `evalbench/`（含 `runs` / `cache` / `out`） | 实查零 `tenant` 命中、不 import backend，无需适配层；两套体系独立 | DOC-13 |
| FIX-04 | 冻结文档行 | `docs/evaluation-plan.md`（含 `__TBC__` 的第 706 / 733 行） | 冻结口径字面量及周边文字均不改 | DOC-16 |
| FIX-05 | 历史日志 | `docs/CHANGELOG.md` | 只记录已发生的改动，历史条目不回改 | DOC-15 / DEP-05 |
| FIX-06 | backend 数据模型 | `backend/app/models/identity.py`、`backend/app/db/base.py`、`backend/app/db/repository.py`、`backend/app/core/deps.py`、`backend/app/services/permission.py` | 用户指令「暂不实现多租户」；`tenant_id` / `TenantRepository` 作为技术债保留 | SEC-01 / SEC-02 |
| FIX-07 | 基线迁移与迁移路线 | `backend/migrations/versions/0001_initial.py`、`docs/org-model-change-plan.md` 的 13 步 / `0002` | 基线冻结；13 步为未来路线，本轮不执行 | SEC-03 / DOC-13 |
| FIX-08 | 真实外部资源与前端工程 | 真实外部 LLM 调用、供应商账单 / 单价校准、PostgreSQL / Milvus 生产实例、`frontend/src/**`、`nginx/`、`docker-compose.yml` | 本轮纪律：不运行真实外部模型调用、不校准账单、不改编排与 React 工程 | CFG-05 / UI-05 / DEP-01 |

---

## 10. 可量化验收标准索引

> 本节**只做索引**，不重复抄录任何判定数字或命令结果。数字与命令以被指向文件为准。

| 编号 | 指向文件 | 索引内容 | 用途 |
|---|---|---|---|
| IDX-01 | `docs/acceptance-report.md` | 本轮可量化验收标准、三分区回归、放行门禁 | 本轮并行产出的验收单一事实源（DOC-04） |
| IDX-02 | `docs/enterprise-readiness.md` §8 | V1–V14 现状验收标准表 | 现状基线与演示切片验收口径（DOC-14） |
| IDX-03 | `docs/org-model-change-plan.md` §8.1–§8.5 | A1–A13、A13 七条断言、30 条越权用例、三分区回归、六个一票否决项 | 完整 13 步改造的准出门禁；本轮不触发（见 `architecture-review.md` §4） |
| IDX-04 | `docs/change-manifest.md` §11 | 本清单条目统计与编号区间 | 改动范围核对与条目数复算 |

---

## 11. 条目统计

| 模块 | 编号区间 | 条数 |
|---|---|---|
| §1 模型配置 | CFG-01 ~ CFG-08 | 8 |
| §2 可观测与成本 | OBS-01 ~ OBS-09 | 9 |
| §3 限流 | RATE-01 ~ RATE-06 | 6 |
| §4 评估测试子页 | EVT-01 ~ EVT-05 | 5 |
| §5 权限与安全 | SEC-01 ~ SEC-06 | 6 |
| §6 前端展示 | UI-01 ~ UI-05 | 5 |
| §7 文档 | DOC-01 ~ DOC-17 | 17 |
| §8 部署与版本管理 | DEP-01 ~ DEP-06 | 6 |
| **主清单小计（§1–§8）** | — | **62** |
| §9 明确不动表 | FIX-01 ~ FIX-08 | 8 |
| §10 可量化验收标准索引 | IDX-01 ~ IDX-04 | 4 |
| **登记记录合计** | — | **74** |

> 说明：DEP-06 是模块索引行，与 DOC-05 / DOC-06 指向同一文件，计入条数但不计入唯一文件数；§9 与主清单部分重叠，单列便于核对「有意识地不改」。
> 状态分布以交付前的落盘复核为准：A / B 本轮落地项的增量部分（含 OBS、RATE、UI、CFG-06~08、EVT-01~02）标「未做（进行中）」；Discovery 网关与默认模型、既有回归脚本、backend 数据模型、冻结区标「已实现」；并行产出文档 DOC-04~DOC-11 与 DEP-06 经全目录复核后由「未做」更正为「已实现（已落盘）」，DOC-03 / DOC-12 仍未落盘；webapp 部门能力下沉与 13 步改造标「只设计」或「明确不动」。**既没有把「只设计」写成「已实现」，也没有把已落盘文档误标为「未做」。**
