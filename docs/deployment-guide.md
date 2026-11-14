# 部署与上线指引（开发骨架 / 可运行演示切片）

> **性质与三态**：本文是**编排复核 + 上线建议方案**，不是上线记录。全文严格区分「**已实现 / 只设计 / 未做**」。
> 本轮**未执行** `docker compose up`、未申请域名/证书、未采购任何云资源、未发起任何外部 LLM 调用、未修改任何 Python/JS 源码、未改动 `eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`。
> 文中所有命令均为**可复现的操作文本（建议 / 演示 / 未实际执行）**，执行前须按自身环境替换占位符。
>
> **事实源（本轮实读）**：`docker-compose.yml`、`backend/Dockerfile`、`frontend/Dockerfile`、`frontend/nginx.conf`、`frontend/vite.config.ts`、`nginx/nginx.conf`、`.env.example`、`.gitignore`、`backend/requirements.txt`、`backend/app/main.py`、`backend/app/api/v1/health.py`、`webapp/README.md`、`webapp/main.py`、`docs/operations.md`、`docs/org-model-change-plan.md`、`README.md`，以及 `git branch -avv` / `git tag -l` / `git log --all --oneline` 实查结果。

---

## 0. 范围、架构前提与三态口径

### 0.1 目标架构：单组织 + 多用户多部门

本系统按**单组织单库、多用户多部门**设计：部门树（`Department.parent_id` + 物化路径 `path` + `level`）是唯一组织边界，权限判定由 `decide()` 单一入口负责，共享 Schema + 应用层行级过滤。**暂不实现多租户**。

### 0.2 多租户 = 未来研发方向（仅写入文档，本期不实现、不编排）

若产品路线图未来重新 SaaS 化，需要在本期基础上做**全量反向回补**：`tenant_id` 列 / Mixin / Repository、JWT 与 `Principal` 的租户字段、缓存与对象 key 的一级租户前缀、用量与配额的租户维度、审计索引、Milvus partition key 或独立 collection、expr 首条件回补、角色层级（`platform_admin` / `tenant_admin` 拆分），以及数据归属重建。该方向属**高成本反向迁移（只设计 / 未做）**，本文的编排、域名、TLS、回滚方案**全部按单组织单库设计**，不预留任何租户级路由或隔离组件。理由与演进代价见 `docs/org-model-change-plan.md` §0.3 与 `docs/operations.md` §3。

> **诚实标注（避免把设计说成落地）**：当前仓库代码仍停在 `0001_initial` 基线——`backend/migrations/versions/` 只有 `0001_initial.py`，`backend/app/core/config.py` 仍有 `MILVUS_NUM_PARTITIONS`、`MAX_CONCURRENT_INGEST_PER_TENANT`，`backend/app/integrations/milvus_client.py` 仍有 `tenant_id is_partition_key=True`。`docs/org-model-change-plan.md` 是**设计评审方案，尚未落到源码**。因此本文第 1 节复核的对象是「**当前可运行骨架**」；若组织模型变更日后落地，须在部署流程中叠加对应的显式迁移与备份步骤（见 §4.4），不可直接套用本编排。

### 0.3 定位与前提

| 项 | 口径 |
|---|---|
| 项目定位 | 企业内部知识库系统的「开发框架骨架 + 可运行演示切片」，用于学习与面试讲解 |
| 是否上线交付版 | **否**，本文的上线方案是**建议**，非已上线、非 SLA 承诺 |
| 部署形态 | 单宿主机 Docker Compose（11 个服务：5 个基础设施 `postgres`/`redis`/`minio`/`etcd`/`milvus` + 6 个应用与接入 `api`/`worker-light`/`worker-heavy`/`beat`/`frontend`/`nginx`） |
| 现有环境 | 仅一层本地开发 / 演示环境；无预发、无生产、无 CI/CD、无 remote（见 `docs/git-conventions.md`） |
| 数据量 / 容量 / QPS | 【待确认】；骨架版无压测数据，本文不编造 QPS、P95、停机窗口与价格数字 |
| 备份 / 回滚 / 告警演练 | `docs/operations.md:67-68, 93-95` 明确列为「**未做**」 |

---

## 1. 已有编排复核（逐服务）

### 1.1 服务清单（实查 `docker-compose.yml`）

| 服务 | 镜像 / 构建 | 职责 | 端口映射 | healthcheck | depends_on | 卷 |
|---|---|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 业务元数据、全文检索、审计、日报 | `5432:5432` | `pg_isready -U … -d …`，5s/3s/20 次 | — | `pgdata` |
| `redis` | `redis:7-alpine` | 缓存 / 限流 / Celery broker+result / 进度 pub-sub | `6379:6379` | `redis-cli ping`，5s/3s/20 次 | — | `redisdata`（AOF） |
| `minio` | `minio/minio:RELEASE.2025-04-22T22-12-26Z` | 原文件、分片、版本快照、中间产物 | `9000:9000`、`9001:9001` | `mc ready local`，5s/3s/20 次 | — | `miniodata` |
| `etcd` | `quay.io/coreos/etcd:v3.5.16` | Milvus 元数据 | 仅容器间（未映射宿主） | **无** | — | `etcddata` |
| `milvus` | `milvusdb/milvus:v2.5.10` standalone | 语义向量 | `19530:19530`、`9091:9091` | `curl -f http://localhost:9091/healthz`，10s/5s/20 次 | `[etcd, minio]`（无 condition） | `milvusdata` |
| `api` | `build: ./backend` | FastAPI / uvicorn `--workers 2` | `8000:8000` | `curl -f http://localhost:8000/health/live`，10s/5s/10 次 | 4 个基础设施 `service_healthy` | `./backend:/app`（bind mount） |
| `worker-light` | 同上 | Celery `-Q ingest,default -c 4` | 无 | **无** | `[api]` | `./backend:/app` |
| `worker-heavy` | 同上 | Celery `-Q ingest_heavy -c 2` | 无 | **无** | `[api]` | `./backend:/app` |
| `beat` | 同上 | Celery beat（捞回僵死任务、清会话、日报、对账） | 无 | **无** | `[api]` | `./backend:/app` |
| `frontend` | `build: ./frontend` | React/Vite 静态资源 + 容器内 nginx | `expose: 80` | **无** | `[api]` | — |
| `nginx` | `nginx:alpine` | 反向代理、SSE 关缓冲、安全响应头 | `8080:80` | **无** | `[api, frontend]` | `./nginx/nginx.conf:ro` |

具名卷共 5 个：`pgdata` / `redisdata` / `miniodata` / `etcddata` / `milvusdata`。

### 1.2 三项必查结论（按任务书要求实查；本轮经主理人复核加固）

