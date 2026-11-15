# PR #1 · 修 P0 密钥漏检与 QA 5 条缺陷，补项目演进页与容器化

| 项 | 值 |
|---|---|
| 分支 | `feature/p0-security-fix` → `demo/v1` |
| 提交 | `38cde1e` |
| 基线 | `74fc9c4` |
| 合并方式 | `--no-ff`（保留合并提交） |

## 背景

fix(security): 修 P0 密钥漏检与 QA 5 条缺陷；补项目演进讲解页与容器化

## 改动

- P0：API_KEY 正则纳入 -/_，覆盖 sk-proj-*/sk-or-v1-*/租户分段形态；A9 不再回显输入；A5 落盘前兜底扫描，命中即 422 且不写文件
- 框架级 422 统一为 {code,message,trace_id(+errors)} 并剔除 input/ctx；A8 校验失败改 404 并带 checks 全 false；405/静态 404 语义修正
- manifest 补 params_hash/config_snapshot 真实值；V5 验收口径改为入库内容规范化投影
- 新增 interview/journey.html 项目演进讲解（5 段演进/8 亮点/8 真实坑点/15 面试问答）；主解释页加入口并重录同步 standalone
- Dockerfile 支持 BASE_IMAGE/PIP_INDEX_URL 覆盖，容器实测 healthy、两档与滞回升 Pro 正常、外部请求 0
- 新增 docs/showcase/04-acceptance.md 与修复回填；全量 266 单测 + 33/33 e2e

## 变更规模

```
.gitignore                              |    3 +
 .workbuddy/memory/2026-11-15.md         |   70 ++
 .workbuddy/memory/MEMORY.md             |   31 +
 docs/showcase/01-scope.md               |  375 ++++++++
 docs/showcase/02-architecture.md        |  449 +++++++++
 docs/showcase/03-phase2-spec.md         |  251 ++++++
 docs/showcase/04-acceptance.md          |  392 ++++++++
 docs/showcase/aliyun-guide.html         |  441 +++++++++
 guardrails/detectors.py                 |    9 +-
 guardrails/tests/test_guardrails.py     |   22 +
 interview/index-standalone.html         | 1021 +++++++++++++++++++++
 interview/index.html                    |  160 +++-
 interview/journey.html                  |  485 ++++++++++
 interview/showcase-demo.mp4             |  Bin 0 -> 323720 bytes
 showcase/Dockerfile                     |   45 +
 showcase/README.md                      |  220 +++++
 showcase/__init__.py                    |   18 +
 showcase/adapters/__init__.py           |   29 +
 showcase/adapters/aliyun_docmind.py     |   33 +
 showcase/adapters/base.py               |   73 ++
 showcase/adapters/local_free.py         |   31 +
 showcase/adapters/reducto.py            |   30 +
 showcase/api.py                         |  769 ++++++++++++++++
 showcase/guard.py                       |   38 +
 showcase/ingest_preview.py              |  239 +++++
 showcase/llm_router.py                  |  565 ++++++++++++
 showcase/make_video.py                  |  360 ++++++++
 showcase/out/.gitkeep                   |    0
 showcase/params.py                      |  169 ++++
 showcase/samples/complex.canonical.json |  129 +++
 showcase/samples/complex.png            |  Bin 0 -> 22378 bytes
 showcase/samples/complex.report.json    |  131 +++
 showcase/samples/make_samples.py        |  184 ++++
 showcase/samples/simple.canonical.json  |   84 ++
 showcase/samples/simple.pdf             |   92 ++
 showcase/samples/simple.report.json     |   86 ++
 showcase/server.py                      |  157 ++++
 showcase/snapshot.py                    |  404 +++++++++
 showcase/static/app.js                  | 1506 +++++++++++++++++++++++++++++++
 showcase/static/index.html              |  274 ++++++
 showcase/static/style.css               |  436 +++++++++
 showcase/tests/test_contracts.py        |  170 ++++
 showcase/verify_integration.py          |  214 +++++
 43 files changed, 10192 insertions(+), 3 deletions(-)
```

<details><summary>文件清单</summary>

```
M	.gitignore
A	.workbuddy/memory/2026-11-15.md
M	.workbuddy/memory/MEMORY.md
A	docs/showcase/01-scope.md
A	docs/showcase/02-architecture.md
A	docs/showcase/03-phase2-spec.md
A	docs/showcase/04-acceptance.md
A	docs/showcase/aliyun-guide.html
M	guardrails/detectors.py
M	guardrails/tests/test_guardrails.py
A	interview/index-standalone.html
M	interview/index.html
A	interview/journey.html
A	interview/showcase-demo.mp4
A	showcase/Dockerfile
A	showcase/README.md
A	showcase/__init__.py
A	showcase/adapters/__init__.py
A	showcase/adapters/aliyun_docmind.py
A	showcase/adapters/base.py
A	showcase/adapters/local_free.py
A	showcase/adapters/reducto.py
A	showcase/api.py
A	showcase/guard.py
A	showcase/ingest_preview.py
A	showcase/llm_router.py
A	showcase/make_video.py
A	showcase/out/.gitkeep
A	showcase/params.py
A	showcase/samples/complex.canonical.json
A	showcase/samples/complex.png
A	showcase/samples/complex.report.json
A	showcase/samples/make_samples.py
A	showcase/samples/simple.canonical.json
A	showcase/samples/simple.pdf
A	showcase/samples/simple.report.json
A	showcase/server.py
A	showcase/snapshot.py
A	showcase/static/app.js
A	showcase/static/index.html
A	showcase/static/style.css
A	showcase/tests/test_contracts.py
A	showcase/verify_integration.py
```

</details>

## 验证

33/33 e2e `DEMO_READY`（报告按字节还原）；31/31 集成；guardrails 33 项（新增 6 项密钥形态）；5 种真实密钥形态实测全拦、零回显

## 风险与边界

A5 落盘前兜底扫描会把文档里自带的 `sk-…` 示例串也阻断，属「宁可阻断不可漏检」的有意取舍

## 检查清单

- [x] 相关单测 / 集成脚本 / e2e 自测通过
- [x] `git diff --exit-code -- eval/ demo/ backend/ parsing/ guardrails/ cache/ knowledge/` 为空（除本 PR 声明范围）
- [x] 新增交付物密钥 grep 零命中
- [x] 三态标注（已实现 / 只设计 / 未做）与事实一致
- [x] 变更说明包含「为什么」而不只是「改了什么」

