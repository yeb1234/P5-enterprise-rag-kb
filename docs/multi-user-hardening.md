# 多用户加固与网关限流的四层分工

> 需求原话：「针对多用户，和能在腾讯云上面做加固，网关限流什么的」。
> 这份文档只讲**加固与限流**，不讲云账号注册流程——腾讯云逐步操作见
> `docs/showcase/tencent-cloud-guide.html`，阿里云版见 `docs/showcase/aliyun-guide.html`。

## 1. 多用户到底要隔离什么

「多用户」不是一个开关，而是六个互相独立的隔离维度。少任何一条，都会出现
「A 用户能看见 / 影响 B 用户」的具体故障：

| 维度 | 要防的故障 | 本项目落点 | 三态 |
|---|---|---|---|
| 身份 | 撞库、弱口令、会话被劫持 | PBKDF2 + 随机盐、JWT 短期有效 | 已实现（演示级） |
| 权限 | 普通用户进管理页、越权读他人文档 | `decide()` 纯函数 + `require_admin` + `ADMIN_VIEWS` | 已实现（规则级） |
| 数据 | 列表漏过滤、详情能看到别人的文档 | `owner_id` 过滤 + 检索后逐条 `decide()` 复核 | 已实现（演示级） |
| 配额 | 一个用户跑量烧光全局预算，其他人全被拦 | 全局预算 + **按用户预算覆盖（本轮新增）** | 本轮补齐 |
| 速率 | 一个用户/一个 IP 刷爆接口 | `user / endpoint / model` 三维 + **IP 维度（本轮新增）** | 本轮补齐 |
| 内容 | 身份证、密钥进日志/导出/向量库 | `guardrails/` 四出口 | 已实现（规则级） |

审计（`audit_logs` 只追加）贯穿以上六项：谁在什么时候做了什么，必须能查。

## 2. 为什么只靠云网关不够

云厂商的 API 网关 / WAF 能挡住「入口洪水」，但挡不住「合法身份下的滥用」：

- 云网关只认识 **IP、appId、路径、QPS**，它不知道「这个用户本月已经烧了 48 元预算」；
- 云网关的限流是**按秒**的，挡不住「每分钟 59 次、持续 8 小时」这种慢速刷；
- WAF 的 CC 防护基于频率与人机特征，误伤正常用户时你要能一键 Bypass。

所以分工是：**云网关管入口，应用层管业务配额，两层都要有**。

| 层 | 管什么 | 拦不住什么 |
|---|---|---|
| DNS / DDoS 高防 | 流量型攻击、带宽打满 | 应用层慢速刷 |
| WAF | SQL 注入、XSS、CC、BOT、0day 虚拟补丁 | 合法账号的越权与超额 |
| CLB | 连接数、新建连接速率、带宽、HTTPS 卸载 | 业务语义 |
| 云 API 网关 | 按秒 QPS、HMAC 鉴权、app 配额 | 按用户/部门的成本配额 |
| **应用层（本项目）** | **user / endpoint / model 三维 + IP + 并发闸 + 请求体上限 + 按用户预算** | 超大流量（要靠云侧） |

## 3. 应用层这五项加固（本轮新增）

> **实施状态（2026-11-15，实跑）**：五项均已在 `webapp/` 演示切片落地，不是设计。
> `python -m pytest -q webapp/tests/test_gateway_hardening.py` → **18 passed**；
> 全量 `python -m pytest -q webapp/tests` → **127 passed**；`webapp/selftest.py` → **33/33 `DEMO_READY`**。
> **`backend/` 生产骨架仍只有预留**（`redis_client.acquire_slot` 全仓无调用点），
> 两套体系不要混写——页面与文档都必须分开标注。

### 3.1 IP 维度限流

- 目的：挡住**未登录**或**刚注册的小号**刷接口——这类请求没有稳定的 `user_id`。
- 与既有三维的关系：`RATE_DIMENSIONS` 仍是 `("user","endpoint","model")`（业务配额口径不变），
  IP 是**附加维度**（`IP_DIMENSION = "ip"`），默认规则「600 次 / 60 秒 / 每 IP」。
- 超限返回统一 429 契约，`X-RateLimit-Dimension: ip`，前端按 `RATE_LIMIT_IP_EXCEEDED` 提示。

### 3.2 可信代理与真实 IP

**这是最容易做错的一处**：如果无条件采信 `X-Forwarded-For`，攻击者随便伪造一个头就绕过了 IP 限流。

```python
def client_ip(host: str, forwarded_for: str | None, trusted: set[str]) -> str:
    # 只有直连对端是可信代理（CLB / WAF / 本机 Nginx）时，才看 XFF 的第一个地址
    if host not in trusted:
        return host
    first = (forwarded_for or "").split(",")[0].strip()
    return first or host
```

- 可信列表来自 `TRUSTED_PROXY_IPS`（默认 `127.0.0.1,::1`）；上云后把 CLB/WAF 的回源段加进去。
- 多级代理时只取**第一个**（最靠近客户端的那一跳），后面的都可被伪造。
- 验证方法：带 `X-Forwarded-For: 1.2.3.4` 直连应用端口，限流应按真实来源计数，**不按 1.2.3.4**。

### 3.3 全局并发闸

