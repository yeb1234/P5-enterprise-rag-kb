# PR #4 · 任务中心改为按用户/按部门积压聚合，短码可区分

| 项 | 值 |
|---|---|
| 分支 | `feature/queue-ui` → `demo/v1` |
| 提交 | `2206a3b` |
| 基线 | `402bade` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

feat(queue-ui): 任务中心改为按用户/按部门积压聚合；短码可区分

## 改动

- 统计卡片改为 处理中/排队中/涉及用户/涉及部门；新增按用户积压表与按部门表（部门>1 才显示）
- admin 专属文件名明细折叠表；普通用户只显示部门与首尾短码，不再读 busy.active/queued 的 filename
- 用户名短码从「前2位+***」改为「首尾各2位+***」，避免同前缀账号在表里无法区分（qu***_a / qu***_b）
- 新增 demo_seed_queue.py：可 seed/clean 的队列积压演示种子，标注「不会被真正处理」
- 容器内实测：普通用户 total=130、details=0、响应不含文件名/邮箱；admin details=130、含文件名
- ?v=18；C33 未破坏（10 视图 / 6 loader 存在）

## 变更规模

```
docs/queue-observability.md         | 256 +++++++++++++++---------------
 webapp/demo_seed_queue.py           | 110 +++++++++++++
 webapp/routers_ops.py               |  15 +-
 webapp/static/app.js                |  79 ++++++++--
 webapp/static/index.html            |  32 +++-
 webapp/tests/test_queue_overview.py | 302 ++++++++++++++++++------------------
 6 files changed, 490 insertions(+), 304 deletions(-)
```

<details><summary>文件清单</summary>

```
M	docs/queue-observability.md
A	webapp/demo_seed_queue.py
M	webapp/routers_ops.py
M	webapp/static/app.js
M	webapp/static/index.html
M	webapp/tests/test_queue_overview.py
```

</details>

## 验证

109 单测；33/33 e2e；容器内双账号实测（研发部 100 / 交付部 30，短码 `qu***_a` / `qu***_b`）

## 风险与边界

演示种子会被启动自检逐条消费，需演示前重新 `--seed`

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

