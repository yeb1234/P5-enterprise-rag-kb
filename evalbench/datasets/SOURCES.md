# 公开测试集调研与选用依据

调研日期：2026-11-12 ｜ 用途：为 `evalbench/` 真实 API 评估工作台选择任务类别、指标口径与参数基线。
**性质**：网络公开资料检索 + 官方仓库 README 核对；以下来源均为公开可访问页面。

---

## 1. 候选基准清单

| 基准 | 来源 | 许可证 | 覆盖能力 | 推荐评测设定 | 结论 |
|---|---|---|---|---|---|
| **RGB**（Benchmarking LLMs in RAG） | [GitHub chen700564/RGB](https://github.com/chen700564/RGB/blob/master/readme.md) · [论文 arXiv:2309.01431](https://arxiv.org/abs/2309.01431) | **CC BY-NC-SA 4.0，仅非商业** | 噪声鲁棒性、负面拒绝、信息整合、反事实鲁棒性 | `--temp 0.2`、`--noise_rate 0.6`、`--passage_num 5`；指标 `all_rate` / `reject_rate` / `ED` / `CR` | ✅ **选用（任务框架 + 参数基线）** |
| **SQuAD 2.0** | [rajpurkar.github.io/SQuAD-explorer](https://rajpurkar.github.io/SQuAD-explorer/) | CC BY-SA 4.0（可商用） | 抽取式阅读理解 + **不可回答样本须弃答** | EM / F1；归一化（去冠词、标点、大小写）；dev 集 11,873 条，可答/不可答约 1:1 | ✅ **选用（EM/F1 口径 + 弃答判定）** |
| RGB 衍生数据 `zh_refine.json` | 同上仓库 `data/` | 同上（非商用） | 中文 RAG 四能力 | 2024/03 修订版，修正了正负文档与答案 | ⚠️ **未直接采用**，见 §3 |
| CRUD-RAG / CMMLU / C-Eval | 通用检索 / 中文通用能力基准 | 各异 | 通用能力，非 RAG 生成环节 | — | ❌ **落选**：与「给定检索片段 → 生成/拒答」这一被测环节不对齐 |
| BEIR / MS MARCO | 检索侧基准 | 各异 | 召回排序 | NDCG@10 / MRR | ❌ **落选**：本项目此轮评估对象是生成环节，检索排序另有 `webapp/out/eval_report.json` 实测 |

## 2. 选用依据（为什么是这两个）

1. **任务对齐**：RGB 的四项能力里，「负面拒绝」正好对应本项目已实现的「检索为空诚实拒答」，
   「噪声鲁棒性」对应项目实测中「含关键词但语义不相关」的干扰文档，
   「信息整合」对应来源追溯的多分块引用。不是硬套基准，是能力本来就在系统里。
2. **指标可判定**：SQuAD 的 EM/F1 提供客观、可复现的字符串口径，避免用模型当裁判带来的漂移；
   RGB 的 `all_rate` / `reject_rate` 提供按能力分组的通过率口径。两者都不依赖第二个 LLM 打分。
3. **许可证可用**：本项目是学习 / 求职演示用途，属 RGB 允许的**非商业**范围；
   SQuAD 2.0 的 CC BY-SA 4.0 更宽松。**若转为商业用途，RGB 部分需取得正式授权或替换。**
4. **成本可控**：官方全量数据集（SQuAD dev 11,873 条 / RGB 数百条）在本项目的
   RPM 50 / 5h 墨点配额下不现实；采用小规模分层子集做方法验证，如实标注规模。

## 3. 数据获取情况（诚实声明）

- 已通过 WebFetch 核对 [RGB 官方 README](https://github.com/chen700564/RGB/blob/master/readme.md)，
  确认参数、指标名、数据文件与许可证原文。
- **尝试下载** `https://raw.githubusercontent.com/chen700564/RGB/master/data/zh_refine.json`
  在 25s 内超时（0 字节），SQuAD dev JSON 体积约 40MB 亦不适合在此环境拉取。
- 因此 `samples_v1.jsonl` 是**依据上述公开基准的任务定义与评测口径自建的中文小规模样本**，
  结构对齐 RGB（`positive` / `noisy` passages、`expected_behavior=abstain`）与 SQuAD 2.0（`key_facts` 参考结果）。
  **它不是官方数据集的抽样，不得称为「在 RGB / SQuAD 上跑出的分数」。**
  所有对外结论只描述「自建样本上的表现」，并标注 `dataset=self_built_v1`。

## 4. 参数配置依据

| 参数 | 来源 | 设定 |
|---|---|---|
| `temperature=0.2` | RGB 官方 `evalue.py --temp 0.2` | 参数组 A（官方推荐基线） |
| `noise_rate=0.6` | RGB 官方 `--noise_rate 0.6`；`noise_rate=1` 为纯负面拒绝场景 | 参数组 A/B/C 共同；`negative_rejection` 类强制 1.0 |
| `passage_num=5` | RGB 官方默认 `--passage_num 5` | 参数组 A/B；收敛后 C 改为 3 |
| `temperature=0` | 可复现性实践：[Setting Up a Fair Test Environment](https://www.aitoolsoasis.com/en/tutorial/ai-model-comparison-series-gpt-vs-claude-vs-gemini-vs-deepseek-2-setting-up-a-fair-test-environment-for-ai-model-comparison)、[Indic ASR evaluation（reproducibility checklist）](https://yavchn.parkscomputing.com/story/hn/48578447) | 参数组 B/C：贪心解码降低 run-to-run 方差 |
| 固定 `max_tokens`、pin 精确 model id、记录 prompt hash、每组合多次观测 | 同上两篇 + [Non-Determinism（snapshot everything）](https://tamaton.com/blog/ai-agents/non-determinism-why-your-agent-won-t-do-the-same-thing-twice) | 写入 `config_snapshot.json` 与缓存键 |
| 显式拒答出口（给模型一个字面 token 可发） | [Temperature does not stop hallucinations](http://patrickgawron.com/articles/temperature-does-not-stop-hallucinations/) | 提示词 v2/v3 引入 `INSUFFICIENT` |
| 归一化后再评分 | SQuAD 官方 `evaluate-v2.0.py` 口径 | `scoring.py::_normalize()` |

> 反例提醒：上述资料也指出 `temperature=0` 并非万能（贪心可能锁死首个 token、
> 共享 GPU 批处理仍会漂移）。因此本工作台把「同一组合多次观测」和
> 「prompt + 参数 + 模型版本哈希入快照」作为硬要求，而不是只信一个温度值。

## 5. 与项目既有评测体系的关系

`eval/` 是**冻结的离线占位体系**：真实 API 字段一律 `__TBC__`、
`real_api` 档位被契约门禁阻断、占位结果 `comparable=false` / `NOT_COMPARABLE`。
`evalbench/` 是**新增的平行真实 API 工作台**，不修改 `eval/` 任何对象、不改动
`run_id=RUN-095a2a4a40c6`、不据此冻结 `eval/` 的质量基线。两套体系独立，互不替代。
