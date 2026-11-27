# P5 文档索引

<p align="center">
  <a href="../README.md">中文</a> · <a href="../README.en.md">English</a> · <a href="architecture-review.md">架构说明</a> · <a href="README.md">文档索引</a>
</p>

> 项目总览看 [`../README.md`](../README.md)；英文总览看 [`../README.en.md`](../README.en.md)。
> 本页是 `docs/` 的全量入口，按“先读 / 架构 / 检索与评测 / 解析与安全 / 生产化 / 变更记录”分组。
> 新增或重命名文档时，顺手更新本页，避免 README 里的链接变成死链。

## 建议阅读顺序

1. [`../README.md`](../README.md) — 项目定位、能力边界、运行方式与已知限制。
2. [`architecture-review.md`](architecture-review.md) — 当前架构边界、三套运行时和多用户多部门口径。
3. [`acceptance-report.md`](acceptance-report.md) — 最终验收范围、测试结果与未完成项。
4. 按主题进入下面的检索、解析、安全或生产化分组。

---

## 一、先读与总览

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 项目总览：能力、架构、快速开始、测试、评测、限制 |
| [`../README.en.md`](../README.en.md) | 英文总览；适合对外介绍和快速浏览 |
| [`architecture-review.md`](architecture-review.md) | 架构复核：三套运行时、多用户多部门边界、本轮范围 |
| [`acceptance-report.md`](acceptance-report.md) | 最终全面测试与功能验收报告 |
| [`enterprise-readiness.md`](enterprise-readiness.md) | 企业级就绪改造：模块化清单、限流/权限/成本规则、验收标准 |
| [`kb-capability-checklist.md`](kb-capability-checklist.md) | 企业知识库/RAG 能力清单与差距对照 |
| [`local-checklist.md`](local-checklist.md) | 本机约 5 分钟自测清单 |

## 二、架构与运行

| 文档 | 内容 |
|---|---|
| [`async-upload.md`](async-upload.md) | 大文件与多文档批量上传：异步处理、进度反馈、断点续传 |
| [`permission-model.md`](permission-model.md) | 权限模型：部门、角色、显式授权、逐段落预留 |
| [`org-model-change-plan.md`](org-model-change-plan.md) | 多租户 → 多用户多部门的改造方案与未来路线 |
| [`multi-user-hardening.md`](multi-user-hardening.md) | 多用户加固与网关限流的四层分工 |
| [`queue-observability.md`](queue-observability.md) | 全局队列可观测性与权限边界 |
| [`shared-infra/01-design.md`](shared-infra/01-design.md) | 接入共享基础设施的设计与边界 |
| [`HARNESS.md`](HARNESS.md) | YEBSPEC 通用 Harness 开发规约 |
| [`git-conventions.md`](git-conventions.md) | 分支、提交信息与版本管理规范 |
| [`agent-extension.md`](agent-extension.md) | Agent 内部编排扩展点 |
| [`plugin-selection.md`](plugin-selection.md) | 插件三维度选型与高频场景考察 |
| [`next-version-messaging-and-plugins.md`](next-version-messaging-and-plugins.md) | 下一版本消息层职责重划与插件清单 |

## 三、检索、知识与评测

### 3.1 三路取证

| 文档 | 内容 |
|---|---|
| [`kb-routes/01-design.md`](kb-routes/01-design.md) | 向量 / 图谱 / LLM Wiki 三路取证总设计 |
| [`kb-routes/03-jobs-and-incremental.md`](kb-routes/03-jobs-and-incremental.md) | 后台任务落库与按变化文档增量重建 |
| [`kb-routes/04-namespace-isolation.md`](kb-routes/04-namespace-isolation.md) | 图谱检索跨项目隔离：属性命名空间 + 查询层强制 |
| [`kb-routes/05-mcp-integration.md`](kb-routes/05-mcp-integration.md) | 三路取证的 MCP 风格工具端点与传输边界 |

### 3.2 知识治理

| 文档 | 内容 |
|---|---|
| [`knowledge/01-research.md`](knowledge/01-research.md) | 入库新鲜度与事实冲突检测调研 |
| [`knowledge/02-candidates.md`](knowledge/02-candidates.md) | 候选企业 RAG 能力清单 |
| [`knowledge/04-design.md`](knowledge/04-design.md) | 新鲜度、有效期与冲突裁决设计 |

### 3.3 检索评估与参数

| 文档 | 内容 |
|---|---|
| [`retrieval-param-convergence.md`](retrieval-param-convergence.md) | 检索参数收敛与链路性能改造（历史推导，2026-11-18） |
| [`retrieval-param-review-2026-11-27.md`](retrieval-param-review-2026-11-27.md) | 2026-11-27 复核：482 项测试、3k/12k 双池、公开基准复跑 |
| [`cmedqa-retrieval-evaluation.md`](cmedqa-retrieval-evaluation.md) | CmedqaRetrieval（C-MTEB）检索链路评估 |
| [`cmrc-chain-evaluation.md`](cmrc-chain-evaluation.md) | CMRC2018 端到端生成链路评估 |
| [`evaluation-plan.md`](evaluation-plan.md) | 评估与测试方案 |
| [`evaluation-report.md`](evaluation-report.md) | 真实外部模型配置下的评估报告 |
| [`cost-metrics-spec.md`](cost-metrics-spec.md) | 成本指标定义与部门维度数据模型 |

### 3.4 对外接口

| 文档 | 内容 |
|---|---|
| [`mcp.md`](mcp.md) | 只读 Knowledge MCP 网关（JSON-RPC 2.0） |

## 四、解析、内容安全、缓存与展示

