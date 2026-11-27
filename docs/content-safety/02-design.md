# 内容安全 PII 防护 · 实现对照设计文档

> 文档路径：`docs/content-safety/02-design.md`｜编制：`pii-researcher`｜日期：2026-11-14
> 性质：**本仓实现对照设计说明**。逐处对照 `01-research.md` 的工业调研结论与当前代码，说明「哪些已落地、哪些仍是设计、哪些明确未做」。
> 事实源：**本轮逐文件实读 `guardrails/` 纯函数包、`webapp/` 四出口接入点与两组测试**，全部以代码为准；不沿用 `01-research.md` 编制时「未做」的旧口径。
> 项目定位：企业内部知识库 RAG「开发框架骨架 + 可运行演示切片」，用于工程演示，**不是上线交付版**。
> 变更范围：本次只新增本文件；未改任何源码、未动任何既有文件。

---

## 0. 定位、三态纪律与 01→02 对照

### 0.1 三态纪律（沿用项目既有口径，不重新定义）

| 状态 | 含义 |
|---|---|
| `已实现` | 仓库里有可运行代码 / 表结构 / 接口支撑且已在链路中生效。**本文凡标 `已实现`，均指 `webapp/` 演示切片的规则级实现（含演示替身），不等于生产骨架实现，更不等于企业级内容安全达标**。 |
| `只设计` | 有字段、注释、设计文档、架构预留位或配置项，但无可运行的该功能逻辑。 |
| `未做` | 代码与设计里都没有落地，或明确标注不做。 |

两套体系必须分开看：`backend/` 是生产骨架（FastAPI + PG/Milvus/Redis + 七节点编排），`webapp/` 是可运行演示切片（SQLite + 进程内队列替身）。**本轮 PII 防护全部落在 `webapp/` 与 `guardrails/`；`backend/app/agent/orchestrator.py` 仍是两条正则 + `verify_node()` 输出替换，未接本包**（`pipeline.py:144` 的 `status()["integration"]["backend_skeleton"]` 如实标注「未接本包」）。混写即失真。

### 0.2 01 与 02 的边界声明

- `01-research.md` 是 **2026-11-14 的工业调研**：6 次 WebSearch、29 条可核实 URL，按当时实查把 PII 全流程脱敏标为 `未做`，并给「四级判定链 / 四个出口 / 四种动作 / 工程要点」的建议。**它记录的是编制时状态，不是当前状态**。
- `02-design.md`（本文）是 **本仓实现对照**：用户新指令「内容安全 PII 也做一层」之后，主理人已在 `webapp/` 演示切片真正落地了规则级 PII 防护。本文以当前代码为唯一事实源，逐条说明 01 的哪些结论被采纳、哪些仍留白。
- 本文**不重复** 01 的外部来源清单与法规梳理，也不引用其中的实测数字、价格、阈值；所有阈值一律注明「演示默认值，需真实语料校准」。
- 仍遵守 01 §C.3 的口径：**做就做四处一致，不做半套**。本轮正是按这个判断把四个出口一次性接齐（`guardrails/contracts.py:26-30` 的 `GATES` 是单点定义）。

### 0.3 01 的 A–F 结论逐条对照（哪些被采纳 / 哪些明确未做）