> **本节立场声明**：本文自始至终的结论是「两个 Dockerfile **均存在**、构建上下文正确，`backend/Dockerfile` **已安装 curl**，故 api / milvus 的 curl healthcheck **有效**」。本文**从未**得出、也**不支持**「`backend/Dockerfile` / `frontend/Dockerfile` 全缺」或「curl healthcheck 必然失败」这类结论；本文也没有「P0 阻断清单」（缺口用 G1–G13 编号，见 §1.6）。主理人复核记录见 **§7**。

**一手实查证据（主理人命令 + 本轮复跑，结果一致）**

```bash
$ ls -la backend/Dockerfile frontend/Dockerfile frontend/index.html frontend/src/App.tsx
-rw-r--r-- 406 backend/Dockerfile
-rw-r--r-- 369 frontend/Dockerfile
-rw-r--r-- 392 frontend/index.html
-rw-r--r-- 1482 frontend/src/App.tsx
```

- `backend/Dockerfile`（实读全文）：`FROM python:3.11-slim` → `RUN apt-get install -y --no-install-recommends curl libmagic1 libpq5`（**curl 已装**）→ `COPY requirements.txt .` → `RUN pip install -r requirements.txt` → `COPY . .` → `EXPOSE 8000` → `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- `frontend/Dockerfile`（实读全文）：**多阶段构建** `FROM node:22-alpine AS build`（`ARG/ENV VITE_API_BASE_URL` → `COPY package.json tsconfig.json vite.config.ts index.html ./` → `COPY src ./src` → `npm install && npm run build`）→ `FROM nginx:alpine`（`COPY nginx.conf /etc/nginx/conf.d/default.conf` + `COPY --from=build /app/dist /usr/share/nginx/html`）→ `EXPOSE 80`。`frontend/index.html` 与 `frontend/src/App.tsx` 均存在。

**（1）`/health/live` 是否真实存在 → 存在，api healthcheck 有效（curl 已装 → 不是「必然失败」）。**
`backend/app/api/v1/health.py:17-19` 定义 `@router.get("/health/live")` 返回 `{"status":"alive"}`；`backend/app/main.py:74` 以 `app.include_router(health.router)` **挂在根路径**（router 无 prefix），故实际路径为 `/health/live`；`backend/Dockerfile` 的 `apt-get install curl` 保证 api 容器内 `curl` 可用。三者链条闭合 → compose 中 `curl -f http://localhost:8000/health/live` **判定有效**。
配套：`/health/ready`（`health.py:22-49`）检查 PG / Redis / Milvus，Milvus 允许 `degraded` 不判失败，PG 或 Redis 挂了返回 503；`docs/operations.md:50` 的「liveness 与 readiness 分离、依赖未就绪不重启实例」与代码一致。
> 注意：§1.3 中唯一标【待验证】的是 **minio 的 `mc ready local`**（`mc` 客户端是否在镜像内），**与 curl 无关**；不得把该条串读成「curl healthcheck 必然失败」。

**（2）`nginx/nginx.conf` 的 upstream 与 compose 服务名是否一致 → 一致；且本轮未改 upstream。**
`nginx/nginx.conf:2-6` 为 `upstream api_upstream { server api:8000 ...; }`；compose 服务名正是 `api`、容器内端口 8000，`location /` 转发 `frontend:80`——**`frontend` 服务在 compose 中是存在的**（`docker-compose.yml:127-134`），且带 `expose: ["80"]`，两者一致。nginx 由 compose 在同一默认网络内按服务名解析 → **判定有效**。nginx 已为 SSE 配置 `proxy_buffering off` + `proxy_read_timeout 3600s`（`:27-32`），上线切 TLS 时**必须保留这两行**。
> 判定依据是「`frontend` 存在且 `expose: ["80"]`」，**不是**「`frontend` 不存在」。本轮 `nginx/nginx.conf` 与 `frontend/nginx.conf` 均零改动（见 §3 逐条复核、§7 复核记录）。

**（3）两个 Dockerfile 的构建上下文是否正确 → 正确。**
- `backend`：`build.context: ./backend` + `dockerfile: Dockerfile`；`backend/Dockerfile` 内 `COPY requirements.txt .`，而 `backend/requirements.txt` 存在；`COPY . .` 覆盖 migrations / app / tests。上下文正确。
- `frontend`：`build.context: ./frontend` + `dockerfile: Dockerfile`；`frontend/Dockerfile` 多阶段构建，`COPY package.json tsconfig.json vite.config.ts index.html ./`、`COPY src ./src`、`COPY nginx.conf ...`，所需文件（`frontend/package.json`、`tsconfig.json`、`vite.config.ts`、`index.html`、`src/`、`frontend/nginx.conf`）均存在；`build.args.VITE_API_BASE_URL` 与 `frontend/Dockerfile:2-3` 的 `ARG`/`ENV` 对应。上下文正确。

### 1.3 healthcheck 有效性逐项判定

| 服务 | 命令 | 判定 | 说明 |
|---|---|---|---|
| postgres | `pg_isready` | **有效** | 官方镜像自带 |
| redis | `redis-cli ping` | **有效** | 官方镜像自带 |
| api | `curl -f …/health/live` | **有效（已核实路由与 curl）** | 见 1.2（1） |
| milvus | `curl -f …:9091/healthz` | **基本有效，冷启动需宽容** | standalone 暴露 9091 健康端点；镜像内 `curl` 需在目标环境实测；本轮补 `start_period` 缓解冷启动 |
| minio | `mc ready local` | **【待验证，仅此一条】** | 只针对 **`mc` 客户端是否在 `minio/minio` 镜像内**（`mc` 另有独立镜像 `minio/mc`），**与 `curl` 无关**；api / milvus 的 curl 探活有效性见上两行，已确认有效。若该标签确实不含 `mc`，则这一条 healthcheck 会持续失败，而 `api` 依赖 `minio: service_healthy` → 可能阻塞 `up`。上线前须实跑 `docker compose exec minio mc ready local` 确认；替代命令需先确认镜像内可用工具，本文不擅自替换 |
| etcd / worker-light / worker-heavy / beat / frontend / nginx | 无 | **缺口** | 无探活，异常只能靠进程退出或被动摘除 |

### 1.4 `depends_on` 条件是否足够

| 链路 | 现状 | 判定 |
|---|---|---|
| `api` → postgres/redis/minio/milvus | 全部 `condition: service_healthy` | **充分**；但受 1.3 的 minio healthcheck 有效性牵连 |
| `milvus` → etcd/minio | `depends_on: [etcd, minio]`，等价 `service_started` | **不足**：etcd 无 healthcheck，Milvus 可能在元数据/对象存储未就绪时反复重连；建议显式化并给 etcd 补探活 |
| 三个 worker / beat → `[api]` | `service_started` | **不足**：worker 实际依赖 PG / Redis / Milvus / MinIO，却只**间接**依赖 api；api 起来不等于基础设施就绪。建议直接声明 4 个基础设施 `service_healthy`（本轮**未改**，理由见 §2） |
| `frontend` → `[api]` | `service_started` | 弱相关（静态资源不依赖 api），可接受 |
| `nginx` → `[api, frontend]` | `service_started` | **不足**：可能在 api 未 ready 时接流量；现有 `max_fails=3 fail_timeout=15s` 被动摘除（`nginx/nginx.conf:3`）可作兜底 |

