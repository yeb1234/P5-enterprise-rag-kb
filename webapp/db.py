"""PostgreSQL 数据层（psycopg3）。**运行时不再使用 SQLite。**

## 这一层发生了什么变化

改造前：SQLite 单文件 + `sqlite3` 标准库 + `CREATE TABLE IF NOT EXISTS` 脚本 + 三段
「给旧库补列」的迁移函数（`_migrate_tenant_to_department` / `_migrate_users_department` /
`_migrate_knowledge_columns`）。

改造后：真 PostgreSQL。建表 DDL 收敛到 **`scripts/pg_init.sql` 一份**——
容器首次启动用它、`init_db()` 也用它、迁移脚本的目标结构还是它。三处共用一份 DDL，
是因为「建表语句有两个版本」必然会漂移，而这次改造把 `cache_entries` / `runtime_state`
漏在 `pg_init.sql` 外面，就是漂移的实例。

三段「补列」迁移函数**已删除**：它们的成因是 SQLite 的 `CREATE TABLE IF NOT EXISTS`
不会改已存在的表。PostgreSQL 用显式 DDL + 版本化迁移解决同一个问题，
继续保留这些函数只会让人误以为运行时还可能是旧结构。

## 为什么占位符还是 `?`

见 `pgkit.py` 的模块注释：138 处调用点，批量改文本的回归风险不值得在
换存储的同一次改动里承担。方言差异全部收敛在 `pgkit._translate()`。

## 连接与事务

`get_conn()` 每次新建一条连接（**没有连接池**），调用方负责 `commit()` / `close()`，
与原来的 SQLite 用法保持同形，所以 69 处调用点不用改。
生产应换成 `psycopg_pool.ConnectionPool` 或 FastAPI 依赖注入——这一点写在
`docs/production/01-data-migration.md` 的「仍未做」里，不假装已经做完。
"""
from __future__ import annotations

import hashlib
import threading
import time
import uuid
from pathlib import Path

import pgkit

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".data" / "webapp"
DATA_DIR.mkdir(parents=True, exist_ok=True)
# 上传文件仍落本地磁盘（生产对应对象存储），与数据库无关。
UPLOAD_DIR = DATA_DIR / "uploads"

# 建表 DDL 的唯一事实源。容器 initdb、init_db()、迁移脚本都用这一份。
DDL_PATH = ROOT / "scripts" / "pg_init.sql"

_lock = threading.Lock()

# 限流默认配额（唯一事实源）。`_seed()` 用它插种子，测试隔离用它还原，
# `scripts/pg_init.sql` 的种子值必须与此逐字对齐（PG 侧无法 import，只能人工同步）。
# 业务配额是三维 user/endpoint/model；ip 是接入层防刷附加维度，不进 RATE_DIMENSIONS。
# department 只作成本归因，**不建规则行**（按部门限流是明确不做的）。
RATE_RULE_DEFAULTS: dict[str, tuple[int, int]] = {
    "user": (1200, 60),
    "endpoint": (900, 60),
    "model": (600, 60),
    "ip": (600, 60),
}


def get_conn() -> pgkit.PgConn:
    """每个请求一条连接。事务由调用方 commit；异常路径不自动回滚（连接即弃）。"""
    return pgkit.connect()


def record_safety_counts(gate: str, by_kind: dict, source: str,
                         blocked: bool = False, action: str = "",
                         rule_version: str = "") -> None:
    """按 kind 写命中台账；流式闸只有计数、没有逐条 finding，走这个入口。"""
    if not by_kind:
        return
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = get_conn()
        try:
            for kind, n in by_kind.items():
                conn.execute(
                    "INSERT INTO safety_events(id,ts,gate,kind,hit_count,blocked,action,"
                    "rule_version,source) VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, now, gate, kind, n, bool(blocked),
                     action, rule_version, source),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # 台账是旁路：写失败不能影响业务。但也不能静默到没人知道——
        # 这里的静默是刻意的（审计链路异常不应打断回答），由 /metrics 的计数暴露。
        pass


def record_safety_event(result, source: str = "") -> None:
    """把一次脱敏命中按 kind 汇总落台账（旁路，异常不影响业务）。

    刻意只记数量与类别：台账是给运维看「哪个出口在漏」的，不是给回放原文用的。
    """
    if not result or not getattr(result, "hit_count", 0):
        return
    d = result.as_dict()
    actions = d.get("actions", {})
    record_safety_counts(
        d["gate"], d.get("by_kind", {}), source,
        blocked=bool(d.get("blocked")),
        action=max(actions, key=actions.get) if actions else "",
        rule_version=d.get("rule_version", ""),
    )