| 01 章节 | 01 当时的结论 / 建议 | 02 本仓状态 | 落点 |
|---|---|---|---|
| A.1 12 类标识符逐类判定 | 建议给「规则可行性 / 误报风险 / 是否需上下文」 | `已实现`（规则级）：11 条 `Rule` 覆盖 API_KEY / 身份证 18·15 位 / 手机号 / 银行卡 / 邮箱 / IPv4 / MAC / 护照 / 工号 / 中文姓名；中文姓名、工号、护照靠**字段名门控** | `detectors.py:77-110` |
| A.2 四级判定链 + 防误报 | 「格式 → 校验位 → 上下文 → 置信度」，重点防误报 | `已实现`：`_valid_id18` / `_luhn` / `_valid_ipv4` 校验位、`require_context` 上下文门控、`min_confidence` 置信度过滤、重叠消解长命中优先 | `detectors.py:26-62,121-148`；`redact.py:99` |
| A.3 准标识符 / k-匿名 / 泛化 | 只讲原理，明确留白，不编指标 | `未做`：本包只处理**直接标识符**，不做准标识符组合识别、k-匿名、泛化 | — |
| A.4 检测粒度（文档 / 分块 / 字段级） | 三层叠加，字段级最可靠 | `部分已实现`：文档级 = `analyze_document` 整篇 ingest；字段级 = `sources()` 只处理 `text/snippet/doc_title` 三字段；**分块级独立扫描未做**（靠切块前一次性脱敏覆盖） | `rag.py:80-94`；`pipeline.py:98-111` |
| B.1 入库前脱敏 vs 展示时打码 | 核心结论：必须 `redact before index` | `已实现`（webapp）：分块前脱敏，且脱敏失败 **fail-closed 阻断入库** | `rag.py:80-93` |
| B.2 进模型前 + 引用可核验性 | 三种取舍：原文旁路 / admin 回溯 / 带类型稳定占位符 | `部分已实现`：问题与来源片段进模型前脱敏，来源卡片与 `chat_sources` 读到同一份脱敏文本；**原文旁路存储、admin 回溯、带类型稳定序号占位符均未做**（当前 `mask` 是保首 3 尾 4） | `routers_chat.py:71-81`；`redact.py:61-69` |
| B.3 输出侧 SSE 跨 chunk | 建议重叠缓冲窗口，防标识符被切断 | `已实现`：`StreamRedactor` 跨下发边界的连续候选串整串扣住 | `redact.py:123-185` |
| B.4 日志 / 审计 / 导出 | 只存摘要、导出脱敏、trace 关联、token vault 放哪/谁能解 | `已实现`（前两项）：`log_action` 脱敏 `target/detail`、`safety_events` 只存统计、CSV/JSON 走同一规则；**trace_id 关联未做**（`safety_events` 无 `trace_id` 列）；**token vault 治理未做**（默认关闭、仅内存） | `db.py:256-268,323-357`；`routers_badcase.py:174-219` |
| B.5 四种动作 | `mask`/`hash`/`token`/`block`，默认不启用可逆 token | `已实现`：5 种（`remove` 为新增），`reversible_token` 默认 `False` | `contracts.py:18-23`；`redact.py:77-87` |
| B.6 可观测与规则版本化 | 命中分布、误报反馈闭环、规则版本与回放 | `已实现`（部分）：`RULE_VERSION` 随每条命中、`safety_events` 按 gate/kind 汇总、`/api/safety/status|demo` 可现场展示；**误报反馈闭环未做**（无反馈写入口）、**规则回放未做**（只有版本号，无历史版本比对） | `contracts.py:15,40`；`pipeline.py:130-195` |
| C.1 前中后置三段式 | 不另立框架，挂进既有三段式 | `已实现`（webapp 侧）：作为数据变换叠加，不改 `DEFAULT_ORDER`、不改「先按权限过滤 → 再重排 → 再进模型」红线；**backend 骨架未接** | `status()["integration"]` |
| C.2 parsing / knowledge / cache 关系 | 「缓存里不能存未脱敏内容」 | `已实现`（webapp 侧）：`chunks.text` 是脱敏文本，`chunk_embeddings` 由脱敏文本派生；**旁路原文、删除/改版的失效事件传播未做** | `rag.py:94-100,143-180` |
| C.3 C09 裁决 | 用户新指令覆盖方向，四处一致不做半套 | `已实现`：四出口全部接入 `Guardrails.protect()` 同一实现 | `pipeline.py:83-96` |
| C.4 11 项既有不变量 | 冻结区、C33、三维限流、90:10、`eval/` 阻断口径、缓存、静态 `ORG_NAMESPACE`、PG 真值、`chunk_embeddings.cache_key`、`WEBAPP_`/`PARSING_` 前缀、三态 | `已实现`（保持）：本包用独立的 `GUARDRAILS_*` 命名空间，不新增 `WEBAPP_` 键、不动冻结区、不改限流维度 | `pipeline.py:20-39` |
| D 工程要点 10 条 | 每条错误答法 + 正确答法 | 本文 §8 另给 **9 组**实现版问答（含 01 未涉及的 SSE 契约、演示盐、断线重连边界） | §8 |
| E 落地建议 8 条 | 1–7「需补齐 / 已具备基础」，8「本期不建议做」 | 1–7 大部分已 `已实现`；**第 8 条 A/B 档上线级仍 `未做`/`只设计`** | §9 |
| F 来源清单 29 条 | 可核实 URL + 标题 | 02 不重复外部调研；只引用项目内实读代码与既有验收结论 | 附录 |

> 结论：01 的**工程判断被整体采纳**（尤其是「入库前脱敏」「四处一致」「防误报四级链」「默认不启用可逆 token」），01 里属于 A/B 档上线级的部分**明确留白**（见 §9）。

---

## 1. 实现全景：纯函数包与四级判定链

### 1.1 包结构与数据契约

`guardrails/` 是**独立纯函数包**（`__init__.py:1-8` 自述「规则级检测（正则 + 校验位 + 上下文门控），不是模型级 NER，也不是合规审计」），不依赖 webapp / backend 的既有键：

| 文件 | 职责 | 关键标识 |
|---|---|---|
| `contracts.py` | 数据契约 | `RULE_VERSION="PII-RULES@1.0"`:15；`ACTIONS` 5 种 `mask/hash/token/remove/block`:18-23；`GATES` 四出口 `ingest/context/output/audit`:26-30；`PIIFinding`/`_preview`:33-53；`RedactionPolicy`:62-85；`RedactionResult`:88-115；`SafetyReport`:118-135 |
| `detectors.py` | 检测 | `RULES` 11 条:77-110；`scan()`:121-148；`rule_catalog()`:151-154 |
| `redact.py` | 执行与流式闸 | `HOLD=24`:26；`TokenVault`/`VAULT`:29-58；`redact_text()`:90-120；`StreamRedactor`:123-185；`redact_many()`:188-190 |
| `pipeline.py` | 统一入口 | `_env_policy()`:20-39；`DEMO_SAMPLES` 9 条:48-58；`Guardrails`:76-114；`status()`:130-148；`run_demo()`:151-195；`export_rows()`:198-208 |

`PIIFinding.as_dict()` 回显 `preview`（`_preview()` 只留首尾各 2 位，`contracts.py:49-53`），`RedactionResult.as_dict()` 给 `by_kind`/`actions`/`hit_count`/`rule_version`，`SafetyReport` 汇总四出口——**这些是给前端/演示看的运行时结构，不是落库结构**（落库见 §4）。

### 1.2 四级判定链（代码落点）

