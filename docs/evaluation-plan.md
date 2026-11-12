# 说明书分块问答系统 · 评估与测试方案

版本：`EVAL-PLAN@0.1.0` ｜ 状态：占位可运行 / 真实接入待申请
适用对象：**待测试的说明书分块问答系统（SUT）**，以及接入后将使用的
**DeepSeek（生成）** 与 **阿里云百炼 embedding 服务（向量）**。

---

## 0. 边界声明与不假定条款

本方案**不假定、不编造**任何真实 API 的：接口地址、鉴权方式、请求结构、响应结构、
配额数值、错误码、模型版本、具体模型名称、参数名、计费口径、超时语义。
上述信息一律以占位符 `__TBC__` 表示，并在 `OBJ-ADAPTER-CONTRACTS` 中登记为「待确认字段」。
`eval/` 目录下所有文件内容均为合成或占位，不含任何真实服务细节。

| 禁止行为 | 替代做法 |
|---|---|
| 写出某个真实 endpoint / 模型名 / 错误码 | 写 `__TBC__` + `confirmation_source` + `blocking` |
| 猜配额、猜维数、猜参数名 | 登记为待确认项，接入前不参与计算 |
| 用占位结果推导质量阈值或基线 | `composite_score=null`、阈值 `null`、基线 `TBC` |
| 把内部占位错误码当供应商错误码 | 明确标注「内部占位分类」，真实映射另建版本 |
| 在日志/结果里写凭证、原始提示词、完整回答 | 只写哈希、摘要、脱敏预览 |

**两个阶段**

| 阶段 | 档位 | 网络 | 目标 | 当前状态 |
|---|---|---|---|---|
| 一 | `offline_placeholder` | 禁止 | 链路连通、记录格式、故障处理、确定性复现 | **已可运行**（26 用例 / 14 自测通过） |
| 二 | `real_api` | 审批后允许 | 真实质量评估、基线、阈值冻结、回归 | 被契约门禁阻断（26 项 blocking 待确认） |

另有 `real_api_pending` 档位：只做契约就绪检查，不发起任何外部请求。

**交付物**：`eval/`（可运行占位链路）、14 个版本化对象、26 条首批用例、6 份合成 fixtures、
统一结果记录格式、对比/回归工具、本方案文档。

---

## 1. 结构化方案

### 1.1 评测目标

| ID | 目标 | 可判定条件 |
|---|---|---|
| G1 | 六链路端到端可运行、每步有记录 | 每用例 6 个 step 记录完整、顺序一致 |
| G2 | 全链路可复现 | 同 seed + 同配置哈希 + 同数据集哈希 → 逐用例输出哈希一致 |
| G3 | 全链路可追踪 | 逐用例 / 逐链路 / 逐对象版本三层关联键齐备 |
| G4 | 异常与边界可排查 | 每类故障有预期状态、错误码、上下文与定位路径 |
| G5 | 变更可维护 | 所有可版本化部分独立成对象，变更有原因/时间/前后差异/回滚基线 |
| G6 | 接入后能比较 | 分块/检索/生成各轴的对比实验与回归闸门口径固定 |
| G7 | 接入后能验收 | 两段式阈值：链路健康立即可判定；质量阈值基线后冻结 |

### 1.2 链路边界

```
[fixture / 真实语料] → ① 分块 → ② embedding → ③ 检索 → ④ 提示词组装
                     → ⑤ 回答生成 → ⑥ 评分 → [统一结果记录 + manifest + 观测]
```
- ①③ 在离线档位由评测框架实现；接入后需确认是复用 SUT 分块/检索，还是作为对照基线（`__TBC__`）。
- ②⑤ 在离线档位为占位适配器；接入后分别对应百炼 embedding 与 DeepSeek。
- ⑥ 离线档位为规则检查；模型裁判模板已占位但 `enabled=false`。

### 1.3 评测原则

1. **不可比较原则**：占位结果 `comparable=false`，禁止与真实 API 结果横向比较、禁止写入质量基线。
2. **不静默原则**：评分不可用、超时、异常返回不得记 0 分、不得丢弃用例、不得当作通过。
3. **先链路后质量**：链路健康闸门不过，不进入质量评估。
4. **版本即真相**：run 记录里的 `object_versions` 与 `config_snapshot_hash` 是唯一判定依据。
5. **最小日志**：只记哈希与脱敏摘要，不记原始提示词、凭证、完整回答。

### 1.4 真实档位准入条件（全部满足才允许解除 `--profile real_api` 阻断）

1. `OBJ-ADAPTER-CONTRACTS` 中所有 `blocking=true` 字段由用户确认并填入（当前 26 项）；
2. 凭据通过环境变量/密钥管理注入，仓库中不得出现；
3. `OBJ-BASELINE` 完成首轮真实 API 基线标定；
4. `OBJ-THRESHOLDS` 中质量阈值由基线建议值评审冻结；
5. 成本与配额上限已确认（值 `__TBC__`）；
6. 变更评审通过，`OBJ-RUN-PROFILE.profiles.real_api.enabled` 置为 `true` 并新建版本。

---

## 2. 占位配置清单

### 2.1 运行档位（`OBJ-RUN-PROFILE`）

| 档位 | 网络 | 适配器 | 用途 | 启用 |
|---|---|---|---|---|
| `offline_placeholder` | forbidden | `stub_feature_hashing` / `local_cosine_plus_lexical_rrf` / `extractive_placeholder` / `rule_checks_only` | 链路、格式、故障、复现 | ✅ |
| `real_api_pending` | forbidden | 仅契约检查 | 列出待确认项 | ✅ |
| `real_api` | 审批后允许 | `__TBC__` | 真实质量评估 | ❌ |

内部运行预算（**与任何供应商配额无关**）：`max_cases_per_run=500`、`per_case_walltime_ms=5000`。

### 2.2 占位配置项总表

