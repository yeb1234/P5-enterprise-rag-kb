# PR #3 · 全局队列可观测性与权限边界；修 system/busy 无鉴权

| 项 | 值 |
|---|---|
| 分支 | `feature/queue-observability` → `demo/v1` |
| 提交 | `402bade` |
| 基线 | `3a783e8` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

feat(queue): 全局队列可观测性与权限边界；修 system/busy 无鉴权

## 改动

- 新端点 GET /api/system/queue（登录可读）：by_user/by_department/details/scope，真值取 documents.status 而非内存队列
- 隐私边界：普通用户只见部门 + 用户ID前8位 + 用户名前2位短码 + 计数；文件名/邮箱/完整名仅 admin
- users 补 department 展示列（幂等迁移，种子 admin=平台运营），不参与权限判定
- 修安全缺陷：/api/system/busy 此前无鉴权且返回 filename，未登录可探测；现需登录并对普通用户裁剪
- selftest C07/C26 改为带 admin token（调用方式跟随鉴权变更，非放宽断言）
- 文档 docs/queue-observability.md；测试 8 项；webapp 109 passed + 33/33 e2e

## 变更规模

```
.workbuddy/memory/2026-11-15.md     |  28 +++++++
 docs/queue-observability.md         | 128 ++++++++++++++++++++++++++++++
 interview/index-standalone.html     |  22 +++++-
 interview/index.html                |  20 +++++
 showcase/static/app.js              | 131 ++++++++++++++++++++++++++++++-
 showcase/static/index.html          |   4 +-
 showcase/static/style.css           |  29 +++++++
 webapp/db.py                        |  22 +++++-
 webapp/routers_docs.py              |  15 +++-
 webapp/routers_ops.py               |  97 +++++++++++++++++++++++
 webapp/selftest.py                  |   6 +-
 webapp/static/app.js                |  20 +++--
 webapp/static/index.html            |  13 ++--
 webapp/static/style.css             |   9 +++
 webapp/tests/test_queue_overview.py | 151 ++++++++++++++++++++++++++++++++++++
 15 files changed, 673 insertions(+), 22 deletions(-)
```

<details><summary>文件清单</summary>

```
M	.workbuddy/memory/2026-11-15.md
A	docs/queue-observability.md
M	interview/index-standalone.html
M	interview/index.html
M	showcase/static/app.js
M	showcase/static/index.html
M	showcase/static/style.css
M	webapp/db.py
M	webapp/routers_docs.py
M	webapp/routers_ops.py
M	webapp/selftest.py
M	webapp/static/app.js
M	webapp/static/index.html
M	webapp/static/style.css
A	webapp/tests/test_queue_overview.py
```

</details>

## 验证

8 项队列权限测试；109 单测；33/33 e2e；真实 100 条积压下普通用户 `details=0`、响应不含文件名与邮箱

## 风险与边界

真值取 `documents.status`，不是内存队列；`department` 只是展示标签，不参与权限判定

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

