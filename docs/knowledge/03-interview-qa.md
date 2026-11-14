# 03 · 面试问答资料：入库新鲜度与事实冲突检测

> - 文档路径：`docs/knowledge/03-interview-qa.md`｜编制：`kb-researcher-b`｜日期：2026-11-14
> - 用途：**用户直接拿去准备面试**的话术与演示指引，不是调研报告、不是设计文档、不是验收报告
> - 分工：`01-research.md` 写工业实践调研与落地建议、`02-candidates.md` 写候选功能与取舍裁决，本文只写「面试官怎么问、你怎么答、指哪里给人看、哪些话不能说」，不重复前两份的结论
> - 项目定位：企业内部知识库 RAG「开发框架骨架 + 可运行演示切片」，用于学习与面试讲解，**不是上线交付版**
> - 变更范围：本次只新增本文件；未改任何 `.py` / `.html` / `.js` / `.yml`，未动冻结区 `eval/` / `demo/` / `evalbench/`，未碰 `01-research.md`、`02-candidates.md`、`parsing/` 与 `docs/parsing/`

## 0.0 主理人最终状态回填（2026-11-14 链路接入后，优先级高于下文）

> 本文编制时 `knowledge/` 只有算法包，尚未接入演示切片。**同日主理人已完成接入**，
> 请以本节为准；下文 Q&A 里的旧状态标注保留不回改，用于追溯「设计 → 落地」过程。

| 项 | 编制时状态 | 最终状态 |
|---|---|---|
| `knowledge/` 算法包 | 已实现 | 已实现（23 项离线单测实跑通过） |
| `webapp/db.py` 时效元数据列 | 未做 | **已实现**：`valid_from` / `expires_at` / `last_verified_at` / `revision` / `authority` / `scope_json` + 幂等迁移 |
| `webapp/routers_knowledge.py` API | 未做 | **已实现**：状态 / 策略（admin 写）/ 场景 / 自定义检测 4 个端点 |
| `webapp/rag.py` 新鲜度重排 | 未做 | **已实现但默认关闭**：`WEBAPP_FRESHNESS_ENABLED=1` 开启；hit 带 `freshness` / `effective_score`，`webapp/routers_chat.py` 的 `_source_dict()` 透传到 SSE 来源 |
| 前端「知识治理」视图 | 只设计 | **已实现**（第 13 个 `data-view`，不破坏 C33）：策略表单、排序前后对照、逐文档新鲜度表、冲突簇裁决日志、EvidenceRecord 与负向对照 |
| 内置合成演示场景 | 未做 | **已实现**：`knowledge/samples.py`，6 份文档，两类裁决（新版本需复核 / 权威度裁决）+ 3 条负向对照，不写业务库 |
| 生成层注入 EvidenceRecord / 逐句 grounding | 只设计 | **仍为只设计**：证据记录已就绪但尚未注入 prompt，逐句高亮未做 |
| 指标统计看板 / 金标集 / 真实压测 / 质量基线 | 未做 | **仍未做**，保持原标注 |

**仍然成立的诚实边界**：合成样本不是质量基线；阈值是演示经验起始值；权威注册表属治理流程、未做；`eval/` 冻结口径不变。
由此，Q1 / Q2 / Q5 / Q10 / Q11 里「应做成的前端证据」现在**都能在页面上直接指给人看**了，答法可以相应从「设计上应该」改成「现场点开给你看」。

## 0. 怎么用这份资料

### 0.1 三态口径（本文统一用项目主口径）

本文每个能力标注 **已实现 / 只设计 / 未做**：**已实现**指仓库里有可运行代码或表结构；**只设计**指有契约、字段、设计文档或演示约定但没有可运行的该功能逻辑；**未做**指连落点都没有。注意与 `01-research.md` §0.1 的三档（「已具备基础 / 需补齐 / 本期不建议做」）是**不同用途的落地判断，不互相换算、不覆盖**；本文凡引用既有能力，沿用原文档原词。

### 0.2 现状速览（2026-11-14 逐文件实查，先读这段再背答案）

- `knowledge/` 算法包**已实现**：`contracts.py` / `freshness.py` / `claims.py` / `conflicts.py` / `samples.py`，`knowledge/tests/test_knowledge.py` 23 项离线单测**已实跑通过**（零外部调用）。
- **链路与前端已接入**（详见 §0.0 回填）：`webapp/db.py` 时效列 + 幂等迁移、`webapp/routers_knowledge.py` 4 个 API、`webapp/rag.py` 可选新鲜度重排（默认关闭）、`webapp/routers_chat.py` 透传来源字段、前端「知识治理」视图。
- 因此端到端准确说法是：**算法层、演示 API、前端展示均已实现；真实检索链的新鲜度重排默认关闭（开关可控）；生成层注入 EvidenceRecord 仍为只设计**。凡下文写「前端应做成……」，凡是 §0.0 标为已实现的，都可以现场点开给人看。
- 演示切片是 SQLite + 进程内队列替身，生产骨架是 PostgreSQL / Milvus / Redis + Celery；两者不可混说（详见 §3）。

### 0.3 术语速查（英文键名首次出现均配中文说明）