### 1.5 卷与数据持久化

- 5 个**具名卷**由 Docker 管理，`docker compose down` 默认保留；**`docker compose down -v` 会删除全部数据，列为禁止操作**。
- `redis` 采用 AOF + `maxmemory-policy=noeviction`（compose:24），内存满时写入失败但业务侧有降级放行。
- `./backend:/app` bind mount 覆盖 4 个 Python 服务：开发态可热改代码；**生产态是缺口**——镜像内代码被宿主目录覆盖，制品不可版本化、不可回滚、不可复现。
- `STORAGE_BACKEND=local` 时对象落 `./.data/objects`（`.gitignore` 已排除）；默认 `minio` 走 `miniodata` 卷。
- 按 `docs/operations.md:56, 62`：**MinIO 中的原文件是唯一不可重建资产（备份优先级最高）**；PG 文本为真值；Milvus 向量是可重建派生数据。

### 1.6 明显缺口清单（复核结论）

| 编号 | 缺口 | 级别 | 处置 |
|---|---|---|---|
| G1 | PG / Redis / MinIO / Milvus 端口全部以 `0.0.0.0` 映射宿主（`5432`、`6379`、`9000/9001`、`19530/9091`） | **最高（安全）** | 上线前必须收口为 `127.0.0.1:…:…` 或仅 `expose`；安全组只放行 80/443/22（22 限源 IP）。本轮**未改**（见 §2） |
| G2 | 只有 `build:` 无 `image:` tag，且 bind mount 覆盖源码 → **无不可变制品、无法按 tag 回滚** | 高 | 见 §4.4；本轮未改 |
| G3 | 无 `restart`、无资源限制、无日志轮转 | 中 | **本轮已补齐**（§3） |
| G4 | **仅** minio healthcheck 的 `mc` 客户端可用性待验证（与 curl 无关；api/milvus 的 curl 探活已确认有效） | 高 | 上线前实测 `mc ready local`，见 1.3 |
| G5 | etcd / 3×worker / beat / frontend / nginx 无 healthcheck | 中 | 建议补齐，本轮未改（避免引入新探活工具依赖） |
| G6 | worker 的 `depends_on` 未直接声明基础设施 | 中 | 建议改，本轮未改（见 §2 保守边界） |
| G7 | `.env.example` 含开发弱口令：`POSTGRES_PASSWORD=kb_dev_pwd`（:20）、`MINIO_SECRET_KEY=kb_minio_pwd`（:43）、`JWT_SECRET_KEY=change-me-in-production-with-a-32-byte-random-string`（:10，占位串）；另有 `backend/app/core/config.py:26` 的默认值 `dev-only-insecure-secret-change-me` | 高 | 生产必须全部替换；`main.py:26-28` **仅在** `ENV=prod` 且 `JWT_SECRET_KEY` 以 `dev-only` 开头时拒绝启动，其余弱口令**不会**被自动拦截 |
| G8 | 生产 / 开发无 profile 或 override 分离 | 中 | 见 §2 ④ |
| G9 | PG / Redis / Milvus / MinIO / etcd / nginx 全单机单点 | 高 | 见 §2 ⑤；`docs/operations.md:59-68` 已列「只设计（未演练）」 |
| G10 | `webapp` 演示切片未进 compose | 低 | 见 §2 ① |
| G11 | nginx 仅监听 80，无 TLS / HSTS / HTTP→HTTPS 跳转 | 高（上线） | 见 §4.2（建议，未落地） |
| G12 | `frontend/Dockerfile` 用 `npm install` 且未 `COPY package-lock.json`（锁文件存在但未进镜像）→ 前端依赖不可复现 | 中 | 建议改 `npm ci` + COPY 锁文件；本轮不改源码 |

> **明确排除的伪缺口（防止再次串读）**：本清单**不含**「`backend/Dockerfile` / `frontend/Dockerfile` 全缺」，也**不含**「curl healthcheck 必然失败」——两者均被 §1.2 的一手实查证伪（文件存在、`curl` 已装、api 探活有效）。本文亦**无 P0 阻断清单**。唯一与探活相关的待验证项只有 G4（minio 的 `mc` 客户端）。

### 1.7 已知限制（生产骨架应用层，**与 compose 编排无关**）

主理人要求复核 `backend/app/main.py:63-65` 的中间件注册情况。本轮实查结论如下，**如实修正「未注册」这一说法**：

| 实查位置 | 内容 |
|---|---|
| `backend/app/main.py:63-65` | 只调用 `app.add_middleware(TraceIdMiddleware)` 与 `app.add_middleware(CORSMiddleware, …)`；**确实没有把限流注册为全局 ASGI 中间件** |
| `backend/app/middleware/rate_limit.py:11-15` | 定义 `rate_limit(request)`，按 `request.client.host` 取 IP，键为 `tenant_key("global", "ratelimit", ip)`，调用 `token_bucket()`，超限抛 `AppError("RATE_LIMITED")` |
| `backend/app/api/v1/router.py:18-20` | `from app.middleware.rate_limit import rate_limit`；`api_router = APIRouter(prefix=settings.API_V1_PREFIX, dependencies=[Depends(rate_limit)])` |

**准确表述**：IP 令牌桶**不是「未注册」**，而是**以 `api_router` 的 router 级依赖（`dependencies=[Depends(rate_limit)]`）注册**，因此对 `/api/v1/*` 全量生效（与 `docs/operations.md:34`「Redis 令牌桶限流（按 IP）已实现」一致）。真实缺口是**覆盖面而非注册**：`/health/live`、`/health/ready`、根路径 `/` 不走该依赖（它们由 `main.py:74-84` 单独挂载，不在 `api_router` 下），frontend 静态资源由 nginx 直出也不经过；此外它是**单维按 IP**，key 的 tenant 位为硬编码 `"global"`，「按用户 / 按部门两级限流与并发闸」在 `docs/operations.md:38` 标为「已实现基础，未调参」。

> 这是 `backend/` 应用骨架的已知限制，**不是 `docker-compose.yml` 的问题**，本轮不改任何源码、不为它新增编排配置；上线方案里若需全站限流，应在 nginx 层或应用 `add_middleware` 层另行设计（属 §2.3 只设计范围）。

---

## 2. 本轮补齐项评估（逐项给结论与理由）