- 目的：防雪崩。慢请求堆积会把连接池和内存吃满，此时**快速失败比排队更安全**。
- 实现：`asyncio.Semaphore(MAX_INFLIGHT)`（默认 32），只作用于 `/api/`；获取超时 2 秒 → **503 `SERVER_BUSY`** + `Retry-After: 1`。
- 注意：静态资源不过闸；`StreamingResponse`（SSE 问答）必须在流结束后才释放令牌，否则并发数会被低估。

### 3.4 请求体大小上限

- `Content-Length` 超过 `MAX_BODY_BYTES`（默认 10 MiB）→ **413 `PAYLOAD_TOO_LARGE`**。
- 无 `Content-Length` 的 chunked 请求不做全量缓冲（缓冲本身就是攻击面），
  由上传接口自己的逐块上限兜底（A5 是 8 MiB）。

### 3.5 按用户配额（预算隔离）

- 现状问题：`check_budget` 只查全局，一个用户跑量就能把全局预算烧光，其他人全被 `policy=block` 拦住。
- 改法：`budgets` 表已有 `scope / key` 结构，新增 `scope='user', key=<user_id>`；
  `check_budget(user_id=...)` 优先用户级、没有则回退全局；返回体加 `scope` / `overridden` 两个字段。
- 用户级花费仍从 `usage_events` 按 `user_id` 聚合，不需要新表。
- 验证：给 A 设 0.01 元且已超支 → A 被 block，**B 不受影响**。

## 4. 腾讯云侧的三处限流

| 位置 | 能配什么 | 关键限制（2026-11 公开信息） |
|---|---|---|
| **API 网关** | 按秒 QPS、HMAC 鉴权、环境与版本、app 配额 | 共享实例**单服务 500 QPS**、请求体 ≤16MB、**不支持 VPC**，达到上限**可能丢请求**（不是排队）；要接 WAF/EIAM 必须**专享实例** |
| **CLB** | 四/七层转发、连接数与新建速率、带宽、HTTPS 卸载、按域名/URL 路由 | 依托大禹做 DDoS/CC 清洗；限流是连接层，不认识业务 |
| **WAF** | Web 攻击、CC 防护、BOT 管理、IP 信誉、地理围栏、API 安全（企业版起） | 高级版 3,880 元/月起；误伤要能一键 Bypass |

**选型建议**：
- 演示 / 小流量（日请求 < 10 万）：**轻量应用服务器 + 免费额度**即可，不买 WAF；
- 小规模生产（有真实外部用户）：CLB + WAF 高级版 + 应用层五项，API 网关可用共享实例；
- 需要 VPC 内网回源、要接 WAF/EIAM、QPS 稳定超 500：才上 API 网关**专享实例**。

## 5. 验证方法（可复跑）

```bash
# 1) IP 维度：伪造 XFF 不应改变计数口径
curl -H "X-Forwarded-For: 1.2.3.4" http://127.0.0.1:8765/api/health

# 2) 请求体上限
python -c "print('x'*11*1024*1024)" > /tmp/big.txt
curl -X POST --data-binary @/tmp/big.txt http://127.0.0.1:8765/api/chat/stream   # 期望 413

# 3) 并发闸（把 MAX_INFLIGHT 调小后压）
ab -n 100 -c 40 http://127.0.0.1:8765/api/health   # 期望出现 503 SERVER_BUSY

# 4) 三维 + IP 限流
ab -n 600 -c 20 http://127.0.0.1:8765/api/docs      # 期望 429，看 X-RateLimit-Dimension

# 5) 用户配额隔离
#    A 设 0.01 元并超支 → A 的问答被拦；B 正常
```

## 5.1 实测结果（2026-11-15）

| 场景 | 实测结果 |
|---|---|
| 直连伪造 `X-Forwarded-For: 1.1.1.1` | 第二次仍 `429 key=testclient`——伪造头**没有**换限流桶 |
| 可信代理下 `XFF=5.5.5.5` / `6.6.6.6` | 各自计数；再打 5.5.5.5 → `429 key=5.5.5.5` |
| 并发 2 / 上限 1 | `[200, 503]`，`code=SERVER_BUSY`，`Retry-After: 1` |
| `Content-Length` 超过 10 MiB | `413 PAYLOAD_TOO_LARGE`，`kind=body_size` |
| 用户 A 超支（0.05 / 0.01） | `scope=user overridden=True allowed=False` |
| 用户 B（未单独配置） | `scope=global allowed=True`——**不受 A 影响** |
| IP 维度命中 | `code=RATE_LIMIT_IP_EXCEEDED`、`X-RateLimit-Dimension: ip` |

复现：`python -m pytest -q webapp/tests/test_gateway_hardening.py`（18 项）。

## 6. 边界（别夸大）

- **没有真实压测**：上面的 `ab` 命令是验证手段，不是容量结论；`MAX_INFLIGHT=32` 是演示起始值。
- **没有 WAF 规则调优**：云 WAF 的 CC 阈值、BOT 策略需要按真实流量标定，本项目未做。
- **没有等保 / 合规审计**：只做了「可解释的工程加固」，不等于合规达标。
- **限流是单机内存窗口**：多副本部署时各副本独立计数，生产应换 Redis 滑动窗口或云网关统一配额。
- **`department` 仍只是展示标签**，不参与权限判定；部门级权限在 `docs/org-model-change-plan.md` 的 13 步里，未落地。