| 英文键名 | 中文说明 |
|---|---|
| `valid_from` / `expires_at` | 生效起始时间 / 失效时间（**事实为真的区间**，不等于发布时间） |
| `last_verified_at` | 最后人工核实时点，不由编辑自动推进；算文档年龄时优先于 `indexed_at` |
| `scope` | 适用范围（`region` 地区 / `plan` 套餐 / `product_version` 产品版本） |
| `authority` | 来源权威度，由业务负责人按领域定义，不是开发写死的全局常量 |
| `half_life_days` / `blend` / `demote_floor` | 半衰期天数 / 新鲜度在有效分里的权重 / 衰减下限（保底不归零） |
| `base_score` / `effective_score` / `original_rank` | 原分（相关性）/ 有效分（叠加新鲜度后）/ 原排名，供前端做开关前后对照 |
| `Claim.key` | `主体\|属性\|适用范围` 组成的断言键，决定「哪些断言在说同一件事」 |
| `conflict cluster` | 冲突簇：冲突图连通分量，每个簇独立裁决，不整批只留最新一份 |
| `EvidenceRecord` | 证据记录：给生成层的结构化契约，含断言 / 冲突 / 选中与排除清单 / `warnings` 告警 / `resolution_log` 裁决日志 / `conflict_signal` 冲突信号 |
| `ghost vector` | 幽灵向量：源文档已删除、向量索引里仍残留的孤立向量 |
| `bi-temporal` | 双时态：事务时间（何时录入）与有效时间（事实何时为真）是两个独立维度 |
| `abstention` | 拒答：证据不足或无法裁决时如实说明，不硬凑答案 |

### 0.4 每组问答的固定结构

下面 12 组均按「**问题（面试官口吻）→ 常见错误答法 → 正确答法（分层）→ 本项目怎么落地 / 演示 → 诚实边界**」五段写。「本项目怎么落地 / 演示」里尽量给一条**能在前端指给人看的证据**，并注明该证据的状态（字段已就绪 / 前端展示只设计或未做）——重点是演示技术深度，不是调效果。

## 1. 核心问答（12 组）

### Q1 「知识库里两份文档对同一件事说法相反，RAG 到底信谁？别跟我说『让模型自己判断』。」

**常见错误答法**：「信向量相似度最高的那份」「永远信发布时间最新的」「把两份都塞进 Prompt 让大模型自己判断」。三者都错：相似度只说明「像不像」，不说明「该不该信」，位置偏差、语气强弱、词法重合会把模型带偏；最新不等于适用（下月才生效的政策、仍适用于某群体的旧合同、仍有效的地区细则）；把知识治理责任甩给一次随机生成，不可解释、不可审核、不可回滚。

**正确答法（分层）**：① 先判**适用性**——按 `scope`（地区 / 套餐 / 产品版本）与 `valid_from` / `expires_at` 硬过滤，先回答「哪份对这个问题有效」，而不是「哪份赢」；② 再判**权威与版本**——查 `authority registry`（来源权威注册表，业务负责人维护，不是开发写全局常量）与生效期，把冲突关系建成图、按连通分量分簇、**每簇独立裁决**；③ 最后才是**生成契约**——裁决结果进 `EvidenceRecord`，`conflict_signal` 显式传给生成层，无法裁决时并列两版本 + 各自原文 + 转人工，**不发明 tie-breaking（平局裁决）规则**；只有合格候选之间才用相关性排序。

**本项目怎么落地 / 演示**：`knowledge/conflicts.py` 的 `connected_components()`（DFS 连通分量）与 `resolve_cluster()`（裁决顺序：业务权威度 → 生效期但需复核 → 并列转人工）已实现；`contracts.py` 的 `RESOLVE_AUTHORITY` / `RESOLVE_RECENCY` / `RESOLVE_ESCALATE` 是三种裁决结果。`knowledge/tests/test_knowledge.py` 有 `test_authority_wins_without_review`、`test_recency_wins_but_requires_review`、`test_equal_authority_escalates_without_inventing_tiebreak` 三个自建样本可现场跑给面试官看。**前端证据**：应做成「冲突簇裁决日志」卡片，逐条展示 `Conflict.key` / `kind` / `resolution` / `winner_id` / `needs_review` / `reason`；数据已由 `EvidenceRecord.resolution_log` 产出（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：算法层**已实现**（纯函数 + 离线单测用例，本文未实跑）；`authority registry` 注册表本身是治理流程、代码里只读 `DocMeta.authority`，**未做**；接入 `webapp/rag.py` / `routers_chat.py` 与前端展示**未做**。别把「有裁决函数」说成「线上问答已经会裁决」。

### Q2 「怎么防止 AI 拿过期政策回答用户？」

**常见错误答法**：「按 `updated_at` 倒序、只取最新一篇」「每次问答前重新爬一遍源站」「在提示词里写一句『请用最新资料』」。前两个要么把双时态混为一谈、要么成本不可控；最后一条把治理塞进提示词，模型不会执行、也无法验证。

**正确答法（分层）**：① **元数据先行**——`valid_from` / `expires_at` / `last_verified_at` / `scope` / `status` 各司其职，发布时间不等于生效时间，`valid_from` 在未来的文档要排除而不是判它过期；② **三层组合**——召回前硬过滤（已过期 / 未生效 / 不适用直接剔除）→ 半衰期时间衰减软信号（`decay = 0.5^(age/H)`）→ 按权威层复核；③ **链路闭环**——源侧删除作为一等事件、墓碑 + 软删传播到向量 / 检索缓存 / 答案缓存，杜绝 `ghost vector`（幽灵向量）；缓存做 `version-aware read`（版本感知读取），版本不符即拒用旧结果；`indexed_at` 只用来算「源变更 → 可检索」的传播延迟，**不能只看最新时间戳判断适用性**。