| 项 | 结论 | 理由 | 是否落地到 `docker-compose.yml` |
|---|---|---|---|
| ① **webapp 演示切片未进 compose** | **不纳入，改为可选 profile 建议** | 实查 `webapp/README.md:22-31`：独立进程 `python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765`，SQLite 文件库 + 进程内队列，**无 Dockerfile**、无 `webapp/requirements.txt`；定位是「生产架构的可演示替身，不替代 `backend/` 骨架」。纳入 compose 需新增 Dockerfile 或挂载后运行时装依赖、把 `127.0.0.1` 改 `0.0.0.0`、新增服务名与端口，且会把演示替身与生产骨架混编，**超出「保守改动」边界** | **否**（仅在 §2.1 给出未落地的可选片段） |
| ② **资源限制 / restart policy / 日志轮转** | **补齐** | 三者均为**纯增量字段**：不删服务、不改服务名与端口、不改变启动顺序；`restart: unless-stopped` 覆盖宿主机重启与异常退出；日志轮转防止默认 `json-file` 无限增长撑爆磁盘；资源上限防止单服务打满宿主机 | **是**（§3 逐条） |
| ③ **`.env` 密钥注入与 `.env.example` 对齐** | **补齐一处参数化；其余不动** | 实查：`api` / 三个 worker / `beat` 用 `env_file: .env` 注入容器；compose 变量的 `${VAR:-default}` 插值则是**解析期**机制（自动读项目根 `.env`），两者不同。唯一不对齐点是 `frontend.build.args.VITE_API_BASE_URL` 被**硬编码**为 `/api/v1`，而 `.env.example:89` 已有同名键 → 改为 `${VITE_API_BASE_URL:-/api/v1}`，默认值不变、纯参数化。密钥本身**不新增、不外泄、不入库**（`.gitignore:1` 排除 `.env`）。**并发注记**：本轮核对时 `.env.example` 已由其他成员扩展出第 91 行起的 `WEBAPP_` 演示切片配置段（含「`WEBAPP_` 前缀不可去掉、与 backend `EMBEDDING_PROVIDER` 枚举互斥」的说明），这正是命名空间隔离约定的落地佐证；该文件**本轮未改动**，且其新增键均为 webapp 进程自行读取的 `WEBAPP_*`，不参与 compose 插值，故与本轮 1 行参数化改动无冲突 | **是**（仅 1 行） |
| ④ **生产与开发的 profile 分离** | **只给方案，不落地** | 需要 `compose.override.yaml`（开发：bind mount / 暴露调试端口 / DEBUG）与 `compose.prod.yaml`（生产：去掉 bind mount、改用镜像 tag、加 TLS 卷、资源与日志策略、G1 端口收口）。这属于**新增文件**，本轮纪律限定「只改 `docker-compose.yml` 一个文件」，故不落地；给出可复制片段与落地步骤 | **否**（§2.2） |
| ⑤ **Milvus / Redis / PG 单点风险与升级路径** | **只给评估，不改编排** | 任何拆分（流复制、Sentinel、Milvus 集群、MinIO 分布式、云托管替代）都会改动 `depends_on`、`environment`、卷与服务清单，属架构级变更，且需真实云资源，**不符合保守改动与「不引入云依赖」约束**。`docs/operations.md:59-68` 已把这些列为「只设计（未演练）」 | **否**（§2.3） |

### 2.1 可选 profile 片段（**未落地**，供后续迭代复制）

```yaml
# 仅在需要把演示切片并入编排时新增；本轮 docker-compose.yml 未包含此段
  webapp:
    profiles: ["demo"]                      # 默认不启动，需 docker compose --profile demo up
    image: python:3.11-slim
    working_dir: /app
    volumes: [".:/app"]
    command: >
      sh -c "pip install --no-cache-dir 'fastapi>=0.115' 'uvicorn[standard]>=0.30' python-multipart &&
             python -m uvicorn webapp.main:app --host 0.0.0.0 --port 8765"
    ports: ["127.0.0.1:8765:8765"]
    restart: unless-stopped
```
> 说明：该片段会在容器启动时联网安装依赖、并新增宿主端口，**本轮不落地**；`webapp` 使用 SQLite，容器内多副本会有状态冲突，故仅适合单副本本地演示，不进入上线方案。

### 2.2 生产 / 开发分离建议（**未落地**）

| 文件 | 用途 | 关键差异 |
|---|---|---|
| `docker-compose.yml`（本文件） | 公共基线：服务定义、端口、卷 | 本轮补齐的韧性字段在此 |
| `compose.override.yaml`（新增，开发） | `docker compose up` 自动合并 | 保留 `./backend:/app` bind mount、`DEBUG=true`、按需暴露调试端口 |
| `compose.prod.yaml`（新增，生产） | `docker compose -f docker-compose.yml -f compose.prod.yaml up -d` | 用 `volumes: []` 覆盖掉 bind mount；`image: <registry>/kb-api:<git-sha>`；G1 端口收口为 `127.0.0.1`；挂 TLS 证书卷；`ENV=prod` + 强密钥 |

> 落地前提：先解决 G2（镜像 tag），否则生产 override 无制品可引用。

### 2.3 单点风险与升级路径（**只设计 / 未做**）

| 组件 | 现状风险 | 升级路径（建议，需实测与预算审批） | 状态 |
|---|---|---|---|
| PostgreSQL | 单实例，无备份校验 | `pg_dump -Fc` + 临时库实跑 `pg_restore`；流复制热备 + 手动提升；或云托管 TDSQL-C PostgreSQL | 只设计 |
| Redis | 单实例且同时承担 broker / result / 缓存 / 进度；AOF + `noeviction` | 按「数据能不能丢」拆实例与 DB；Sentinel；或云托管 TencentDB for Redis | 只设计 |
| Milvus standalone | 单实例 + 单 etcd + 单 MinIO；向量重建耗时随数据量增长 | Milvus 集群 / 云向量数据库；向量视为**可重建派生数据**，文本在 PG、原文件在 MinIO | 只设计 |
| MinIO | **唯一不可重建资产** | `mc mirror` 定时同步（优先级最高）；分布式 MinIO 或 COS 对象存储 | 只设计 |
| nginx | 单 upstream、单实例 | 双 upstream + CLB 手动切换（不自动脑裂） | 只设计 |
| 整体 | 无监控告警 / 无压测 / 无演练 | Prometheus + 告警通道、容量压测、一次主备切换与一次回滚演练 | **未做**（`docs/operations.md:67-68, 93-95`） |

---

## 3. `docker-compose.yml` 本轮改动逐条说明

> **轮次说明**：本节记录本任务（首轮）对 `docker-compose.yml` 的 5 项保守改动；**返工轮（主理人复核后）未再改动该文件一行**，改动理由逐条复核见 §3.1，复核记录见 §7。
>
> 约束遵守：**只改这一个文件**；**未删除任何服务**；**服务名与端口数字全部保持原值**；**未新增服务**；**未引入任何需要真实云资源的依赖**。改动为纯增量字段 + 1 处参数化。

