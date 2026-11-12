# 运维、安全与高可用：设计与边界

> 这一页的作用是「别把设计当成已完成」。每条都标了 **已实现 / 只设计 / 未做**。

## 1. 服务与端口

| 服务 | 端口 | 状态 | 说明 |
|---|---|---|---|
| nginx | 8080 | 已实现 | `/api/` 反代（SSE 关缓冲）、`/health/` 探活、安全响应头 |
| api | 8000 | 已实现 | uvicorn `--workers 2`，无状态，可 `docker compose up -d --scale api=2` |
| worker-light | — | 已实现 | `ingest,default` 队列，4 并发 |
| worker-heavy | — | 已实现 | `ingest_heavy` 队列，2 并发，可独立扩容 |
| beat | — | 已实现 | 5min 捞回僵死任务、每小时清过期会话、01:00 日报、02:00 对账 |
| postgres | 5432 | 已实现 | 业务元数据、全文索引、审计、日报；触发器维护 tsvector |
| redis | 6379 | 已实现 | AOF；缓存/限流/broker/result/进度通道分 DB 或前缀 |
| milvus | 19530 | 已实现 | standalone + etcd + MinIO；tenant_id 为 partition key |
| minio | 9000/9001 | 已实现 | 原文件、分片、版本快照、中间产物 |

配置全部走 `.env`；镜像里不写死密钥、地址与阈值。生产环境若 `JWT_SECRET_KEY` 仍是默认值，
`lifespan` 会直接抛错拒绝启动。

---

## 2. 安全清单

| 能力 | 状态 | 落点 |
|---|---|---|
| JWT 签名校验 / 过期 / 刷新令牌轮换 / 单点登出 | 已实现 | `core/security.py`、`api/v1/auth.py` |
| bcrypt 密码哈希、httpOnly + SameSite 刷新 Cookie | 已实现 | 同上 |
| 密钥外置、生产默认密钥拒绝启动 | 已实现 | `core/config.py`、`main.py` |
| 统一错误码契约（不回传堆栈） | 已实现 | `core/errors.py` |
| 安全响应头 / CORS 白名单 | 已实现 | nginx + trace 中间件 |
| 文件后缀黑名单 + 扩展名推断 + 首片魔数校验 + 大小/配额 | 已实现 | `services/upload.py` |
| Redis 令牌桶限流（按 IP） | 已实现 | `middleware/rate_limit.py` |
| 租户行级隔离 + 权限集中判定 + 召回双重过滤 | 已实现 | 见 `docs/permission-model.md` |
| 审计只追加（含权限拦截） | 已实现 | `services/audit.py` |
| 规则级 Prompt 注入 / PII 输出拦截 | 已实现（骨架） | `agent/orchestrator.py` |
| 按用户/租户两级限流、并发闸 | 已实现基础，未调参 | `redis_client.acquire_slot` 预留 |
| **三道闸：模型级注入检测、恶意文件沙箱、PII 入库/召回/输出同规则** | 只设计 | B 档 P0 |
| 审计留存策略、脱敏检索、合规导出、异常告警 | 未做 | C 档必做 |
| 密钥轮换 / IP 白名单 / 重放防护 / 依赖漏洞扫描 | 未做 | C 档 |
| HTTPS 证书、飞书/企微回调验签 | 未做（本期无该入口） | C 档 |

---

## 3. 高可用设计

**已实现**
- API 无状态多副本 + nginx upstream 被动健康检查；
- liveness 与 readiness 分离：依赖未就绪返回 503 让 LB 摘流量，但**不重启实例**；
- Milvus 不可用时 ready 标 `degraded`、语义检索自动降级为仅关键词，服务不整体不可用；
- 连接池 `pool_pre_ping` + `pool_recycle`，PG 主备切换后不会一直用断掉的连接；
- 任务 `acks_late` + `reject_on_worker_lost` + `visibility_timeout = hard_limit × 2` +
  PG 台账 + beat 心跳捞回，worker 被杀可重投；
- 跨库一致性：幂等 `chunk_id`、先 PG 后 Milvus、软删异步清、每日差集对账；
- **向量视为可重建的派生数据**：文本在 PG、原文件在 MinIO，Milvus 故障只需重跑 embedding。

**只设计（未演练）**
| 组件 | 方案 | RPO / RTO 目标 |
|---|---|---|
| PostgreSQL | 流复制热备（异步）+ 手动提升 | 秒级 / 分钟级 |
| MinIO | `mc mirror` 定时同步（唯一不可重建资产，优先级最高） | 分钟级 / 分钟级 |
| Redis | AOF + 定时快照，丢失可重建 | 可丢失 / 秒级 |
| Milvus | 不做一等备份，按 chunk 重建 | 小时级 / 随重建时长 |
| nginx | 双 upstream + 手动切换开关，**坚决不做自动脑裂** | — |

**未做**：主备切换实测、拔电演练、备份校验、Prometheus + 告警通道、错误聚合、
全量回归、长稳压测、灰度与回滚脚本。

---

## 4. 日常运维动作

```bash
# 迁移
docker compose exec api alembic upgrade head

# 看任务与队列
docker compose logs -f worker-light worker-heavy beat

# 手动触发对账（PG ↔ Milvus 差集）
docker compose exec api python -c \
  "from app.tasks.maintenance import reconcile_vectors; print(reconcile_vectors.delay().get(timeout=600))"

# 手动补算日报
docker compose exec api python -c \
  "from app.tasks.celery_app import celery_app; celery_app.send_task('app.tasks.maintenance.refresh_stats')"

# 健康检查
curl -i http://localhost:8080/health/ready
```

**上线前必补的 checklist**：压测报告（P95 首 token / 整答 / 错误率）、越权用例集全部拦截、
注入与敏感输出样本拦截率、一次主备切换演练并记录实测 RTO/RPO、一次回滚演练、
监控告警联通、运维手册与故障处理手册、响应时限承诺（SLA）。

---

## 5. 已知运维风险

| 风险 | 缓解 |
|---|---|
| Redis 同时承担 broker/result/缓存/进度，故障面集中；`visibility_timeout` 对长任务有误重投风险 | v0.2 按「数据能不能丢」重划：RabbitMQ 管不可丢投递与 DLQ、Redis 退回可重建状态、Celery 退役 result 依赖、ack 落在业务落库之后。详见 `docs/next-version-messaging-and-plugins.md`；**不是改一处 broker 配置** |
| SSE 同步生成器占线程池线程 | 大规模并发换 ASGI 原生流或独立推送服务；限制单租户同时连接数 |
| 逻辑隔离靠代码约束 | 越权用例集 + 审计回放；强合规客户上独立 Schema |
| 换 embedding 模型要全量重建向量 | 代码里做维度校验并明确报错；按知识库分批重建，评估耗时后再切 |
| `metric_events` / `audit_logs` 无分区 | 生产按月声明式分区 + 冷归档 |
| 中文全文用 `simple` + trgm | 装 zhparser 换 `zhcfg`，触发器改一行，业务代码不动 |
| 解析中间产物未做生命周期清理 | 加保留策略；当前按 `intermediate/{doc}/` 前缀可批量清 |