**本项目怎么落地 / 演示**：`knowledge/freshness.py` 的 `evaluate()`（适用性门控 + 三种 `expired_action`：`exclude` 排除 / `demote` 降权 / `warn` 只告警）、`rerank()`（保留 `original_rank` / `base_score` 供开关前后对照）、`summary()`（年龄分布 `buckets`、`stale_ratio`、`not_yet_effective`、`excluded`、`alert`）已实现；`age_days()` 优先取 `last_verified_at`。**前端证据**：应做成「新鲜度表格」，每行并列 `base_score`（原分）/ `decay` / `effective_score`（有效分）/ `action` / `reason`，并带一个「关掉新鲜度开关恢复原排序」的对照按钮；`test_rerank_keeps_before_after_evidence` 正是这个对照的样本（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：`FreshnessPolicy` 的 `half_life_days=90` / `blend=0.65` / `stale_after_days=365` / `demote_floor=0.10` / `stale_alert_ratio=0.10` 是**演示默认经验起始值，非本项目实测、非推荐配置**，代码里已写明「需用真实语料校准」；真实 CDC / 近实时增量索引、缓存失效链路、软删墓碑在 `docs/kb-capability-checklist.md` 中为「未做」，本包未实现，别讲成已上线。

### Q3 「引用的原文一字不差，为什么答案还是错的？」

**常见错误答法**：「那就是模型幻觉，换个更大的模型 / 调低温度就行」「说明检索没召回对，再加大 top_k」。这两句把三种完全不同的失败混成一个「效果不好」，面试官一追问就露馅。

**正确答法（分层）**：把正确性拆成**三个独立检查**，一个聚合分数会互相掩盖：① **相关性**（retrieval）——有没有找对资料，`recall@k` / 词法门禁命中；② **适用性与新鲜度**（applicability）——找对了资料但选错了版本 / 范围 / 生效期，比如引用了还有效的旧版条款却答给了新版套餐的用户；③ **忠实度 / groundedness**（generation）——引用确实支持这句话吗，段落级引用会掩盖断言级错误，必须把引用绑定到具体 `claim`（断言）。原文一样但答案错，绝大多数出在 ② 或 ③：要么用的是「对别人有效」的版本，要么那句结论根本不由所引片段支撑。

**本项目怎么落地 / 演示**：`knowledge/claims.py` 每条 `Claim` 都带 `text`（支持片段原文）与 `chunk_id`（分块标识），`Claim.key = 主体|属性|适用范围` 把不同 `scope` 的取值天然隔开；`knowledge/conflicts.py` 的 `EvidenceRecord.selected_claim_ids` / `excluded_claim_ids` 明确哪些断言进了主上下文、哪些被排除。**前端证据**：应做成「逐句 claim → 支持片段」对照，点一句答案就高亮它绑定的 `Claim.text` 与 `chunk_id`，并标出 `excluded_claim_ids`（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。注意现有 `chat_sources`（`webapp/db.py:84`）只到分块级引用，`observability.py` 的 `citation` 插件边界写明「未做答案逐句 grounding」。

**诚实边界**：`claim` 级抽取与绑定是**只设计 / 未做**（`knowledge/` 包能产出结构化断言，但 `routers_chat.py` 生成链未接、前端无逐句高亮）；RAGAS 的 `faithfulness` / `citation_support` 等质量指标本项目**未做**质量基线，别报数值。

### Q4 「冲突检测为什么不能只靠大模型？让模型读两段判一下矛盾不就行了？」

**常见错误答法**：「模型语义理解强，两段一对比就知道谁对」「用 LLM-as-judge 打分，分数高的赢」。问题在于：模型输出是**随机、不可复算**的，同一对片段换顺序 / 换措辞可能改口；它看不到权威等级、生效期、适用范围这些元数据；而且成本与延迟随语料规模线性增长。

**正确答法（分层）**：① **规则级优先**（确定性）——数字、日期、版本、状态这类结构化事实，用字段解析 + 归一化比模型可靠，每一步都能在前端展示、可单测、可复算；② **模型级只做候选识别**（告警线索）——自然语言表述复杂时可以让模型标「疑似矛盾」，但**模型输出不能自动裁决谁胜出**，裁决必须来自可解释、可审核、可回滚的规则与业务注册表；③ **成本与失败面**——离线入库阶段做一次检测并保存结果，不必每次查询重新发现；在线只做兜底。还要讲防误报：年份 `1900–2099`、裸小整数 `1–9`、单位 / 否定 / 例外必须原样保留，否则「2023 年报」和「2024 年报」会被当成冲突。