def log_action(actor: str, role: str, action: str, target: str = "", detail: str = "") -> None:
    """只追加一条审计记录。审计是旁路：任何异常都不能影响业务主流程。

    审计出口（GATE_AUDIT）：target / detail 是最容易带上用户输入的两个字段，
    写库前统一过一遍脱敏规则，避免「回答打码了、审计日志里还是明文」。
    actor 是内部账号标识，不在此处脱敏；生产若用邮箱当账号需单独加规则。
    """
    try:
        from guardrails import default as guard_default
        g = guard_default()
        if g.policy.enabled:
            tr = g.audit(target or "")
            dr = g.audit(detail or "")
            target, detail = tr.text, dr.text
            if tr.hit_count:
                record_safety_event(tr, f"log:{action}")
            if dr.hit_count:
                record_safety_event(dr, f"log:{action}")
    except Exception:
        # fail-closed：脱敏链路异常时宁可丢字段，也不把可能的原文写进审计日志
        target, detail = "[脱敏失败:字段已丢弃]", "[脱敏失败:字段已丢弃]"
    try:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_logs(id,actor,role,action,target,detail,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, actor, role, action, target, detail,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def init_db() -> None:
    """建表 + 种子（幂等）。DDL 来自 `scripts/pg_init.sql` 这一份事实源。"""
    with _lock:
        ddl = DDL_PATH.read_text(encoding="utf-8")
        conn = get_conn()
        try:
            conn.executescript(ddl)
            _seed(conn)
            conn.commit()
        finally:
            conn.close()


def _seed(conn: pgkit.PgConn) -> None:
    """首次启动种入演示账号与评估任务，保证开箱即可演示。全部 ON CONFLICT 幂等。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 管理员账号 admin / admin123（口令哈希口径与 security.py 完全一致）
    salt = uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac(
        "sha256", "admin123".encode(), bytes.fromhex(salt), 100_000
    ).hex()
    conn.execute(
        "INSERT INTO users(id,username,email,password_salt,password_hash,role,department,created_at)"
        " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (username) DO NOTHING",
        (uuid.uuid4().hex, "admin", "admin@example.com", salt, dk, "admin", "平台运营", now),
    )

    # 演示评估任务
    tasks = [
        ("ET-001", "产品说明书问答评测", "验证分块问答在说明书场景的准确率与引用完整性", "running", 20),
        ("ET-002", "客服话术合规评测", "检查回答是否符合合规与品牌口径", "running", 15),
        ("ET-003", "知识库检索召回评测", "评估检索为空/超时等边界场景的降级表现", "paused", 10),
    ]
    for tid, name, desc, status, target in tasks:
        conn.execute(
            "INSERT INTO eval_tasks(id,name,description,status,target_count,created_by,created_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (tid, name, desc, status, target, "admin", now),
        )

    # 可观测与治理的默认配置（幂等：只在缺失时插入，不覆盖 admin 后续修改）
    conn.execute(
        "INSERT INTO budgets(scope,key,limit_cny,policy,updated_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT (scope,key) DO NOTHING",
        ("global", "default", 50.0, "warn", now),
    )
    # 限流配额本体是三维；department 只作成本归因，不在此建规则行。
    # ip 是**接入层防刷附加维度**（不进 RATE_DIMENSIONS，不改变三维业务配额口径），
    # 600 次/分/IP 为演示起始值，防止未登录/匿名刷接口；可用 admin 页调小复现。
    # 数值来自模块级 RATE_RULE_DEFAULTS（唯一事实源，测试隔离也用它还原）。
    for dim, (lim, win) in RATE_RULE_DEFAULTS.items():
        conn.execute(
            "INSERT INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT (dimension) DO NOTHING",
            (dim, lim, win, True, now),
        )

    cfg = {"profile": "offline_placeholder", "prompt_version": "v2_grounded",
           "params_id": "B", "samples": "all", "project": "default", "department": "default"}
    for k, v in cfg.items():
        conn.execute(
            "INSERT INTO eval_configs(key,value,updated_at) VALUES (?,?,?)"
            " ON CONFLICT (key) DO NOTHING",
            (k, v, now),
        )