| 级 | 代码 | 说明 |
|---|---|---|
| L1 格式 | `RULES` 各 `Rule.pattern` | 数字边界断言 `(?<!\d)` / `(?!\d)`；捕获组 `group=1` 只取字段值 |
| L2 校验位 | `_valid_id18`（ISO 7064 MOD 11-2，`_ID_WEIGHTS`/`_ID_CHECK="10X98765432"`，含 `datetime.strptime` 验出生日期）:22-34；`_valid_id15`:37-44；`_luhn`:47-57；`_valid_ipv4`:60-62 | 校验位不通过一律跳过，正则写多长都替代不了 |
| L3 上下文 | `require_context` + `_has_context()`（`CONTEXT_WINDOW=40`）:112-118 | 15 位身份证、护照、工号、中文姓名必须靠关键词 / 字段名 |
| L4 置信度 | `Rule.confidence` + `RedactionPolicy.min_confidence`（默认 0.60）在 `redact_text():99` 过滤 | 低分不改写正文，避免「误脱敏即损坏内容」 |

`scan()` 的重叠消解（:141-146）按 `(start, -(end-start), -confidence)` 排序，**长命中优先、同长度高置信度优先**，避免同一串数字被报成多种类型。

### 1.3 四类真实误报与两个已修实现缺陷（重点）

**四类误报（01 §A.2 的结论，代码里逐条兑现）**：

| 误报类型 | 代码里的防线 | 负向对照 |
|---|---|---|
| 18 位订单号被当身份证 | `ID_CARD_CN` 正则 `(?<!\d)\d{17}[\dXx](?!\d)` + `_valid_id18`（出生日期 + MOD 11-2） | `订单号 11010119900307123456 不是身份证。` |
| 13 位长号里的手机号子串 | `PHONE_CN` 用 `(?<!\d)1[3-9]\d{9}(?!\d)` 前后数字边界 | `流水号 1138123456789 不是手机号。` |
| 银行卡要 Luhn | `BANK_CARD` 带 `validator=_luhn`，只靠位数不够 | `证件号 11010119900307193X 校验位不合法，不应误报。` |
| 中文姓名需字段名门控 | `NAME_CN` 正则要求 `(?:姓名|联系人|客户姓名|收件人)\s*[:：]\s*…`，只取捕获组 | `长按复位键进入配网模式…`（普通技术文档） |

`DEMO_SAMPLES`（`pipeline.py:48-58`）共 9 条：5 条正向（合法身份证 / 手机号+邮箱 / API 密钥应拦截 / 工号+姓名 / Luhn 合法银行卡）+ **4 条负向对照**。`test_guardrails.py:60-69` 断言四条负向对照 `scan(t) == []`；`run_demo()["negative_controls"]` 收集命中数为 0 的负向样本，`test_safety_api.py:106-111` 断言长度为 4。

**缺陷① · BANK_CARD 正则右边界漏了 `X/x`**（已修，`detectors.py:92-93`）：

- 现规则：`r"(?<!\d)\d{16,19}(?![\dXx])"`。
- 旧写法只挡数字 `(?!\d)`。对「校验位非法的身份证」`11010119900307193X`：`\d{16,19}` 会贪婪匹配到 17 位数字 `11010119900307193`，下一个字符是 `X`（不是数字），右边界通过 → 这段数字被当成**独立的银行卡候选**送进 `_luhn`；按 Luhn 复算该 17 位串恰为 10 的倍数，于是「校验位非法的身份证」会被**巧合命中成银行卡**，负向对照失败。
- 修法：`(?![\dXx])` 把 `X`/`x` 也纳入右边界排除，整串不再作为银行卡候选，交给 `ID_CARD_CN` 规则——而 `_valid_id18` 会判它校验位非法并跳过，双重防线闭合。`:93` 的 `note` 逐字写明这个理由。
- 配套 `test_overlap_resolution_keeps_id_card_not_bank_card`（`test_guardrails.py:72-75`）：合法 18 位身份证同时符合身份证与银行卡形状时，只报 `ID_CARD_CN`。

**缺陷② · 旧 `StreamRedactor` 只留尾部 `HOLD` 会泄漏**（已修，`redact.py:123-185`）：

- 旧逻辑：`boundary = len(buf) - HOLD`，只把尾部 `HOLD=24` 个字符留下不**下发**。长文本**逐字符**下发时，若 18 位身份证的起点落在「可以下发」的区域、终点还在保留区，按字符切片会把前 10 位先发出去——**发出去就撤不回来**。
- 修法（`push()`:154-179）：先用 `_CANDIDATE_RE = re.compile(r"[A-Za-z0-9_.@+\-:]+")`（:134，候选串含数字/字母与 API key、邮箱、MAC、工号常见分隔符；中文与中文标点天然充当边界）找出**横跨下发边界**的连续候选串，若 `m.start() < boundary < m.end()` 则把 `boundary` 收缩到 `m.start()` 并 `self.held += 1`，即**整串扣住**；随后只对 `f.end <= boundary` 的完整命中做 `redact_text()`。`flush()`:181-185 收尾时把剩余缓冲区整段脱敏后返回。类 docstring（:123-130）逐字记录了这个缺陷与修复理由。
- 验证：`test_stream_does_not_leak_identifier_split_across_chunks`（:161-167）断言 `ID_OK not in out` 且 `sr.held > 0`；`test_stream_chunk_sizes_never_leak`（:170-176）参数化 **7 种 chunk 大小** `[1,2,5,9,17,31,64]` 全部不泄漏。