**本项目怎么落地 / 演示**：`knowledge/claims.py` 是纯标准库正则 + 归一化（`_norm_value()` 把年份无单位压到置信度 0.2、裸小整数 1–9 压到 0.5），`knowledge/conflicts.py` 的 `detect_conflicts()` 用 `min_confidence=0.5` 过滤低置信度断言、`topic_sim_min=0.30` 做主题门控（Jaccard：中文相邻二元组 + 英文整词，与 `webapp/rag.py` 词法门禁同构）。`test_year_and_small_integer_are_not_conflict_fuel`、`test_low_confidence_does_not_trigger_conflict`、`test_topic_similarity_gates_unrelated_topics` 是三条可直接跑的防误报样本。**前端证据**：应做成「规则命中轨迹」面板，逐条显示抽取到的断言、归一化值、置信度、被门控拦下的原因（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：这是**规则级**实现，**不具备 NLI（自然语言推理）模型能力**，也没有接任何 LLM 做矛盾分类；`topic_sim_min=0.30` 是经验起始值、需按本项目嵌入模型重新校准，别报成实测阈值（详见 §3）。

### Q5 「新鲜度和相关性怎么平衡？会不会把老文档全干掉？」

**常见错误答法**：「给新文档加分、旧文档减分，调个权重就行」「老文档直接过滤掉」。前者说不出量纲和保底，后者会把「仍适用于某群体的旧合同」「高度相关的经典手册」误杀。

**正确答法（分层）**：① **硬过滤在前**——已过期 / 未生效 / 不适用直接排除，这一步不参与打分；② **可复算的软衰减**——给出公式并说明每个参数：实现里 `decay = max(floor, 0.5 ^ (age_days / H))`，`effective = base_score × (1 − blend + blend × decay)`，其中 `base_score` 是原相关性分、`H` 是半衰期、`blend` 是新鲜度权重、`floor` 是衰减下限；③ **保底不归零**——`(1 − blend)` 是恒定的相关性保底项，`floor` 又给 `decay` 兜底，所以老文档的有效分有下界、不会被全干掉。用实现默认值手算：`blend=0.65`、`floor=0.10`，`base=1.0` 的极老文档有效分 = `1 × (0.35 + 0.65×0.10) = 0.415`，仍大于 0；`H=90` 时 90 天折半、180 天到 0.25，但被 `floor` 截在 0.10。`H` / `blend` 必须用评测集按内容类型校准，不猜。

**本项目怎么落地 / 演示**：`knowledge/freshness.py` 的 `decay_factor()` 与 `evaluate()` 就是上面两个公式，`contracts.py` 的 `FreshnessPolicy.as_dict()` 把 `H` / `blend` / `floor` 全部外置可展示；`test_decay_is_half_life_and_floored`（半衰期折半、9999 天不归零）、`test_stale_does_not_override_relevance_completely`（高度相关老文档仍保留）是可现场手算复核的样本。**前端证据**：应做成「原分 → 有效分」对照表 + 一个可拖动的 `H` / `blend` 滑块，让面试官亲眼看到排序翻转与老文档保底（**字段与策略表单均已实现；可拖动滑块仍只设计，见 §0.0**）。

**诚实边界**：`01-research.md` §2.2 另列了公开资料的两种写法（`0.5^(t/H)` 与 `e^(−t/halfLife)`，参数量纲差约 1.44 倍），本题按**本项目实现**的乘法形式作答，别把两套公式混着背；所有默认值都是演示起始值，非实测、非推荐值。

### Q6 「怎么评估冲突检测做得对不对？别只给我看几个跑对的例子。」

**常见错误答法**：「拿几十个问题问一遍，看答案对不对」「用整体准确率 / 一个 faithfulness 分数」。普通「一问一标准答案」的测试集测不出冲突能力，而且一个聚合分数会掩盖不同工程问题——答案忠实度高，也可能「忠实地引用了错的权威」。

**正确答法（分层）**：① **指标分开统计**——`correct_source_selection_rate`（正确来源选择率）、`effective_date_accuracy`（生效日期 / 有效期判断准确率）、`conflict_detection_rate`（检出率）与误报率、`citation_support`（每条关键结论是否被所引证据支撑）、`unnecessary_abstention`（无谓拒答率，且要与「缺失证据」「未决冲突」「权限拒绝」「无依据生成」**分开**统计）；② **主动注入冲突样本**——旧版本、地区细则、低权威干扰文档、明天才生效的政策、源更新后缓存仍存活；③ **必测鲁棒性用例**——**证据重排不应翻转结论**（防位置偏差）、**重复副本不应改变结果**（防「票数多」胜出）、回归用例连同当时的来源快照一起保存，否则拿今天可变索引重放会掩盖原始故障。

**本项目怎么落地 / 演示**：`knowledge/tests/test_knowledge.py` 已有可当场跑的定向样本：`test_same_value_is_corroboration_not_conflict`（同值互相印证不误报）、`test_different_scope_is_not_conflict`（不同地区是适用性差异、不是冲突）、`test_numeric_conflict_detected`、`test_authority_wins_without_review`、`test_recency_wins_but_requires_review`、`test_evidence_record_keeps_both_sides_and_logs_decision`（被淘汰断言留痕而非删除）。**前端证据**：应做成「评测用例看板」，逐条列出用例名、输入的两份断言、期望的 `resolution` / `winner_id` / `warnings`（**离线单测用例已实现；指标统计与看板只设计 / 未做**）。

**诚实边界**：`01-research.md` §3.7 只**定义**了上述指标口径（只设计），本仓库没有实现这些率的统计脚本；「证据重排不翻转结论」「重复副本不改变结果」两条必测用例**当前单测未覆盖**（未做），不能说已测；`eval/` 冻结占位结果 `comparable=false` / `NOT_COMPARABLE` 不参与闸门，本项目**没有**质量基线（未做）。

