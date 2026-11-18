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

下载命令（可复现）：

```bash
curl -sL -o evalbench/datasets/public/raw/squad_v2_validation.parquet \
  'https://hf-mirror.com/datasets/rajpurkar/squad_v2/resolve/main/squad_v2/validation-00000-of-00001.parquet'
curl -sL -o evalbench/datasets/public/raw/ragtruth_qa.parquet \
  'https://hf-mirror.com/datasets/flowaicom/RAGTruth_test/resolve/main/data/qa-00000-of-00001.parquet'
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

- SQuAD 2.0 CC BY-SA 4.0：本用途为学习/求职演示，非商业；引用需署名（见本报告数据来源段）。
- RAGTruth MIT：允许使用与修改，需保留许可声明。
- **若项目转为商业用途**，需重新核对两数据集许可义务；旧 SOURCES.md 对 RGB 的非商用提醒仍有效。
