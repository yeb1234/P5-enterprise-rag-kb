# PR #2 · 完整功能首页与解析工作台双向互链，补 webapp 镜像与一键启动

| 项 | 值 |
|---|---|
| 分支 | `feature/nav-and-containers` → `demo/v1` |
| 提交 | `3a783e8` |
| 基线 | `38cde1e` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

feat(nav): 完整功能首页与解析工作台双向互链，补 webapp 镜像与一键启动

## 改动

- 8767 顶栏加「← 完整功能首页」，A1 /api/health 下发 links.main_site（可用 SHOWCASE_MAIN_SITE_URL 覆盖），前端回退同主机:8765
- 8765 侧栏加「解析工作台」入口（openShowcase，同主机:8767），无 data-view 不影响 C33 的 10 视图/6 加载器断言
- 新增 webapp/Dockerfile 与 docker-compose.showcase.yml；Docker 29 + Compose v5 的 bake 在中文路径下报 gRPC 头编码错误，故提供 run-demo.sh 用 build+run 绕开
- 两容器实测 healthy；33/33 e2e DEMO_READY（报告按字节还原）

## 变更规模

```
docker-compose.showcase.yml | 54 ++++++++++++++++++++++++++++++++++++++++++++
 run-demo.sh                 | 55 +++++++++++++++++++++++++++++++++++++++++++++
 showcase/api.py             |  7 ++++++
 showcase/static/app.js      | 18 +++++++++++++++
 showcase/static/index.html  |  1 +
 webapp/Dockerfile           | 44 ++++++++++++++++++++++++++++++++++++
 webapp/static/app.js        |  8 +++++++
 webapp/static/index.html    |  5 +++++
 8 files changed, 192 insertions(+)
```

<details><summary>文件清单</summary>

```
A	docker-compose.showcase.yml
A	run-demo.sh
M	showcase/api.py
M	showcase/static/app.js
M	showcase/static/index.html
A	webapp/Dockerfile
M	webapp/static/app.js
M	webapp/static/index.html
```

</details>

## 验证

两个容器 `healthy`、8765/8767 均 200；33/33 e2e；双向链接用 curl 抓真实 HTML 与 A1 JSON 核对

## 风险与边界

Docker 29 + Compose v5 的 bake 在中文路径下报 gRPC 头编码错误，故用 `docker build` + `docker run` 绕开

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

