# 公开评估集：来源、许可与适配口径

调研与下载日期：2026-11-18 ｜ 用途：给 `evalbench/` 补上**真实公开集**评测，替代此前只有自建样本的缺口。

> 与旧 `datasets/SOURCES.md` 的关系：那份文档诚实声明了「samples_v1 是自建样本、不是官方抽样」。
> 本目录是那句话的后续——**真的把公开集下下来跑了**。两套样本并存，不互相覆盖：
> `samples_v1.jsonl` 仍是默认集，`public/*.jsonl` 通过 `--dataset` 显式指定。

---

## 1. 数据集清单

| 数据集 | 仓库 / 来源 | 许可 | 下载通道 | 原始文件 | 用途 |
|---|---|---|---|---|---|
| **SQuAD 2.0 validation** | `rajpurkar/squad_v2` | CC BY-SA 4.0 | hf-mirror.com（国内镜像） | `squad_v2/validation-00000-of-00001.parquet`（1,351,051 B / 11,873 行） | 生成质量：抽取准确性 + 不可回答拒答 |
| **RAGTruth test / qa** | `flowaicom/RAGTruth_test`（原论文 [arXiv:2305.11747](https://arxiv.org/abs/2305.11747) / [ParticleMedia/RAGTruth](https://github.com/ParticleMedia/RAGTruth)） | MIT | hf-mirror.com | `data/qa-00000-of-00001.parquet`（497,975 B / 900 行） | 忠实度专项：检索增强生成的幻觉检测 |
| **CMRC 2018 validation** | `hfl/cmrc2018`（Chinese Machine Reading Comprehension 2018，CLUE 基准） | CC BY-SA 4.0 | hf-mirror.com | `data/validation-00000-of-00001.parquet`（1,136,115 B / 3,219 行） | **中文**抽取式 QA：中文语料上的抽取准确率 |
| **RAGTruth-processed（span 标注）** | `wandb/RAGTruth-processed`（原论文人工 span 标注版） | MIT | hf-mirror.com | `data/test-00000-of-00001.parquet`（3,882,700 B / 2,700 行） | **span 级**幻觉标注复现（Evident Conflict / Baseless Info） |
| **SQuAD 2.0 validation 全量** | 同上 SQuAD | CC BY-SA 4.0 | hf-mirror.com | 全量 11,873 条（`squad_v2_full.jsonl`） | 全量质量基线（分批跑，退避+断点续跑） |

下载命令（可复现）：

```bash
curl -sL -o evalbench/datasets/public/raw/squad_v2_validation.parquet \
  'https://hf-mirror.com/datasets/rajpurkar/squad_v2/resolve/main/squad_v2/validation-00000-of-00001.parquet'
curl -sL -o evalbench/datasets/public/raw/ragtruth_qa.parquet \
  'https://hf-mirror.com/datasets/flowaicom/RAGTruth_test/resolve/main/data/qa-00000-of-00001.parquet'
curl -sL -o evalbench/datasets/public/raw/cmrc2018_validation.parquet \
  'https://hf-mirror.com/datasets/hfl/cmrc2018/resolve/main/data/validation-00000-of-00001.parquet'
```

选择 hf-mirror 的原因：本机直连 Hugging Face / GitHub raw 均超时（旧 SOURCES.md 记录的失败），
hf-mirror 实测 HTTP 200 / 1.1s。**镜像只换传输通道，数据集内容与官方一致**（parquet 行数与仓库 README 声明一致）。

---

## 2. SQuAD v2 → evalbench 样本

适配脚本：`build_samples.py`。抽样固定 `seed=42`，可答/不可答各 30 条（共 60），
不跑全量 11,873 条——API 配额有限，小规模分层子集是方法验证，不是质量基线。

| evalbench 字段 | 映射规则 |
|---|---|
| `id` | `SQ_` + SQuAD 原始 id |
| `task_category` | 有答案 → `grounded_extraction`；无答案 → `negative_rejection` |
| `question` | SQuAD `question` |
| `passages` | SQuAD `context` 作为单个 positive passage，doc_id 固定 `S1` |
| `key_facts` | 有答案 → 多标注者多数投票的 span；无答案 → 空 |
| `expected_behavior` | 有答案 → `answer`；无答案 → `abstain` |

**不可答题的价值**：SQuAD v2 的不可答样本不是"问题与段落无关"，而是"段落主题相关但没有答案"——
这正是 RAG 系统最容易硬凑答案的场景，比随机无关段落更能测拒答纪律。

**已知偏差**：SQuAD 是英文抽取式 QA，而项目默认提示词（v1/v2/v3）都要求中文输出。
评分的 `answer_accuracy` 用归一化字符串匹配，英文答案能匹配；但 `answer_economy` 的
"≤200 字"约束在英文下过松。报告里按维度分别解读，不把跨语言分数混为一谈。

---

## 2.5 CMRC 2018 → 中文抽取式 QA

适配脚本：`build_samples.py --dataset cmrc --cmrc-n 60 --seed 42`。
CMRC 字段（`context` / `question` / `answers.text`）与 SQuAD 完全同构，直接复用同样的映射：

| evalbench 字段 | 映射规则 |
|---|---|
| `id` | `CMRC_` + 原始 id |
| `task_category` | 全部 `grounded_extraction`（CMRC 2018 **没有不可回答样本**） |
| `passages` | `context` 作单个 positive passage，doc_id `C1` |
| `key_facts` | 多标注者多数投票的 span |
| `expected_behavior` | 全部 `answer` |

**与英文集互补**：SQuAD v2 有一半不可答题可测拒答，CMRC 全可答只测抽取准确率。
因此中文集**不覆盖拒答维度**，报告里明确标注，不能拿 CMRC 的分数说"中文拒答也好"。

**提示词**：中文集用 `v3_structured`（项目为中文语料设计的结构化提示词），
英文集用 `v4_en_grounded`（本次新增的语言适配版）。两者提示词版本不同，
所以中英 accuracy 只能作为"各自语言下的表现"分别陈述，**不能归因于语言差异**——
提示词、语料、任务构成都是混杂变量。要做纯语言对照需固定提示词版本另跑（见报告局限）。

---

## 3. RAGTruth → 忠实度专项

**关键诚实声明**：RAGTruth 的 `score`（1=忠实 / 0=有幻觉）是对**数据集自带 response** 的标注，
不是我们系统生成答案的金标。因此：

- ❌ 不把它的 `response` 当 `reference_answer` 或 `key_facts`——那等于拿别人的答案考我们；
- ❌ 不把它做成 evalbench 常规样本——`scoring.py` 的 `answer_accuracy` / `faithfulness`
  语义会被误用；
- ✅ 只用它的 `question` + `source_info.passages` 作为**题干与证据**，让我们系统生成答案，
  再用机械规则做**幻觉检测**：生成答案里的数字 / 专名若不在 passages 中，即为疑似幻觉。
  这与 RAGTruth 的核心考察点（答案是否超出给定证据）一致，但判定是确定性字符串规则，
  不是 LLM-as-judge，也不复现论文的 span 级标注。

字段来源（实测确认）：

| RAGTruth 字段 | 内容 | 用途 |
|---|---|---|
| `source_info` | JSON 字符串，含 `question` 与 `passages`（"passage 1:..." 格式） | 题干 + 证据 |
| `response` | 数据集自带的回答 | **不用作金标**，仅在对拍时作参考 |
| `score` | 1=该 response 忠实 / 0=有幻觉 | 用于分层（选 score=0 的样本作"高幻觉风险"子集） |
| `source` | 全部为 `MARCO` | 记录来源 |

评估脚本：`evaluate_faithfulness.py`（独立脚本，不进 evalbench 常规 run）。

---

## 4. 三态声明

| 项 | 状态 | 说明 |
|---|---|---|
| 公开集下载与格式适配 | **已实现** | 本目录 raw parquet + build_samples.py |
| SQuAD 生成质量评测 | **已实现** | 经 evalbench runner 实跑 |
| RAGTruth 忠实度专项 | **已实现** | 独立脚本，机械幻觉检测 |
| span 级幻觉标注复现 | **未做** | 需要复现论文的人工标注口径，本评估用机械近似替代 |
| 全量 11,873 条 SQuAD | **未做** | API 配额限制；60 条分层子集无统计显著性，不作基线 |

---

## 5. 许可合规

- SQuAD 2.0 / CMRC 2018 均为 CC BY-SA 4.0：本用途为学习/求职演示，非商业；引用需署名（见报告数据来源段）。
- RAGTruth MIT：允许使用与修改，需保留许可声明。
- **若项目转为商业用途**，需重新核对三个数据集许可义务；旧 SOURCES.md 对 RGB 的非商用提醒仍有效。

---

## 6. 限流退避：长跑评估的正确姿势

网关的 `Cluster TPM rate limit exceeded` 是**分钟级**窗口，runner 内置的短退避不够。
用 `evalbench/run_with_backoff.py` 自动处理：失败就等待后重跑，
已成功样本走缓存（runner 只对 `status=ok` 写缓存），因此每轮只补失败项，直到全绿。

```bash
python evalbench/run_with_backoff.py --label public-cmrc-deepseek-v3 \
  --max-rounds 8 --wait 30 --factor 1.5 --cap 120 -- \
  python evalbench/run_eval.py --dataset public/cmrc2018_sample_60.jsonl \
    --models deepseek-v4.1-flash-1017 --prompts v3_structured --params B --quiet
```

每轮产生独立 run_id、失败轮也保留（负结果可查）；全部补齐后落盘
`evalbench/out/<label>_backoff.json`，含所有轮次的 run_id 与最终汇总。
**不要**手动反复敲 `run_eval.py` 碰运气——退避脚本的等待是确定性的、可复现的。

---

## 7. 全量基线与 span 级标注复现（2026-11-18 追加）

### 7.1 全量 SQuAD 11,873 条

`build_samples.py --full --dataset squad` 生成全量样本（不抽样、保持原始顺序，
可答 5,928 / 不可答 5,945）。`run_full_baseline.py` 分批跑：

```bash
python evalbench/run_full_baseline.py --label squad-full-A \
  --dataset public/squad_v2_full.jsonl --batch 400 \
  --models deepseek-v4.1-flash-1017 --prompts v4_en_grounded --params A \
  --wait 30 --factor 1.6 --cap 300 --max-retries 15
```

**指数退避设计**：`wait × factor^attempt` 再叠加 ±20% 抖动（防多进程同频重试），
单次上限 `--cap=300s`。每批失败项靠"runner 只对 ok 写缓存"补齐，
进度写 `out/full_baseline_progress.json`（已完成的 offset + 累计 run_id），
Ctrl+C 后重跑同一命令从断点续上。批次 run 全部保留，不删历史。
实测首批 400 条首轮 397 ok / 3 rate_limited，退避后补齐——机制有效。

### 7.2 RAGTruth span 级标注复现（LLM 裁判破例）

**数据源**：`wandb/RAGTruth-processed` test（2,700 条，含人工 span 标注），
其中 QA 子集 900 条、160 条有 span。标注三类：
`Evident Baseless Info`(156) / `Subtle Baseless Info`(49) / `Evident Conflict`(30)。

**方法学死结与破例**：
- 确定性规则法（`evaluate_span_hallucination.py`）：句子级锚点比对 + IOU 对拍，
  实测 precision 0.061 / recall 0.25 / **F1 0.098**——语义型人工标注无法用字符串规则复现。
- 项目主口径明确"不用第二个 LLM 当裁判"（`core/scoring.py`、`README.md`）。
- 用户决定**破例**：`span_llm_judge.py` 用 deepseek-v4.1 做 span 专项裁判，
  **严格隔离**：不进 evalbench 主 run、不参与任何收敛、产物只在 `datasets/public/`。
  裁判看不到人工标签（防泄露假绿）。

**裁判工程坑（都已修）**：
1. deepseek 是推理模型，默认思考过程吃掉 1024 token 预算 → JSON 输不完，
   8 条里 6 条 `judge_failed`。修法：`thinking_fields(disabled=True)` + `max_tokens=2048`
   （该字段经 2026-11-14 网关实测 reasoning_tokens 55→0），失败率降到 2/8。
2. 分句正则把 `$23.70` 的小数点当句末 → 残句。修法：小数点换单字符占位再切句。
3. 句首虚词（Then/Fold）被当专名 → 误报。修法：`_SENT_STARTERS` 过滤。
4. 人工 span 是短语级(~75字符)、裁判是整句级(~205字符) → IOU 天然低。
   修法：`--refine` 让规则缩边界到句内含证据外锚点的子句（判定仍由 LLM 做）。

**结果**（QA 子集 50 条，43 条裁判成功）：

| 方法 | precision | recall | F1 |
|---|---|---|---|
| 确定性规则 | 0.061 | 0.250 | 0.098 |
| LLM 裁判（整句级） | 0.188 | 0.375 | 0.250 |
| LLM 裁判 + refine | 0.217 | 0.417 | **0.286** |

**诚实定位**：即使 LLM 裁判 + refine，F1 也只有 0.286，**不是论文级 span 复现**——
论文是人工标注，无法用脚本替代。本评估只证明"LLM 裁判明显优于规则法"，
且提供可复现的 span 级对拍框架。样本量小、句子级粒度、裁判自身误差都要一起读。
