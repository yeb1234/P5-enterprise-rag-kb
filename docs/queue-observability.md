# 全局队列可观测性与权限边界

> 需求原话：「要不要给用户权限区分下，普通用户登进去只是能用能上传，
> 能简单看服务器整个任务队列，比如说哪个部门哪个用户 ID 正在排队分析文档
> （假设有人排了 100 个文档）」。

## 1. 结论：做，但隐私边界要写死

普通用户确实需要判断「**是我的任务慢，还是全站在排队**」。不给他看全局队列，
他只能反复重传，反而加重积压。所以开放全局视图；但「看全局」不等于「看全部信息」：

| 信息 | 普通用户 | admin |
|---|---|---|
| 处理中 / 排队总数 | 可见 | 可见 |
| 涉及用户数 / 部门数 | 可见 | 可见 |
| 部门名 | 可见 | 可见 |
| 用户 ID | **前 8 位** | 前 8 位 |
| 用户名 | **前 2 位 + `***`** | 完整 |
| 邮箱 | **不可见** | 不可见（成员页单独管） |
| 文件名 | **不可见** | 可见（明细表） |
| 提交时间 / 大小 | 不可见 | 可见 |

一句话：**普通用户看「哪个部门、哪个用户短码、积压多少」，admin 才看「具体哪份文件」**。
文件名是最容易带个人信息（「张三简历.pdf」）和业务信息的字段，不能因为「想看队列」就一起暴露。

## 2. 数据真值：`documents.status`，不是内存队列

聚合口径取自 `documents.status IN ('queued','analyzing')`，按 `owner_id + status` 分组：

```sql
SELECT d.owner_id, u.username, u.department, d.status, COUNT(*) AS n
FROM documents d LEFT JOIN users u ON u.id = d.owner_id
WHERE d.status IN ('queued','analyzing')
GROUP BY d.owner_id, u.username, u.department, d.status
```

为什么不用 `queue._active` / `_queued`：那两个是进程内状态，**重启就丢**；
而文档状态是持久化真值，刷新/重启后仍能重建（对应既有「状态三分类」纪律）。
内存队列只用于「当前并发占用」的实时提示。

`users.department` 是**展示标签**：旧库通过 `_migrate_users_department()` 幂等补列
（`PRAGMA table_info` 检查后再 `ALTER TABLE`），新库由 `SCHEMA` 直接建。
它**不参与权限判定**——webapp 仍是单组织多用户，部门级权限不在本轮范围。

## 3. API 契约

### `GET /api/system/queue`（需登录）

```json
{
  "busy": true,
  "active_count": 5,
  "queue_count": 130,
  "total_pending": 135,
  "user_count": 2,
  "department_count": 2,
  "by_user": [
    {"user_id_short": "a1b2c3d4", "username": "qu***", "department": "研发部",
     "active": 5, "queued": 100, "total": 105}
  ],
  "by_department": [
    {"department": "研发部", "active": 5, "queued": 100, "total": 105}
  ],
  "details": [
    {"doc_id": "...", "filename": "a.pdf", "status": "queued", "size_bytes": 1024,
     "created_at": "...", "owner_id_short": "a1b2c3d4",
     "username": "admin", "department": "平台运营"}
  ],
  "scope": "aggregate",
  "message": "上传分析繁忙：5 个任务处理中，130 个排队中，新上传将依次等待。",
  "note": "真值取自 documents.status（queued / analyzing），不是内存队列；普通用户只看部门、用户短码与计数，文件名与邮箱不可见。"
}
```

- `scope`：普通用户恒为 `aggregate`（`details` 为空数组）；admin 为 `full`。
- `by_user` / `by_department` 已按 `total` 降序，前端不用再排。
- `total_pending = active_count + queue_count`，三个数都来自聚合，互相自洽。

### `GET /api/system/busy`（需登录，已修安全缺陷）

此前该端点**无鉴权**且直接返回 `filename`：未登录即可探测「现在谁在上传什么文件」。
现在：

- 必须登录（`Depends(get_current_user)`）；
- 普通用户：`active` / `queued` 置空数组 + `redacted: true` + 说明文案；
- admin：保留明细；
- `busy` / `active_count` / `queue_count` / `message` 四个字段对所有人保留（页面状态条要用）。

自测 C07 / C26 原来是不带 token 调这个端点的，已改为带 admin token——
**这是调用方式跟随鉴权变更**，不是放宽断言。

## 4. 权限区分现状（普通用户能做什么）

| 能力 | 普通用户 | 说明 |
|---|---|---|
| 登录 / 上传 / 删除自己的文档 | 可以 | `owner_id` 过滤 |
| 流式问答 / 检索调试 | 可以 | 只检索自己可见的文档 |
| 采集 badcase | 可以 | 查看与导出仅 admin |
| 看全局队列聚合 | **可以（本轮新增）** | 部门 + 用户短码 + 计数 |
| 进「管理后台 / 审计日志 / 模型设置」 | **不可以** | `ADMIN_VIEWS` 前端拦截 + 后端 `require_admin` |
| 看别人的文件名 / 邮箱 | 不可以 | 本轮明确边界 |
| 改评测配置 / 限流规则 / 缓存策略 | 不可以 | 仅 admin |

## 5. 验证（实跑）

```
python -m pytest -q webapp/tests              → 109 passed（新增 8 项）
python webapp/selftest.py                     → 33/33 DEMO_READY，报告按字节还原
```

新增 8 项覆盖：
1. `/api/system/queue` 未登录 401/403；
2. `/api/system/busy` 未登录 401/403（安全修复）；
3. 普通用户 `scope=aggregate`、`details=[]`、用户名短码、响应全文不含文件名与邮箱；
4. 普通用户按部门聚合数字正确（100 排队 + 5 处理中 → 部门 total=105）；
5. `total_pending == active_count + queue_count` 自洽；
6. 普通用户 `/api/system/busy` 的 `active`/`queued` 为空且 `redacted=true`；
7. admin 可见 `details` 与完整用户名；
8. 迁移后 `users.department` 存在且种子 admin 为「平台运营」。

## 6. 边界与未做

- **不是部门级权限**：`department` 只是展示标签，不参与 `decide()`；真正的部门树权限仍在
  `docs/org-model-change-plan.md` 的 13 步改造里（方案已定、代码未全改）。
- **不是实时推送**：页面 2.5 秒轮询；SSE 权威快照仍为「只设计」。
- **不是生产队列**：进程内信号量是 Celery + Redis 的演示替身；真实环境要看 broker 的
  queue depth、worker 并发与 dead letter，这里没有。
- **没有跨组织视图**：单组织前提；多租户仅作为未来方向写入文档。
