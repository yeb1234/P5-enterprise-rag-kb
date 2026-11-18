# 公开评估集实跑报告

**日期**：2026-11-18 ｜ **分支**：`eval/public-benchmarks` ｜ **基线 commit**：`514d6d9`
**性质**：真实 API 调用（网关），`comparable=true`；所有 run 可复现，原始记录逐条落盘。

---

## 0. 结论先行

| 发现 | 数据 | 可信度 |
|---|---|---|
| ① 项目在**英文抽取式 QA** 上有实测能力 | SQuAD v2 可答题 accuracy **0.862**（字符串口径），人工复核上界 **0.967** | 中：60 条分层子集，无统计显著性 |
| ② **拒答几乎完美** | 不可回答题 `rejection_correctness` = **1.000**（28/28，两提示词均满分） | 中：SQuAD v2 的不可答是"相关但无答案"，正是难例 |
| ③ **提示词语言必须匹配问题语言** | v4 英文适配 0.862 vs v2 中文 0.621，**+0.241**（同一模型、同 57 条样本） | 高：同批样本公平对照，差异远大于噪声 |
| ④ 生成答案**忠实度良好** | RAGTruth 高风险组疑似幻觉率 21.4%，对照组 0% | 低：机械检测器，29 条样本，非论文 span 标注 |
| ⑤ **中文抽取准确率被测出** | CMRC 2018 字符串口径 **0.767**，人工复核上界 **0.983**（14 条失分 13 条是假阴性） | 中：60 条中文子集 |
| ⑥ **中文同义改写的假阴性比英文更严重** | 中文 13/14 失分为措辞差异，英文 3/4；字符串口径严重低估中文能力 | 中：人工逐条复核，见 `cmrc_manual_review.json` |
| ⑦ **首次调用有 TPM 限流** | 首轮 60 条中 4 条 `rate_limited`，重跑补齐后 60/60 OK；中文集首轮 5 条限流，退避脚本 30s 后补齐 | 事实：记录可查 |

**最重要的一条**：此前项目所有质量结论都基于**自建 12 条样本**（旧 `datasets/SOURCES.md` 已诚实声明"不是官方抽样"）。本报告首次用**真实公开集**验证，补上了这个缺口——但规模是"方法验证级"，**不构成质量基线**，也不得对外说成"在 SQuAD/RAGTruth 上达到 X 分"而不 explanatory 限定条件。

---

## 1. 数据集与来源

| 数据集 | 来源 | 许可 | 下载通道 | 规模 |
|---|---|---|---|---|
| SQuAD 2.0 validation | `rajpurkar/squad_v2` | CC BY-SA 4.0 | hf-mirror.com（国内镜像） | 11,873 条，取 60 条分层子集 |
| RAGTruth test / qa | `flowaicom/RAGTruth_test`（原 ParticleMedia/RAGTruth，论文 arXiv:2305.11747） | MIT | hf-mirror.com | 900 条，取 30 条两组对照 |
| CMRC 2018 validation | `hfl/cmrc2018`（中文机器阅读理解，CLUE 基准） | CC BY-SA 4.0 | hf-mirror.com | 3,219 条，取 60 条中文子集 |

选择理由：任务对齐（给定 passages → 回答/拒答）、评分客观（字符串/规则，不靠第二个 LLM）、许可清晰。
旧 SOURCES.md 记录的"GitHub raw / HF 直连超时"问题，通过 hf-mirror 解决（实测 HTTP 200 / 1.1s）。

抽样：固定 `seed=42`，SQuAD 可答/不可答各 30 条，交错排列；RAGTruth 按原 `score` 标签分两组各 15 条。
适配脚本与逐条映射规则见 `datasets/public/SOURCES.md`。

---

## 2. 方法