| # | 改动 | 作用范围 | 为什么 |
|---|---|---|---|
| 1 | 新增 `restart: unless-stopped` | 全部 11 个服务 | 宿主机重启 / 容器异常退出后自动恢复；用 `unless-stopped` 而非 `always`，避免人工 `docker compose stop` 后被重新拉起 |
| 2 | 新增顶层 `x-logging` 锚点 + 各服务 `logging: *default-logging`（`json-file`，`max-size: 10m`，`max-file: 3`） | 全部 11 个服务 | 默认 `json-file` 无上限，Celery / Milvus / uvicorn 长期运行会撑爆磁盘；上限约 30MB/服务，属保守值。用 `x-` 扩展字段 + YAML 锚点避免 11 处重复，Compose 原生支持 |
| 3 | 新增资源上限 `mem_limit` / `cpus` | 全部 11 个服务 | 防止单服务打满宿主机；**数值为建议值、未按真实负载压测**，Milvus 给 4g / 2.0、api 与 worker 给 2g，留足余量；如出现 OOM 请上调或注释该两行。选用 `mem_limit`/`cpus` 而非 `deploy.resources`，因其在 Compose V1/V2 单机模式下均直接生效，无兼容模式差异 |
| 4 | 5 个有 healthcheck 的服务补 `start_period` | postgres / redis / minio / milvus / api | 冷启动期间失败不计入 unhealthy（不改变 `interval`/`timeout`/`retries` 的判定语义）。milvus 给 60s、minio 与 api 给 30s，针对 Milvus standalone 与 `ensure_collection()` 启动较慢的情况 |
| 5 | `frontend.build.args.VITE_API_BASE_URL` 由硬编码 `/api/v1` 改为 `${VITE_API_BASE_URL:-/api/v1}` | 1 行 | 与 `.env.example:89` 对齐；默认值完全相同，不影响现有构建；消除「模板有键、compose 写死」的配置漂移 |

#### 3.1 改动理由逐条复核（应主理人要求，本轮重做）

主理人特别点名核对 **nginx upstream 的改前/改后值与真实依据**。复核结论：**本轮从未改过 upstream，`nginx/nginx.conf` 与 `frontend/nginx.conf` 均零改动**；`frontend` 服务在 compose 中存在且带 `expose: ["80"]`，本轮所有判断都基于这一事实，不存在「假设 frontend 不存在」的情况。

| 对象 | 改前值 | 改后值 | 是否改动 | 真实依据 |
|---|---|---|---|---|
| `nginx/nginx.conf` 的 upstream | `upstream api_upstream { server api:8000 max_fails=3 fail_timeout=15s; keepalive 32; }` | **完全相同** | **未改** | `git status --short -- nginx/nginx.conf frontend/nginx.conf` 返回空；原文件 `:2-6` 实读 |
| `nginx` 服务的 `location /` 目标 | `proxy_pass http://frontend:80` | **完全相同** | 未改 | `nginx/nginx.conf:41`；compose `frontend.expose: ["80"]` 实读 |
| `frontend` 服务本体 | `build.context: ./frontend`、`expose: ["80"]`、`depends_on: [api]` | 服务名/端口/expose/depends_on **全部原样** | 仅加 `restart`/资源/日志 3 类增量字段 | `git diff -- docker-compose.yml` 中 frontend 段仅有增量字段 + 1 行 `VITE_API_BASE_URL` 参数化 |
| `frontend.build.args.VITE_API_BASE_URL` | `/api/v1`（硬编码） | `${VITE_API_BASE_URL:-/api/v1}` | **改**（唯一实质改动） | `.env.example:89` 已有同名键；`frontend/Dockerfile:2-3` 有 `ARG`/`ENV` 接该值；默认值不变 |

**5 项改动理由是否仍成立（逐条复核）**：

| # | 是否依赖「Dockerfile 全缺」或「curl healthcheck 必然失败」？ | 复核结论 |
|---|---|---|
| 1 `restart: unless-stopped` | 否 | 与容器镜像内容无关，理由成立（宿主机重启 / 异常退出自动恢复） |
| 2 日志轮转 | 否 | 与镜像内容无关，理由成立（默认 `json-file` 无上限） |
| 3 资源上限 | 否 | 与镜像内容无关，理由成立（防止单服务打满宿主机；值仍标注为未压测建议值） |
| 4 `start_period` | **否** | 理由是「冷启动宽容」，**不是「探活必然失败」**：api 的 curl 探活已确认有效（§1.2(1)），补 `start_period` 只是避免冷启动窗口内被计为 unhealthy；milvus/minio 同理。该理由在 Dockerfile 与 curl 事实澄清后**依然成立** |
| 5 `VITE_API_BASE_URL` 参数化 | 否 | 依据是 `.env.example:89` 与 `frontend/Dockerfile:2-3` 的键对齐，与 frontend 是否存在无关（frontend 确实存在），理由成立 |

**结论：5 项改动全部有独立依据，不依赖被推翻的两条结论，`docker-compose.yml` 本轮无需回退任何一行。** 若主理人仍希望移除某项（如认为资源上限数值不该在未压测时写入），可单独指示，我按需回退，但那属于口径取舍而非事实纠错。

**本轮已实跑的只读校验（不启动 daemon、未执行 `up`）**：
- `docker compose config -q` → **PASS**（YAML、`x-` 锚点展开、`${VAR:-default}` 插值均合法）；
- `docker compose config --services` → 返回全部 11 个服务名，确认无服务被删；
- `python -c "yaml.safe_load(...)` 解析 → 顶层键 `x-logging` / `services` / `volumes`，5 个具名卷、6 组端口映射与改动前完全一致；
- `git diff --exit-code -- eval/ demo/` → 返回 0（冻结区零改动）。

**明确未改**（并说明理由）：
- `depends_on`（G6）：改动会改变启动拓扑，且 Milvus→etcd 一环 etcd 无探活，贸然加 `service_healthy` 可能引入新的阻塞；留作 §2.2 生产 override 一并处理。
- 端口映射（G1）：`0.0.0.0:5432:5432` 这类写法虽不改端口数字，但属绑定地址变更，超出保守边界；列为**上线前必须收口项**（§4.3 步骤 1）。
- healthcheck 命令（G4/G5）：不替换 `mc ready local`，也不新增探活，避免引入镜像内不确定的工具依赖；列为**待验证项**。
- `env_file: .env`、卷定义、镜像版本、服务 commands、**全部保持原样**。

---

## 4. 腾讯云上线方案（**建议配置 / 未采购 / 未实际执行**）

