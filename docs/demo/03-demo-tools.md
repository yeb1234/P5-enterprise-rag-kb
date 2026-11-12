# 第三部分 · 配套工具

**物料编号**：`DEMO-KBQA-EVAL-001` ｜ 全部纯标准库、离线、零密钥，Windows / macOS / Linux 均可。
工作目录统一为项目根目录；路径含中文也可以，直接用下面的相对命令即可（不需要写 `.bat`）。

---

## 1. 工具清单

| 工具 | 路径 | 用途 | 一键命令 | 输出 |
|---|---|---|---|---|
| 场景事实源 | `demo/scenario.json` | 场景卡、术语、常量、9 步、话术行、预期字面量 | 被下面三个工具读取 | — |
| **一键演示 runner** | `demo/demo_runner.py` | 自动跑完 S1–S9，打印【话术/操作/预期/实际/对齐】 | `python demo/demo_runner.py` | `demo/out/demo_run_result.json` |
| **逐用例查看器** | `demo/inspect_record.py` | 回答「这条记录到底长什么样」 | `python demo/inspect_record.py C-001` | 终端人类可读摘要 / `--json` 结构化 |
| **一键自测** | `demo/selftest.py` | 校验文档、话术、工具、两端对齐 | `python demo/selftest.py` | `demo/out/selftest_report.json` |
| 底层评测链路 | `eval/run_eval.py` | 26 用例、双跑、闸门、manifest | `python eval/run_eval.py` | `eval/results/<run_id>/` |
| 对比 / 回归工具 | `eval/compare_runs.py` | 按 `case_id` 配对两次 run | 见第 3 节 | `comparison.json` |
| 14 个版本化对象 | `eval/config/objects/*.json` | 配置、模板、阈值、基线的版本化载体 | 由 registry 加载 | `eval/config/registry.json` |

**14 个对象标识（`@0.1.0`，演示时可逐个指给观众）**：
`OBJ-EVAL-SPEC`、`OBJ-ADAPTER-CONTRACTS`、`OBJ-RUN-PROFILE`、`OBJ-CHUNK-PARAMS`、
`OBJ-EMBEDDING-CFG`、`OBJ-LLM-MODEL-CFG`、`OBJ-RETRIEVAL-CFG`、`OBJ-GENERATION-CFG`、
`OBJ-PROMPT-RAG-ANSWER`、`OBJ-PROMPT-SCORING`、`OBJ-SCORING-RULES`、`OBJ-DATASET`、
`OBJ-THRESHOLDS`、`OBJ-BASELINE`。
每个对象独立含 `object_id / version / effective_scope / depends_on / content / rollback_baseline / changelog`。

## 2. 单一事实源（为什么文档不会和工具对不上）

```
demo/scenario.json  ──►  demo_runner.py（演示侧：9 步、话术、预期）
                    ──►  selftest.py  （校验侧：文档/话术/工具逐项核对）
                    ──►  01/02/03/04 四份文档（人读，数字与话术取自同一文件）
```

- 9 句话术原文存在 `scenario.json.talk_lines`，runner 逐字打印，话术文档逐字收录，自测逐字比对。
- 所有关键数值（26 条 / 14 个 / 35 个 / 26 项 / 9 项 / 23 / 2 / 1）集中在 `constants`，
  文档与 runner 都从这里取，自测再反向检查文档是否包含这些字面量。
- **演示侧**产物 `demo/out/demo_run_result.json` 与**验证侧**产物
  `eval/results/<run_id>/manifest.json`、`summary.json`、`records.jsonl` 由同一批命令产生，
  自测逐字段比对，这就是「两端对齐」。

## 3. 命令速查（可直接复制）

