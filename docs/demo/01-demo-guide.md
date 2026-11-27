# 第一部分 · 演示文档

**物料编号**：`DEMO-KBQA-EVAL-001` ｜ **场景版本**：1.0.0 ｜ **档位**：`offline_placeholder`
**配套**：[测试话术](02-demo-talktrack.md) · [配套工具](03-demo-tools.md) · [自测结果与对齐说明](04-selftest-and-alignment.md)

---

## 1. 场景说明

| 项 | 内容 |
|---|---|
| 场景 | 一次说明书分块问答评测：**从开场到结果确认**的完整闭环 |
| 演示者 | 单人，无需协助、无需网络、无需密钥 |
| 观众 | 内部技术评审 / 演示作品讲解 / 团队新人培训 |
| 时长 | 12 分钟，9 步 |
| 要回答的问题 | 这套评测体系到底能不能独立跑起来、结果敢不敢信 |
| 成功标准 | 照着文档念、按命令敲，观众看到的实际输出与文档预期逐项一致；不需要任何真实 API |
| 边界声明 | **本演示只证明链路与工程约束，不证明检索/生成质量**；所有质量指标 `comparable=false`，质量阈值未冻结，结论为 `NOT_COMPARABLE` |

**故事线**：评审现场，演示者先亮出契约门禁说明「没接真实 API 也不会假装能评」，
列出 14 个版本化对象，跑一次全量批次，挑一条正常用例逐链路核对，
再故意展示三类边界情况证明异常不静默，做一次对比实验说明可比较性边界，
最后用 manifest 的四个哈希完成结果确认。

**一句话主线**：**先看住边界 → 再跑通链路 → 再证明异常不静默 → 最后确认结果可复现。**

## 2. 术语（全程只用这一套说法）

| 术语 | 含义 |
|---|---|
| 六链路 | `chunk → embed → retrieve → assemble_prompt → generate → score` |
| 占位档位 | `offline_placeholder`，禁止网络、只用确定性占位实现 |
| 契约槽位 | `OBJ-ADAPTER-CONTRACTS` 中预留的 `__TBC__` 接入字段 |
| 版本化对象 | 独立可标识、带版本/生效范围/依赖/变更记录的配置单元 |
| 统一记录 | `eval-record/v0.1` 逐用例 JSONL 记录 + manifest + summary |
| 两端对齐 | 演示侧 `demo/out/demo_run_result.json` 与验证侧 `eval/results/` 产物逐字段一致 |
| 不静默 | 异常/评分不可用必须记录状态与原因，不得记 0 分或丢弃用例 |
| 可比较性 | `comparable=false` 表示占位结果不得用于质量结论 |

## 3. 流程

```mermaid
flowchart LR
    A[S1 场景卡] --> B[S2 环境自检]
    B --> C[S3 契约门禁]
    C --> D[S4 对象清单]
    D --> E[S5 全量批次]
    E --> F[S6 单用例六链路]
    F --> G[S7 边界情况]
    G --> H[S8 对比实验]
    H --> I[S9 结果确认]
```

- **开场（S1–S2）**：先把边界与前置条件说清楚。
- **建立可信度（S3–S4）**：先证明「不会假装能评」与「配置可追溯」。
- **主演示（S5–S6）**：跑全量、再钻取单条。
- **边界与对比（S7–S8）**：证明异常不静默、并明确可比较性边界。
- **收尾（S9）**：用四个复现要素完成结果确认，落到 `NOT_COMPARABLE`。

## 4. 前置条件（3 条，1 分钟内可确认）

1. Python 3.10+（本机实测 3.13.14）；**无需 pip install、无需网络、无需任何凭据**。
2. 工作目录为项目根目录（路径含中文不影响，按文档里的相对命令执行）。
3. 已存在 `eval/` 离线评测链路与 `demo/` 物料；若 `eval/results/` 为空，S5 会自动生成。

## 5. 操作步骤与预期结果