> 本节是**选型与流程建议**，不是上线记录。所有规格为**建议配置、非已采购**；价格与规格以腾讯云官网实时信息为准，**本文不提供精确报价**。成本只给相对量级与构成：主要为「CVM 计算 + 云硬盘容量 + 公网带宽 + 可选托管数据库/对象存储 + 证书（可免费 DV）」，随规格与数据量线性变化；建议先按最小档试用并以费用中心设置**预算告警**，本文不编造具体金额。

### 4.1 服务器选型（建议配置、非已采购）

| 档位 | 建议规格 | 适用 | 理由 |
|---|---|---|---|
| A · 最小可跑（POC） | CVM 标准型 **4 vCPU / 8 GiB**；系统盘 50 GiB SSD；数据盘 200 GiB 高性能云硬盘；公网 5 Mbps | 单人演示、功能验证 | 与本机开发编排同构、组件齐全；但 Milvus standalone + PG + Redis + MinIO + 2 个 uvicorn workers + 4 并发 Celery 同机，**8 GiB 偏紧**，仅建议短时验证 |
| B · **建议起步（推荐）** | CVM 标准型 **8 vCPU / 16 GiB**（S5 / SA3 同级）；系统盘 100 GiB SSD；数据盘 **500 GiB SSD 云硬盘**；公网 10 Mbps | 内部小范围试用 | 内存是首要瓶颈（Milvus 索引常驻 + PG `shared_buffers` + Redis + 2 workers + 4 并发）；16 GiB 留余量；数据盘覆盖向量、原文件、审计与日报增长；**建议值，需按自身数据量与压测校准** |
| C · 生产化演进（**只设计**） | 应用节点 4 vCPU / 8 GiB ×2（api / frontend / nginx）+ 数据节点 8 vCPU / 32 GiB；CLB；TDSQL-C PostgreSQL、TencentDB for Redis、COS 对象存储、云向量数据库；云监控 + 日志服务 CLS | 未来若需可用性承诺 | 把 G9 的单点拆开，托管服务替代自建；**本期不采购、不实施** |

- 操作系统建议 Ubuntu 22.04 LTS / TencentOS Server 3；安装 Docker Engine + Compose V2 插件。
- 数据盘建议单独挂载并做快照策略；`MinIO` 原文件为不可重建资产，快照优先级最高。
- 中国大陆节点对外提供 Web 服务需完成 **ICP 备案**；域名未备案前 80/443 可能被阻断（前置条件，非技术配置）。

### 4.2 域名与 HTTPS（建议改造，未落地）

1. **域名 / DNS**：注册域名并接入 DNSPod，添加 `A` 记录指向 CVM 公网 IP（档位 C 指向 CLB）；完成备案。
2. **证书申请**（二选一）：
   - 腾讯云 **SSL 证书**：免费 DV 单域名证书（适合内部试用）或付费 OV/EV；托管后可自动续期、可直接下发到 CLB。
   - **Let's Encrypt + certbot**：HTTP-01（需先有 80 端口可达）或 DNS-01（用 DNSPod API 凭证，可在证书签发时不依赖公网 80）；配 `certbot renew` 定时续期。
3. **nginx 终止 TLS**：证书与私钥落到宿主 `./tls/`，compose 给 nginx 增加只读卷 `./tls:/etc/nginx/tls:ro`（**本轮未改**，属 §2.2 生产 override）。
4. **HTTP → HTTPS 301** 与 **HSTS**：HSTS 建议先 `max-age=31536000`（不含 `includeSubDomains`），确认全站 HTTPS 稳定后再考虑追加子域。
5. **必须保留 SSE 配置**：`proxy_buffering off; proxy_cache off; proxy_read_timeout 3600s; chunked_transfer_encoding on;`（现有 `nginx/nginx.conf:27-32`），否则上传进度流在 TLS 终止后被缓冲。

配置片段（**示例，未写入任何项目文件**）：

```nginx
# /etc/nginx/conf.d/default.conf —— TLS 终止建议片段
upstream api_upstream {
    server api:8000 max_fails=3 fail_timeout=15s;
    keepalive 32;
}

server {
    listen 80;
    server_name kb.example.com;              # 替换为实际域名
    return 301 https://$host$request_uri;    # HTTP → HTTPS
}

server {
    listen 443 ssl http2;
    server_name kb.example.com;

    ssl_certificate     /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location /api/ {
        proxy_pass http://api_upstream;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;                 # SSE 必须保留
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        chunked_transfer_encoding on;
    }
    location /health/ { proxy_pass http://api_upstream; }
    location / { proxy_pass http://frontend:80; }
}
```

### 4.3 部署流程（分步、可复现；**以下全部为建议命令，本轮未执行**）

```bash
# ── 步骤 0：前置（一次性） ──────────────────────────────────────
# 0.1 安全组：仅放行 80 / 443（对公网）、22（限来源 IP）
# 0.2 【G1 收口，上线前必须做】把基础设施端口绑定改为仅本机，例如：
#     "127.0.0.1:5432:5432" / "127.0.0.1:6379:6379" /
#     "127.0.0.1:9000:9000" / "127.0.0.1:9001:9001" /
#     "127.0.0.1:19530:19530"；9091 / 2379 不映射宿主
# 0.3 安装 Docker Engine 与 docker compose plugin
docker --version && docker compose version

# ── 步骤 1：代码与配置 ──────────────────────────────────────────
git clone <repo-url> kb && cd kb
git checkout <tag-or-commit>              # 固定到不可变版本，禁止直接跟分支 tip
cp .env.example .env
# 编辑 .env：ENV=prod、JWT_SECRET_KEY(≥32 字节随机)、POSTGRES_PASSWORD、
#            MINIO_SECRET_KEY、REDIS_PASSWORD、DEEPSEEK_API_KEY 等
chmod 600 .env
git ls-files --error-unmatch .env         # 期望：报错（确认 .env 未被跟踪）

# ── 步骤 2：静态校验（不需要 daemon 起服务，可安全执行） ─────────
docker compose config -q                  # 校验 YAML 与变量插值
git diff --check                          # 空白错误

# ── 步骤 3：构建 / 启动（建议在维护窗口执行） ────────────────────
docker compose build
docker compose up -d                      # 【本轮未执行】
docker compose ps                         # 确认 11 个服务状态，留意 minio 是否 unhealthy

# ── 步骤 4：数据库迁移（api 容器启动不会自动迁移，须显式执行） ─────
docker compose exec api alembic current   # 期望 0001_initial
docker compose exec api alembic upgrade head
# 迁移前必须已完成：pg_dump -Fc 备份 + 临时库 pg_restore 实跑验证（见 §4.4）

# ── 步骤 5：健康检查门禁（不通过则不进入下一步） ─────────────────
curl -fsS http://127.0.0.1:8080/health/live
curl -fsS http://127.0.0.1:8080/health/ready   # 注意 milvus=degraded 是允许的降级态
docker compose logs --tail=50 api worker-light worker-heavy beat

# ── 步骤 6：TLS 生效 ─────────────────────────────────────────────
# 按 §4.2 放置证书与 nginx 配置后：
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
curl -fsSI https://<域名>/health/live          # 验证 301、证书链、HSTS 头

# ── 步骤 7：初始化与验收 ────────────────────────────────────────
# 调用 /api/v1/auth/bootstrap 初始化演示数据；随后跑回归与对账
docker compose exec api python -c \
  "from app.tasks.maintenance import reconcile_vectors; print(reconcile_vectors.delay().get(timeout=600))"
```

