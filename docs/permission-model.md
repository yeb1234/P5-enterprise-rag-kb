# 权限模型：部门 / 角色 / 显式授权 / 逐段落预留

## 1. 一条红线

**先按权限过滤，再重排、再进模型。** 反过来的顺序，一次就越权。
代码里这条约束落在三个位置，任何一处都不能省：

1. `TenantRepository`：所有 PG 查询强制带 `tenant_id`，业务代码拿不到「不过滤」的 Query；
2. `MilvusVectorStore.build_permission_expr()`：**唯一合法**的向量检索表达式构造器，
   `tenant_id` 永远排第一且调用方无法移除，再叠加部门 / 敏感度 / 知识库；
3. `PermissionService.filter_candidates()`：召回结果进上下文前逐片段二次校验。

缓存 key 也带 `tenant + user + role + dept`，否则 A 的检索结果会被 B 命中。

---

## 2. 判定顺序（`services/permission.py::decide()`）

纯函数，不依赖 DB，可单测、可回放、可解释：

| # | 规则 | 结果 |
|---|---|---|
| 0 | 租户不匹配（platform_admin 除外） | deny `tenant-mismatch` |
| 1 | 显式 deny 授权 | deny `explicit-deny`（优先级最高） |
| 2 | 片段/段落级 ACL 钩子 | 当前默认放行；开关打开后按白名单判定 |
| 3 | platform_admin | allow |
| 4 | 写操作 + viewer / 非管理员做 manage | deny |
| 5 | 敏感度超过角色上限 | deny，除非有显式 allow |
| 6 | tenant_admin | allow |
| 7 | 显式 allow 授权 | allow |
| 8 | visibility：`public` / `tenant` / 同部门 / 部门子树(dept_admin) / owner | allow 或 deny |

角色与敏感度上限：

| 角色 | 可见范围 | 敏感度上限 | 可写 |
|---|---|---|---|
| platform_admin | 全平台（唯一可跨租户） | confidential(3) | ✅ |
| tenant_admin | 本租户全部部门 | confidential(3) | ✅ |
| dept_admin | 本部门 + 子树 | dept(2) | ✅ |
| dept_user | 本部门 + 显式授权 | internal(1) | ✅ |
| viewer | 本部门只读 | internal(1) | ❌ |

敏感度偏序：`public(0) < internal(1) < dept(2) < confidential(3)`，判定只比数值。

---

## 3. 表结构落点

```
tenants ─┬─ departments (parent_id, path 物化路径)
         ├─ users (department_id, role)
         ├─ knowledge_bases (department_id, visibility, default_sensitivity)
         ├─ documents (department_id, kb_id, sensitivity, owner_id, attributes JSONB)
         ├─ chunks (department_id, sensitivity, acl JSONB)  ← 片段级预留
         └─ acl_grants (subject_type/id, resource_type/id, scope, action, effect, conditions)
```

`audit_logs` 只追加：登录、上传、编辑、删除、检索、导出、授权、权限拦截（denied）全部留痕，
带 `trace_id` / `ip` / `params_hash`。生产建议按月声明式分区 + 冷存储归档 + 只读账号。

---

## 4. 逐行 / 逐段落精细权限：架构怎么预留

当前实现到 **知识库级 + 文档级**。要往片段/段落走，不需要改表、不需要改调用方，只需三步：

**第一步：数据。** `acl_grants.scope` 已支持 `chunk | paragraph`，`conditions` 是 JSONB + GIN 索引，
可写 `{"doc_id": "...", "chunk_ids": [...], "sensitivity_max": 2}`。
`chunks.acl` 也已预留，形如：

```json
{
  "allowed_users": ["u3", "u7"],
  "allowed_departments": ["d2"],
  "allowed_roles": ["tenant_admin"],
  "denied_users": ["u9"]
}
```

**第二步：开关。** `app/services/permission.py` 里的 `FINE_GRAINED_ACL_ENABLED = False` 改为 True。
`evaluate_fine_grained()` 已经实现：deny 优先 → 三类白名单命中 → 无限制放行 → 否则丢弃。
`decide()` 的第 2 步会调用它，`filter_candidates()` 传的是 `scope=chunk`，
所以命中但无权限的片段**不会进入 LLM 上下文**。

**第三步：写入侧。** 入库时在 `embed_and_index` 里把文档的段落级策略写到 `Chunk.acl`，
检索时 `semantic_search` / `hybrid_search` 已经把 `acl` 一并带出来了。

**为什么这样设计**：权限判断只有 `decide()` 一个函数，UI、API、检索、后台任务、Agent 工具
全走它；粒度从文档升到段落，只是「数据更细 + 一个开关」，不会出现「网页挡住了但向量召回没挡住」的裂缝。

---

## 5. 逻辑隔离的诚实边界

默认方案是**共享 Schema + tenant_id 行级隔离**。这是代码约束，不是数据库约束：
一条漏写 `tenant_id` 的查询就是越权。生产环境必须：

- 所有列表/详情查询强制走 `TenantRepository`（本骨架已收敛）；
- 补 20+ 条越权用例集（跨租户、跨部门、敏感度、显式 deny、缓存串租户、任务跨租户）；
- 需要更强隔离的客户，走独立 Schema / 独立库 + 连接池按租户路由（本骨架未实现）；
- 审计日志可按人可回放，异常拦截要接告警。

`tests/test_permission.py` 覆盖了租户边界、显式 deny、viewer 只读、敏感度上限、
跨部门、部门子树、精细粒度开关这七类，可以直接扩成越权用例集。
