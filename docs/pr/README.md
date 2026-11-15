# PR 索引（本地 origin：`.data/remote.git`）

> 这些描述由 `scripts/gen_pr_docs.py` 从真实 git 历史生成，diffstat 与文件清单不是手写。
> 推送与建 PR 的远端操作用 `scripts/push-to-github.sh`（需要你的仓库与凭证）。

| PR | 分支 | 提交 | 标题 | 文件数 |
|---|---|---|---|---|
| [#1](PR-1.md) | `feature/p0-security-fix` | `38cde1e` | 修 P0 密钥漏检与 QA 5 条缺陷，补项目演进页与容器化 | 43 |
| [#2](PR-2.md) | `feature/nav-and-containers` | `3a783e8` | 完整功能首页与解析工作台双向互链，补 webapp 镜像与一键启动 | 8 |
| [#3](PR-3.md) | `feature/queue-observability` | `402bade` | 全局队列可观测性与权限边界；修 system/busy 无鉴权 | 15 |
| [#4](PR-4.md) | `feature/queue-ui` | `2206a3b` | 任务中心改为按用户/按部门积压聚合，短码可区分 | 6 |
| [#5](PR-5.md) | `feature/gateway-hardening` | `66d5c24` | 多用户网关加固五项 + 腾讯云指导页 | 15 |
| [#6](PR-6.md) | `feature/case-study` | `8ac4224` | 14 个真实缺陷案例集 + 图文教学页 + PR 记录 | 5 |

## 状态

全部 6 个 PR 已在本地 `origin` 推送（`git branch -r` 可见），其中 #6 已 `--no-ff` 合并进 `demo/v1`；#1–#5 为交付点快照分支。