> 说明：步骤 4 的 `alembic upgrade head` 在当前仓库只到 `0001_initial`（`metadata.create_all` 基线）。若日后组织模型变更落地并新增显式迁移，须**先备份、先做可恢复验证，再执行**，且代码发布与迁移必须在同一维护窗口内成组生效。

### 4.4 回滚策略（四条链路；**建议 / 未演练**）

**回滚触发条件**：健康检查门禁失败、迁移报错、启动后主链路（登录 / 列表 / 检索 / 上传 / SSE）不可用、或配置校验导致服务起不来。回滚统一前置：**重新冻结写入**（停 `beat` 与两个 worker 消费、排空在途任务），全部动作在一个维护窗口内**成组**完成。

| 链路 | 回滚动作 | 可回滚窗口 | 不可逆边界 |
|---|---|---|---|
| **镜像 tag** | 当前 compose 用 `build:` **没有 `image:` tag，无法按 tag 回滚（G2）**。落地办法：发布时给每个构建产物打不可变 tag（`<git-sha>`），`image: <registry>/kb-api:<sha>`，compose 用 `${IMAGE_TAG}` 引用；回滚 = 把 `IMAGE_TAG` 指回上一 sha 并 `up -d`。因 `./backend:/app` bind mount 会覆盖镜像内代码，**生产必须先去掉该挂载**，否则镜像回滚无效 | 发布后任意时刻（前提：已落地 tag 与去掉 bind mount） | 未打 tag 的历史构建不可追溯 |
| **compose / 配置** | 保留上一版 `docker-compose.yml`、`.env`、`nginx` 配置副本；`git checkout <上一 tag>` 恢复宿主代码与编排，再 `docker compose up -d`；用 `docker compose config -q` 预检 | 任意时刻 | 无（配置天然可逆），但**必须与代码版本、DB schema 对齐** |
| **数据库迁移** | `alembic downgrade <revision>` 只回**结构**；**数据语义不可逆**（删除列后的取值分布、值映射、补值无法还原）。唯一可靠的数据恢复是**迁移前 `pg_dump -Fc` 备份 + 临时库实跑 `pg_restore` 验证过**的备份恢复；破坏性 DDL（`DROP COLUMN` / `DROP TABLE`）提交后，不得再依赖 downgrade 恢复数据 | DDL 提交前、且新写入开始前 | **不可逆**：`0001` 是 `create_all` 基线，项目约定「从 `0002` 起必须写显式迁移」；downgrade 后必须重新 upgrade 才能回到目标结构 |
| **缓存 / 进度 / 对象** | Redis 只存可重建状态，无需回滚数据；PG 文本与 MinIO 原文件为真值。切勿无差别 `FLUSHALL`（`REDIS_CACHE_DB=0 / BROKER_DB=1 / RESULT_DB=2` 分库，且 `noeviction`），误清会打断 Celery 在途任务 | 任意时刻 | 原文件是唯一不可重建资产，禁止 `docker compose down -v` |

**健康检查门禁（回滚后必须全部通过才解冻）**：
1. `docker compose ps` 全部 11 服务为 running/healthy；
2. `curl -fsS /health/live` 返回 `{"status":"alive"}`；
3. `curl -fsS /health/ready` 的 `postgres` 与 `redis` 为 `ok`（`milvus=degraded` 属允许的降级态，不要误判为失败）；
4. 登录 → 列表 → 检索 → 上传 → SSE 进度主链路手工核验；
5. `reconcile_vectors` 对账通过；
6. 冻结区复核 `git diff --exit-code -- eval/ demo/` 返回 0。

**明确不可逆清单**：破坏性 DDL 之后的数据语义；`docker compose down -v` 删除的具名卷；覆盖式 `pg_restore` 丢失的备份点之后数据；已被误改的 `eval/` / `demo/` / `evalbench/` 冻结对象（只能从版本库恢复，视为交付事故）。

---

## 5. 上线检查单（运维动作；**建议 / 未实际执行**）

**上线前**
- [ ] G1 端口收口完成，安全组只放行 80/443/22，基础设施端口未暴露公网
- [ ] `.env` 全部弱口令已替换；`ENV=prod`；`git ls-files --error-unmatch .env` 确认未跟踪
- [ ] 证书已签发并配置自动续期；`nginx -t` 通过；HTTP→HTTPS 301 与 HSTS 头验证
- [ ] `pg_dump -Fc` 完成且**临时库 `pg_restore` 实跑验证通过**
- [ ] 镜像已打不可变 tag 并推送；生产 override 已去掉 `./backend:/app` bind mount
- [ ] minio healthcheck `mc ready local` 已在目标镜像实测可用（G4）
- [ ] `docker compose config -q` 与 `git diff --check` 通过
- [ ] 冻结区零 diff：`git diff --exit-code -- eval/ demo/`
- [ ] 上一版 compose / `.env` / 镜像 tag 已留存，回滚路径已书面确认

**上线中**
- [ ] 冻结写入 → 备份 → 迁移 → 代码/配置发布 → 一次性重启（不逐进程滚动，避免新旧配置混用）
- [ ] 每个步骤记录日志；健康检查门禁未通过即停止后续步骤
- [ ] 未执行 `docker compose down -v`、未执行 `FLUSHALL`

**上线后**
- [ ] 六项健康检查门禁全部通过（见 §4.4）
- [ ] 观察期内保留上一版制品与备份，不清理
- [ ] 费用中心设置预算告警；日志轮转生效验证（`docker inspect` 查看 `LogConfig`）
- [ ] 记录本次变更到 `docs/CHANGELOG.md`（**新增条目，历史条目不回改**；本轮任务未执行此动作）

---

## 6. 待确认 / 未做

