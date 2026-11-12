# evalbench · 真实 API 评估工作台（Demo 版）

**定位**：基于项目已接入的 Discovery 网关（14 个可用模型）搭建的**平行评估体系**，
用多组提示词与参数做对比评估，逐轮收敛配置，每轮独立 Git 分支可追溯，最终产出一个可演示的结果展示页。

> **边界（务必先读）**：`eval/` 是冻结的离线占位体系（真实 API 字段 `__TBC__`、
> `real_api` 被契约门禁阻断、结果 `NOT_COMPARABLE`）。`evalbench/` **不修改** `eval/` 的任何对象，
> 不改动 `run_id=RUN-095a2a4a40c6`，也不据此冻结 `eval/` 的质量基线。两套体系独立。
> 样本是依据公开基准任务定义**自建**的 12 条中文数据，不是 RGB / SQuAD 官方数据抽样。

---

## 1. 快速开始

```bash
# ① 离线冒烟（零网络、零密钥，验证评估链路；结果 comparable=false）
python evalbench/run_eval.py --offline --label smoke --models minimax-m3 \
    --prompts v1_baseline --params A

# ② 多模型兼容性探测（真实 API，落 out/models_probe.json）
python evalbench/probe_models.py

# ③ 一轮真实评估（密钥从根目录 .env 读取，不入库）
python evalbench/run_eval.py --label v1-baseline \
    --models minimax-m3,kimi-k2.6,deepseek-v4.1-flash-1017 \
    --prompts v1_baseline --params A

# ④ 前后对照
python evalbench/compare.py --a RUN-xxx --b RUN-yyy --label v1_vs_v2

# ⑤ 生成展示页
python evalbench/report.py --final-run RUN-xxx
# 打开 evalbench/out/index.html

# 自检
python -m unittest discover -s evalbench/tests -t .
```

`--samples S01,S04` 指定样本子集，`--limit N` 取前 N 条，`--repeats N` 多次观测，
`--quiet` 关闭逐条日志。相同组合的结果**按缓存键复用**，重跑只补缺失/失败项，不重复烧额度。

## 2. 目录与资产

```
evalbench/
  datasets/
    SOURCES.md            公开基准调研：来源 / 许可证 / 推荐设定 / 选用依据 / 获取情况
    samples_v1.jsonl      12 条样本 + 参考结果（4 类任务 × 3 条）
  prompts/
    v1_baseline.md        朴素基线
    v2_grounded.md        grounding + 显式拒答出口 INSUFFICIENT
    v3_structured.md      单行结构化 + few-shot + 引用编号
    CHANGELOG.md          版本改动、依据、收敛口径
  configs/
    params_A.json         RGB 官方推荐：temp 0.2 / passage_num 5 / noise_rate 0.6
    params_B.json         确定性贪心：temp 0（与 A 只差采样）
    params_C.json         收敛候选：temp 0 / passage_num 3 / max_tokens 768
    models.json           3 个入选模型 + 适配项 + 落选理由
    metrics.json          4 个评估维度 + 归一化口径 + 已知局限
  core/{runner,scoring,gitmeta}.py   执行 / 评分 / Git 快照
  runs/<run_id>/          config_snapshot.json + records.jsonl + summary.json
  iterations/comparisons/ 前后对照 JSON；iteration_log.md 逐轮收敛结论
  out/                    models_probe.json + dashboard.json + index.html（展示页）
  cache/responses.jsonl   按组合去重的真实响应缓存
```

### 任务类别与指标

| 任务类别 | 主指标 | 对应公开基准能力 |
|---|---|---|
| `grounded_extraction` | `answer_accuracy` | SQuAD 抽取式问答 |
| `noise_robustness` | `answer_accuracy` + `faithfulness` | RGB 噪声鲁棒性（noise_rate=0.6） |
| `negative_rejection` | `rejection_correctness` | RGB 负面拒绝（noise_rate=1）+ SQuAD 2.0 弃答 |
| `information_integration` | `answer_accuracy` | RGB 信息整合 |

四个维度（`answer_accuracy` / `rejection_correctness` / `faithfulness` / `format_compliance`）
**按类别和模型分别给结论**，不混成一个笼统总分。评分是确定性字符串匹配，不用第二个 LLM 当裁判。

## 3. Git 分支与版本对应

每个迭代版本独立分支，分支从上一轮分出，因此**每个分支自带到该轮为止的全部 run**：

| 分支 | 内容 |
|---|---|
| `main` | 项目基线 + 工作台骨架（资产、代码、调研、探测结果） |
| `eval/v1-baseline` | 第 1 轮：基线提示词 × 官方参数 × 3 模型 |
| `eval/v2-prompts` | 第 2 轮：提示词版本对比 → 收敛 |
| `eval/v3-params` | 第 3 轮：参数组对比 → 收敛 |
| `eval/v4-models` | 第 4 轮：最终配置 × 3 模型 + 模型差异结论 |
| `demo/v1` | 最终 Demo：展示页 + 完整说明 |

每个 `runs/<run_id>/config_snapshot.json` 记录 `branch` / `commit`、提示词与参数哈希、
样本哈希、模型、模式与时间戳，可直接把结果对应回代码版本。

## 4. 工程约束

- 密钥只从 `.env` 读取，**不写入任何版本化文件**；`.gitignore` 已排除 `.env`、产物与缓存大文件。
- 429 分两类：`rate_limit_exceeded` 指数退避 + 抖动重试；`quota_exceeded` 不重试。
  失败组合标 `rate_limited` / `quota_exceeded` / `http_error` 并落盘，`aggregate` 明确跳过，
  **绝不填造结果**；`--resume` 语义由缓存天然提供，重跑只补失败项。
- 离线模式 `comparable=false`，只用于验证链路，展示页与结论均将其隔离。
- 提示词、参数、指标、样本均为独立版本化文件；已产生 run 的版本不原地修改。

## 5. 已知限制

12 条自建样本无统计显著性；字符串匹配对同义改写不鲁棒；未做全局并发闸/令牌桶与压测；
未冻结质量基线。RGB 许可为 **CC BY-NC-SA 4.0（仅非商业）**，商业用途需另行授权或替换基准。
