# 说明书分块问答评测 · 离线占位链路

**零依赖、零网络、零密钥即可启动。** 只要 Python 3.10+，不需要任何真实 API、不需要安装第三方包。

本目录只做一件事：把「分块 → embedding → 检索 → 提示词组装 → 回答生成 → 评分」六条链路
用确定性占位实现跑通，产出**可直接检查的统一结果记录**，并验证故障处理与可复现性。
它**不产出任何质量结论**：占位向量不具语义能力，占位生成器只做抽取式复述，
所有质量指标都带 `comparable=false`。真实结论必须等接入 DeepSeek、阿里云百炼 embedding
与待测说明书分块问答系统后，按 `docs/evaluation-plan.md` 执行。

## 启动（三条命令）

```bash
# 1. 全量 26 条离线用例 + 确定性双跑 + 链路健康闸门
python eval/run_eval.py

# 2. 列出所有 __TBC__ 接入契约待确认项（不发起任何请求）
python eval/run_eval.py --check-contracts

# 3. 对比实验变体（只改内存，不改版本化对象文件）
python eval/run_eval.py --set OBJ-RETRIEVAL-CFG.content.top_k=3 --label exp-topk3
python eval/compare_runs.py eval/results/<基线run目录> eval/results/<变体run目录>

# 自测
python -m unittest discover -s eval/tests -t .
```

其他入口：`--list-objects`（列对象版本）、`--profile real_api`（契约未就绪时快速失败）、
`--seed`、`--label`、`--out`、`--skip-determinism`。

## 目录

```
eval/
  run_eval.py              主入口（全量用例 + 双跑 + 闸门 + manifest）
  compare_runs.py          对比实验/回归比对（按 case_id 配对）
  config/
    registry.json          14 个版本化对象索引 + 版本策略 + 变更记录必填字段
    objects/*.json         每个对象独立文件：标识/版本/生效范围/依赖/内容/变更记录
  datasets/
    cases_v0.1.0.jsonl     首批 26 条用例（7 个套件）
    fixtures_v0.1.0.json   6 份合成文档 fixtures（含空文档/超长/长行）
  core/
    registry.py            对象加载、DAG 校验、配置快照哈希、逐链路对象关联
    contracts.py           接入契约门禁；blocking 字段未确认则 real_api 快速失败
    adapters.py            占位 embedding / 生成 / 评分 + 内部占位错误分类
    pipeline.py            六链路实现与故障注入
    metrics.py             检索与生成指标计算
    recorder.py            统一记录、模式校验、离线闸门
    ids.py                 确定性 run_id / trace_id / span_id / 哈希
  tests/test_harness.py    14 项自测
  results/                运行产物（已 gitignore）
```

## 首批用例覆盖

| 套件 | 条数 | 场景 |
|---|---|---|
| smoke | 3 | 正常问答链路连通 |
| retrieval | 6 | 金标证据召回、页码、多关键词 |
| generation | 3 | 引用格式、拒答、枚举 |
| boundary | 5 | 空文档、超长文本、检索为空、提示词溢出、空问题 |
| fault_injection | 4 | 超时、异常返回、评分不可用、空召回 |
| safety | 2 | 提示词注入、敏感信息泄漏负向对照 |
| regression_seed | 3 | 回归种子集 |

金标用**证据原文片段**（`gold_evidence`）而不是预置 `chunk_id`，分块参数变化不会让标注失效。

## 当前实测结果（可复现）

`verdict=OFFLINE_READY`，26 条用例：`ok=23 / degraded=2 / incomplete=1`；
确定性双跑 `true`；链路健康闸门 7 项全通过；契约门禁 `blocking_pending=26`（即真实档位仍被正确阻断）。
`run_id` 由 `profile + seed + config_snapshot_hash + code_hash + dataset_hash + label` 确定性生成：
**改动任意版本化对象、评测代码或数据集都会产生新的 run_id，不会覆盖历史结果**。

## 严禁事项

- 不要把 `__TBC__` 替换成猜测的接口地址、鉴权、请求结构、错误码、配额或模型名；
- 不要把占位结果写进质量基线，也不要据此冻结质量阈值；
- 不要修改已经产生 run 记录的对象版本或用例，一律新建版本；
- 不要把原始提示词、凭证、完整回答写进日志或结果文件。