- **评估对象**：`deepseek-v4.1-flash-1017`（项目默认模型），参数组 B（`temp=0 / passage_num=5 / max_tokens=512`）。
- **提示词对照**：
  - `v2_grounded`：项目既有中文提示词（规则 grounding + `INSUFFICIENT` 出口）
  - `v4_en_grounded`：**本次新增**的英文适配版（同规则，显式要求"与问题同语言、沿用片段原文措辞"）
- **评分口径**：沿用 `METRICS@1.2.0`，不修改评分器（冻结语义）。
  `answer_accuracy` = 归一化后 key_facts 命中率；`rejection_correctness` = 应拒答/应回答的判定正确率；
  `faithfulness` / `format_compliance` / `answer_economy` 为辅助维度。
- **忠实度专项**：独立机械检测器——生成答案中的数字/专名若不在 passages 中即标记"疑似幻觉"。
  **不是** RAGTruth 论文的 span 级人工标注，也不复用其 response 作金标（见 §4 局限）。

---

## 3. 结果

### 3.1 SQuAD v2 生成质量（主结果）

公平对照：同一模型、相同 57 条有效样本（首轮限流的样本重跑补齐后对齐）。

| 提示词 | 样本数 | grounded_extraction accuracy | 不可答拒答正确率 | 平均延迟 |
|---|---|---|---|---|
| **v4_en_grounded** | 29 可答 + 28 不可答 | **0.8621** | **1.000** | 2,240 ms |
| v2_grounded（中文提示） | 29 可答 + 28 不可答 | 0.6207 | 0.9655 | 1,524 ms |
| 差值 | — | **+0.2414** | +0.0345 | +716 ms |

完整 run（v4，60/60 成功）：`RUN-a7a73b648d6b`，overall **0.9334**。
首轮 `RUN-5d5cd988fdd2` 因 4 条 TPM 限流为 56/60、overall 0.9445——**两个数字都保留**，
不挑好看的那个；以补齐后的 60/60 为准。

`overall` 为何高于 accuracy：overall 是 4 类等权平均，其中 `negative_rejection` 的
主维度（拒答）满分 1.0 拉高了均值。看能力要分维度看，不要只看 overall。

### 3.2 accuracy 失分的人工复核（重要）

字符串口径下 4 条判 0，人工逐条核查：

| 金标 | 模型答案 | 判定 |
|---|---|---|
| Slave craton in northwestern Canada | Northwestern Canada | 缺限定词，部分正确 |
| there are **now** numerous such communities... | there are numerous such communities... | 仅差一个副词，语义正确 |
| capturing three traders and killing 14 people... | Three traders were captured and 14 Miami people... killed | **语义完全正确**，纯改写 |
| Ilkhanate | Reaching the highest-post in the government | **真错**（答非所问，原文证据支持 Ilkhanate） |

**结论**：字符串口径 accuracy 0.862 是**保守下界**；人工复核后真实正确率约 **29/30 ≈ 0.967**。
报告不擅自改分（评分口径冻结），但两个数字都给出，避免把评分器局限当成模型缺陷。

### 3.3 RAGTruth 忠实度专项

| 分组 | 有效样本 | 疑似幻觉率 | 平均疑似实体数 |
|---|---|---|---|
| faithful（原 score=1 对照组） | 14 | **0%** | 0 |
| hallucinated（原 score=0 高风险组） | 14 | **21.4%** | 1.43 |

高风险组检出的疑似实体是 `open settings` / `google chrome` / `default web browser` 等
**证据中不存在**的操作步骤——与 RAGTruth 标注的幻觉类型一致。对照组 0% 说明检测器没有系统性误报。

**方法学插曲（负结果照报）**：初版检测器把模型按提示词输出的引用标记 `(P1, P3)` 当成"证据外专名"，
导致对照组疑似率虚高到 **100%**、高风险组反而 50%——完全反直觉。定位到是 `P\d+` 未排除，
修正后重算（不重复调 API）得到上表。这个坑已固化进脚本注释，避免重犯。

### 3.4 中文公开集 CMRC 2018（补齐中文语料空白）

