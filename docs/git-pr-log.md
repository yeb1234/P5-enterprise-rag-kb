# 分支与 PR 记录

> 用户要求：「对项目创建分支版本，每一次功能测试了可以模拟多 PR，继续创造分支版本，全自动就行。」

## 做法说明（为什么不改写历史）

当前 `demo/v1` 的历史是**线性**的（每个功能一个提交）。要"变成"多个 PR 有两条路：

1. **改写历史**：把提交拆到 feature 分支再 merge——会重写所有 commit hash，
   已经写进文档、验收报告、记忆里的 hash 全部失效，**可追溯性被破坏**；
2. **保留历史 + 补分支快照 + 从本轮起走真实分支流程**——本文采用这条。

所以：

- 已完成的 5 个功能，用 `feature/*` 分支**指向各自的交付提交**（`git branch <name> <sha>`），
  `git branch -vv` 能看到每个功能独立成一个分支，但它们仍在同一条历史线上；
- **从 PR #6 开始**，新功能真实地走 `git checkout -b feature/xxx` → 提交 → `git merge --no-ff`
  回 `demo/v1`，图中会出现真实的合并提交；
- 每个 PR 的「范围 / 验证 / 合并点」记录在下面，讲解时可以指着说「这是我们的 PR 节奏」。

## PR 列表

| PR | 分支 | 范围 | 验证 | 合并点 |
|---|---|---|---|---|
| #1 | `feature/p0-security-fix` | 修 P0 密钥漏检（正则纳入 `-`/`_`）、A9 不回显、A5 落盘兜底；QA 报的 5 条 API 缺陷；项目演进讲解页 | 33/33 e2e、31/31 集成、guardrails 33 项 | `38cde1e` |
| #2 | `feature/nav-and-containers` | 完整功能首页 ↔ 解析工作台双向互链；`webapp/Dockerfile`、`docker-compose.showcase.yml`、`run-demo.sh` | 两容器 healthy、33/33 e2e | `3a783e8` |
| #3 | `feature/queue-observability` | `GET /api/system/queue`（按用户/部门聚合）；`/api/system/busy` 加鉴权与裁剪；`users.department` 幂等迁移 | 8 项队列权限测试、109 单测、33/33 e2e | `402bade` |
| #4 | `feature/queue-ui` | 任务中心改聚合视图（按用户积压 / 按部门 / admin 明细）；用户名短码可区分；演示种子脚本 | 109 单测、33/33 e2e、容器内双账号实测 | `2206a3b` |
| #5 | `feature/gateway-hardening` | 应用层网关五项（IP 维度 / 可信代理 / 并发闸 / 请求体上限 / 用户级预算）+ `RATE_LIMIT_IP_EXCEEDED`；腾讯云加固指导页 | 127 单测（含 18 项加固）、33/33 e2e、容器内 413/429/503 实测 | `66d5c24` |
| #6 | `feature/case-study` | `docs/case-studies.md`（14 个真实缺陷）、`interview/cases.html`（数据流向 + 故障点图）、本 PR 日志 | 见下方「验证」 | 本次 `--no-ff` 合并 |

## 每个 PR 的验证口径

统一按「三层证据」：

1. **可复跑命令**：单测 / 集成脚本 / e2e 自测，任一失败即不合入；
2. **关键场景实跑**：不是「测试通过」，而是「攻击面能被挡住」——例如伪造 XFF 不换限流桶、
   用户 A 超支不影响 B、流式响应不提前释放令牌；
3. **范围纪律**：`git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/`
   必须为空（除本 PR 明确声明的范围），密钥 grep 零命中。

## 常用命令

```bash
git branch -vv                    # 看所有功能分支指向哪个交付点
git log --oneline --graph -12     # 看历史（PR #6 起会出现真实 merge）
git log demo/v1 --merges          # 只看合并提交
git diff feature/queue-ui demo/v1 # 对比某个功能分支与主线
```

## 已知局限

- PR #1–#5 的「分支」是**交付点快照**，不是真实的并行开发分支（没有多作者并发场景）；
  真实团队里每个 PR 还应有 review 记录、CI 状态、变更说明，这里只保留了变更说明与验证结论；
- 没有远端仓库，所以没有 `git push` / PR 页面；如果后续推到 GitHub/Gitee，
  可以按上表把分支推上去再补 PR 描述。