---

## 2. 四出口接入表与「检索用原文」的次序

### 2.1 四出口接入点

| 出口 | 文件 / 函数 | 时机 | fail-closed 行为 | 台账 |
|---|---|---|---|---|
| ① `ingest` 入库前 | `webapp/rag.py::analyze_document`（:80-93） | `extract_text()` 之后、`chunk_text()` 与 `chunks` 落库之前；命中记 `upload:{doc_id[:8]}` | **有**：`try/except` 包住 `g.ingest()`，异常 → `_set_error(..., "内容安全脱敏不可用，已阻断本次入库（fail-closed）")` 并 `return`，整份不入库 | `db.record_safety_event(ingest_result, …)` |
| ② `context` 进模型前 | `webapp/routers_chat.py::chat_stream`（:71-81） | `rag.retrieve()` 之后、送 LLM 与 `_persist()` 之前；`g.context(question)` + `g.sources(sources_raw)` | **无独立 fail-closed 包裹**：`guardrails` 调用异常会冒泡并中断该次请求（诚实边界，见 §9） | 问题命中与每个来源片段命中均 `db.record_safety_event(..., f"chat:{conv_id[:8]}")` |
| ③ `output` 输出前 | `routers_chat.py::chat_stream::gen()`（:97-129） | SSE 逐 token，`sr.push(item["token"])`；收尾 `sr.flush()` | **无独立 fail-closed 包裹**（同上） | `db.record_safety_counts("output", sr.by_kind, …, rule_version=RULE_VERSION)`（:126-129） |
| ④ `audit` 日志与导出前 | `webapp/db.py::log_action`（:323-357）、`webapp/routers_badcase.py`（:27-38 `_safe_fields`、:174-219 `/export`）、`guardrails/pipeline.py::export_rows` | 写 `audit_logs` / `badcases` 采集·回显·改状态 / CSV·JSON 导出之前 | **有**：`log_action` 异常 → `target, detail = "[脱敏失败:字段已丢弃]"`（:341-343），宁可丢字段不写原文 | `log:{action}` / `badcase:*` 前缀写 `safety_events` |

补充事实：
- `badcases` 是**采集即脱敏**（`routers_badcase.py:99-104` 注释「库里就不该存明文」），`list_badcases`（:131-142）再过一次以兼容升级前已落库的明文数据；`/export` 用 `export_rows(..., fields=_TEXT_FIELDS)`，CSV/JSON 走同一份规则（:185-196）。
- `_persist()`（`routers_chat.py:178-213`）写 `chat_logs.content` 的是 `safe_question` 与脱敏后的 `answer`，`chat_sources.snippet/chunk_text` 是 `g.sources()` 产出的脱敏文本——**落库面与模型输入面是同一份文本**。
- `db.log_action` 的 `actor` 不脱敏（:328 注释「生产若用邮箱当账号需单独加规则」），属明确边界。

### 2.2 为什么「检索用原文、进模型前才脱敏」

`chat_stream()` 的顺序是：**`rag.retrieve(body.question)` 用原始问题（:65-66）→ `g.context()` 得到 `safe_question`（:72）→ `rag.stream_answer(safe_question, safe_hits, …)`（:102）**。代码注释 :65 逐字写明理由：「脱敏后的问题会破坏词法召回，顺序是『先检索、后脱敏』」。

- `rag.retrieve()` → `_lexical_terms()`（`rag.py:361-378`）把问题拆成英文/数字整词 + 中文相邻二元组做词法门禁。若先把手机号 / 身份证打码成 `138****5678`，门禁词就变了，会召回失败或错配。
- 注意「用原文检索」指的是**用原始问题串**；索引侧的 `chunks.text` 已经是 ① `ingest` 脱敏后的文本（`_load_ready_chunks()` 从 `chunks` 表读），所以文档侧从一开始就没有明文可检索。
- 来源片段经 `g.sources()` 脱敏后作为 `safe_hits` 进模型，同时用于 `meta.sources` 与 `chat_sources`（:79-81 注释保证三者读到同一份文本）。
- 与 01 §B.2「脱敏与权限过滤正交、分层」对齐：演示切片**没有逐条 `decide()` 权限复核**（`search_debug()` 的 `permission_filter` 自述「生产为部门树 + decide() 在召回前过滤」），这是既有边界，不因脱敏改造被说成已具备。

---

## 3. SSE 流式闸与断线续传契约冲突（C13）

### 3.1 约束：脱敏不能改包数

既有断线续传契约（团队口径 C13）要求 **token 包 `index` 连续**：`rag.py::stream_answer()` 产出的每个包带 `index`（原始 token 序号，:409-415），前端 `app.js:503` 用 `lastIndex = obj.index`、:510 `resumeFrom = lastIndex + 1`，重连时 `body.resume_from` 让服务端 `rag.py:406` 跳过已发 token。`test_safety_api.py:164` 断言 `all(t["index"] == i for i, t in enumerate(tokens))`，:184-192 断言 `resume_from=12` 时 `tokens[0]["index"] == 12`。

因此流式脱敏**不能合并/拆分/丢弃包**，也不能改 `index`。

### 3.2 解法：安全片段作为当前包 token + `done.tail` 补齐

