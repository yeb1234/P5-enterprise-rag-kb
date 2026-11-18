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

## 迭代 3 · 参数组收敛（分支 `eval/v3-params`）

```bash
python evalbench/run_eval.py --label v3-params --models minimax-m3 \
  --prompts v2_grounded --params A,B,C
```
固定提示词与模型，只改参数组。`RUN-3fea07c6fa70`：36/36 ok（A 的 12 条命中缓存，24 次新调用）。
组内对照：`iterations/comparisons/params_A_B_C.json`。

| 参数组 | 设定 | 质量四维 | `answer_economy` | 平均延迟 |
|---|---|---:|---:|---:|
| A | RGB 官方 `temp=0.2 / passage=5 / noise=0.6` | 1.000 | 1.000 | 1347 ms |
| **B** | **确定性贪心 `temp=0 / passage=5 / noise=0.6`** | **1.000** | **1.000** | **1163 ms** |
| C | `temp=0 / passage=3 / max_tokens=768` | 1.000 | 1.000 | 1356 ms |

**收敛结论**：三档质量与精简度**全部打平**，说明样本已饱和，减少注入文档（C）也无可判定收益。
采纳 **B**，主要依据不是单次延迟差（1163 vs 1347 ms 可能受网关抖动影响，未做多次观测），
而是调研结论：`temperature=0` 贪心解码是降低 run-to-run 方差的首要设置，对**评估可复现性**最关键；
同时 B 保留 RGB 官方的 `passage_num=5 / noise_rate=0.6` 上下文设定，便于与公开基准口径对齐。
C 的 `passage_num=3` 会减少噪声文档、改变被测条件，且 `max_tokens=768` 无收益 → 否决。

定稿 run：`RUN-b1a734b77e26`（12/12 命中缓存，零新增 API 调用）。
前后对照：`iterations/comparisons/v2_vs_v3_adopted.json` → 主指标 delta = 0.000（不退化）。

> 诚实边界：`temperature=0` 在共享 GPU 批处理下**不保证**逐字节确定（见 `datasets/SOURCES.md` §4）；
> 本工作台靠「固定 prompt/参数/模型哈希 + 原始回答落盘 + 缓存复用」保证可追溯，而非只依赖温度值。

---

## 迭代 4 · 最终配置 × 三模型分别记录（分支 `eval/v4-models`）

**最终配置**：提示词 `v2_grounded` + 参数组 `B`（`temp=0 / passage_num=5 / noise_rate=0.6`）。
```bash
python evalbench/run_eval.py --label v4-final \
  --models minimax-m3,kimi-k2.6,deepseek-v4.1-flash-1017 \
  --prompts v2_grounded --params B
```
`RUN-b0454d8e17db` 首次 35 ok / 1 rate_limited；缓存续跑补齐后 **`RUN-a48c08c0b721`：36/36 ok**（最终定稿）。

### 按模型 × 评估维度分别给结论

| 模型 | answer_accuracy | rejection | faithfulness | format | **economy** | overall（主指标） | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `minimax-m3` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | **1.000** | **1.16 s** |
| `deepseek-v4.1-flash-1017` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | **1.000** | 9.81 s |
| `kimi-k2.6` | 0.815 | 0.833 | 0.917 | 0.833 | 0.833 | **0.861** | 6.85 s |

按任务类别（主指标）：`minimax` 与 `deepseek` 四类全 1.000；
`kimi` 事实抽取 1.000 / 负面拒绝 1.000 / 噪声鲁棒 0.667 / 信息整合 0.778。

### 跨轮对照与解读

`iterations/comparisons/v1_vs_final.json`：主指标均值 **−0.0582**。
**不能解读成「配置退化」**：v1 基线用的是 `v1_baseline`（允许复述资料，因此关键事实全部命中），
收敛后的 `v2_grounded` 要求简短结论，`minimax` / `deepseek` 保持 1.000 不退化，
全部降幅来自 `kimi-k2.6` 的指令遵循问题。这恰恰说明**单一提示词不可无验证地推广到所有模型**。

### 模型差异与兼容性适配（三步实证）

| 步骤 | 配置 | 结果 |
|---|---|---|
| ① 最终配置 | `v2_grounded × B(temp=0)` × kimi | 4 条掉分：S05「7个」漏「工作日」；S10 漏热线/时间；**S11/S12 整段复述 prompt** |
| ② 温度适配 | `v2_grounded × A(temp=0.2)` × kimi（`RUN-bc1ff8330a87`） | 噪声鲁棒 0.667→1.000、overall 0.861→0.945；但 S10/S11/S12 仍失败（3 条）。温度只解决过度压缩 |
| ③ few-shot 适配 | `v3_structured × A` × kimi × S10/S11/S12（`RUN-27b130a44e43`） | **仍整段复述 prompt**，与温度、few-shot 无关 → 模型级指令遵循问题 |