### Q7 「为什么不直接按最新时间戳判断该用哪份文档？」

**常见错误答法**：「`ORDER BY updated_at DESC LIMIT 1` 就行」。这是把**事务时间**（何时录入 / 何时改过）当成**有效时间**（事实何时为真），即 `bi-temporal`（双时态）混淆。

**正确答法（分层）**：① 举反例说明——旧合同仍适用于某群体、下月才 `valid_from` 的未来政策、今天发的总部通用通知盖不掉仍在有效期内的地区细则、v2 套餐的「500 次/分钟」不适用于仍按 v1 套餐的用户、更新的草稿比正式制度新但权威度低；② 讲清字段分工——`published_at`（发布）≠ `valid_from`（生效）≠ `indexed_at`（索引）≠ `last_verified_at`（人工核实），`updated_at` 只回答「何时改过」；③ 结论——适用性由 `scope` + 生效期 + 权威注册表决定，时间戳只在「权威性相当、生效期不同」时作为带复核的先验。

**本项目怎么落地 / 演示**：`knowledge/contracts.py` 的 `DocMeta` 把 `valid_from` / `expires_at` / `last_verified_at` / `indexed_at` / `authority` / `scope` 分成独立字段；`freshness.py` 的 `evaluate()` 对 `valid_from > now` 返回 `not_yet_effective=True, excluded=True` 且 `reason` 写明「不是判它过期」，`test_not_yet_effective_is_excluded_not_judged_stale` 就是这个样本。`resolve_cluster()` 只在权威度相同时才用 `valid_from` 且强制 `needs_review=True`。**前端证据**：应做成「文档元数据卡」，把四个时间字段与 `scope` 并排列出，未来生效的那份标「未生效·排除」而非「过期」（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：`published_at` / `superseded_by`（废止链）/ 审批发布工作流在本仓库**未做**；`authority` 目前是 `DocMeta` 里的整数、由调用方传入，注册表（谁维护、覆盖关系、审批流）属治理流程、**未做**。

### Q8 「幽灵向量是什么？源文档都删了，为什么答案还在？」

**常见错误答法**：「删数据库记录就删干净了」「每晚全量重建一次索引」。前者忘了向量库 / 各级缓存 / 派生摘要是**独立副本**，后者把删除当成批处理副产品、期间窗口内照样返回已不存在的内容。

**正确答法（分层）**：① 定义——`ghost vector`（幽灵向量）是源文档下架后仍残留在向量索引里的孤立向量，答案是「自信地指向已不存在的事物」；② 处置——把**删除作为一等事件**，按 `(source_id, version)` 传播到词法索引、向量库、rerank 特征缓存、答案缓存；用 `tombstone`（墓碑）+ `soft delete`（软删）保留审计与回放能力，并做周期性差集对账兜底最终一致性；③ 缓存——`version-aware read`（缓存 key 携带文档版本与配置快照，读取时比对，不符即拒用），别指望「改提示词」能修好一个忽略版本的缓存 key。

**本项目怎么落地 / 演示**：本项目 `Document` / `Chunk` 继承 `SoftDeleteMixin`、`chunk_id` 作幂等键、向量视为可重建派生数据（`docs/kb-capability-checklist.md` G4 有 `reconcile_vectors` 对账口径），是软删与对账的**已具备基础**；`02-candidates.md` 的 C13 把「答案缓存版本感知失效」列为**只写设计**。**前端证据**：应做成「删除传播轨迹」时间线，显示源侧删除 → 各级缓存失效的时点与留痕（**只设计 / 未做**）。

**诚实边界**：真实删除事件、跨缓存失效链路、答案级缓存、`delete_propagation_delay` / `cache_invalidation_latency` 埋点在本仓库**未做**；`knowledge/` 包不含索引 / 缓存实现，别把「有软删基类」说成「删除传播已闭环」。

### Q9 「冲突检测为什么要做到 claim 级？整篇文档比一下不行吗？」

**常见错误答法**：「两篇文档算个相似度，超过阈值就判冲突」。两本长手册可能 99% 一致、只在一个操作限值上打架，文档级比较太粗，且会把「互补信息」误判成二选一。

**正确答法（分层）**：① 粒度——抽取 `claim`（断言）并比较「实体 / 属性 / 值 / 条件 / 适用期」，为每个断言保留支持片段原文，引用才能回溯；② 归一化的纪律——单位、否定、例外、适用范围必须原样保留，把句子压成短摘要常会删掉「正是解释差异的那个条件」；③ 分类不一刀切——`complementary`（互补）要合成、`conflicting opinions`（观点分歧）要中立并列、`freshness`（过时）优先最新、`misinformation`（错误信息）要升级并排除低可信来源，只有 `no conflict` 才直接作答。

**本项目怎么落地 / 演示**：`knowledge/claims.py` 的 `extract_claims()` 逐句扫赋值句与表格行（规则不命中就跳过、不硬凑），产出带 `text` / `chunk_id` / `polarity` / `unit` / `scope` 的 `Claim`；`Claim.key` 把不同 `scope` 隔开，`test_different_scope_is_not_conflict` 与 `test_same_value_is_corroboration_not_conflict` 分别验证「适用性差异不误报」「同值不误报」；`test_end_to_end_from_text_to_evidence` 从两段文本跑到证据记录。**前端证据**：应做成「断言列表」，每条显示 `subject|attribute|scope` / `value_norm` / `polarity` / 支持片段，冲突的标 `kind`（numeric / negation / freshness / authority / unresolved）（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：`extract_claims()` 是**规则级正则抽取**，不是 NLI / 语义解析，复杂自然语言的断言抽取能力**未做**；CONFLICTS 五类分类法只在本包里落了 `kind` 的近似判定，**未做**完整的观点 / 错误信息分类与升级流程。

