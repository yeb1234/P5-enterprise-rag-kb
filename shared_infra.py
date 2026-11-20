"""共享基础设施命名空间：P5 在「多项目共用的 PG + Redis」里如何不撞车。

## 为什么要这一层

用户的其他项目（P1 SlimTrack / P2 灵境 CoreAI / P4 Agent 商城）已经共享
**同一个 PostgreSQL 实例 + 同一个 Redis**。P5 作为新接入方，必须做到两件事：

1. **PG**：不碰别人的表。用 P5 专属 schema（`p5_kb`），不用独立 database
   —— 共享实例的价值就是共用连接、共用扩展、共用备份，另起 database 等于放弃这些。
2. **Redis**：key 不能撞。所有 key 必须以 `p5kb:{user_id}:` 开头，
   **项目前缀 + 用户字段缺一不可**。只写 `p5kb:` 会撞同项目的其他用户，
   只写 user_id 会撞别的项目。

## 这不是「权限系统」

本模块只解决**命名空间隔离**（不撞车、能追溯归属）。
真正的 allow/deny 仍归 `permission.decide()`；`visible_department_ids`
只缩小候选集。**不要拿 key 前缀当权限控制**——这是项目硬约束。

## 三态声明

- **已实现**：本模块提供的 key 构造、前缀校验、schema 隔离判定（纯函数）。
- **演示**：`scripts/shared_infra_demo.py` 在本机模拟多项目共存。
- **未做**：真实接入 P1/P2/P4 那台共享实例（本机没有连接串）、DB role 最小权限。
"""
from __future__ import annotations

import re

# ---- PG schema -----------------------------------------------------------
P5_SCHEMA = "p5_kb"
# 扩展是库级的，装不进 schema：vector 类型与 <=> 操作符都在 public。
# search_path 末尾必须带 public，否则 DDL 在建 vector(1024) 列时直接报
# `type "vector" does not exist`。
REQUIRED_SEARCH_PATH_SUFFIX = "public"
NEIGHBOR_SCHEMAS = ("p1_slimtrack", "p2_coreai", "p4_agentmall")

# ---- Redis key -----------------------------------------------------------
# 前缀结构：{project}:{user_id}:{业务段...}
# 项目段必须是小写字母+数字+下划线，用户段不允许含 ':'（防注入出额外段）。
PROJECT_PREFIX = "p5kb"
_KEY_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def p5_search_path(schema: str = P5_SCHEMA) -> str:
    """返回 P5 可用的 search_path：自己的 schema 在前，public 兜底扩展。

    `pgkit.connect()` 在 `WEBAPP_DB_SCHEMA` 存在时会用
    `connect_args={"options": "-c search_path=<schema>,public"}`，
    并在每次 checkout 补一次 `SET` 复核（池化后会话级 SET 会跨借用人存活）。
    """
    return f"{schema},{REQUIRED_SEARCH_PATH_SUFFIX}"


def search_path_is_valid(path: str, schema: str = P5_SCHEMA) -> bool:
    """校验 search_path：首位必须是自己的 schema，且末尾必须带 public。"""
    parts = [p.strip() for p in (path or "").split(",") if p.strip()]
    return bool(parts) and parts[0] == schema and parts[-1] == REQUIRED_SEARCH_PATH_SUFFIX


def is_foreign_schema(name: str, schema: str = P5_SCHEMA) -> bool:
    """判断一个 schema 名是否属于「别人」——用于越权读取的前置拒绝。

    用**白名单**而不是「不是 public 就是别人的」：P5 自己可能有多个 schema
    （如测试 schema），黑名单会把它们误判为越权。
    """
    return bool(name) and name not in {schema, REQUIRED_SEARCH_PATH_SUFFIX}


def build_p5_key(user_id: str, *segments: str, project: str = PROJECT_PREFIX) -> str:
    """构造 P5 的 Redis key：``p5kb:{user_id}:{seg1}:{seg2}...``。

    为什么 user_id 必须显式传而不是「有就拼没有就算」：
    共享 Redis 里，缺了用户段的 key 会变成跨用户共用窗口——
    一个人的限流会打到另一个人身上。宁可在这里抛错，也不要静默生成弱 key。
    """
    if not user_id or not _KEY_SAFE.fullmatch(user_id):
        raise ValueError(f"user_id 非法（必须 1-64 位 [A-Za-z0-9_.-]）：{user_id!r}")
    if not project or not _KEY_SAFE.fullmatch(project):
        raise ValueError(f"project 前缀非法：{project!r}")
    out = [project, user_id]
    for seg in segments:
        if not seg or not _KEY_SAFE.fullmatch(seg):
            raise ValueError(f"key 段非法（必须 1-64 位 [A-Za-z0-9_.-]）：{seg!r}")
        out.append(seg)
    return ":".join(out)


# 业务段白名单：P5 目前会往共享 Redis 写的类别就这几种。
# 为什么要白名单而不是「段数 >= 3」：`p5kb:rl:user` 也是三段，
# 但它把业务段 rl 当成了用户——两个人都会写进同一个 key。弱 key 必须被拒。
KNOWN_SEGMENTS = ("rl", "cache", "state", "queue", "budget")


def is_p5_key(key: str, *, require_user: bool = True) -> bool:
    """判断一个 key 是否是合规的 P5 key。

    `require_user=True` 时要求第二段是用户、第三段是已知业务段——
    防止 `p5kb:rl:user` 这类「把业务段当用户」的弱 key 混进共享 Redis。
    """
    parts = [p for p in (key or "").split(":") if p]
    if not parts or parts[0] != PROJECT_PREFIX:
        return False
    if require_user:
        # 只验格式还不够：'rl' 也是合法格式的 user_id，但它其实是业务段。
        # 用「第三段必须是已知业务段」把两类 key 分开。
        if len(parts) < 3 or parts[2] not in KNOWN_SEGMENTS:
            return False
    return all(_KEY_SAFE.fullmatch(p) for p in parts)


def key_owner(key: str) -> str | None:
    """从 key 里取项目前缀。不属于任何已知项目就返回 None。"""
    known = {PROJECT_PREFIX, "p1slim", "p2core", "p4mall"}
    first = (key or "").split(":", 1)[0]
    return first if first in known else None