| 步 | 时间 | 操作（可复制） | 预期结果 | 观测点 |
|---|---|---|---|---|
| S1 | 1′ | 读 `demo/scenario.json` 场景卡 | 场景 / 目标 / 边界三者明确，9 步 12 分钟 | 场景卡 |
| S2 | 1′ | `python demo/demo_runner.py` | 纯标准库、`network_calls=0`、`credentials_loaded=false` | 台本 S2【实际】 |
| S3 | 1′ | `python eval/run_eval.py --check-contracts` | **35 个**契约槽位、**26 项** blocking、**9 项** optional、`ready_for_real_api=false` | 台本 S3【实际】 |
| S4 | 1′ | `python eval/run_eval.py --list-objects` | **14 个** `object_id@version`，`config_snapshot_hash` 可见 | 台本 S4【实际】 |
| S5 | 2′ | `python eval/run_eval.py` | **26 条**用例；`ok=23 / degraded=2 / incomplete=1`（**23 / 2 / 1**）；`OFFLINE_READY`；`determinism_pass=true`；`stage_1_pass=true` | 台本 S5【实际】 |
| S6 | 2′ | `python demo/inspect_record.py C-001` | 六步齐全；`chunk_count=4`；`recall_at_k=1.0`；回答带 `[1]` 引用 | 台本 S6【实际】 |
| S7 | 2′ | `python demo/inspect_record.py C-013`、`C-018`、`C-020` | 空文档 `chunk_count=0` 且拒答；超时 `degraded` 且留 `E_PLACEHOLDER_TIMEOUT`；评分不可用 `incomplete` 且 `composite_score=null` | 台本 S7【实际】 |
| S8 | 1′ | 见 [配套工具](03-demo-tools.md) 的变体 + 对比两条命令 | 变体 run 生成；**26 条**逐用例配对；`comparable=false` | `comparison.json` |
| S9 | 1′ | 读 `eval/results/<run_id>/manifest.json` 与 `summary.json` | 四个复现要素齐备；两端对齐；结论 `NOT_COMPARABLE` | 台本 S9【实际】 |

> 最省事的演示方式：直接执行 `python demo/demo_runner.py`。它会**自动依次跑完 S1–S9**，
> 逐步打印【话术 / 操作 / 预期 / 实际 / 对齐】，末尾给出 `9/9 步对齐` 的结论。
> 上表命令用于「观众要求单独复现某一步」时手动敲。

## 6. 预期结果汇总（关键数值速查）

| 数值 | 含义 | 出现位置 |
|---|---|---|
| 26 条 | 首批量用例数 | S5、话术、自测报告 |
| 14 个 | 版本化对象数 | S4、话术 |
| 35 个 / 26 项 / 9 项 | 契约槽位总数 / blocking / optional | S3、话术 |
| 23 / 2 / 1 | `ok / degraded / incomplete` 状态分布 | S5、话术 |
| 6 | 六链路步数 | S6、术语表 |
| 7 | 套件数 | 数据集说明 |
| 7 项 / 9 项 | Stage1 可判定阈值 / Stage2 未冻结阈值 | 闸门说明 |
| `OFFLINE_READY` | 链路健康闸门结论 | S5、S9 |
| `NOT_COMPARABLE` | 质量结论（阈值未冻结） | S9 |
| `comparable=false` | 占位结果不可用于质量结论 | 全篇 |
| `eval-record/v0.1` | 统一记录模式 | 结果确认 |

复现四要素：`run_id`、`config_snapshot_hash`、`code_hash`、`dataset_hash`；
外加 `object_versions`（14 个）与 `seed=20261111`。

## 7. 正常与边界场景清单

**正常**：`C-001`（DPI 切换按键在哪里？）为主演示用例，金标证据
「DPI 切换按键位于设备顶部」，预期 `recall_at_k=1.0`、带 `[1]` 引用。
其余正常用例覆盖软件手册、制度文档、枚举与简洁回答。

**边界**（全部由故障注入确定性触发，不依赖真实网络）：

| 用例 | 场景 | 预期状态 | 证据 |
|---|---|---|---|
| `C-013` | 空文档 | `ok` | `chunk_count=0`、拒答短语 |
| `C-015` | 检索为空 | `ok` | `forced_empty=true`、拒答 |
| `C-016` | 提示词溢出 | `ok` | `prompt_overflow=true` + warning |
| `C-018` | 超时 | `degraded` | `E_PLACEHOLDER_TIMEOUT`，用例记录仍在 |
| `C-020` | 评分不可用 | `incomplete` | `scoring.status=unavailable`、`composite_score=null` |

统一原则：**不静默**——异常不丢用例、不当通过、评分不可用不记 0 分。

## 8. 演示失败时的处理

| 现象 | 处理 |
|---|---|
| 台本出现 `✗ 存在不一致` | 直接读该步 `!` 行，里面写明「预期 / 实际」；按 [测试话术](02-demo-talktrack.md) 的应急话术说明 |
| 命令报「找不到 run」 | 先执行 `python eval/run_eval.py` 再重跑该步 |
| 时间不够 | 只跑 S1、S3、S5、S6、S7、S9（约 7 分钟），S4/S8 用话术带过 |
| 观众追问质量结论 | 统一回答：占位结果 `comparable=false`，结论 `NOT_COMPARABLE`，阈值冻结前不判 PASS |

## 9. 明确不追求

不追求生产级完整度：无真实 API、无 UI、无并发压测、无质量基线、无监控告警。
本物料的验收标准是**拿来即可演示**：文档、话术、工具三者术语一致、数据一致、预期一致。