**为什么加**：前 3 节全是英文集，只能说明项目在英文上的表现；项目本身是中文企业知识库，
缺中文语料实测。补 CMRC 2018 validation（CC BY-SA 4.0，3,219 条，取 60 条固定 seed=42），
全是可答题，中文提示词 `v3_structured`，同一模型 deepseek-v4.1-flash-1017。

| 指标 | 字符串口径 | 人工复核上界 | 说明 |
|---|---|---|---|
| 可答题 accuracy | **0.7667**（46/60） | **0.9833**（59/60） | 14 条判 0 中 13 条语义正确、1 条误拒答 |
| 误拒答率 | 1 条 `INSUFFICIENT` | — | 金标"上海轨道交通20号线"在片段中，模型却拒答 |
| 格式/忠实度/经济 | 1.000 / 1.000 / 1.000 | — | 辅助维度全过 |

**逐条复核可追溯**：`evalbench/out/cmrc_manual_review.json`（14 条逐条给出金标、答案、分类、理由）。
不修改冻结的 `METRICS@1.2.0` 评分——两个数字都报，说明字符串口径在中文上的系统性偏差。

**中英 accuracy 不可直接比较**：CMRC 用 v3 中文提示词、全可答；SQuAD 用 v4 英文提示词、含一半不可答题。
语料、提示词、任务构成都是混杂变量。0.767（中文）vs 0.862（英文）只能各自陈述，
**不能**说"中文比英文差 0.1"——那会把提示词和样本构成的差异误归因为语言能力。

**中文提示词对照（同 60 条，仅换提示词）**：

| 提示词 | 字符串 accuracy | 延迟 | 格式/忠实度/经济 |
|---|---|---|---|
| `v3_structured` | **0.7667** | 2,095 ms | 1.000 / 1.000 / 1.000 |
| `v2_grounded` | 0.7333 | 2,107 ms | 1.000 / 1.000 / 1.000 |
| 差值 | +0.0333 | −12 ms | 无差异 |

**这个 +0.033 要谨慎解读**：两个提示词在字符串口径下只差 2 条样本，且人工复核后
14 条失分里 13 条都是假阴性——提示词版本的真实差异被评分器噪声淹没。
结论只能是"**中文提示词版本对准确率没有可辨识影响**"，不能说 v3 比 v2 好。
这与英文集上 +0.241 的语言适配效应形成对照：语言匹配是强效应，中文提示词微调是弱效应。

**限流处理（本轮新增能力）**：中文集 v3 首轮 5 条、v2 首轮 4 条 `rate_limited`，
用 `run_with_backoff.py` 等待 30s 后自动重跑，已成功样本走缓存、只补失败项，
两轮均在第二轮 60/60 全绿。四个 run_id 都保留（含限流的失败轮），负结果可查。

---

## 4. 局限（必须与结果一起读）

1. **规模小**：SQuAD 60 条、RAGTruth 30 条，无统计显著性，误差棒远大于 0.02 的差异不可信。
2. **字符串评分假阴性**：不做同义改写识别（如上表 3/4 条失分），accuracy 是下界。
3. **语言耦合**：本轮增益来自提示词语言匹配，不是 RAG 架构改进。项目默认中文提示词在中文语料上的表现
   **未**因本轮改变——不要把 +0.241 说成"项目检索/生成能力提升了"。
4. **忠实度是机械近似**：检测器对中文、同义改写、数字格式差异都可能误判，只作相对对照，非论文复现。
5. **TPM 限流**：英文首轮 4 条、中文首轮 5 条失败，均已等待重跑补齐；网关 `Cluster TPM rate limit exceeded`，非项目缺陷。
6. **中文假阴性更严重**：CMRC 字符串 accuracy 0.767 vs 人工复核 0.983，差 0.217——
   中文同义改写/语序灵活让字符串匹配系统性低估，**任何中文分数必须以人工复核上界一起读**。