### 4.1 解析子系统

| 文档 | 内容 |
|---|---|
| [`parsing/01-overview.md`](parsing/01-overview.md) | 解析子系统架构总览与评审讲法 |
| [`parsing/02-pdf-text-layer-and-ocr.md`](parsing/02-pdf-text-layer-and-ocr.md) | PDF 文本层、扫描件与 OCR 的判定和落点 |
| [`parsing/03-table-structure-semantics.md`](parsing/03-table-structure-semantics.md) | 表格结构语义：RAG 最容易坏在表格上 |
| [`parsing/04-image-and-multimodal.md`](parsing/04-image-and-multimodal.md) | 图片型文档与多模态兜底的边界 |
| [`parsing/05-escalation-and-budget.md`](parsing/05-escalation-and-budget.md) | 质量门禁、确定性升级与预算熔断 |
| [`parsing/06-department-queue.md`](parsing/06-department-queue.md) | 多文件/大文件异步链路与多部门队列口径 |
| [`parsing/07-api-contracts.md`](parsing/07-api-contracts.md) | 外部服务契约、实测记录与待补清单 |

### 4.2 内容安全

| 文档 | 内容 |
|---|---|
| [`content-safety/01-research.md`](content-safety/01-research.md) | PII 检测与脱敏调研 |
| [`content-safety/02-design.md`](content-safety/02-design.md) | 内容安全 PII 防护实现对照 |
| [`llm-safety-positions.md`](llm-safety-positions.md) | 安全前中后置与低思考快速档的技术口径 |

### 4.3 缓存与恢复

| 文档 | 内容 |
|---|---|
| [`cache-recovery/01-research.md`](cache-recovery/01-research.md) | 缓存设计与重启状态稳定性调研 |
| [`cache-recovery/02-design.md`](cache-recovery/02-design.md) | 通用缓存层与重启恢复设计 |

### 4.4 展示与演示

| 文档 | 内容 |
|---|---|
| [`showcase/01-scope.md`](showcase/01-scope.md) | 解析工作台 Phase 1 需求范围与演示剧本 |
| [`showcase/02-architecture.md`](showcase/02-architecture.md) | 解析工作台架构与选型 |
| [`showcase/03-phase2-spec.md`](showcase/03-phase2-spec.md) | Phase 2 实现 spec（综合与裁决） |
| [`showcase/04-acceptance.md`](showcase/04-acceptance.md) | Phase 3 独立验收报告 |
| [`demo/README.md`](demo/README.md) | 演示物料包总索引 |
| [`demo/01-demo-guide.md`](demo/01-demo-guide.md) | 演示文档 |
| [`demo/02-demo-talktrack.md`](demo/02-demo-talktrack.md) | 测试话术 |
| [`demo/03-demo-tools.md`](demo/03-demo-tools.md) | 配套工具 |
| [`demo/04-selftest-and-alignment.md`](demo/04-selftest-and-alignment.md) | 自测结果与对齐说明 |

## 五、生产化

| 文档 | 内容 |
|---|---|
| [`production/00-overview.md`](production/00-overview.md) | 从演示到上线的六件事与顺序 |
| [`production/01-data-migration.md`](production/01-data-migration.md) | 从 SQLite 演示库到 PostgreSQL + Milvus 的数据迁移 |
| [`production/02-load-testing.md`](production/02-load-testing.md) | 压测与容量计划 |
| [`production/03-ratelimit-and-metrics.md`](production/03-ratelimit-and-metrics.md) | 多副本限流与可观测指标 |
| [`production/04-monitoring.md`](production/04-monitoring.md) | 监控与告警 |
| [`production/05-backup-dr.md`](production/05-backup-dr.md) | 备份与灾备：概念到演练 |
| [`production/06-compliance.md`](production/06-compliance.md) | 备案、证书、等保三件事 |
| [`production/07-remaining-work.md`](production/07-remaining-work.md) | 剩余工作清单：哪些真没做 |
| [`production/08-vector-store-decision.md`](production/08-vector-store-decision.md) | 向量存储选型：pgvector 还是 Milvus |
| [`deployment-guide.md`](deployment-guide.md) | 部署与上线指引（开发骨架 / 演示切片） |
| [`operations.md`](operations.md) | 运维、安全与高可用：设计与边界 |
| [`rate-limit-and-security-review.md`](rate-limit-and-security-review.md) | 限流设计与多部门权限安全复核 |
| [`frontend-audit.md`](frontend-audit.md) | 两套前端全面检查报告 |

## 六、变更记录与 PR

| 文档 | 内容 |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | 项目整体改进日志与版本坐标 |
| [`change-manifest.md`](change-manifest.md) | 本轮模块化改动清单总纲 |
| [`git-pr-log.md`](git-pr-log.md) | 分支与 PR 记录 |
| [`pr/README.md`](pr/README.md) | PR 索引（本地 origin：`.data/remote.git`） |
| [`pr/PR-1.md`](pr/PR-1.md) | 修 P0 密钥漏检与 QA 缺陷，补演进页与容器化 |
| [`pr/PR-2.md`](pr/PR-2.md) | 完整功能首页、解析工作台互链、webapp 镜像与一键启动 |
| [`pr/PR-3.md`](pr/PR-3.md) | 全局队列可观测性与权限边界 |
| [`pr/PR-4.md`](pr/PR-4.md) | 任务中心按用户/部门积压聚合 |
| [`pr/PR-5.md`](pr/PR-5.md) | 多用户网关加固五项 + 腾讯云指导页 |
| [`pr/PR-6.md`](pr/PR-6.md) | 14 个真实缺陷案例集 + 图文教学页 |
