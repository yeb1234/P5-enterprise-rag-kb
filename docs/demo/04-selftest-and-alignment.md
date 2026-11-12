# 第四部分 · 自测结果与对齐说明

**物料编号**：`DEMO-KBQA-EVAL-001` ｜ 自测方式：`python demo/selftest.py`
**产物**：`demo/out/selftest_report.json`（结构化）、`demo/out/demo_run_result.json`（演示侧）
**本轮结论**：`DEMO_READY` — **16 / 16** 项检查通过；演示 **9 / 9** 步对齐；逐项核对 **42 / 42** 全过。

---

## 1. 自测方法

自测不是「再看一遍文档」，而是把三方拉到同一张桌子上对账：

```
demo/scenario.json（事实源）
   ├─ 校验 01/02/03/04 四份文档：数字、话术、用例 ID、对象 ID、命令、术语
   ├─ 实跑 demo_runner.py（演示侧）→ demo/out/demo_run_result.json
   ├─ 读 eval/results/<run_id>/（验证侧）→ manifest / summary / records.jsonl
   └─ 逐字段比对两端 → 输出 selftest_report.json
```

自测脚本会**再跑一次** `demo_runner.py` 验证幂等，并扫描 `demo/` 与 `docs/demo/`
确认没有真实 API 地址、鉴权、模型名或配额数值泄漏。
（扫描会排除 `demo/selftest.py` 自身，因为该文件包含检测正则的字面量，否则会自命中。）

## 2. 逐项检查结果（本轮实测）

| ID | 检查内容 | 结果 | 实测证据 |
|---|---|---|---|
| C01 | 四部分文档 + 索引齐备 | ✅ | 5 个文件存在且可读 |
| C02 | `scenario.json` 结构完整 | ✅ | 9 步 / 9 句话术 / 5 个边界用例 / 8 条术语 |
| C03 | 文档关键数值与术语齐全 | ✅ | 21 个字面量全部命中 |
| C04 | 话术关键数值与用例齐全 | ✅ | 15 个字面量全部命中 |
| C05 | 9 句话术与事实源逐字一致 | ✅ | T1–T9 全部逐字命中 |
| C06 | 引用用例 ID 均存在 | ✅ | 数据集 26 条，文档引用全部命中 |
| C07 | 引用版本化对象均存在 | ✅ | 注册表 14 个，文档引用全部命中 |
| C08 | 命令可复制且脚本存在 | ✅ | 8 条命令全部可在文档复制、脚本均存在 |
| C09 | 术语纪律与必需术语 | ✅ | 禁用词越界 0 处，必需术语缺失 0 个 |
| C10 | 一键演示 9/9 步对齐 | ✅ | 9/9 步，42/42 逐项，`OFFLINE_READY` |
| C11 | 演示侧 / 验证侧逐字段对齐 | ✅ | 7 个字段全部一致（见第 3 节） |
| C12 | 记录模式与边界状态收敛 | ✅ | 26 条记录模式错误 0 处；`comparable` 全为 false |
| C13 | 演示结果可复现 | ✅ | 双跑除 `generated_at` 外逐字节一致 |
| C14 | 无真实 API 细节泄漏 | ✅ | 扫描命中 0 处 |
| C15 | 查看器与原始记录一致 | ✅ | `C-001` 五个关键字段全一致 |
| C16 | 自测文档与实测结论一致 | ✅ | 7 个结论字面量全部命中 |

## 3. 两端对齐矩阵（核心）

**演示侧** = `demo/out/demo_run_result.json`；**验证侧** = `eval/results/<run_id>/` 的
`manifest.json` / `summary.json` / `records.jsonl`。

| 字段 | 演示侧 | 验证侧 | 一致 |
|---|---|---|---|
| `run_id` | `RUN-095a2a4a40c6` | `RUN-095a2a4a40c6` | ✅ |
| `config_snapshot_hash` | `65f1c17c7b07…` | `65f1c17c7b07…` | ✅ |
| `code_hash` | `c9ca4d21de88…` | `c9ca4d21de88…` | ✅ |
| `dataset_hash` | `a5bceccc657a…` | `a5bceccc657a…` | ✅ |
| `verdict` | `OFFLINE_READY` | `OFFLINE_READY` | ✅ |
| `quality_verdict` | `NOT_COMPARABLE` | `NOT_COMPARABLE` | ✅ |
| 状态分布 | `23 / 2 / 1` | `23 / 2 / 1` | ✅ |

**逐链路对齐**：9 步共 42 项逐项核对全过 —