### Q10 「数值冲突规则怎么防误报？我两份年报会不会被判成打架？」

**常见错误答法**：「两个数字不一样就报冲突」。那「2023 年报」和「2024 年报」、「第 1 步」和「第 2 步」会全被误报，告警一多就没人看了。

**正确答法（分层）**：① 低置信度过滤——年份 `1900–2099` 且无单位、裸小整数 `1–9` 且无单位，不单独触发冲突；② 门控——只在 `Claim.key` 相同（同主体同属性同适用范围）时比对，跨 key 的否定不对称检测再用 `topic similarity`（主题相似度）门控，避免「退款期限 10 天」和「接口超时 30 秒」被凑成一对；③ 信号与原文——归一化只用于比对、不改原文，单位 / 否定 / 方向词不对称作为辅助信号，模型输出只作告警线索不自动裁决。

**本项目怎么落地 / 演示**：`knowledge/claims.py` 的 `_norm_value()` 把无单位年份置信度压到 `0.2`、裸小整数压到 `0.5`；`detect_conflicts()` 用 `min_confidence=0.5` 过滤，并用 `topic_similarity()`（中文相邻二元组 + 英文整词 Jaccard）做 `topic_sim_min=0.30` 门控；`test_year_and_small_integer_are_not_conflict_fuel`、`test_topic_similarity_gates_unrelated_topics` 可直接跑。**前端证据**：应做成「被门控拦下」的对照列表，显示哪些候选断言因置信度 / 主题门控没进冲突簇（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：`topic_sim_min=0.30` 是**经验起始值、非本项目实测**，换嵌入模型 / 换语料必须重新校准；`01-research.md` §3.3 提到的 `topic_sim >= 0.68` 是公开实现针对特定模型的实验值，两者不是同一个阈值，别混着报。

### Q11 「两份同等权威、同一适用范围，实在裁不了怎么办？」

**常见错误答法**：「让模型按语气更肯定的那份来」「取个平均值 / 随机选一个」「干脆整题拒答」。前者发明了平局规则、不可复现；后者把能答的子问题也一起掐掉，是无谓拒答。

**正确答法（分层）**：① 诚实并列——明确写「当前知识库存在两个不同版本，A 规定 10 天、B 规定 15 天，暂无法确认哪个有效」，附两份原文与生效日，请用户补充地区 / 版本，或转知识负责人审核；**不发明 tie-breaking**；② 只答被支持的部分——证据只支持部分结论就只答那部分，未决冲突不必导致整题拒答；③ 拒答不越权——解释拒答时只披露用户**有权访问**的证据，为解释而暴露受限文档的标题或内容是越权。

**本项目怎么落地 / 演示**：`knowledge/contracts.py` 的 `RESOLVE_ESCALATE = "present_both_and_escalate"`，`resolve_cluster()` 在权威度相同且无可裁决生效期时返回 `winner_id=None` / `severity="block"` / `needs_review=True`，`build_evidence_record()` 往 `warnings` 写「存在无法裁决的冲突：并列呈现来源与原文，转知识负责人审核」；`test_equal_authority_escalates_without_inventing_tiebreak` 断言 `conflict_signal=True` 且没有 `winner_id`。**前端证据**：应做成「EvidenceRecord.warnings 面板」，把三条告警（检测到冲突必须显式披露 / 无法裁决并列转人工 / `needs_review` 需人工确认）与 `resolution_log` 一起展示（**字段与前端展示均已实现：知识治理视图，见 §0.0**）。

**诚实边界**：`warnings` 只是**结构化字段**，生成层是否照做**未做**（`routers_chat.py` 未接 `EvidenceRecord`）；权限裁剪的拒答话术在 `webapp/rag.py` 有「检索为空」的诚实降级，但「拒答解释按身份裁剪」的完整契约**只设计**。别把「有 warnings 字段」说成「回答已经会并列两版本」。

### Q12 「你把新鲜度 / 冲突检测接进现有检索链路，怎么保证不打乱已有口径？」

**常见错误答法**：「在 `rag.py` 里加个 if 分支就行」「把 90:10 预算闸门挪一点给冲突检测用」。前者会破坏词法门禁「不相关不硬凑」与「rerank 不可用优雅跳过并留痕」的既有语义，后者把**不同量纲**的东西合并成一个「预算」，口径直接被打乱。

**正确答法（分层）**：① 分层落点——新鲜度硬过滤放在**词法门禁之后、向量排序之前**（与「无词法重叠 → 空列表 → 诚实拒答」同构），冲突检测放在检索与生成之间的**治理阶段**，产出 `EvidenceRecord` 再交给生成层；② 契约零改动——`parsing/` 的 `ChunkSpec(page_no, section_path, block_type, confidence, source_level, parent_seq)` 下游契约不动，新增元数据优先落 `Document` 已预留的 `expire_at` / `indexed_at` / `current_version` / `attributes` JSONB，不改解析侧字段；③ 量纲隔离——`90:10`（`VISION_ESCALATION_RATIO = 0.10`）是**解析阶段「最贵链路最多占 10% 页」的熔断线**，`H` / `blend` / `stale_alert_ratio` / `topic_sim_min` 属检索与入库治理层，不复用同一常量、不合并成一个预算；若未来模型级识别要调 LLM，另立闸门；`eval/` / `demo/` / `evalbench/` 冻结区零改动，占位结果 `NOT_COMPARABLE` 不参与闸门。