| 环节 | 代码 | 行为 |
|---|---|---|
| 每包 | `routers_chat.py:106-120` | `safe_token = sr.push(item["token"])` 作为该包的 `token` 下发（**可能为空串**，也可能包含之前扣住后释放的内容）；`index` 仍用 `item["index"]`。一个原始 token 仍对应一个 SSE 包 |
| 前端中间包 | `app.js:500-505` | `bot.bubble.insertAdjacentText('beforeend', obj.token)`；空串即空追加，不掉包 |
| 收尾 | `routers_chat.py:123-125` | `tail = sr.flush()`，`answer = "".join(full)`；`done` 事件同时带 `answer` 与 `tail`（:134-146）及 `safety: sr.summary()` |
| 前端补齐 | `app.js:492-496` | `if (obj.tail) bot.bubble.insertAdjacentText('beforeend', obj.tail)`；注释逐字写明「流式脱敏闸会扣住尾部若干字符等右边界确认；收尾时由 done.tail 补齐，这样『一个原始 token 一个包』的续传索引契约与内容安全两者都不破」 |

`done.tail` 是补偿通道：被 `StreamRedactor` 扣住、没能在中间包下发的尾部，在流正常收尾时一次性追加，既保住包数契约，又不让扣留内容丢失。

### 3.3 剩余边界（诚实写出，未做）

- `StreamRedactor` 在 `gen()` 内**每次请求新建**（`routers_chat.py:100`），`buf` 不跨连接持久化，无 `conv_id` 维度的状态恢复。
- 前端游标 `resumeFrom = lastIndex + 1` 基于**原始 token 索引**，而服务端重放 `rag.stream_answer(..., resume_from, ...)` 也是按**原始 token 序列**跳过；但脱敏后的「安全输出」与「原始 token 序列」不再一一对应（`push()` 返回值可能为空串、可能延迟释放之前扣住的内容）。
- 结果：**真实断线重连时，断线瞬间被扣留在 `sr.buf` 里的尾部既未下发、也不跨连接保留；重连后从原始索引续传会把这些 token 跳过，答案可能少几个字**。
- 生产正解：把续传游标改为**基于安全输出字节偏移**（服务端记录已下发的脱敏字节数，重连按安全输出回放），或按 `conv_id` 持久化 `StreamRedactor` 的缓冲区与已下发偏移；本轮**未做**。
- 现有 `test_resume_from_index_contract_still_holds`（`test_safety_api.py:184-192`）只验证「`index` 契约不破」，**不验证断线重连后内容逐字无损**——这是测试覆盖的明确边界，不能拿它当「断线无损」的证据。

---

## 4. 台账 `safety_events`：字段与「只存统计」的理由

### 4.1 表结构与写入口

`webapp/db.py:256-268`：

| 字段 | 含义 |
|---|---|
| `id` / `ts` | 主键与时间戳 |
| `gate` | `ingest` / `context` / `output` / `audit` 四出口之一 |
| `kind` | 命中类别（如 `ID_CARD_CN`、`PHONE_CN`） |
| `hit_count` | 该类命中数量 |
| `blocked` | 是否触发 `block` |
| `action` | 本次采用的主导动作 |
| `rule_version` | 规则版本 `PII-RULES@1.0` |
| `source` | 调用点标识：`chat:{id}` / `upload:{id}` / `badcase:{id}` / `log:{action}` |

索引 `ix_safety_events_ts`、`ix_safety_events_gate`。写入口两个：`record_safety_counts()`（:282-303，按 kind 逐条插，流式闸只有计数走这里）与 `record_safety_event()`（:306-320，从 `RedactionResult.as_dict()` 只取 `by_kind`/`actions`/`blocked`/`rule_version`，`action` 取计数最多的动作）。两者都是**旁路**，异常 `pass`，不影响业务主流程。读取侧 `routers_safety.py::_ledger()`（:36-58）按 gate / (gate,kind) 汇总，`GET /api/safety/events` 与 `status().ledger` 暴露给前端。

### 4.2 为什么只存统计，不存原文 / preview / 可逆映射

1. **避免台账成为新的泄漏面**：01 §B.4 说日志是「影子副本」。若把原文或 `preview` 写进 `safety_events`，等于把 PII 再复制一份到一张新表，且这张表会被 `/api/safety/events` 任意登录用户读取。表注释（:256-257）逐字写明「不存原文、不存首尾 preview、不存可逆 token 映射」。
2. **代码层面确实不取 findings**：`record_safety_event()` 只调 `result.as_dict()` 里的聚合字段，**从不取 `findings`**，所以 `PIIFinding.as_dict()` 里的 `preview`（`contracts.py:46`）根本不会进台账。
3. **可逆映射默认关闭且只在内存**：`TokenVault._fwd/_rev`（`redact.py:29-58`）是模块级内存字典，不落库；`reversible_token` 默认 `False`，`_replace()` 仅在两者同时为真才写 `VAULT.put()`。`status()["vault"]`（`pipeline.py:135-136`）只暴露 `enabled` 与 `entries` 计数，不暴露映射内容。
4. **运行时返回与落库严格区分**：`/api/safety/check` 与 `/demo`（`routers_safety.py:77-94`）返回的 `report` 里确实含 `findings[].preview`（脱敏后的首尾 2 位）与内置合成样本 `original`，但这两个接口**只跑内存、不写台账、输入不保存**（:7、:87、:93）；`test_safety_api.py:102` 断言 `ID_OK not in json.dumps(d)`。这与 `safety_events` 的落库口径是两件事，不能混为一谈。
5. `test_safety_events_ledger_records_real_hits_by_gate`（:231-242）断言台账里 `ID_OK not in json.dumps(d)`，且 `by_gate` 真实含 `ingest` 与 `context`。

