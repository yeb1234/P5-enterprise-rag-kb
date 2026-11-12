# 提示词版本记录

版本文件不可原地修改；新版本必须新建文件并在此登记。
每次评估的 `config_snapshot.json` 记录所用版本的 SHA-256，保证结果可对应。

| 版本 | 文件 | 相对上一版的改动 | 依据 | 状态 |
|---|---|---|---|---|
| v1 | `v1_baseline.md` | 初始朴素提示：资料 + 问题，无 grounding 约束、无拒答出口、无格式约束 | 基线，用于暴露「硬凑答案 / 复述资料 / 噪声泄漏」 | 基线 |
| v2 | `v2_grounded.md` | ① 明确「只能用资料」；② 引入字面拒答出口 `INSUFFICIENT`；③ 要求简短结论 + 引用编号；④ 禁止复述原文 | [显式 escape hatch 实践](http://patrickgawron.com/articles/temperature-does-not-stop-hallucinations/)；RGB 负面拒绝需可判定信号 | 待实测对照 |
| v3 | `v3_structured.md` | ① 单行、1~3 句、分号分隔；② 结论后统一 `（参考 Dxx）`；③ 加 1 条 few-shot；④ 显式禁止思考标签与推理过程 | 结构化输出 / few-shot 锚定（见 `datasets/SOURCES.md` §4）；便于流式过滤器与格式合规判定 | 待实测对照 |

## 收敛口径

- 每轮只改一个变量族（提示词轮固定参数组与模型；参数轮固定提示词与模型），避免归因混淆。
- 采纳条件：在 `negative_rejection` 的 `rejection_correctness` 不下降的前提下，
  整体 `answer_accuracy` 与 `faithfulness` 均不下降、且至少一项提升。
- 未达条件则保留上一版，并在 `iterations/iteration_log.md` 记录否决理由。
- 所有对照数据落在 `iterations/comparisons/`，结论必须引用具体 run_id。