7. **未测检索环节**：本轮给的是"已给定 passages"的生成评估；检索排序（BM25/vector/nDCG）另有
   `docs/evaluation-report.md` 的历史实测，两者不混谈。
8. **中英不可直接比**：语料/提示词/样本构成三者同时变化，0.767 vs 0.862 不是纯语言能力差异。

---

## 5. 三态声明

| 能力 | 状态 | 依据 |
|---|---|---|
| 公开集下载 / 适配 / 实跑 | **已实现** | 本目录 raw parquet、脚本、5 个 run 目录 |
| 提示词语言匹配对英文集有效 | **已实测**（本轮） | 同 57 条公平对照 +0.241 |
| 全量 SQuAD 11,873 条的质量基线 | **未做** | API 配额限制；本轮是方法验证 |
| RAGTruth span 级幻觉标注复现 | **未做** | 用机械检测近似替代 |
| 中文公开集 CMRC 2018 实跑 | **已实测**（本轮补） | 60 条中文抽取，字符串 0.767 / 人工复核 0.983 |
| 限流自动等待退避 | **已实现** | `run_with_backoff.py`，中文集 5 条限流 30s 后补齐 |

---

## 6. 可复现命令

```bash
# 公开集下载（hf-mirror）
curl -sL -o evalbench/datasets/public/raw/squad_v2_validation.parquet \
  'https://hf-mirror.com/datasets/rajpurkar/squad_v2/resolve/main/squad_v2/validation-00000-of-00001.parquet'
curl -sL -o evalbench/datasets/public/raw/ragtruth_qa.parquet \
  'https://hf-mirror.com/datasets/flowaicom/RAGTruth_test/resolve/main/data/qa-00000-of-00001.parquet'

# 生成 60 条分层样本
python evalbench/datasets/public/build_samples.py --n-per-class 30 --seed 42

# 主结果：英文提示词（run_id 会因 nonce 不同，指标应一致）
python evalbench/run_eval.py --label public-squad-deepseek-v4 \
  --dataset public/squad_v2_sample_60.jsonl \
  --models deepseek-v4.1-flash-1017 --prompts v4_en_grounded --params B

# 中文提示词对照
python evalbench/run_eval.py --label public-squad-deepseek-v2zh \
  --dataset public/squad_v2_sample_60.jsonl \
  --models deepseek-v4.1-flash-1017 --prompts v2_grounded --params B

# 忠实度专项
python evalbench/datasets/public/evaluate_faithfulness.py --limit 15 \
  --model deepseek-v4.1-flash-1017 --sleep 0.5

# 中文公开集 CMRC（限流自动等待退避）
python evalbench/datasets/public/build_samples.py --dataset cmrc --cmrc-n 60 --seed 42
python evalbench/run_with_backoff.py --label public-cmrc-deepseek-v3 \
  --max-rounds 8 --wait 30 --factor 1.5 --cap 120 -- \
  python evalbench/run_eval.py --dataset public/cmrc2018_sample_60.jsonl \
    --models deepseek-v4.1-flash-1017 --prompts v3_structured --params B --quiet
```

> 注意：评估脚本会从项目根 `.env` 读网关配置；批跑前确保无冲突环境变量。
> 公开集 run 的 `dataset.origin` 字段会标为 `public_dataset`，与自建样本严格区分。

---

## 7. 数据来源与署名

- SQuAD 2.0: Rajpurkar et al., "SQuAD: 100,000+ Questions for Machine Comprehension of Text", EMNLP 2016. CC BY-SA 4.0.
- RAGTruth: Niu et al., "RAGTruth: A Hallucination Corpus for Developing Trustworthy Retrieval-Augmented Language Models", ACL 2024. MIT.
- CMRC 2018: Cui et al., "A Span-Extraction Dataset for Chinese Machine Reading Comprehension", EMNLP 2018. CC BY-SA 4.0.
- 通过 hf-mirror.com 镜像下载，仅变更传输通道，内容与原仓库一致（parquet 行数与仓库声明一致）。