```bash
# 一键演示（推荐，约 10 秒跑完，台本按 12 分钟讲）
python demo/demo_runner.py

# 只排练某一步（需先跑过全量批次）
python demo/demo_runner.py --step S6

# 一键自测
python demo/selftest.py

# 底层命令：单独复现某一步
python eval/run_eval.py --check-contracts
python eval/run_eval.py --list-objects
python eval/run_eval.py
python demo/inspect_record.py C-001
python demo/inspect_record.py C-020 --json

# 对比实验（S8）
python eval/run_eval.py --set OBJ-RETRIEVAL-CFG.content.top_k=3 --label exp-topk3
python eval/compare_runs.py eval/results/<基线run目录> eval/results/<变体run目录>

# 真实档位门禁（应被阻断，退出码 2）
python eval/run_eval.py --profile real_api
```

> `<基线run目录>` 与 `<变体run目录>` 填 `eval/results/` 下实际目录名；
> 一键演示的 S8 会自动填好，不需要手工查。

## 4. 逐用例查看器输出示例（`python demo/inspect_record.py C-001`）

```
用例 C-001 | 套件=smoke | 场景=normal | 状态=ok（预期 ok）
run_id=RUN-095a2a4a40c6  comparable=false
六链路：
  1. chunk             ok       对象版本 8 个 | 告警 0 | 错误 否
  2. embed             ok       对象版本 9 个 | 告警 1 | 错误 否
  3. retrieve          ok       对象版本 8 个 | 告警 0 | 错误 否
  4. assemble_prompt   ok       对象版本 8 个 | 告警 0 | 错误 否
  5. generate          ok       对象版本 9 个 | 告警 1 | 错误 否
  6. score             ok       对象版本 10 个 | 告警 0 | 错误 否
分块=4  召回=4  recall_at_k=1.0  mrr_at_k=1.0
...
评分状态=ok  composite_score=None
复现要素：config=65f1c17c7b0… code=c9ca4d21de88… data=a5bceccc657a…
```

加 `--json` 输出结构化字段，供自测和两端对齐使用；`--run RUN_ID` 可指定某次运行。

## 5. 产物说明

| 文件 | 内容 | 谁产生 |
|---|---|---|
| `demo/out/demo_run_result.json` | 9 步的话术、操作、预期、实际、逐项检查、对齐结论 | `demo_runner.py` |
| `demo/out/selftest_report.json` | 16 项自测的通过状态与证据 | `selftest.py` |
| `eval/results/<run_id>/records.jsonl` | `eval-record/v0.1` 逐用例 26 条 | `eval/run_eval.py` |
| `eval/results/<run_id>/manifest.json` | 复现四要素、对象版本、环境、契约门禁 | `eval/run_eval.py` |
| `eval/results/<run_id>/summary.json` | 状态分布、闸门、verdict | `eval/run_eval.py` |
| `eval/results/<variant>/comparison.json` | 逐用例配对差异、守护栏结果 | `eval/compare_runs.py` |

`eval/results/` 与 `demo/out/` 已在 `.gitignore` 中忽略产物目录规则；文档与脚本纳入版本管理。

## 6. 环境要求与常见问题

| 项 | 要求 |
|---|---|
| Python | 3.10+（实测 3.13.14） |
| 第三方依赖 | 无，`eval/requirements.txt` 已声明仅标准库 |
| 网络 / 密钥 | 均不需要；`network_calls=0`、`credentials_loaded=false` |
| 中文路径 | 支持；不要另写 `.bat`/`.ps1`，直接用 `python` 命令 |

| 问题 | 处理 |
|---|---|
| `找不到 run` | 先 `python eval/run_eval.py` |
| 台本出现 `✗ 存在不一致` | 看该步 `!` 行的预期/实际；再跑 `python demo/selftest.py` 定位 |
| `real_api` 退出码 2 | 预期行为：26 项 blocking 契约字段仍为 `__TBC__` |
| 想换演示用例 | 改 `demo/scenario.json` 的 `featured_cases`，然后重跑自测（文档数字需同步） |
| 想加一步 | 同时在 `scenario.json.steps`、`demo_runner.py`、话术文档、演示文档补，再跑自测 |

## 7. 明确不追求

不做 Web UI、不做真实 API 适配、不做并发与压测、不做质量基线。
工具的目标是**让 12 分钟演示不出意外、且每个数字都能当场核对**。