**本项目怎么落地 / 演示**：`01-research.md` §5.9 逐条写了与 `parsing/` 三级策略、90:10 预算闸门、冻结区共存的六条边界；`02-candidates.md` §5 写了候选功能应落在解析层 / `webapp/rag.py` / `routers_chat.py` / 新建治理层的分层归属；`knowledge/` 包目前是**独立纯函数库**，正是「先做可测算法、再谈接入」的落法。**前端证据**：应做成「口径不变量」自检卡，列出三维限流 `("user","endpoint","model")`、90:10 熔断线、C33 的 10 具名 view + 6 具名 loader 存在性检查、冻结区零 diff 四项，改动前后各刷一次（**既有口径已实现；本期自检卡只设计 / 未做**）。

**诚实边界**：`knowledge/` 包**尚未接入** `webapp/rag.py`（本次 Grep 零命中），所以「接进链路」目前是**只设计 / 未做**；三维限流、90:10 预算闸门、C33 具名检查、冻结区零改动均为项目既有口径，本问答资料**逐字沿用、不重新定义**。

## 2. 面试官追问链

### 2.1 四层追问模型

面试官不会停在「怎么做的」，通常沿四层往下压：**① 怎么做的**（要能讲到具体文件 / 字段 / 公式，不背流程图）→ **② 为什么不这么做**（要能说出被否掉的方案与代价，比如为什么不用相似度、为什么不让模型自动裁决）→ **③ 怎么证明**（要能指到自建样本、前端证据、可复算公式，而不是「感觉效果不错」）→ **④ 生产会遇到什么**（要能说出真实 CDC、注册表维护、缓存传播、回归基线这些骨架版没做、上线才补的东西，并诚实说明本项目是演示切片）。

### 2.2 逐题应对（每题 1–2 句，按最容易被压的一层给）

| 题 | 最容易被追问的层 | 1–2 句应对 |
|---|---|---|
| Q1 | ② 为什么不这么做 | 「相似度不含权威度与生效期，位置偏差和词法重合会让它挑错来源；所以我把裁决放在检索与生成之间，用规则 + 注册表，而不是让模型自己和解。」 |
| Q2 | ③ 怎么证明 | 「指新鲜度表格的 `base_score` → `effective_score` 对照，再关掉开关恢复原排序；`test_rerank_keeps_before_after_evidence` 就是这个对照的可跑样本。」 |
| Q3 | ① 怎么做的 | 「我把正确性拆成相关性、适用性与新鲜度、忠实度三层分别检查；原文一样还答错，多半是选错版本或引用没绑定到具体断言。」 |
| Q4 | ② 为什么不这么做 | 「规则级可单测、可复算、零模型流量；模型输出只当告警线索，因为换顺序可能改口、且看不到元数据，不能自动裁决。」 |
| Q5 | ③ 怎么证明 | 「公式 `effective = base × (1 − blend + blend × decay)` 可以直接手算；`blend=0.65`、`floor=0.10` 时老文档有效分有 `0.35` 的保底项，不会归零，单测里也断言了高度相关老文档仍保留。」 |
| Q6 | ③ 怎么证明 | 「五个指标分开统计，并注入旧版本 / 地区细则 / 低权威干扰样本；另外必测证据重排不翻转结论、重复副本不改变结果——后两条我如实说明当前还没覆盖。」 |
| Q7 | ② 为什么不这么做 | 「`updated_at` 是事务时间，回答不了『对谁、在何时为真』；未来生效政策、仍适用的旧合同、地区细则都会让『最新赢』出错。」 |
| Q8 | ④ 生产会遇到什么 | 「生产要按 `(source_id, version)` 把删除传播到向量库与各级缓存，用墓碑 + 软删 + 周期对账兜底；本项目目前只有软删基类，这条链路还没做。」 |
| Q9 | ① 怎么做的 | 「按 `主体\|属性\|适用范围` 抽断言、保留支持片段原文与 `chunk_id`；不同地区取值不同是适用性差异，不进同一个冲突簇。」 |
| Q10 | ③ 怎么证明 | 「两个反例样本可跑：无单位年份置信度压到 0.2、裸小整数压到 0.5 都不触发冲突；主题门控把『退款期限』和『接口超时』拦开。」 |
| Q11 | ② 为什么不这么做 | 「发明平局规则不可复现、不可审核；所以 `winner_id=None` 并列两版本 + `warnings` 提示转人工，只答证据支持的那部分子问题。」 |
| Q12 | ④ 生产会遇到什么 | 「真上线要处理注册表由谁维护、真实 CDC 近实时增量、跨缓存失效与 CI 质量闸门；本项目是 SQLite + 进程内队列的演示切片，这些我都如实标注未做。」 |

## 3. 别越线的话术

### 3.1 四条红线（逐条给「错误说法 → 正确说法」）