---

## 5. 策略与开关

### 5.1 环境变量与默认值（`pipeline.py::_env_policy()`, :20-39）

| 键 | 默认 | 作用 | 边界 |
|---|---|---|---|
| `GUARDRAILS_ENABLED` | `1`（`_truthy` 把 `0/false/no/off/空` 判为假） | 总开关 | 关闭后四个出口全部放行原文 |
| `GUARDRAILS_ACTION` | `mask` | 默认动作，取值须在 `ACTIONS` 内 | `PUT /api/safety/policy` 校验，非法值返回 400 |
| `GUARDRAILS_MIN_CONFIDENCE` | `0.60` | 置信度下限，`float()` 解析失败回退 0.60 | `PUT` 校验 `[0,1]` |
| `GUARDRAILS_HASH_SALT` | `demo-salt-change-me` | `_hash()` 的 HMAC 盐 | **演示占位** |
| `GUARDRAILS_REVERSIBLE_TOKEN` | `0` | 是否允许可逆 token | 默认关闭 |

`RedactionPolicy`（`contracts.py:62-85`）默认 `enabled=True`、`default_action=mask`、`block_kinds=("API_KEY",)`、`min_confidence=0.60`、`reversible_token=False`、`hash_salt="demo-salt-change-me"`、`mask_keep_head=3`、`mask_keep_tail=4`。`as_dict()` 给 `hash_salt_configured`（:84，判断是否已换掉占位盐），前端 `app.js:1618` 据此把「否（演示占位盐）」标红。

**默认盐的诚实边界**：`_env_policy()` 注释（:23-24）逐字写明「默认盐只是演示占位，用默认盐做 hash 脱敏等于没脱（彩虹表可反推）」。`_hash()`（`redact.py:72-74`）是 `HMAC-SHA256(salt, value)[:12]`，对手机号 / 身份证这类小空间值，固定且公开的盐可被穷举反推。**生产必须把盐换成密钥管理系统托管的值**；本轮 `未做`。

### 5.2 运行时接口仅用于对照演示

- `PUT /api/safety/policy`（`routers_safety.py:97-134`）**仅 admin**（`require_admin`），改动 `db.log_action` 留痕，`set_policy()` 本进程即时生效不重启；`PolicyPatch` 可改 `enabled` / `default_action` / `min_confidence` / `reversible_token` / `hash_salt` / `mask_keep_head` / `mask_keep_tail`。
- 返回的 `warning`（:127-133）明确：关闭 `enabled`「会让四个出口全部放行原文，仅限对照演示；生产环境应由配置管理系统锁定，不提供运行时关闭入口」；开启可逆 token 则警告映射是新的泄漏面。
- `test_policy_disabled_is_warned_and_really_disables`（`test_safety_api.py:126-132`）验证关闭后 `/check` 的 `total_hits == 0` 且原文出现在结果里——**这是对照演示，不是安全状态**。
- `GET /status`、`/events`、`POST /check`、`POST /demo` 任意登录用户可读（:4-5 注释「评审方要能现场点开看」）；写接口仅 admin。

---

## 6. 前端呈现（简述）

`webapp/static/index.html:717-778` 的 `view-safety` 页：四级判定链说明、当前策略与四出口台账（`sf-policy` / `sf-integration` / `sf-rules`）、一键演示、任意文本四出口检测、admin 策略开关；:775 的边界文字逐字写明「规则级检测不是模型级 NER；中文姓名、工号、护照靠字段名词定位；可逆 token 默认关闭；关闭启用仅限对照演示；台账只存类别与数量」。

`webapp/static/app.js`：`loadSafety()`（:1606-1638）渲染状态/台账/规则表，`runSafetyDemo()`（:1640-1661）、`runSafetyCheck()`（:1663-1677）、`saveSafetyPolicy()`（:1679-1694）分别对应 `/demo`、`/check`、`PUT /policy`；SSE 侧的 `done.tail` 追加见 §3.2。

---

## 7. 验收清单（可量化，逐条给断言与落点）

> 以下为**断言条件与既有验收结论**，本文未重新执行测试；数字均来自实读的测试源码与团队既有结论，不是新测出来的。