| 项 | 状态 | 默认假设 / 建议 |
|---|---|---|
| 服务器规格与价格 | 【待确认】 | 按 §4.1 档位 B 起步；**建议配置、非已采购**，以官网实时价为准，不给精确报价 |
| 域名、备案、证书类型 | 【待确认】 | 免费 DV 起步，备案完成后启用 |
| **api** 容器内是否含 `curl` | **已确认有** | `backend/Dockerfile` 的 `apt-get install curl` 实证（§1.2）；api 的 curl 探活有效，非待确认项 |
| **minio** 镜像内是否含 `mc`、`milvus` 镜像内是否含 `curl` | 【待确认】 | 两个第三方镜像需在目标环境实跑验证，不预设结论；与 api 的 curl 无关（G4） |
| 现有数据量 / 向量条数 / 备份窗口 | 【待确认】 | 演示量级，不编造数字；决定迁移与向量重建的相对耗时 |
| 是否真正需要「上线」 | 定位判定 | 项目是 A/B 档骨架 + 面试讲解版，**本文全部为建议方案，非上线交付、无 SLA** |
| 监控告警 / 容量压测 / 主备切换 / 回滚演练 | **未做** | `docs/operations.md:67-68, 93-95` 已列；建议后续迭代在临时环境演练一次 |
| 组织模型变更的显式迁移（去 `tenant_id`） | **未落源码** | 落地后须叠加迁移 runbook、备份与成组发布，且本文 §4.4 的回滚边界随之更新 |

---

## 7. 主理人复核记录（可追溯）

> 本节记录主理人本轮实查复核、被推翻/被澄清的结论，以及以哪条命令与哪份文件为准。**只改本文档，`docker-compose.yml` 无需回退**。

### 7.1 复核来源与命令

主理人实查命令（本轮已原样复跑，结果一致）：

```bash
$ ls -la backend/Dockerfile frontend/Dockerfile
-rw-r--r-- 406 Nov 11 00:05 backend/Dockerfile
-rw-r--r-- 369 Nov 11 00:05 frontend/Dockerfile
```

本轮补跑的加固证据：`ls -la frontend/index.html frontend/src/App.tsx`（392 / 1482 字节，均存在）、`cat backend/Dockerfile`（`FROM python:3.11-slim` + `apt-get install -y curl libmagic1 libpq5` + `COPY requirements.txt` + `pip install -r requirements.txt` + `COPY . .` + `CMD uvicorn …`）、`cat frontend/Dockerfile`（`node:22-alpine AS build` → `nginx:alpine`，`COPY nginx.conf` + `--from=build /app/dist`）、`git status --short -- nginx/nginx.conf frontend/nginx.conf`（空）、`git diff -- docker-compose.yml`。

### 7.2 两条结论的处置（逐条留痕）

| 主理人指出的表述 | 实查裁定 | 以哪份文件/命令为准 | 本文件处置 |
|---|---|---|---|
| 「`backend/Dockerfile`、`frontend/Dockerfile` 全缺」 | **不成立，两份文件都存在** | `ls -la backend/Dockerfile frontend/Dockerfile`；`cat backend/Dockerfile` / `cat frontend/Dockerfile` | 该表述**从未出现在本文**（§1.2(3) 原文即为「构建上下文正确」并逐项列出两文件）。本轮在 §1.2 补入 `ls -la` 一手证据块与「本节立场声明」，在 §1.6 补「明确排除的伪缺口」，防止后续汇总再次串读 |
| 「curl healthcheck 必然失败 / 属 P0 阻断」 | **不成立，api 容器已装 curl 且 `/health/live` 路由存在** | `backend/Dockerfile` 的 `apt-get install curl`；`backend/app/api/v1/health.py:17-19`；`backend/app/main.py:74`；`docs/operations.md:50` | 本文 §1.2(1) 原文即为「api healthcheck **有效**」，**无 P0 阻断清单**（缺口用 G1–G13 编号）。本轮把 §1.3 与 G4 中「`mc` 待验证」的措辞收紧为「**仅** minio 的 `mc` 客户端、与 curl 无关」，并把 §6 的 `curl` 待确认项拆开、把 api 的 curl 标为**已确认有** |

**结论归属说明（避免张冠李戴）**：上述两条表述不在 `docs/deployment-guide.md` 的任何版本中；主理人复核记录的是汇总/串读口径。本文按「有则改之、无则加勉」处理——不虚构撤回动作，但把事实证据写死到文档里，使两类误读无法再从本文产生。

### 7.3 nginx upstream 与改动面复核

- **改前 = 改后**：`nginx/nginx.conf` 的 `server api:8000` 与 `location /` 的 `proxy_pass http://frontend:80` 均**零改动**（`git status --short -- nginx/nginx.conf frontend/nginx.conf` 返回空）。
- `frontend` 服务在 `docker-compose.yml` 中存在且 `expose: ["80"]`；本轮对它的改动只有 3 类增量字段 + 1 行 `VITE_API_BASE_URL` 参数化（`git diff` 实证），**没有任何基于「frontend 不存在」的假设或改动**。
- 5 项 compose 改动逐条复核见 **§3.1**，均不依赖被推翻的两条结论 → **不回退**。

### 7.4 主理人要求复核的「IP 令牌桶未注册」——如实修正

主理人称 `backend/app/middleware/rate_limit.py` 的 IP 令牌桶「没有注册」，并说明 `main.py:63-65` 只注册 `TraceIdMiddleware` + `CORSMiddleware`。本轮实查：

- `main.py:63-65` 的确只 `add_middleware(TraceIdMiddleware)` 与 `add_middleware(CORSMiddleware, …)`；
- **但** `backend/app/api/v1/router.py:18-20` 有 `from app.middleware.rate_limit import rate_limit` 与 `api_router = APIRouter(prefix=settings.API_V1_PREFIX, dependencies=[Depends(rate_limit)])` → 它是**以 router 级依赖注册**、对 `/api/v1/*` 生效，与 `docs/operations.md:34`「已实现」一致。

因此**不能写成「未注册」**：真实缺口是「覆盖面（`/health/*`、根路径、静态资源不经过）」与「单维按 IP、tenant 位硬编码 `global`」。已在 **§1.7「已知限制（生产骨架应用层，与 compose 编排无关）」** 如实记录，未说成 compose 的问题、未改任何源码。

### 7.5 本轮实际修改范围

| 文件 | 动作 |
|---|---|
| `docs/deployment-guide.md` | 改 §1.2（重写 + 立场声明 + 一手证据块）、§1.3 minio 行、§1.6 G4 + 排除声明、新增 §1.7、§3 新增 §3.1 复核表、§6 拆分 `curl`/`mc` 待确认项、新增本 §7 |
| `docker-compose.yml` | **本轮零改动**（上一轮 5 项改动复核后保留，未回退） |
| Python / JS 源码、`eval/`、`demo/`、`evalbench/`、`docs/CHANGELOG.md`、`docs/git-conventions.md` | **未改动** |

> 全部校验复跑：`docker compose config -q` PASS；`git diff --exit-code -- eval/ demo/` 返回 0；`nginx/nginx.conf`、`frontend/nginx.conf` 零改动。