**另一个必须记录的评分发现**：复述 prompt 会把资料原文带进答案，从而**骗过 `answer_accuracy`**
（v3 探测里信息整合主指标显示 1.000），但 `format_compliance` / `answer_economy` /
`rejection_correctness` 同时为 0。因此只看 overall 会误判，必须按维度分别给结论
—— 这也是第 1 轮新增 `answer_economy` 的价值所在。

### 处理结论

- **默认选型 `minimax-m3`**：全维度 1.000、延迟 1.16 s，配置收敛结论在其上成立。
- **`deepseek-v4.1-flash-1017`** 质量相同但延迟高 **8.4 倍**（9.81 s），仅在需要推理能力时选用；
  已在 `models.json` 放宽超时到 180 s 并保留 `>=512` token 预算。
- **`kimi-k2.6` 不纳入该配置的默认选型**：对带规则列表的提示词会复述 prompt，换温度/加 few-shot 均未解决。
  若必须使用，需在适配层增加「输出复读检测 + 截断或改用无规则列表极简提示词」——**当前未实现，属后续工作**。
- 网关侧共性：`max_tokens=24` 时 6 个模型全部空正文，`temperature=0/top_p=1` 全部接受；
  详见 `out/models_probe.json` 与 `datasets/SOURCES.md`。

---

## 迭代 5 · 公开集参数收敛（2026-11-18，分支 `eval/public-benchmarks`）

**背景**：前四轮参数收敛在自建 12 条样本上完成，结论是「A/B/C 质量打平，采纳 B(temp=0) 主要依据可复现性」。
本轮用真实公开集（SQuAD v2 60 条 + CMRC 2018 60 条）重做，模型固定 `deepseek-v4.1-flash-1017`，
提示词固定各自最优（英文 v4 / 中文 v3），**评分器 METRICS@1.2.0 不动**（防调分刷分）。

### 变量隔离（先核实再跑）

`passage_num` 在公开集上**不生效**：两个公开集每样本只有 1 段，A/B/C 实际都只注入 1 段。
因此有效变量是：A↔B = `temperature`(0.2/0)；B↔C = `max_tokens`(512/768)。

### 单次结果（全部 60/60 补齐）

| 组 | EN acc | EN 拒答 | EN 延迟 | ZH acc | ZH 拒答 | ZH 延迟 |
|---|---|---|---|---|---|---|
| A (temp=0.2) | **0.900** | 1.000 | 1801ms | **0.7833** | 0.9833 | 1889ms |
| B (temp=0) | 0.8667 | 1.000 | 2240ms | 0.7667 | 0.9833 | 2095ms |
| C (temp=0,768tok) | 0.8667 | 1.000 | 1402ms | 0.7333 | 0.9833 | 1821ms |

### 关键反证：temp=0 并未更可复现

自建样本轮的采纳理由是「temp=0 可复现」。本轮对英文集做 3 次独立重复（key 含 rep，非缓存）：

| 组 | 三次 acc | 均值 | 极差 | 逐答案一致率 | 逐题分数翻转 |
|---|---|---|---|---|---|
| A (temp=0.2) | 0.9 / 0.9 / 0.9 | 0.900 | **0** | 81.7% | **0/60** |
| B (temp=0) | 0.8667 / 0.9 / 0.8333 | 0.8667 | 0.0667 | 81.7% | **2/60** |

**结论**：两组逐答案一致率相同（81.7%），但 temp=0 反而有 2 题分数在 0/1 间翻转，
temp=0.2 为 0。这条网关上 temp=0 的"可复现性优势"不成立——与旧 SOURCES.md §4 早已写下的
「贪心并非万能，共享 GPU 批处理仍会漂移」一致。**推翻自建样本轮的采纳理由。**

### 处理结论

- **采纳 A**（`temperature=0.2 / top_p=1.0 / max_tokens=512`）：中英 acc 均不低于 B，
  3 次重复稳定性不劣于 B，延迟介于 B/C 之间。
- **否决 B**：acc 不占优、可复现性也未占优，原采纳理由被本轮实证推翻。
- **否决 C**：中文 acc 降到 0.7333；`passage_num` 变量在公开集无效，B→C 的归因不纯，
  延迟收益（1402ms）无法确认来自 max_tokens 还是后端波动。
- **推翻前轮**：这是首次公开集证据推翻自建样本结论，记录在 `iterations/comparisons/params_public_v1.json`。

### 必须一起读的保留意见

- EN 差异 0.033 = 2/60 题，ZH 差异 0.017 = 1/60，接近噪声；采纳 A 靠的是"不劣于 B 的重复性"
  而非大幅质量提升。
- 中文字符串口径假阴性严重（13/14 失分为同义改写），A/B 的 acc 都是保守下界。
- 重复实验只做英文集，中文稳定性未验证。
- 本轮不动评分器、不动模型；`eval/` 冻结区零 diff。