| # | 验收项 | 断言 / 判据 | 落点 |
|---|---|---|---|
| 1 | 合成样本校验位合法 | `ID_OK=110101199003071938` 过 `_valid_id18`；`ID_BAD=11010119900307193X` 判非法；`CARD_OK=6222021234567894` 过 `_luhn`；月份 13 / 少一位均判非法 | `test_guardrails.py:21-24,37-49` |
| 2 | 4 条负向对照零命中 | `scan(t) == []`；`run_demo()["negative_controls"]` 长度 4 | `test_guardrails.py:60-69`；`pipeline.py:192-193`；`test_safety_api.py:106-111` |
| 3 | 流式 `leaked=false` | `run_demo()["stream"]["leaked"]`（判 `"110101199003071938" in stream_out`）为 `False` | `pipeline.py:173-182`；`test_guardrails.py:208-216`；`test_safety_api.py:106-111` |
| 4 | 7 种 chunk 大小不泄漏 | `parametrize [1,2,5,9,17,31,64]` 全部 `ID_OK not in out` | `test_guardrails.py:170-176` |
| 5 | 无 PII 文本流式 roundtrip 逐字相同 | `assert out == text`（长中文文本逐字符下发） | `test_guardrails.py:179-184` |
| 6 | 四出口结果一致 | `len({r.text for r in results}) == 1`；`/check` 的 `total_hits == 8`（2 命中 × 4 出口）；`run_demo()["chain"]` 4 项文本相同 | `test_guardrails.py:197-206`；`test_safety_api.py:95-103`；`pipeline.py:164-171` |
| 7 | 台账不存原文 | `ID_OK not in json.dumps(ledger)`；`audit_logs.target/detail`、badcase 采集/列表/CSV/JSON 导出、`chat_logs`/`chat_sources` 均不含原文 | `test_safety_api.py:137-150,155-181,197-242` |
| 8 | SSE 包索引契约不破 | `all(t["index"] == i)`；`resume_from=12` 时首包 `index==12` | `test_safety_api.py:155-192` |
| 9 | 全量回归 | `guardrails/tests/test_guardrails.py` **27 项**、`webapp/tests/test_safety_api.py` **11 项**（已实读源码确认）；全量 **248 项单测 + 33/33 e2e DEMO_READY** 为团队验收结论（含主理人后续补跑的 backend 检索契约对拍 10 项），本文未重新执行全量 | 两组测试文件；团队交付结论 |

> 合成样本里的证件号 / 手机号 / 卡号均按校验位算法合成的教学用号码（`routers_safety.py:65-68` 的 `samples_note`），不是任何真实个人信息；`run_demo()["note"]`（`pipeline.py:194`）也自述「仅验证规则连通性与防误报，不是质量基线」。

---

## 8. 常见问题（9 组）

| # | 问 | 错误答法 | 正确答法要点 |
|---|---|---|---|
| 1 | **为什么不在展示层打码？** | 「用户看不到就安全了。」 | 展示层只覆盖一个出口；原文会先进入 `chunks`、向量缓存、模型上下文、`chat_logs`、`chat_sources`、CSV 导出。本轮把 `g.ingest()` 放在 `chunk_text()` 之前（`rag.py:80-94`），因为 `chunks` 是检索 / 缓存 / 来源卡片的共同真值源，晚一步就处处带出去。 |
| 2 | **规则级和模型级 NER 的边界？** | 「上了正则就等于有内容安全模型。」 | 本包是 11 条正则 + 校验位 + 字段名门控，只能识别**有稳定格式或字段名**的直接标识符；自由文本里的人名、地址、语义变形、图片表格内 PII 一律识别不了。`__init__.py:6-7` 与 `status()["integration"]["model_level_ner"]="未做（A/B 档）"` 如实标注。 |
| 3 | **可逆 token 为什么默认关？** | 「能还原最灵活，默认开更好。」 | `TokenVault._rev` 是「假名 → 真名」映射，本身就是高敏资产，引出加密存储、独立授权、谁能解、保留多久一整套治理。`reversible_token` 默认 `False`，`_replace()` 双条件才写 `VAULT`，且 vault 只在内存不落库（`redact.py:29-58,77-87`）。 |
| 4 | **脱敏会不会伤检索？** | 「脱敏了照样能搜，没关系。」 | 会，所以顺序是「先检索、后脱敏」：`rag.retrieve(body.question)` 用**原始问题串**，因为 `_lexical_terms()` 靠数字整词与中文二元组做词法门禁；打码会破坏召回。而索引侧的 `chunks.text` 已经是 ingest 脱敏后的文本。代价是**无法按原始标识符精确检索**，这是取舍，不是「无影响」。 |
| 5 | **身份证正则怎么防误报？** | 「把正则写长点，加生日段。」 | 四级链：`(?<!\d)\d{17}[\dXx](?!\d)` 格式 → `_valid_id18` 的 ISO 7064 MOD 11-2 + `strptime` 日期 → 上下文 → `min_confidence`；18 位订单号、校验位非法的 `11010119900307193X` 都被负向对照挡住。 |
| 6 | **那 `11010119900307193X` 为什么曾误报成银行卡？** | 「身份证规则会拦住它，不会误报。」 | 旧 BANK_CARD 右边界只挡数字，17 位数字前缀被当成独立卡号送进 `_luhn` 且恰好通过；已改成 `(?![\dXx])` 把 `X/x` 也排除（`detectors.py:92-93`）。这是「一条规则的漏洞由另一条规则的正则引发」，单看某条规则查不出来。 |
| 7 | **SSE 流式怎么脱敏？包数能改吗？** | 「每个 chunk 扫一遍就行」／「把命中包合并掉」。 | 不能改包数：`index` 必须连续（C13 续传契约）。解法是 `sr.push()` 返回的安全片段**作为当前包 token**（可为空串），收尾 `sr.flush()` 由 `done.tail` 补齐，前端 `insertAdjacentText` 追加（`routers_chat.py:106-146`、`app.js:492-502`）。 |
| 8 | **断线重连后内容一定完整吗？** | 「有 `resume_from` 就无损。」 | **不一定**。`StreamRedactor` 每请求新建、buf 不跨连接保留，游标基于原始索引；断线瞬间被扣住的尾部会丢，重连可能少几个字。生产应改为基于**安全输出字节偏移**的游标或按 `conv_id` 持久化缓冲状态；现有用例只验证索引契约、不验证逐字无损。 |
| 9 | **台账为什么不存原文？hash 盐又为什么危险？** | 「存个 preview 方便排查」／「hash 了就匿名了」。 | `safety_events` 只存 gate/kind/数量/动作/规则版本，存 preview 等于再造一份影子副本；`record_safety_event()` 根本不取 `findings`。而 `hash_salt` 默认 `demo-salt-change-me` 是公开演示盐，对小空间值可被彩虹表反推（`pipeline.py:23-24` 注释自述「等于没脱」），生产必须换密钥管理；`as_dict()["hash_salt_configured"]` 与前端标红就是提醒这件事。 |

