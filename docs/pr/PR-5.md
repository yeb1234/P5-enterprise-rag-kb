# PR #5 · 多用户网关加固五项 + 腾讯云指导页

| 项 | 值 |
|---|---|
| 分支 | `feature/gateway-hardening` → `demo/v1` |
| 提交 | `66d5c24` |
| 基线 | `2206a3b` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

feat(hardening): 多用户网关加固五项 + 腾讯云指导页

## 改动

- gateway_guard：413 请求体上限 → 限流（三维 + IP 附加）→ 并发闸 503；流式响应用 body_iterator 包装，流完才释放令牌
- security.client_ip + TRUSTED_PROXY_IPS：只有直连对端可信才采信 X-Forwarded-For 最左值，伪造头换不了限流桶
- check_budget(user_id=)：budgets(scope=user) 优先、回退 global；问答入口 policy=block 且本人超支 → SSE 首事件拒绝 + 审计，A 超支不影响 B
- 新增 RATE_LIMIT_IP_EXCEEDED 专用码；前端区分「账号被限流」与「网络出口请求过多」
- 文档 docs/multi-user-hardening.md（四层分工 + 五项实测）与 docs/showcase/tencent-cloud-guide.html（三处限流分工 / 分层加固 / 费用估算 / 与阿里云对比）
- README 口径纠偏：四维限流 → 三维业务配额 + IP 接入层防刷；租户 → 部门/单组织
- 验证：292 单测（webapp 127）、33/33 e2e、容器内实测 413/429/503/预算隔离

## 变更规模

```
docs/multi-user-hardening.md           | 146 +++++++++++
 docs/showcase/tencent-cloud-guide.html | 335 +++++++++++++++++++++++++
 interview/index-standalone.html        |   2 +
 webapp/README.md                       |  10 +-
 webapp/db.py                           |   5 +-
 webapp/demo_seed_queue.py              |   7 +-
 webapp/main.py                         | 223 +++++++++++++----
 webapp/observability.py                |  73 +++++-
 webapp/routers_chat.py                 |  38 +++
 webapp/routers_evaltest.py             |  57 ++++-
 webapp/security.py                     |  28 +++
 webapp/static/app.js                   |  26 +-
 webapp/static/index.html               |  10 +-
 webapp/tests/test_evaltest.py          |   4 +-
 webapp/tests/test_gateway_hardening.py | 437 +++++++++++++++++++++++++++++++++
 15 files changed, 1315 insertions(+), 86 deletions(-)
```

<details><summary>文件清单</summary>

```
A	docs/multi-user-hardening.md
A	docs/showcase/tencent-cloud-guide.html
M	interview/index-standalone.html
M	webapp/README.md
M	webapp/db.py
M	webapp/demo_seed_queue.py
M	webapp/main.py
M	webapp/observability.py
M	webapp/routers_chat.py
M	webapp/routers_evaltest.py
M	webapp/security.py
M	webapp/static/app.js
M	webapp/static/index.html
M	webapp/tests/test_evaltest.py
A	webapp/tests/test_gateway_hardening.py
```

</details>

## 验证

127 单测（含 18 项加固）；33/33 e2e；容器内实测 413 `PAYLOAD_TOO_LARGE`、429 `RATE_LIMIT_IP_EXCEEDED`、并发闸 503 `SERVER_BUSY`、伪造 XFF 不换限流桶、A 超支不影响 B

## 风险与边界

限流是单机内存窗口，多副本需换 Redis 或云网关；`backend/` 骨架仍只有预留，未接并发闸

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

