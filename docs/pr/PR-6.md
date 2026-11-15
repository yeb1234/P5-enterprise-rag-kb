# PR #6 · 14 个真实缺陷案例集 + 图文教学页 + PR 记录

| 项 | 值 |
|---|---|
| 分支 | `feature/case-study` → `demo/v1` |
| 提交 | `8ac4224` |
| 基线 | `66d5c24` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

docs(cases): 14 个真实缺陷案例集 + 图文教学页 + PR 记录

## 改动

- docs/case-studies.md：14 条案例（现象/根因/修法/面试问法/证据），按安全·确定性·配额并发·工程分组
- interview/cases.html：九节点数据流向图 + 故障点标注 + 类型分布 + 可展开案例卡 + 面试三层讲法 + 自测清单
- docs/git-pr-log.md：PR #1-#6 记录，并说明为何用分支快照而非改写历史
- index/journey 导航加「故障案例」入口

## 变更规模

```
docs/case-studies.md   | 353 +++++++++++++++++++++++++++++++++++++++++++++
 docs/git-pr-log.md     |  56 ++++++++
 interview/cases.html   | 377 +++++++++++++++++++++++++++++++++++++++++++++++++
 interview/index.html   |   1 +
 interview/journey.html |   1 +
 5 files changed, 788 insertions(+)
```

<details><summary>文件清单</summary>

```
A	docs/case-studies.md
A	docs/git-pr-log.md
A	interview/cases.html
M	interview/index.html
M	interview/journey.html
```

</details>

## 验证

`interview/cases.html` HTMLParser 配对无异常、零外部资源、5 锚点、14 张案例卡；MD 与页面内容一致；走 `--no-ff` 合并生成真实 merge commit

## 风险与边界

案例均为规则级/演示级修复，不含真实压测与 WAF 规则标定

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

