# 迭代收敛记录

每轮只改一个变量族；所有结论必须引用具体 run_id 与分支。

---

## 迭代 0 · 评分口径定稿（第 1 轮内发现并修正，分支 `eval/v1-baseline`）

**发现**：抽查 `RUN-58a0deda72c2` 的 S07，模型已正确拒答（"资料与所问问题无关…无法基于上述资料回答"），
但 v1.0.0 口径只认固定关键词表，判成 `rejection_correctness=0`（假阴性）。

| 项 | 修正前 | 修正后 |
|---|---|---|
| 口径版本 | `METRICS@1.0.0` | `METRICS@1.1.0` → `1.2.0` |
| 拒答判定 | 固定标记词 | 固定标记 **或**（否定词 × 资料指代 共现） |
| `negative_rejection`（minimax） | 0.000 | 1.000 |
| 主指标均值变化 | — | **+0.1111**（对照 `scoring_1.0_vs_1.2.json`） |

**第二个发现**：修正后 4 类主指标在 3 个模型上**全部 1.0**，指标饱和，配置对比将失去分辨力。
抽查确认 v1 朴素提示会复述资料、输出 Markdown 列表与多段内容。故 `1.2.0` 新增产品维度
`answer_economy`（单行 / 不复述 / 不成段列表），**不参与主指标与 overall**，仅用于收敛对照。
v1 实测基线：事实抽取 0.667 / 噪声鲁棒 0.333 / 信息整合 0.000 / 负面拒绝 0.000。

**版本化处置**：`rescore.py` 生成全新 run，原始 `answer` 与旧 run 完整保留，不原地修改任何已产生结果。
前後对照：`iterations/comparisons/scoring_1.0_vs_1.2.json`。

---

## 迭代 1 · v1 基线（分支 `eval/v1-baseline`）

```bash
python evalbench/run_eval.py --label v1-baseline \
  --models minimax-m3,kimi-k2.6,deepseek-v4.1-flash-1017 \
  --prompts v1_baseline --params A
```

| 项 | 结果 |
|---|---|
| 首次运行 | `RUN-301337307a9b`：36 条，**33 ok / 3 rate_limited**（真实限流，已留痕） |
| 缓存续跑补齐 | `RUN-58a0deda72c2`：36/36 ok，只重发 3 次失败请求 |
| 定稿基线（1.2.0 口径） | **`RUN-60d21360c41e`** |
| 配置 | 参数组 A（RGB 官方 `temp=0.2 / passage_num=5 / noise_rate=0.6`）+ 提示词 v1 |

**按模型分别记录（定稿口径，各类别主指标）**

| 模型 | 事实抽取 | 噪声鲁棒 | 负面拒绝 | 信息整合 | overall | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|
| `minimax-m3` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 3.76 s |
| `kimi-k2.6` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 5.68 s |
| `deepseek-v4.1-flash-1017` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 9.65 s |

**结论**：① 质量主指标在自建样本上已饱和，后续轮次改用 `answer_economy` 与延迟作为区分依据；
② 三个模型质量无差异，**延迟相差 2.6 倍**，这是选型的主要依据；
③ v1 朴素提示输出冗长、复述资料，`answer_economy` 全类偏低 → 下一轮用提示词版本收敛这一点。

---

## 迭代 2 · 提示词版本收敛（分支 `eval/v2-prompts`）

```bash
python evalbench/run_eval.py --label v2-prompts --models minimax-m3 \
  --prompts v1_baseline,v2_grounded,v3_structured --params A
```
固定模型与参数组，只改提示词。`RUN-3786315579b0`：36/36 ok（其中 12 条命中缓存）。
组内对照：`iterations/comparisons/prompt_v1_v2_v3.json`。

| 维度 | v1_baseline | v2_grounded | v3_structured |
|---|---:|---:|---:|
| `answer_accuracy` | 1.000 | 1.000 | 1.000 |
| `rejection_correctness` | 1.000 | 1.000 | 1.000 |
| `faithfulness` | 1.000 | 1.000 | 1.000 |
| `format_compliance` | 1.000 | 1.000 | 1.000 |
| **`answer_economy`** | **0.250** | **1.000** | **1.000** |
| 平均延迟 | 2325 ms | **1347 ms** | 1602 ms |

**收敛结论**：质量四维度不变（已饱和），`answer_economy` 由 0.25 提升到 1.00，
真实回答从「Markdown 列表 + 复述资料」变成单行结论（S10 由 154 字降到 106 字）。
v2 与 v3 在可判定指标上打平，但 **v2 延迟低 19%**（1347 vs 1602 ms）；
v3 的 few-shot / 严格单行格式在本样本集上未带来可判定增益 →
**采纳 `v2_grounded`**，v3 保留为候选（复杂语料可再评估），否决理由如上。

定稿 run：`RUN-87feb29fc4bd`（仅 `v2_grounded`，12/12 全部命中缓存，**零新增 API 调用**）。
跨轮前后对照：`iterations/comparisons/v1_vs_v2_adopted.json`
→ 主指标 delta = 0.000（不退化），`answer_economy` 平均 **+0.750**，符合采纳口径。

### 本轮内工程修复

`scoring.aggregate` 的 `by_model.avg_latency_ms` 原先按类别逐个赋值，被最后一个类别覆盖
（显示 2051 ms，records 真实均值 1346.7 ms）。已修为汇总全部记录；
用 `rebuild_summaries.py` **只重建派生的 summary.json**，
`records.jsonl` 与 `config_snapshot.json` 一字未动，任何 run 仍可从原始记录完全重算。

---