1. **不要把双图视觉实测说成质量基线**——❌「我们做过双图视觉实测，检索质量有基线了」；✅「`docs/parsing/` 里的视觉 / OCR 实测是**解析能力**的验证，与 RAG 检索 / 生成质量基线是两件事；本项目 `eval/` 占位结果 `comparable=false`，**没有**冻结质量阈值，也没做质量基线。」
2. **不要把规则级冲突检测说成具备 NLI 模型能力**——❌「我们用 NLI 模型判断两段是否矛盾并自动裁决」；✅「`knowledge/claims.py` 是正则 + 归一化的**规则级**抽取，`kind` 是启发式分类；没有 NLI、没有 LLM 矛盾判定，模型输出即便将来接入也只作告警线索、不自动裁决。」
3. **不要把演示切片的 SQLite / 进程内队列说成生产实现**——❌「我们用 Redis 令牌桶 + Celery 做限流和异步索引」；✅「`webapp/` 是零依赖演示切片：SQLite 替 PostgreSQL、进程内滑动窗口替 Redis 限流、进程内信号量替 Celery；`backend/` 生产骨架是另一套运行时，两者不可混说。」
4. **不要说已做真实压测或质量基线冻结**——❌「压测 p95 达标、质量基线已冻结」；✅「本项目未做真实压测、未接监控告警 SLO、未做账单校准、未冻结质量基线；`demo/scenario.json` 明确『质量阈值未冻结、comparable=false』，所有默认参数都是演示起始值，需用真实语料校准。」

### 3.2 两条补充红线（本次实查新增，同样别越线）

5. **不要把 `knowledge/` 算法包说成已接入问答链路**——❌「用户现在提问就会走冲突裁决、前端能看到裁决日志」；✅「算法包已实现且有离线单测，但 `webapp/` 零引用，接入主链路与前端展示**未做**；我演示的是纯函数跑出的结构化结果，不是线上问答行为。」
6. **不要把演示默认阈值说成实测或推荐值**——❌「半衰期 90 天、blend 0.65 是调出来的最优值」；✅「`FreshnessPolicy` 代码注释写明『演示默认经验起始值，非压测结论，需用真实语料校准』，`topic_sim_min=0.30` 同理；这些数字只用来让公式可复算，不代表效果结论。」

### 3.3 一句安全收尾模板

「以上是**算法层已实现、链路与前端只设计 / 未做**的演示切片；生产要补注册表治理、真实 CDC 增量、跨缓存失效与 CI 质量闸门，这些我都没有声称已做。」——凡被追问到边界，用这句收，不会失分。

## 4. 与既有文档的口径边界及检索来源

### 4.1 口径边界（不重复、不覆盖）

- `01-research.md`：调研报告，定义术语、公开实践、落地建议三档与 20 条来源；本文沿用其术语与裁决顺序，但**不重复调研结论**，只把结论改写成面试话术。
- `02-candidates.md`：候选功能清单与取舍裁决；本文只引用其中 C13（缓存版本感知失效）与 §5（分层归属）两处，不重写候选条目。
- 三态口径：本文用项目主口径「已实现 / 只设计 / 未做」，与 `01-research.md` §0.1 的三档、`docs/kb-capability-checklist.md` 的四档**各管各的，不换算、不覆盖**；`docs/parsing/01–07` 的三态原样保留。
- 既有不变量逐字沿用、不在本文重新定义：三维限流 `("user","endpoint","model")`、90:10 预算闸门、C33 的 10 具名 view + 6 具名 loader 存在性检查、冻结区 `eval/` / `demo/` / `evalbench/` 零改动、`ChunkSpec` 下游契约。
- 本文只写文档、不改任何源码；不含价格、接口地址、鉴权、配额、本项目未实测的性能数字；出现的数值仅为代码里的演示默认值与可复算公式的手算示例，均已标注非实测。

### 4.2 检索来源（本人 WebSearch 命中，检索日期均为 2026-11-14）

以下为编制 `02-candidates.md` 时本人 4 次 WebSearch 命中、与面试问答直接相关的可核实 URL；`01-research.md` §7 另列同日检索的 20 条来源（含中文面试原题 S14–S16），此处不重复列、不冒充本人检索。所有外部结论只作观点线索，不作本项目结论或验收依据。

- https://quidproquo.cc/posts/ai/2026-08-20-ai-engineer-interview-llm-application-en
- https://github.com/youngyangyang04/llm-master/blob/main/docs/interview/llm/rag_interview.md
- https://www.assessarc.com/blog/top-50-rag-interview-questions-and-answers-for-experienced-professionals-2026-guide
- https://www.cioage.com/article/855755.html
- https://paftesting.com/blog/design-rag-around-evidence-lifecycles-not-vector-search
- https://aivetech.in/blog/why-naive-rag-fails
- https://akantik.com/blog/building-production-ready-rag-pipelines-for-enterprise-knowledge-bases-in-2026
- https://blog.habsi.net/?p=2497
- https://nkktech.com/blog/secure-rag-implementation-corporate-data-systems
- https://www.deepnomind.com/blog/基于 Qwen 打造低幻觉千万级文档 RAG 流水线

> 一句话收尾：面试答这 12 题的胜负手不是「背出更多名词」，而是**每句都能指到一个字段、一个可跑样本或一条前端证据，并且清楚说出哪些还没做**。这正是本项目「边界诚实、不静默、可回滚」的取向。