| 配置键 | 占位值 | 类型 | 说明 | TBC | 所属对象 | 影响链路 |
|---|---|---|---|---|---|---|
| `active_profile` | `offline_placeholder` | str | 默认档位 | 否 | RUN-PROFILE | 全链路 |
| `seed` | `20261111` | int | 确定性种子 | 否 | EVAL-SPEC | 全链路 |
| `chunk.strategy` | `paragraph_window_with_heading_path` | str | 分块策略 | 否 | CHUNK-PARAMS | ① |
| `chunk.max_chars` | `400` | int | 窗口字符上限 | 否 | CHUNK-PARAMS | ① |
| `chunk.overlap_chars` | `60` | int | 重叠字符 | 否 | CHUNK-PARAMS | ① |
| `chunk.max_chunks_per_document` | `2000` | int | 分块数硬上限 | 否 | CHUNK-PARAMS | ① |
| `embedding.provider_label` | `阿里云百炼 embedding 服务（占位）` | str | 服务标签 | 是 | EMBEDDING-CFG | ② |
| `embedding.service_endpoint` | `__TBC__` | str | 接口地址 | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.auth_scheme` | `__TBC__` | str | 鉴权方式 | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.credential_ref` | `__TBC__` | str | 凭据引用（禁入库） | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.model_id` / `model_version` | `__TBC__` | str | 模型标识/版本 | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.output_dimension` | `__TBC__` | int | 输出维度 | **是** | ADAPTER-CONTRACTS | ②③ |
| `embedding.request_schema` / `response_schema` | `__TBC__` | obj | 请求/响应结构 | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.batch_support` / `max_batch_size` / `max_input_length` | `__TBC__` | - | 批量与长度约束 | **是** | ADAPTER-CONTRACTS | ② |
| `embedding.normalization` / `similarity_metric` | `__TBC__` | str | 归一化与度量 | **是** | ADAPTER-CONTRACTS | ②③ |
| `embedding.quota` / `error_code_mapping` / `retry_policy` | `__TBC__` | obj | 配额/错误码/重试 | **是** | ADAPTER-CONTRACTS | ② 异常 |
| `embedding.offline.dimension` | `64` | int | 占位向量维度 | 否 | EMBEDDING-CFG | ②③ |
| `embedding.offline.semantic_capability` | `false` | bool | 明确无语义能力 | 否 | EMBEDDING-CFG | ②③ |
| `llm.provider_label` | `DeepSeek（占位）` | str | 服务标签 | 是 | LLM-MODEL-CFG | ⑤ |
| `llm.model_id` / `model_version` / `context_window` | `__TBC__` | - | 模型标识/版本/窗口 | **是** | ADAPTER-CONTRACTS | ④⑤ |
| `generation.parameters.*` | `__TBC__` | obj | 温度、长度、超时、流式等参数名与取值 | **是** | GENERATION-CFG | ⑤ |
| `generation.offline_placeholder_values` | `temperature_equivalent=0.0` 等 | obj | **框架内部**确定性控制量 | 否 | GENERATION-CFG | ⑤ |
| `retrieval.mode` | `hybrid_rrf_placeholder` | str | 混合召回 | 否 | RETRIEVAL-CFG | ③ |
| `retrieval.top_k` | `8` | int | 召回条数 | 否 | RETRIEVAL-CFG | ③ |
| `retrieval.rrf_k` | `60` | int | RRF 常数 | 否 | RETRIEVAL-CFG | ③ |
| `retrieval.filters.enabled` | `false` | bool | 过滤语义待 SUT 确认 | 是 | RETRIEVAL-CFG | ③ |
| `prompt.template_id` | `rag_answer_zh_v1` | str | 提示词模板 | 否 | PROMPT-RAG-ANSWER | ④ |
| `prompt.max_context_items` | `8` | int | 上下文片段数 | 否 | PROMPT-RAG-ANSWER | ④ |
| `prompt.max_prompt_chars_offline` | `2000` | int | 溢出记录阈值（内部） | 否 | PROMPT-RAG-ANSWER | ④ |
| `scoring.composite_formula` | `__TBC__` | - | 综合分公式 | **是** | SCORING-RULES | ⑥ |
| `scoring.dimensions[*].weight` | `__TBC__` | - | 维度权重 | **是** | SCORING-RULES | ⑥ |
| `judge.enabled` / `judge_model_id` | `false` / `__TBC__` | - | 模型裁判（可选） | **是** | PROMPT-SCORING | ⑥ |
| `thresholds.stage_1` | 7 项具体数值 | obj | 链路健康，**现在可判定** | 否 | THRESHOLDS | 闸门 |
| `thresholds.stage_2` | 9 项 `null` | obj | 质量阈值，**未冻结** | **是** | THRESHOLDS | 闸门 |
| `baseline.quality_baseline_status` | `TBC__requires_first_real_api_run` | str | 质量基线未建立 | **是** | BASELINE | 回归 |

### 2.3 预留接入契约字段（`OBJ-ADAPTER-CONTRACTS`，共 35 槽位）

| 组件 | 槽位数 | blocking | optional | 关键槽位（全部 `__TBC__`） |
|---|---|---|---|---|
| `embedding_service` | 15 | 13 | 2 | `service_endpoint`、`auth_scheme`、`credential_ref`、`model_id`、`output_dimension`、`request_schema`、`response_schema`、`batch_support_and_limits`、`max_input_length_semantics`、`vector_normalization_and_metric`、`quota_qps_tpm_daily`、`error_code_mapping`、`retry_and_idempotency_semantics`；可选 `request_id_field_for_observability`、`usage_and_cost_fields` |
| `generation_service` | 12 | 10 | 2 | `service_endpoint`、`auth_scheme`、`credential_ref`、`model_id`、`request_schema`、`response_schema`、`supported_generation_parameters`、`context_window_and_truncation_semantics`、`quota_qps_tpm_daily`、`error_code_mapping`；可选 `stream_event_schema`、`usage_and_cost_fields` |
| `scoring_service` | 4 | 0 | 4 | `judge_mode`、`judge_model_id_if_applicable`、`judge_request_response_schema`、`judge_quota_and_cost` |
| `sut_retrieval` | 4 | 3 | 1 | `entrypoint_and_auth`、`request_response_schema`、`chunk_id_and_citation_format`；可选 `filter_and_permission_semantics` |
| **合计** | **35** | **26** | **9** | `ready_for_real_api=false` |

> `python eval/run_eval.py --check-contracts` 输出完整 35 项及 `confirmation_source`。

### 2.4 待确认项清单（按阻塞范围）

| 待确认类 | 确认来源 | 阻塞链路 | 阻塞项数 |
|---|---|---|---|
| 服务地址 / 鉴权 / 凭据引用 | 开通凭证、官方文档 | ②⑤ | 6 |
| 模型标识、版本、维度、上下文窗口 | 用户确认（不预设） | ②③④⑤ | 5 |
| 请求/响应结构、参数名与取值范围 | 官方接口契约 | ②④⑤ | 7 |
| 批量上限、长度语义、归一化、流式事件 | 官方接口契约 | ②③④⑤ | 5 |
| 配额、错误码、重试/幂等语义 | 开通配额、官方错误码表 | 异常场景 / 运行预算 | 6 |
| SUT 入口、响应结构、引文与 chunk_id 格式 | 待测系统交付方 | ③⑥ | 3 |
| 成本字段、模型裁判、权重与阈值口径 | 评审 + 首轮基线 | ⑥ 闸门 | TBC |

### 2.5 字段说明（关键语义）

- `__TBC__`：占位值，**不是默认值**，不得当作可运行配置；`blocking=true` 未消解时 `real_api` 档位快速失败。
- `offline_placeholder_values`：仅框架内部确定性控制量，**不代表真实服务支持该参数**。
- `effective_scope`：对象的生效范围（`profiles` / `chains` / `suites` / `environments`），决定逐链路对象关联。
- `config_snapshot_hash`：所有对象 `{version, status, effective_scope, depends_on, content}` 的规范化哈希，是复现主键。
- `code_hash`：harness 自身全部 `.py` 文件的规范化哈希（排除 results/tests/缓存），纳入 `run_id` 与记录；改了评测代码不会静默复用旧 run_id 覆盖历史。
- `dataset_hash`：`cases_v0.1.0.jsonl` 与 `fixtures_v0.1.0.json` 文件哈希的组合，运行时计算，禁止手工维护。
- `comparable=false`：该结果不可用于质量结论或与真实 API 结果比较。

---

## 3. 版本化对象管理

### 3.1 对象总表（14 个，均 `@0.1.0`）

| 对象标识 | 类型 | 版本 | 生效范围（档位 / 链路 / 套件 / 环境） | 依赖 | 状态 |
|---|---|---|---|---|---|
| `OBJ-EVAL-SPEC` | eval_spec | 0.1.0 | 全档位 / 六链路 / `*` / local·ci·staging__TBC | — | active |
| `OBJ-ADAPTER-CONTRACTS` | adapter_contract | 0.1.0 | 全档位 / embed·retrieve·generate·score / `*` / `*` | EVAL-SPEC | active |
| `OBJ-RUN-PROFILE` | run_profile | 0.1.0 | 三档位 / `*` / `*` / local·ci·staging__TBC | ADAPTER-CONTRACTS | active |
| `OBJ-CHUNK-PARAMS` | chunk_params | 0.1.0 | 占位·待确认 / chunk / `*` / `*` | EVAL-SPEC | active |
| `OBJ-EMBEDDING-CFG` | embedding_config | 0.1.0 | 占位 / embed·retrieve / `*` / local·ci | ADAPTER-CONTRACTS, CHUNK-PARAMS | active |
| `OBJ-LLM-MODEL-CFG` | llm_model_config | 0.1.0 | 占位 / generate·score / `*` / local·ci | ADAPTER-CONTRACTS | active |
| `OBJ-RETRIEVAL-CFG` | retrieval_config | 0.1.0 | 占位·待确认 / retrieve / `*` / `*` | EMBEDDING-CFG, CHUNK-PARAMS | active |
| `OBJ-GENERATION-CFG` | generation_config | 0.1.0 | 占位·待确认 / generate / `*` / `*` | LLM-MODEL-CFG, PROMPT-RAG-ANSWER | active |
| `OBJ-PROMPT-RAG-ANSWER` | prompt_template | 0.1.0 | 占位·待确认 / assemble_prompt·generate / `*` / `*` | RETRIEVAL-CFG | active |
| `OBJ-PROMPT-SCORING` | prompt_template | 0.1.0 | 待确认 / score / `*` / `*` | SCORING-RULES | dormant |
| `OBJ-SCORING-RULES` | scoring_rules | 0.1.0 | 占位·待确认 / score / `*` / `*` | PROMPT-RAG-ANSWER | active |
| `OBJ-DATASET` | dataset | 0.1.0 | 占位·待确认 / `*` / `*` / `*` | EVAL-SPEC | active |
| `OBJ-THRESHOLDS` | thresholds | 0.1.0 | 占位·待确认 / `*` / `*` / `*` | SCORING-RULES, BASELINE | active |
| `OBJ-BASELINE` | baseline | 0.1.0 | 占位 / `*` / `*` / `*` | DATASET, SCORING-RULES | active |

依赖构成 DAG（注册表启动时校验，出现环直接报错）。

### 3.2 对象元信息必填字段

| 字段 | 说明 |
|---|---|
| `object_id` | 全局唯一标识，与 registry 索引一致 |
| `object_type` | 类型枚举（13 类） |
| `version` | `MAJOR.MINOR.PATCH`，与索引一致 |
| `status` | `active` / `dormant` / `frozen` / `deprecated` |
| `owner` | 责任人（当前 `__TBC__`） |
| `effective_scope` | `profiles` / `chains` / `suites` / `environments` / `note` |
| `depends_on` | `[{object_id, version}]`，必须精确匹配且无环 |
| `content` | 对象实际配置/模板/规则/阈值/数据集引用 |
| `rollback_baseline` | 该对象的回滚基线版本 |
| `changelog` | 变更记录数组，每条须含下表 8 项 |

### 3.3 变更记录要求（每条必填，注册表强制校验）

| 字段 | 含义 | 要求 |
|---|---|---|
| `change_id` | 变更编号 | 全局唯一，建议 `CHG-<对象短名>-NNNN` |
| `changed_at` | 变更时间 | ISO 8601 带时区 |
| `changed_by` | 变更人/角色 | 不得为空 |
| `change_reason` | **变更原因** | 必须说明动机与预期影响，不得写「调整」 |
| `diff_before` | **前值** | 结构化差异；新建对象为 `null` |
| `diff_after` | **后值/前后差异** | 结构化差异或人可读摘要 |
| `rollback_baseline` | **回滚基线** | 明确回退到哪个版本 |
| `review_status` | 评审状态 | `pending_review` / `approved` / `rejected` |

示例（`OBJ-RETRIEVAL-CFG` 假设从 0.1.0 升 0.2.0）：

```json
{
  "change_id": "CHG-RETR-0002",
  "changed_at": "2026-11-XX T..+08:00（示例占位）",
  "changed_by": "eval-owner",
  "change_reason": "接入真实 SUT 后按官方文档确认 top_k 语义，需与线上检索参数对齐",
  "diff_before": {"top_k": 8, "mode": "hybrid_rrf_placeholder"},
  "diff_after": {"top_k": "__TBC__", "mode": "__TBC__"},
  "rollback_baseline": "0.1.0",
  "review_status": "pending_review"
}
```

### 3.4 版本策略

| 变更类型 | 级别 | 是否需重算基线 |
|---|---|---|
| 字段语义 / 评分口径 / 阈值口径变化 | MAJOR | 必须 |
| 新增用例 / 新增参数 / 模板结构变化 | MINOR | 必须评估 |
| 仅拼写、说明、非功能描述 | PATCH | 否 |
| **已产生 run 记录的版本** | 禁止原地修改 | 一律新建版本 |

### 3.5 变体与版本的关系

`--set OBJECT.content.path=VALUE` 只在内存中覆盖，**不修改对象文件**；
manifest 记录 `variant_overrides`（含 before/after）与 `variant_id`。
变体若被采纳，必须新建对应对象版本并写完整变更记录，再用正式版本重跑，
**变体 run 不得作为基线或验收依据**。

---

## 4. 评测维度与指标

### 4.1 六维度（`OBJ-SCORING-RULES`）

| 维度 ID | 维度 | 关注点 | 权重 |
|---|---|---|---|
| `D-RETRIEVAL` | 检索质量 | 金标证据是否被召回、排序位置 | `__TBC__` |
| `D-GROUNDING` | 依据充分性 | 引用格式、依据覆盖、幻觉代理 | `__TBC__` |
| `D-ANSWER` | 回答质量 | 非空、关键词命中、拒答正确性 | `__TBC__` |
| `D-ROBUSTNESS` | 健壮性 | 故障处理、降级记录、无未捕获异常 | `__TBC__` |
| `D-EFFICIENCY` | 效率与成本 | 分步延迟、token、成本 | `__TBC__` |
| `D-SAFETY` | 安全 | 注入抵抗、敏感信息泄漏 | `__TBC__` |

综合公式 `composite_formula=__TBC__`，**冻结前 `composite_score` 恒为 `null`**。

### 4.2 指标定义表

| 指标 | 口径 | 采集步骤 | 离线可比较 |
|---|---|---|---|
| `chain_step_completeness_rate` | 六步记录齐全的用例占比 | 全链路 | 是（链路指标） |
| `config_snapshot_hash` / `dataset_hash` | 规范化 SHA256 | run 级 | 是 |
| `determinism_pass` | 双跑去耗时后规范化哈希一致 | run 级 | 是 |
| `chunk_count` / `truncated` | 分块数与是否触顶 | ① | 是 |
| `vector_count` / `dimension` | 向量条数与维度 | ② | 是 |
| `recall_at_k` | 命中的金标 chunk 数 / 金标 chunk 总数 | ③ | 否 |
| `mrr_at_k` | 首个相关片段排名倒数 | ③ | 否 |
| `gold_evidence_hit` | 是否命中至少一条金标证据 | ③ | 否 |
| `empty_retrieval` | 召回为空 | ③ | 是 |
| `prompt_hash` / `prompt_chars` / `prompt_overflow` | 提示词唯一标识、长度、溢出标记 | ④ | 是 |
| `answer_non_empty` / `answer_char_len` | 回答非空与长度 | ⑤ | 是 |
| `keyword_hit_rate` | 期望关键词命中率 | ⑤ | 否 |
| `has_citation_marker` | 是否含 `[n]` | ⑤⑥ | 是 |
| `abstain_correctness` | 拒答是否与预期一致 | ⑤⑥ | 否 |
| `grounded_token_ratio` | 回答与上下文的 token 重合代理 | ⑥ | 否 |
| `R1..R6` 规则检查 | 6 条布尔/比率检查 | ⑥ | 否 |
| `fault_handled_correct` | 最终状态是否等于用例预期状态 | 全链路 | 是 |
| `latency_ms_by_step` | 分步耗时 | 全链路 | 是（但排除出复现哈希） |
| `token_usage` / `cost` | `__TBC__` | ②⑤ | 否 |

金标 chunk 由 `gold_evidence` 原文片段在运行时映射到 `chunk_id`，分块参数变化不使标注失效。

### 4.3 采集点矩阵

| 步骤 | 主要指标 | 关联对象 |
|---|---|---|
| ① chunk | chunk_count / truncated / document_chars | CHUNK-PARAMS, DATASET |
| ② embed | vector_count / dimension / semantic_capability | EMBEDDING-CFG, ADAPTER-CONTRACTS |
| ③ retrieve | recall_at_k / mrr / empty_retrieval / forced_empty | RETRIEVAL-CFG, EMBEDDING-CFG |
| ④ assemble_prompt | prompt_hash / prompt_chars / prompt_overflow / context_item_count | PROMPT-RAG-ANSWER, RETRIEVAL-CFG |
| ⑤ generate | answer_* / cited_indices / error / token_usage=null | GENERATION-CFG, LLM-MODEL-CFG, PROMPT-RAG-ANSWER |
| ⑥ score | checks / scoring_status / composite_score=null | SCORING-RULES, PROMPT-SCORING, THRESHOLDS |

---

## 5. 测试数据集与用例设计

### 5.1 数据集对象

`OBJ-DATASET@0.1.0`：`DATASET-MANUAL-QA`，`cases_v0.1.0.jsonl`（26 条）+ `fixtures_v0.1.0.json`（6 份合成文档）。
全部为合成文本，不含真实产品资料。超长文本由 `repeat` 规则确定性生成，不落地大文件。

### 5.2 用例字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | str | 用例唯一标识，回归配对主键 |
| `suite` | str | 套件：`smoke`/`retrieval`/`generation`/`boundary`/`fault_injection`/`safety`/`regression_seed` |
| `scenario` | str | 场景（同时驱动故障注入） |
| `fixture_id` | str | 合成文档引用 |
| `query` | str | 问题 |
| `gold_evidence` | list[str] | 金标证据**原文片段**（非 chunk_id） |
| `expected_keywords` | list[str] | 期望关键词 |
| `expected_behavior` | str | 预期行为（含拒答、降级、负向对照） |
| `expected_final_status` | str | `ok` / `degraded` / `incomplete` |
| `tags` | list[str] | 分层标签（语言/难度/文档类型） |
| `acceptance_refs` | list[str] | 关联的对象版本，用于变更影响分析 |
| `note` | str | 可选，说明负向对照等特殊设计 |

### 5.3 首批用例矩阵（26 条）

| 套件 | 条数 | 覆盖 |
|---|---|---|
| smoke | 3 | 三份不同合成文档的正常问答 |
| retrieval | 6 | 跨页召回、多关键词、页码引文 |
| generation | 3 | 简洁回答、无证据拒答、枚举 |
| boundary | 5 | **空文档**、**超长文本**、**检索为空**、**提示词溢出**、**空问题** |
| fault_injection | 4 | **超时**、**返回异常**、**评分不可用**、空召回 |
| safety | 2 | 提示词注入、敏感信息泄漏负向对照 |
| regression_seed | 3 | 回归种子集 |

### 5.4 标注与扩充规范

1. 金标用证据原文片段；切分变化后由程序重新映射 chunk_id。
2. 新增用例必须新建 `OBJ-DATASET` 版本；**坏例归档而非删除**。
3. 真实说明书语料入库的版权/脱敏要求、人工标注抽样比例、仲裁规则、一致性统计量均为 `__TBC__`。
4. 数据集分层配额（难度 / 语言 / 文档类型）`__TBC__`，但分层标签字段已预置。
5. 每个用例必须写 `acceptance_refs`，对象变更时可反查受影响用例。

---

## 6. 对比实验与回归测试

### 6.1 实验设计通则

- **配对**：同一 `case_id` 在控制组与变体组逐条配对，不做汇总均值比较。
- **控制组**：`OBJ-BASELINE` 指向的冻结配置（当前为链路健康基线）。
- **固定量**：`seed`、`dataset_hash`、除被测轴外的全部对象版本。
- **重跑次数 / 置信区间口径 / 样本量要求**：`__TBC__`（真实 API 有随机性后需评审确定，建议配对 bootstrap + Wilson 区间）。
- **统计方法**：逐用例差值分布 + 区间估计；`__TBC__` 冻结前不出「显著优于」结论。
- **守护栏**：状态不得变差、故障处理不得回退、记录模式必须仍合法（工具已实现）。

### 6.2 对比实验矩阵

| 实验 ID | 变更轴 | 变体示例 | 主观测指标 | 决策规则 |
|---|---|---|---|---|
| `EXP-CHUNK-01` | 分块窗口 | `max_chars` 400 / 800 | recall_at_k、chunk_count、prompt_overflow | 召回提升且溢出不恶化 |
| `EXP-CHUNK-02` | 重叠 | `overlap_chars` 0 / 60 / 120 | recall_at_k、chunk_count | 边际收益是否为正 |
| `EXP-EMB-01` | embedding | 维度/模型 `__TBC__` | recall_at_k、mrr、vector_count | **需先确认模型与维度**；变更维度须全量重建索引 |
| `EXP-RETR-01` | top_k | 3 / 8 / 12 | recall_at_k、prompt_chars、latency | 召回与提示词成本权衡 |
| `EXP-RETR-02` | 融合方式 | 仅词法 / 仅向量 / RRF | recall_at_k、mrr | 不得牺牲无答案拒答正确性 |
| `EXP-PROMPT-01` | 提示词模板 | `0.1.0` vs 新版本 | 引用格式、拒答正确性、grounded_ratio | 模板改动必须升版本 |
| `EXP-GEN-01` | 生成参数/模型 | 参数与模型 `__TBC__` | 关键词命中、拒答、延迟、成本 | 全部参数名待确认 |
| `EXP-E2E-01` | 多轴联合 | 上述胜出组合 | 六维度全指标 | 需正式版本重跑，变体不得作为基线 |

运行方式：`python eval/run_eval.py --set OBJ-RETRIEVAL-CFG.content.top_k=3 --label exp-topk3`，
再用 `python eval/compare_runs.py <基线目录> <变体目录>` 产出 `comparison.json`。
离线变体对比 `comparable=false`，只验证工具与记录格式。

### 6.3 回归测试分层

| 层 | 内容 | 触发 | 闸门 |
|---|---|---|---|
| L0 自测 | 注册表、DAG、契约门禁、确定性、故障状态、日志脱敏 | 每次提交 | 必须全过（现有 14 项） |
| L1 链路健康 | 26 条离线用例 + 双跑 + 7 项闸门 | 每次提交 / CI | `OFFLINE_READY` |
| L2 模式与契约 | record/manifest schema、对象变更记录完整性 | 每次提交 | 必须全过 |
| L3 回归种子集 | `regression_seed` 3 条 + 关键 badcase | 对象版本变更 | 不得状态变差 |
| L4 全量真实评测 | 全数据集 + 真实 API | 定时（`__TBC__`）/ 发布前 | 冻结阈值 |
| L5 基线比对 | 与 `OBJ-BASELINE` 配对比较 | L4 之后 | 允许回退幅度 `__TBC__` |

CI 策略：L0–L3 每次提交必跑且**不消耗任何外部配额**；L4/L5 需审批与预算上限（`__TBC__`）。

---

## 7. 验收阈值

### 7.1 两段式闸门（`OBJ-THRESHOLDS@0.1.0`）

| 阶段 | 状态 | 判定 |
|---|---|---|
| Stage 1 链路健康 | `enforceable_now` | **现在就能判**，与真实 API 无关 |
| Stage 2 真实质量 | `TBC__cannot_be_frozen_before_baseline` | 基线实测前一律 `null` |

### 7.2 Stage 1 阈值（已实测通过）

| 指标 | 操作 | 阈值 | 实测 |
|---|---|---|---|
| `chain_step_completeness_rate` | >= | 1.0 | 1.0 |
| `record_schema_valid_rate` | >= | 1.0 | 1.0 |
| `determinism_pass` | == | true | true |
| `fault_scenario_handled_rate` | >= | 1.0 | 1.0 |
| `no_network_calls` | == | true | true |
| `no_secret_or_raw_prompt_in_logs` | == | true | true |
| `unhandled_exception_count` | <= | 0 | 0 |

### 7.3 Stage 2 阈值（全部 `null`，冻结前不得填写）

`recall_at_k`、`mrr_at_k`、`citation_format_valid_rate`、`groundedness_rate`、
`abstain_correctness`、`injection_resisted_rate`、`p95_latency_ms`、
`cost_per_100_cases`、`no_regression_vs_baseline`。

**冻结规则**：首轮真实 API 全量实测 → 生成建议阈值 → 评审 → 新建 `OBJ-THRESHOLDS@0.2.0` 冻结；
冻结前任何质量结论标记为 `NOT_COMPARABLE`。

### 7.4 判定逻辑

```
Stage1 任一不满足              → BLOCK
Stage2 未冻结                  → quality_verdict = NOT_COMPARABLE（不得判 PASS）
Stage1 通过且 Stage2 已冻结    → 按冻结阈值给出 PASS / FAIL
评分不可用或链路异常未收敛      → incomplete / degraded，不得计入通过率
```

---

## 8. 异常与边界场景

### 8.1 场景表

| 场景 | 触发方式 | 预期最终状态 | 必须记录 | 禁止行为 |
|---|---|---|---|---|
| 正常 | `normal` | `ok` | 六步记录 + 引用 | — |
| **空文档** | `empty_document` / `FIX-EMPTY` | `ok` | `chunk_count=0`、拒答 | 不得伪造资料 |
| **超长文本** | `ultra_long_text`（3000 段合成） | `ok` | chunk 数、是否触顶、`prompt_overflow` | 不得静默截断不记 |
| **检索为空** | `empty_retrieval` / `no_evidence` / `empty_query` | `ok` | `forced_empty=true`、拒答 | 不得编造答案 |
| **提示词溢出** | `prompt_overflow`（长行 fixture） | `ok` | `prompt_overflow=true`、warning | 不得写原始提示词进日志 |
| **超时** | `upstream_timeout` | `degraded` | 内部错误码 + 失败 step | 不得当通过、不得丢用例 |
| **返回异常** | `upstream_abnormal_response` | `degraded` | 结构错误上下文 | 不得回传堆栈给前端 |
| **评分不可用** | `scoring_unavailable` | `incomplete` | `status=unavailable`、`composite_score=null` | **不得静默记 0 分** |
| 提示词注入 | `prompt_injection` | `ok` | 注入抵抗检查 | 不得把问题当指令 |
| 敏感信息负向对照 | `normal` + 含号码问题 | `ok` | `R5=false` 用于验证规则有效 | 不得当作系统缺陷掩盖 |
| 空问题 | `empty_query` | `ok` | 强制空召回 | 不得报错退出 |
| 对象/契约未就绪 | `--profile real_api` | `BLOCK` | 26 项待确认清单 | 不得猜测配置继续跑 |

### 8.2 内部占位错误分类（**不是供应商错误码**）

`E_PLACEHOLDER_TIMEOUT`、`E_PLACEHOLDER_MALFORMED_RESPONSE`、
`E_PLACEHOLDER_EMPTY_RESULT`、`E_PLACEHOLDER_SCORING_UNAVAILABLE`、`E_CONTRACT_NOT_READY`。
真实错误码映射确认后，须新建 `OBJ-ADAPTER-CONTRACTS` 版本并单独记录。

### 8.3 统一降级规则

1. 任一 step 异常 → 该 step `status=error` + `error` 对象 + 顶层 `errors` 追加，链路继续走到 score；
2. 评分不可用 → `final.status=incomplete`，`composite_score=null`；
3. 生成失败 → `final.status=degraded`，`answer_text=""`，后续评分照常记录；
4. 任何情况都不得删除该用例记录，不得把不可用值写成 0/空字符串后判通过。

### 8.4 真实接入后须补充的异常用例（全部 `__TBC__`）

鉴权失效 / 凭据过期、限流与配额耗尽（重试与退避语义 `__TBC__`）、
网络抖动与部分成功、批量 embedding 部分失败、长度超限与截断行为、
流式事件中断、模型下线或版本切换、SUT 返回结构变更、成本超预算熔断。
每类须先确认对应真实错误码与响应结构，再新建用例版本。

---

## 9. 统一结果记录格式

### 9.1 产物

| 文件 | 内容 |
|---|---|
| `results/<run_id>/records.jsonl` | **逐用例一条**，每行一个完整 JSON |
| `results/<run_id>/manifest.json` | run 元信息、对象版本、哈希、变体、环境、契约门禁、逐用例哈希、分步延迟 |
| `results/<run_id>/summary.json` | 状态分布、闸门结果、复现哈希、verdict |
| `results/<run_id>/contract_gate.json` | 35 项契约槽位与待确认清单 |
| `results/<run_id>/comparison.json` | 与基线/变体的逐用例差异（运行 compare_runs 后） |

### 9.2 record 字段说明（`eval-record/v0.1`）

| 字段 | 说明 |
|---|---|
| `schema_version` | 记录模式版本 |
| `run_id` | 由 profile+seed+config_hash+dataset_hash+label 确定性生成 |
| `trace_id` | `run_id:case_id`，trace 主键 |
| `case_run_id` | 用例级运行标识（确定性） |
| `case_id` / `case_version` / `suite` / `scenario` | 用例身份与分层 |
| `profile` / `seed` | 档位与复现种子 |
| `experiment_id` / `variant_id` | 对比实验关联 |
| `object_versions` | **全量 14 个对象版本快照** |
| `config_snapshot_hash` / `code_hash` / `dataset_hash` / `baseline_ref` | 复现四要素（配置 / 评测代码 / 数据 / 基线） |
| `chain[]` | 六步记录（见 9.3） |
| `final` | 状态、预期状态、`fault_handled_correct`、回答、引文、检索/生成指标、评分 |
| `warnings` / `errors` | 告警与内部占位错误对象 |
| `observability.trace_fields` | 追踪层关联字段（见第 10 节） |
| `observability.log_policy` | 脱敏策略声明 |
| `placeholder` / `comparable` | 是否为占位结果、是否可比较 |

### 9.3 chain step 字段说明

| 字段 | 说明 |
|---|---|
| `step` | `chunk`/`embed`/`retrieve`/`assemble_prompt`/`generate`/`score` |
| `span_id` / `parent_trace_id` | 逐链路 span 标识与父 trace |
| `status` | `ok` / `error` / `degraded` |
| `duration_ms` | 实测耗时（**排除出复现哈希**） |
| `object_refs` | 该步消费的所有 `object_id@version`（逐链路关联） |
| `input_ref` / `output_ref` | 输入/输出摘要哈希，不存原文 |
| `metrics` | 该步指标（见 4.3） |
| `warnings` / `error` | 告警与内部占位错误 |
| `is_placeholder` | 该步是否为占位实现 |

### 9.4 记录片段（结构示意，非真实 API 数据）

```json
{
  "schema_version": "eval-record/v0.1",
  "trace_id": "RUN-...:C-001",
  "object_versions": {"OBJ-CHUNK-PARAMS": "0.1.0", "...": "0.1.0"},
  "config_snapshot_hash": "65f1c17c...",
  "chain": [{"step": "chunk", "status": "ok", "object_refs": ["OBJ-CHUNK-PARAMS@0.1.0", "..."],
             "metrics": {"chunk_count": 4}, "input_ref": "...", "output_ref": "..."}],
  "final": {"status": "ok", "fault_handled_correct": true,
            "retrieval_metrics": {"recall_at_k": 1.0, "comparable": false},
            "scoring": {"status": "ok", "composite_score": null}},
  "placeholder": true, "comparable": false
}
```

### 9.5 模式校验

`validate_record()` 强制：必填顶层字段齐全、`chain` 六步且顺序一致、
每步有 `object_refs`、观测层有 `config_snapshot_hash`。
`strip_volatile()` 移除耗时/时间戳后计算 `comparability_hash`，用于双跑确定性判定。

---

## 10. 全链路观测能力

### 10.1 四类关联键

| 层级 | 键 | 用途 |
|---|---|---|
| run | `run_id` + `config_snapshot_hash` + `code_hash` + `dataset_hash` + `seed` | 复现整次评测（含评测代码版本） |
| 用例 | `case_id` + `case_run_id` + `case_version` + `trace_id` | 定位单条 badcase |
| 链路 | `trace_id` + `span_id` + `step` + `chain[].object_refs` | 定位哪一步、消费了哪些对象版本 |
| 对象 | `object_id@version` + `content_hash` + `depends_on` + `changelog.change_id` | 定位配置变更来源与回滚点 |

额外关联：`experiment_id` / `variant_id` / `baseline_ref` / `acceptance_refs` / `commit_sha`（CI 注入，当前未采集）。

### 10.2 LangSmith 或等价可观测方案 · 追踪字段

> 是否采用托管 LangSmith、其鉴权与配额均为 `__TBC__`；
> 等价方案可以是「自托管 OpenTelemetry + 追踪后端」或本仓库的 JSONL + manifest（当前实现）。

| 层级 | 字段 | 说明 |
|---|---|---|
| Trace / Run | `project` | `kbqa-eval`（占位名） |
| | `session_id` | = `run_id`，一次评测一个 session |
| | `trace_id` / `run_id` | 追踪主键 |
| | `reference_dataset_id` / `dataset_version` / `dataset_hash` | 数据集版本关联 |
| | `config_snapshot_hash` / `code_hash` / `object_versions[]` | **逐对象版本 + 评测代码版本关联** |
| | `experiment_id` / `variant_id` / `baseline_ref` | 实验与基线关联 |
| | `seed` / `profile` | 复现条件 |
| | `start_time` / `end_time` / `status` / `error` | 生命周期 |
| | `tags[]` | suite / scenario / profile / 环境 |
| | `token_usage` / `cost` | 当前 `null`，字段 `__TBC__` |
| Span / Child Run | `span_id` / `parent_run_id` / `trace_id` | 父子关系 |
| | `name` | 六步名称 |
| | `run_type` | `chain` / `retriever` / `llm` / `tool` / `evaluator`（按步映射） |
| | `inputs` / `outputs` | **脱敏摘要与哈希**，非原文 |
| | `latency_ms` / `status` / `error.code` | 耗时与内部占位错误 |
| | `metadata.object_refs` | 该 span 消费的对象版本 |
| | `metadata.metrics` | 该步指标 |
| | `events[]` | 流式事件占位（`stream_event_schema=__TBC__`） |
| Feedback / Score | `case_id` / `case_run_id` | 逐用例关联 |
| | `metric_name` / `value` / `comparable` | 指标值与可比较性 |
| | `scoring_rules_ref` / `composite_freeze_status` | 评分规则版本 |
| | `scoring_status` | `ok` / `unavailable`，不可用时不写 0 |
| | `evaluator_version` / `judge_mode` | 评分器版本与模式 |

### 10.3 日志字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `ts` | ✅ | ISO 8601 带时区 |
| `level` | ✅ | DEBUG/INFO/WARN/ERROR |
| `trace_id` / `span_id` / `case_id` / `step` | ✅ | 定位四联 |
| `run_id` / `experiment_id` / `variant_id` | ✅ | 批次关联 |
| `object_refs[]` / `change_id` | ✅ | 对象版本与变更关联 |
| `event` | ✅ | 阶段进入/退出、故障注入、降级、闸门结果 |
| `metric_name` / `metric_value` | 可选 | 指标打点 |
| `error_code` / `error_kind` | 异常时 | 内部占位分类；真实错误码 `__TBC__` |
| `latency_ms` | 可选 | 分步耗时 |
| `prompt_hash` / `input_ref` / `output_ref` | ✅ | 只记哈希 |
| `model_id` / `usage` / `cost` | 接入后 | 当前 `null` / `__TBC__` |

**脱敏规则**：禁止记录原始提示词、完整回答正文、凭证、鉴权头、请求/响应原文；
需要排错时用短脱敏预览 + 哈希。留存周期与访问权限 `__TBC__`。

### 10.4 可追踪 / 可复现 / 可排查 / 可维护的落地对应

| 能力 | 机制 |
|---|---|
| 可追踪 | 四类关联键 + 每步 `object_refs` + `trace_id` 贯穿记录与日志 |
| 可复现 | `seed` + `config_snapshot_hash` + `code_hash` + `dataset_hash` + 确定性 stub + 双跑哈希比对 |
| 可排查 | 六步 status/metrics/warnings/error、输入/输出哈希、故障注入可重放、`--check-contracts` 列出待确认项 |
| 可维护 | 14 个独立对象 + 强制变更记录 + DAG 校验 + 变体晋升规则 + 基线回滚 |

### 10.5 典型排查路径

| 现象 | 定位键 | 检查顺序 |
|---|---|---|
| 召回下降 | `config_snapshot_hash` → `OBJ-CHUNK-PARAMS` / `OBJ-EMBEDDING-CFG` | 对象版本差异 → chunk_count → dimension → recall_at_k |
| 回答格式异常 | `prompt_hash` → `OBJ-PROMPT-RAG-ANSWER` 版本 | 模板变更记录 → citation 规则 → R2 |
| 延迟升高 | `latency_by_step` | 逐步耗时 → 是否外部调用 → 配额 `__TBC__` |
| 结果不可复现 | `comparability_hash` + 双跑 | seed / 对象版本 / 数据集哈希 / 是否引入非确定性 |
| 评分缺失 | `scoring_status=unavailable` | 不得按 0 处理；查 `errors` 与降级记录 |

---

## 11. 接入真实 API 的落地步骤（SOP）

> 每一步的输入、输出都必须是**用户确认的信息**，不得由方案推断填写。

| # | 步骤 | 产出 | 前置 |
|---|---|---|---|
| 1 | 收集三类接入信息（DeepSeek / 百炼 embedding / SUT） | 填写 26 项 blocking 契约字段 | 用户提供开通凭证与官方文档 |
| 2 | 新建 `OBJ-ADAPTER-CONTRACTS@0.2.0`，写完整变更记录 | 契约版本 | 评审 |
| 3 | 凭据接入密钥管理，环境变量名登记（值不入库） | 凭据引用 | 安全审批 |
| 4 | 新建各配置对象版本（embedding / llm / generation / retrieval / prompt） | 版本化配置 | 依赖 DAG 校验 |
| 5 | 实现真实适配器（请求/响应结构**按确认后契约**），保留 stub 供回退 | adapter 代码 + 契约测试 | 步骤 1–4 |
| 6 | 小额试跑（用例数 / 预算上限 `__TBC__`） | 试跑 run + 契约测试报告 | 步骤 5 |
| 7 | 全量真实评测，产出 `metrics_snapshot` | 首轮真实 run | 步骤 6 通过 |
| 8 | 人工抽检（比例与仲裁 `__TBC__`） | 标注一致性报告 | 步骤 7 |
| 9 | 新建 `OBJ-BASELINE@0.2.0` 冻结质量基线；生成阈值建议 | 基线 + 阈值建议 | 步骤 8 |
| 10 | 评审冻结 `OBJ-THRESHOLDS@0.2.0`，启用 `real_api`，进入 L4/L5 回归 | 可验收状态 | 步骤 9 |

任意步骤发现契约与确认信息不一致：**回滚到上一对象版本并重新走流程**，不得临时改代码绕过门禁。

---

## 12. 后续迭代维护流程

### 12.1 变更流程

1. 提出变更（对象 ID、原因、预期影响）→ 2. 判断 MAJOR/MINOR/PATCH →
3. 新建对象版本文件 + 写 8 项变更记录 → 4. registry 索引同步更新 →
5. `python -m unittest discover -s eval/tests -t .`（L0）→
6. `python eval/run_eval.py`（L1 链路健康闸门）→
7. 与 `OBJ-BASELINE` 做 `compare_runs`（L3/L5）→
8. 按冻结阈值判定 → 9. 评审 `review_status=approved` →
10. 归档旧版本（标记 `deprecated`，**不删除**）→ 11. 更新基线与回滚点说明。

### 12.2 回滚流程

触发：新版本导致闸门 `BLOCK`、可复现性失败、或质量回退超允许幅度（`__TBC__`）。
动作：切回 `rollback_baseline` 指定版本 → 重跑回归种子集 → 记录回滚原因与时间 →
在变更记录中新增一条 `change_reason` 说明 → 保留失败版本用于复盘。

### 12.3 周期与角色

| 活动 | 频率 | 责任角色（当前 `__TBC__`） |
|---|---|---|
| L0–L3 回归 | 每次提交 / CI | 开发 |
| L4 全量真实评测 | `__TBC__`（建议发布前 + 定期） | 评测负责人 |
| 阈值与基线复核 | 每次 MAJOR/MINOR 变更 | 评审组 |
| 数据集扩充与坏例归档 | 每次线上 badcase 复盘 | 标注 + 评测 |
| 契约字段复核 | 供应商文档更新时 | 集成负责人 |
| 权限/配额/成本复核 | `__TBC__` | 运维 + 财务 |

### 12.4 漂移监控

监控对象：模型标识/版本变更、embedding 维度变化、SUT 分块或引文格式变化、
数据集分布变化、延迟与成本漂移、错误码分布变化。
任一变化触发「重新基线化评估」，阈值口径未确认前不得自动放行。

### 12.5 归档

每次 run 的 records / manifest / summary / comparison / contract_gate 按 `run_id` 归档；
保留期限、冷存储、访问权限 `__TBC__`。归档不得改写，只追加索引。

---

## 13. 需申请或开通的 embedding 模型项占位、权限、配额与所需能力清单

> 本节**单独列出**，仅覆盖 embedding 侧。所有具体服务名、模型标识、版本、数值均为 `__TBC__`，
> 需由用户/开通方确认后填入 `OBJ-ADAPTER-CONTRACTS`，不得由本方案推断。
> DeepSeek（生成）、SUT（待测系统）、可观测平台的开通项见 11 节 SOP 与 2.3 节契约表，不在本节重复。

### 13.1 必需项（未满足则 `real_api` 档位保持阻断）

| # | 项目 | 占位标识 | 需确认内容 | 阻塞链路 |
|---|---|---|---|---|
| M1 | embedding 服务开通权限 | `EMBEDDING_SERVICE_ACCESS__TBC` | 账号/租户/工作空间开通状态、可用区域 | ② |
| M2 | 模型标识与版本 | `EMBEDDING_MODEL_ID__TBC` / `EMBEDDING_MODEL_VERSION__TBC` | 具体模型标识、版本、生命周期与下线通知机制（**不预设名称**） | ②③ 基线 |
| M3 | 接口地址 | `EMBEDDING_ENDPOINT__TBC` | 服务入口、协议、路径（**不预设**） | ② |
| M4 | 鉴权方式与凭据 | `EMBEDDING_AUTH_SCHEME__TBC` / `EMBEDDING_CREDENTIAL_REF__TBC` | 鉴权机制、凭据获取/轮换/吊销流程；凭据不入库 | ② |
| M5 | 请求与响应结构 | `EMBEDDING_REQ_SCHEMA__TBC` / `EMBEDDING_RESP_SCHEMA__TBC` | 字段、批量入参、向量字段位置、错误结构 | ② |
| M6 | 输出维度与相似度度量 | `EMBEDDING_DIM__TBC` / `EMBEDDING_METRIC__TBC` | 维度、是否归一化、与检索端度量一致性 | ②③ |
| M7 | 输入长度与批量上限 | `EMBEDDING_MAX_INPUT__TBC` / `EMBEDDING_MAX_BATCH__TBC` | 单条长度语义、批量条数、超长处理策略 | ①② |
| M8 | 配额与限流 | `EMBEDDING_QUOTA_QPS__TBC` / `TPM__TBC` / `DAILY__TBC` | QPS/TPM/日额度、限流行为、超额策略（**不预设数值**） | 运行预算 |
| M9 | 错误码与重试语义 | `EMBEDDING_ERROR_CODES__TBC` / `RETRY_POLICY__TBC` | 错误码表、可重试判定、幂等性、退避要求（**不预设错误码**） | 异常场景 |
| M10 | 评测运行预算 | `EMBEDDING_EVAL_BUDGET__TBC` | 单次评测可用额度、成本上限、熔断规则 | L4/L5 |
| M11 | 网络与安全审批 | `EMBEDDING_NETWORK_APPROVAL__TBC` | 出口/白名单、数据出域与隐私合规审批、日志脱敏要求 | 全链路 |
| M12 | 所需能力：批量向量化 | `CAP_BATCH_EMBED__TBC` | 是否支持一次多条，以减少调用次数 | ② |
| M13 | 所需能力：稳定维度与确定性 | `CAP_STABLE_DIM__TBC` | 同模型输出维度稳定、结果可重建（影响索引与基线） | ②③ 基线 |
| M14 | 所需能力：可观测标识 | `CAP_REQUEST_ID__TBC` | 是否返回请求/调用标识，用于 trace 关联 | 观测 |
| M15 | 所需能力：用量与成本字段 | `CAP_USAGE_COST__TBC` | 是否返回用量信息，用于成本核算 | D-EFFICIENCY |

### 13.2 可选项（不影响首轮接入，影响后续实验深度）

| # | 项目 | 占位标识 | 价值 | 风险/备注 |
|---|---|---|---|---|
| O1 | 多模型 / 多维度 A/B 权限 | `EMBEDDING_MULTI_MODEL__TBC` | 支撑 `EXP-EMB-01` 对比实验 | 维度变更需全量重建索引并重算基线 |
| O2 | 稀疏向量或重排能力 | `EMBEDDING_SPARSE_OR_RERANK__TBC` | 支撑 `EXP-RETR-02` | 需额外契约字段与版本 |
| O3 | 长文本 / 多语言能力 | `EMBEDDING_LONGTEXT_MULTILINGUAL__TBC` | 支撑长文档与多语言用例 | 长度语义 `__TBC__` |
| O4 | 离线批量推理额度 | `EMBEDDING_OFFLINE_QUOTA__TBC` | 降低全量重建成本 | 需单独审批 |
| O5 | 版本冻结 / 模型下线通知 | `EMBEDDING_VERSION_PINNING__TBC` | 保证基线长期可比 | 影响漂移监控 |
| O6 | 专用沙箱/隔离环境 | `EMBEDDING_SANDBOX__TBC` | 避免评测影响生产配额 | 需额外权限 |
| O7 | SLA 与工单通道 | `EMBEDDING_SLA__TBC` | 故障排查与配额调整 | 不影响功能 |
| O8 | 向量缓存 / 复用服务 | `EMBEDDING_CACHE__TBC` | 降低重复评测成本 | 需纳入 `config_snapshot_hash` 口径 |
| O9 | 模型裁判（评分）额度 | `JUDGE_QUOTA__TBC` | 启用 `OBJ-PROMPT-SCORING` | 需先冻结裁判一致性口径 |
| O10 | 托管可观测平台额度 | `TRACE_PLATFORM_QUOTA__TBC` | 用 LangSmith 或等价托管方案替代 JSONL | 鉴权/配额 `__TBC__` |

### 13.3 开通前待确认项汇总

| 类别 | 待确认 | 确认来源 |
|---|---|---|
| 身份与权限 | M1、M4、M11 | 开通方 / 安全审批 |
| 模型信息 | M2、M6、O1、O5 | 用户确认 + 官方模型清单（**不预设名称**） |
| 接口契约 | M3、M5、M7、M9、M12–M15 | 官方接口文档 |
| 配额与成本 | M8、M10、O4、O9、O10 | 开通配额 / 商务 |
| 能力边界 | O2、O3、O6、O7、O8 | 官方能力清单 / 架构评审 |

> 在 M1–M15 全部确认并写入 `OBJ-ADAPTER-CONTRACTS` 新版本之前，
> `real_api` 档位保持阻断，评测结论一律 `NOT_COMPARABLE`。
