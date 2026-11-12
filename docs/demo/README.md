# 演示物料包 · 总索引

**物料编号**：`DEMO-KBQA-EVAL-001` ｜ 场景：一次说明书分块问答评测，**从开场到结果确认**
**档位**：`offline_placeholder` ｜ **验收标准**：拿来即可演示（不追求生产级完整度）

## 四部分

| 部分 | 文件 | 内容 |
|---|---|---|
| 一 · 演示文档 | [01-demo-guide.md](01-demo-guide.md) | 场景说明、术语、流程、前置条件、9 步操作与预期结果、边界清单、失败处理 |
| 二 · 测试话术 | [02-demo-talktrack.md](02-demo-talktrack.md) | 九步逐字话术、正常场景话术、5 类边界话术、Q&A、应急话术、严禁说的话 |
| 三 · 配套工具 | [03-demo-tools.md](03-demo-tools.md) | 4 个工具、单一事实源、命令速查、产物说明、FAQ |
| 四 · 自测结果与对齐说明 | [04-selftest-and-alignment.md](04-selftest-and-alignment.md) | 16 项自测结果、两端对齐矩阵、三方一致性证据、修正记录、已知限制 |

## 最快路径（两条命令）

```bash
python demo/demo_runner.py     # 一键演示：逐步打印【话术/操作/预期/实际/对齐】
python demo/selftest.py        # 一键自测：校验文档、话术、工具与两端对齐
```

## 五秒速查

- **26 条**用例 ｜ **14 个**版本化对象 ｜ **35 个**契约槽位（**26 项** blocking / **9 项** optional）
- 状态 **23 / 2 / 1** ｜ 闸门 `OFFLINE_READY` ｜ 质量结论 `NOT_COMPARABLE` ｜ `comparable=false`
- 六链路：`chunk → embed → retrieve → assemble_prompt → generate → score`
- 主用例 `C-001`；边界 `C-013` / `C-015` / `C-016` / `C-018` / `C-020`
- 复现四要素：`run_id` + `config_snapshot_hash` + `code_hash` + `dataset_hash`
- 零网络、零密钥、零第三方依赖；所有真实 API 字段统一 `__TBC__`

## 一致性保障

`demo/scenario.json` 是唯一事实源：9 句话术、全部关键数值、用例与对象引用都从这里出发；
`demo/selftest.py` 反向校验四份文档是否包含这些字面量，并把**演示侧**
（`demo/out/demo_run_result.json`）与**验证侧**（`eval/results/<run_id>/`）逐字段对齐。
任一处被改动而没同步，自测会直接给出不对齐项，不会让演示带着错误上台。