---

## 9. 明确未做（A/B 档上线级，留白，不写成已完成）

按 01 §E 第 8 条与 `status()["integration"]` 的既有标注，逐条点名：

| # | 未做项 | 现状三态 | 说明 |
|---|---|---|---|
| 1 | **模型级 NER / 上下文语义识别** | `未做` | 只有规则级检测；自由文本人名、地址、语义变形识别不了 |
| 2 | **非结构化图片 / 表格内 PII** | `未做` | `extract_text()` 只支持 `_TEXT_EXT` 纯文本（`.txt/.md/.csv/.json/.py/...`），不支持的格式直接降级 |
| 3 | **OCR 前处理** | `未做` | 无 OCR 链路，扫描件不在范围内 |
| 4 | **合规审计与留存策略** | `未做` | `safety_events` 无留存 / 归档 / 冷存储策略，`status()["integration"]["compliance_audit"]` 标「未做（留存策略 / 法务审批 / SIEM 对接）」 |
| 5 | **SIEM 对接 / 异常告警** | `未做` | 台账只供页面汇总，无告警通道 |
| 6 | **密态检索 / 脱敏态可检索** | `未做` | 当前靠「检索用原文问题 + 索引侧脱敏文本」绕开，无 FPE / 密态索引 |
| 7 | **压测与阈值标定** | `未做` | 置信度 0.60、`HOLD=24`、保留首 3 尾 4 均为演示默认值；`RedactionPolicy.as_dict()["note"]` 自述「需真实语料校准」；`run_demo()["note"]` 自述「不是质量基线」 |
| 8 | **`backend/` 骨架接入** | `未做` | `backend/app/agent/orchestrator.py:41-44` 仍是两条正则（`1[3-9]\d{9}` / `\d{17}[\dXx]`），`:153-161` `verify_node()` 仍只做输出替换；`Pipeline.status()` 明确「未接本包」 |

**本轮新增的、同样明确留白的边界**（来自 01 但本轮未做）：准标识符组合 / k-匿名 / 泛化（A.3）、原文旁路存储 + admin 可回溯 + 带类型稳定序号占位符（B.2）、`trace_id` 与台账关联（B.4）、误报反馈闭环与规则回放（B.6）、删除/改版的旁路失效事件传播（C.2）、`context`/`output` 出口的独立 fail-closed 包裹（§2.1）、生产密钥管理（§5.1）、断线重连的安全输出字节偏移游标（§3.3）。

> 讲解口径：以上一律按「`未做` / `只设计`」表述；任何「已实现企业级内容安全」「已通过合规审计」「backend 已接入」的说法都与本仓代码冲突。本轮成果的准确说法是：**`webapp/` 演示切片已实现规则级、四出口一致的 PII 脱敏骨架**。

---

## 附录 · 本文事实源索引（全部为本次实读）

| 主题 | 文件 / 位置 |
|---|---|
| 契约与常量 | `guardrails/contracts.py`；`guardrails/__init__.py` |
| 检测规则与校验位 | `guardrails/detectors.py`（`RULES` / `scan` / `_valid_id18` / `_luhn` / `_valid_ipv4` / `_has_context`） |
| 动作与流式闸 | `guardrails/redact.py`（`_mask` / `_hash` / `_replace` / `redact_text` / `StreamRedactor` / `TokenVault`） |
| 四出口统一入口 | `guardrails/pipeline.py`（`_env_policy` / `Guardrails` / `status` / `run_demo` / `export_rows` / `DEMO_SAMPLES`） |
| 入库前出口 | `webapp/rag.py::analyze_document` / `_load_ready_chunks` / `retrieve` / `_lexical_terms` / `stream_answer` |
| 进模型前 / 输出前 / 落库 | `webapp/routers_chat.py::chat_stream` / `_persist` / `_source_dict` |
| 审计出口与台账 | `webapp/db.py::log_action` / `record_safety_counts` / `record_safety_event` / `safety_events` 表 |
| 采集 / 列表 / 导出 | `webapp/routers_badcase.py`（`_safe_fields` / `create` / `list` / `patch` / `export`） |
| 安全 API | `webapp/routers_safety.py`（`_ledger` / `status` / `events` / `check` / `demo` / `put_policy`） |
| 前端 | `webapp/static/index.html` `view-safety`（:717-778）；`webapp/static/app.js` SSE 循环（:469-510）、`loadSafety`（:1606-1694） |
| 测试 | `guardrails/tests/test_guardrails.py`（27 项）；`webapp/tests/test_safety_api.py`（11 项） |
| 上游调研与既有口径 | `docs/content-safety/01-research.md`；`docs/llm-safety-positions.md`；`docs/knowledge/02-candidates.md` C09；`docs/kb-capability-checklist.md` F4/F5 |

> 本文不含代码改动、不含实测数字与价格、不含新增外部 URL；「248 单测 + 33/33 e2e」为团队验收结论（含主理人后续补跑的 backend 契约对拍 10 项），本文未重新执行全量。
