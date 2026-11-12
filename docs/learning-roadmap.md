# 学习路线与交付档次对齐

这份骨架是「边学边加固」用的。路线按你那份《全景设计、本周排期与交付档次》的三档口径排，
核心判断不变：**A 档回答能不能做，B 档回答懂不懂行，C 档回答客户敢不敢把服务器交给你。按顺序推进，不要跳级。**

---

## 1. 用这套代码学什么（建议顺序）

| 阶段 | 主题 | 在代码里看哪里 | 动手验证 |
|---|---|---|---|
| ① FastAPI 依赖注入 | `yield` 依赖、全局依赖、依赖覆盖 | `core/deps.py`、`db/session.py`、`api/v1/router.py` | 给 `/documents` 写 TestClient 用例，`override_dependencies` 换掉 `get_current_user` |
| ② JWT 与会话 | access/refresh 轮换、httpOnly Cookie | `core/security.py`、`api/v1/auth.py` | 抓包看 401 → 自动 refresh → 重放原请求 |
| ③ PG 建模 | Mixin、JSONB、TSVECTOR 触发器、GIN、物化路径 | `models/`、`migrations/versions/0001_initial.py` | `EXPLAIN ANALYZE` 看全文与属性筛选是否命中索引 |
| ④ 权限模型 | 纯函数判定 + DB 包装 + 双重过滤 | `services/permission.py`、`tests/test_permission.py` | 用三个演示账号互换部门，验证互相召回不到 |
| ⑤ Celery 与进度 | chain / chord / 幂等 / 节流 / SSE | `tasks/ingest.py`、`services/progress.py`、`api/v1/uploads.py` | 按 `docs/async-upload.md` 第 8 节做七项故障验证 |
| ⑥ 双写一致性 | PG 先写、Milvus 后写、软删、对账 | `tasks/ingest.py::embed_and_index`、`tasks/maintenance.py::reconcile_vectors` | 手动删一个向量，跑对账看 `missing_fixed` |
| ⑦ 混合检索 | FTS + 向量 + RRF + 引用契约 | `services/search.py`、`tests/test_chunking.py` | 用编号/型号类问题对比 keyword / semantic / hybrid 三档 |
| ⑧ Agent 编排 | State / Node / ToolRegistry / 流式 | `app/agent/`、`api/v1/search.py` | 手写一个 `translate` 节点插进 `DEFAULT_ORDER` |
| ⑨ 看板与可观测 | 预聚合、trace_id、指标口径 | `services/stats.py`、`middleware/request_context.py` | 拿一个 trace_id 从审计表还原完整链路耗时 |

Java / SpringBoot 的后续学习建议：把这套分层当成参照物，重点对照
「依赖注入容器 vs FastAPI Depends」「JPA Repository vs SQLAlchemy Repository」
「Spring Security Filter Chain vs 中间件 + 依赖」「@Async / MQ vs Celery」，
理解的是架构职责，不是语法一一对应。

---

## 2. A 档：骨架现在能演示什么

- 一键 Compose 起全栈，bootstrap 造两部门三角色账号；
- 批量上传多个文件，SSE 进度从分片 → 解析 → 向量化 → 可检索；
- 秒传、断点续传、失败重试、大文件走 heavy 队列；
- 混合检索结果可展开到「文档名 / 页码 / 原文片段」；
- 编辑生成 v2、行级 diff、追加式回滚；
- 看板图表 + CSV 导出 + 任务中心 + 审计日志；
- 同一问题用不同部门账号登录，召回范围不同（逻辑隔离）。

**A 档的诚实边界**：不说高并发、不说企业级安全、不说强隔离、不说高可用、不说已上线。

---

## 3. B 档：面试期迭代（按性价比，每项都要有量化结论）

| 优先级 | 事项 | 验证方式（必须可复现） |
|---|---|---|
| P0 | 接真实外部 embedding + zhparser + cross-encoder 重排 | 固定问题集的 MRR / NDCG@k 前后对比表 |
| P0 | 并发流式压测（1/4/8 并发） | P95 首 token、P95 整答、错误率、瓶颈结论 |
| P0 | 安全三道闸 + PII 同规则 | 30 条注入样本 + 20 条敏感输出样本拦截率报表 |
| P1 | RBAC 完整化 + 越权用例集 | 20+ 条越权用例全部拦截，审计可按人回放 |
| P1 | BadCase 闭环 + 反馈采集 | 分类 → 归因 → 回流评估集的完整链路演示 |
| P1 | trace_id 全链路 + 三项 SLO | 一个请求 id 能还原检索/重排/生成各段耗时 |
| P1 | 解析质量评估集 | 字段完整率、表格还原率、OCR 准确率前后对比 |
| P2 | 知识治理：敏感度 / 新鲜度 / 冲突标记 | 10 组冲突样本命中且回答提示「口径不一致」 |
| P2 | 待复核工作台、多格式解析（MinerU 替换） | 换解析器前后一张解析质量对比表 |
| P3 | MCP 工具化 + 多 Agent 分流 | 工具白名单与拒绝调用的审计证据 |

---

## 4. C 档：离企业级交付还差什么

| 缺口 | 客户会怎么问 | 本骨架状态 |
|---|---|---|
| 高可用与容灾演练 | 「数据库挂了多久能恢复？备份试过吗？」 | 只有方案，无实测 RTO/RPO |
| 容量与压测 | 「200 人同时用扛得住吗？」 | 无 QPS 基线、无并发上限 |
| 审计与合规 | 「谁在什么时候查了什么，能查吗？」 | 有表有明细，无留存/导出/脱敏检索策略 |
| 监控告警 | 「出问题你们怎么知道？」 | 无 Prometheus、无告警通道 |
| 文件治理 | 「员工传个 500M 的 PDF 怎么办？带宏的 docx 呢？」 | 分片/大小/魔数已做，病毒扫描与解析沙箱未做 |
| 物理隔离租户 | 「我们和兄弟公司共用一套库会不会串？」 | 仅逻辑隔离 |
| 向量库运维 | 「换 embedding 模型重建全量向量要多久？」 | 无重建耗时评估 |
| 细粒度权限 | 「某个文档只给三个人看，能做到吗？」 | 文档级已做；片段/段落级开关预留 |
| 灰度与回滚 | 「升级出问题怎么退回去？」 | 无版本灰度与迁移回滚规范 |
| 成品前端 / 配额 / 运维文档 SLA | 「员工用什么界面？怎么维护？」 | 有管理页，无完整成品与手册 |

**面试口径**：两三周内能把所有技术方向跑通、每项都有可复现的验证证据；
上线级质量（全量回归、压测调优、长稳、容灾演练）属于团队并行的质量工作。

---

## 5. 三个月精进路线

- **第 1 月 · 把骨架变成有证据的原型**：压测报告、越权用例表、解析质量基线、注入样本拦截率——四份能放进简历的证据；
- **第 2 月 · 把原型变成可运维的骨架**：trace_id 全链路、三项 SLO、灰度与回滚、成本/容量报表；
- **第 3 月 · 把骨架变成能扩展的平台**：飞书接入、MCP 工具网关、Java/Spring 生态对接说明、K8s 迁移评估。

顺序逻辑：**先拿证据，再建体系，最后做扩展**。反过来（先接一堆外部系统）会导致连自己系统的延迟都说不清。