| 步 | 逐项通过 | 关键实际值 |
|---|---|---|
| S1 场景卡 | 3/3 | 9 步 / 12 分钟 / `offline_placeholder` |
| S2 环境自检 | 3/3 | Python 3.13.14 / `network_calls=0` / 零密钥 |
| S3 契约门禁 | 4/4 | 35 个槽位 / 26 项 blocking / 9 项 optional / `ready_for_real_api=false` |
| S4 对象清单 | 2/2 | 14 个对象 / `config_snapshot_hash` 可见 |
| S5 全量批次 | 5/5 | 26 条 / 23 / 2 / 1 / `OFFLINE_READY` / 双跑 true / Stage1 true |
| S6 单用例六链路 | 5/5 | `C-001` 六步 / `chunk_count=4` / `recall_at_k=1.0` / `[1]` |
| S7 边界情况 | 9/9 | `C-013`=0 分块且拒答 / `C-018`=`degraded` / `C-020`=`incomplete` 且 `composite_score=null` |
| S8 对比实验 | 3/3 | 变体 `RUN-c01cb40ec128` / 26 条配对 / `comparable=false` |
| S9 结果确认 | 8/8 | 四个哈希 + 14 个对象版本 + 0 网络 + 两个 verdict |

## 4. 三方一致性证据（术语 / 流程 / 数据 / 预期）

| 维度 | 演示文档（01） | 测试话术（02） | 配套工具（03 / runner） | 结论 |
|---|---|---|---|---|
| 术语 | 第 2 节术语表 8 条 | 「只说链路与工程约束」+ 严禁说的话 | `scenario.json.glossary` | 同一套，自测 C09 通过 |
| 流程 | 第 3 节流程图 + 第 5 节 9 步表 | 第 2 节九步主话术 | `scenario.json.steps` 9 步 | 顺序 S1→S9 一致，C02/C10 通过 |
| 数据 | 第 6 节速查表 | 第 2/3 节口径 | `demo_run_result.json` 实际值 | 26 条 / 14 个 / 35 个 / 23 / 2 / 1 全部一致，C03/C04/C11 通过 |
| 预期结果 | 第 5 节「预期结果」列 | 第 4 节每条「指给观众看」 | runner 每步 `expected` + `checks` | 42 项逐条比对全过，C10 通过 |
| 话术原文 | — | 第 2 节逐字表 | `scenario.json.talk_lines` | T1–T9 逐字一致，C05 通过 |

**边界场景两端一致**：`C-013`（空文档 `chunk_count=0`）、`C-018`（超时 `degraded` +
`E_PLACEHOLDER_TIMEOUT`）、`C-020`（评分不可用 `incomplete` + `composite_score=null`）
在话术、演示台本、原始 `records.jsonl` 三处描述完全相同（C12 通过）。

## 5. 自测中发现并修正的问题（真实记录）

| # | 问题 | 修正 |
|---|---|---|
| 1 | 首轮 S8 读取 `compare_runs.py` 标准输出的 `case_count` 字段，实际该命令打印的是 `cases`，完整字段在落盘的 `comparison.json` | runner 改为显式调用命令生成文件后读 `comparison.json`；首轮曾出现 `MISALIGNED 8/9`，修正后 9/9 |
| 2 | 环境自检用「不含 pip install」判定纯标准库，但依赖声明里写了「无需 pip install」导致误判 | 改为按「仅使用 Python 标准库」字面判定 |
| 3 | `run_id` 原先只依赖配置与数据集，改评测代码会复用旧 id 覆盖历史 | 引入 `code_hash` 纳入复现主键（本轮之前已修，自测 C11/C13 覆盖） |

## 6. 已知限制（明确不追求）

- 不接真实 API：占位 embedding 无语义能力，质量指标 `comparable=false`，结论 `NOT_COMPARABLE`。
- 不做 UI、并发压测、质量基线、监控告警、生产级错误处理。
- 演示时长按 12 分钟编排，`demo_runner.py` 实际执行约 10 秒；**快的是工具，慢的是讲解**。
- 若改动 `demo/scenario.json`、任一版本化对象、评测代码或数据集，
  `run_id` 会变化，需重跑自测并同步更新本文档第 3 节的哈希与 run_id。

## 7. 复现命令

```bash
python demo/demo_runner.py     # 演示侧：9 步台本 + 对齐
python demo/selftest.py        # 校验侧：16 项检查 + 报告
```

**验收判定**：`demo/out/selftest_report.json` 中 `summary.verdict == "DEMO_READY"`
且 `summary.all_passed == true`，即为「拿来即可演示」。本轮实测满足。
