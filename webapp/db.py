"""SQLite 数据层（标准库实现，零外部依赖，离线可跑）。

设计取舍（与项目生产架构对齐的说明见 docs/next-version-messaging-and-plugins.md）：
- 演示切片用 SQLite 作为“唯一真值”载体，对应生产里的 PostgreSQL（ingest_jobs / audit 台账）。
- 文档分块、badcase、评估任务、聊天日志全部落库，保证“采集—保存—查看—导出”闭环可复现。
- 不使用外部消息中间件；webapp/queue.py 里的进程内信号量是 Celery+Redis/RabbitMQ 的演示级替身，
  用于展示“繁忙排队 / 降级”行为，生产里换成真实 broker 即可。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

# 数据目录放在项目根 .data/webapp 下，已被 .gitignore 忽略
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".data" / "webapp"
DATA_DIR.mkdir(parents=True, exist_ok=True)
# 允许通过环境变量注入独立数据库（测试隔离 / 多实例），默认仍是 app.db。
DB_PATH = Path(os.environ.get("WEBAPP_DB_PATH", str(DATA_DIR / "app.db")))

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    email        TEXT,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    department   TEXT NOT NULL DEFAULT '未分配',  -- 队列展示用的部门标签；webapp 不做部门级权限
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_tasks (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'running', -- draft|running|paused|done
    target_count INTEGER NOT NULL DEFAULT 0,      -- 计划评测的样本总数
    created_by   TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    title        TEXT,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'queued',  -- queued|analyzing|ready|error
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    error_msg    TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    -- 知识治理：时效 / 适用性 / 来源权威（对应 backend/app/models/knowledge.py 已预留字段）
    valid_from       TEXT,                     -- 生效时间，未来生效不得进上下文
    expires_at       TEXT,                     -- 到期时间
    last_verified_at TEXT,                     -- 最近人工复核时间，比入库时间更可信
    revision         TEXT NOT NULL DEFAULT '1',
    authority        INTEGER NOT NULL DEFAULT 1,  -- 由业务注册表定义，不是代码常量
    scope_json       TEXT NOT NULL DEFAULT '{}'   -- region / plan / product_version 等
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    idx          INTEGER NOT NULL,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS chat_logs (
    id           TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL,  -- 'user' | 'assistant'
    content      TEXT NOT NULL,
    degraded     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_conv ON chat_logs(conversation_id);

-- 问答来源追溯：每条助手回答对应的检索命中分块（doc/chunk/得分/方式/全文）。
-- 与 chat_logs 通过 conversation_id + msg_id 关联，是「智能问答可溯源」的真值载体，
-- 对应生产里 answer → citations → chunk 的引用链（Milvus chunk_id + PG 元数据）。
CREATE TABLE IF NOT EXISTS chat_sources (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    msg_id          TEXT NOT NULL,         -- 关联的助手消息 id（chat_logs.id）
    seq             INTEGER NOT NULL,       -- 来源排序（与 retrieve 的 top_k 顺序一致）
    doc_id          TEXT,
    doc_title       TEXT,
    chunk_id        TEXT,
    chunk_idx       INTEGER,
    snippet         TEXT,
    score           REAL,
    retrieval_mode  TEXT,
    rerank          TEXT,
    chunk_text      TEXT,                   -- 命中分块全文，便于前端弹窗查看与审计回溯
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_src_conv ON chat_sources(conversation_id);
CREATE INDEX IF NOT EXISTS ix_chat_src_msg ON chat_sources(msg_id);

CREATE TABLE IF NOT EXISTS badcases (
    id           TEXT PRIMARY KEY,
    eval_task_id TEXT NOT NULL,
    question     TEXT NOT NULL,
    answer       TEXT,
    verdict      TEXT,                  -- 标注/判定结果：正确|错误|部分正确|不确定
    issue_desc   TEXT,                  -- 问题说明
    source       TEXT NOT NULL DEFAULT 'chat', -- chat|manual|eval
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|in_review|resolved
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_badcase_task ON badcases(eval_task_id);
CREATE INDEX IF NOT EXISTS ix_badcase_status ON badcases(status);
CREATE INDEX IF NOT EXISTS ix_badcase_created ON badcases(created_at);

-- 审计台账：只追加（append-only），记录「谁 / 何时 / 对什么 / 做了什么」。
-- 对应生产骨架 README §4 的 /audit-logs 与 C 档缺口「谁在什么时候查了什么」。
-- 演示切片只埋关键动作，不追求字段完备；生产里还要接 trace_id、留存与脱敏策略。
CREATE TABLE IF NOT EXISTS audit_logs (
    id         TEXT PRIMARY KEY,
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    action     TEXT NOT NULL,   -- login|register|upload|delete|chat|badcase|export|search
    target     TEXT,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_logs(actor);

-- 运行时配置（LLM / embedding / rerank 提供者选择与本地端点）。
-- 生产里对应配置中心/环境注入；演示切片落 SQLite，切换即时生效、无需重启。
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 分块向量缓存：按 (chunk_id, model) 缓存，避免每次检索重算。
-- 真实系统里向量在 Milvus；这里作为「可运行替身」，契约与 embed 输出一致。
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id   TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vec        TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

-- ============ 可观测与治理（评估测试子页的台账真值） ============
-- token 用量逐调用台账：项目 / 部门 / 用户 / 调用类型 / 模型 五个可聚合维度。
-- department_id 是「事件发生瞬间的部门快照」，用于成本归因，不随转岗回溯重写，
-- 也不是准入隔离依据（当前为单组织多部门，暂不实现多租户）。
-- 生产对应数仓事实表 + 供应商账单对账；这里落 SQLite，估算口径在 API 里明确标注。
CREATE TABLE IF NOT EXISTS usage_events (
    id                TEXT PRIMARY KEY,
    ts                TEXT NOT NULL,
    project           TEXT NOT NULL DEFAULT 'default',
    department_id     TEXT NOT NULL DEFAULT 'default',
    user_id           TEXT NOT NULL DEFAULT '',
    call_type         TEXT NOT NULL,             -- chat|embedding|rerank|eval|search
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_cny          REAL NOT NULL DEFAULT 0,
    estimated         INTEGER NOT NULL DEFAULT 1,  -- 1=按字符估算，0=供应商 usage
    run_id            TEXT,
    detail            TEXT,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS ix_usage_project ON usage_events(project);
-- department_id 上的索引刻意不放在 SCHEMA 里：旧库执行 SCHEMA 时还没有这个列，
-- CREATE INDEX 会先报 "no such column"，导致整个 executescript 失败、迁移跑不到。
-- 统一由 _migrate_tenant_to_department() 在列就绪后幂等创建。

-- 预算：(scope,key) 一个上限；policy=warn 只告警，block 才拒绝新的付费调用。
CREATE TABLE IF NOT EXISTS budgets (
    scope      TEXT NOT NULL,                    -- global|department|project
    key        TEXT NOT NULL,
    limit_cny  REAL NOT NULL,
    policy     TEXT NOT NULL DEFAULT 'warn',     -- warn|block
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

-- 限流规则：用户 / 接口 / 模型调用三个维度各自独立计数窗口。
-- 部门只用于成本归因（usage_events.department_id），不在这里建规则行。
CREATE TABLE IF NOT EXISTS rate_rules (
    dimension   TEXT PRIMARY KEY,               -- user|endpoint|model
    limit_count INTEGER NOT NULL,
    window_s    INTEGER NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_hits (
    id          TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    key         TEXT NOT NULL,
    path        TEXT NOT NULL,
    limit_count INTEGER NOT NULL,
    window_s    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_hits_ts ON rate_hits(ts);

-- 评估配置：key/value 落库，admin 可改，即时生效。
CREATE TABLE IF NOT EXISTS eval_configs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 评估运行记录与追踪日志（与 evalbench/runs 的 config_snapshot 对齐）。
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id         TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    mode           TEXT NOT NULL,               -- live|offline
    comparable     INTEGER NOT NULL,
    profile        TEXT,
    prompt_version TEXT,
    params_id      TEXT,
    samples        TEXT,
    n_records      INTEGER NOT NULL DEFAULT 0,
    status_counts  TEXT,
    created_at     TEXT NOT NULL,
    actor          TEXT,
    git_branch     TEXT,
    git_commit     TEXT,
    source         TEXT NOT NULL DEFAULT 'webapp'
);
CREATE TABLE IF NOT EXISTS eval_logs (
    id      TEXT PRIMARY KEY,
    ts      TEXT NOT NULL,
    run_id  TEXT,
    level   TEXT NOT NULL,                      -- INFO|WARN|ERROR
    event   TEXT NOT NULL,
    detail  TEXT,
    actor   TEXT
);
CREATE INDEX IF NOT EXISTS ix_eval_logs_ts ON eval_logs(ts);
CREATE INDEX IF NOT EXISTS ix_eval_logs_run ON eval_logs(run_id);

-- 内容安全 / PII：四个出口的命中统计。只存 kind / 数量 / 动作 / 规则版本，
-- 不存原文、不存首尾 preview、不存可逆 token 映射，避免台账本身成为新的泄漏面。
CREATE TABLE IF NOT EXISTS safety_events (
    id            TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    gate          TEXT NOT NULL,       -- ingest|context|output|audit
    kind          TEXT NOT NULL,
    hit_count     INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,
    action        TEXT,
    rule_version  TEXT,
    source        TEXT                -- 调用点标识：chat / upload / badcase / log:<action>
);
CREATE INDEX IF NOT EXISTS ix_safety_events_ts ON safety_events(ts);
CREATE INDEX IF NOT EXISTS ix_safety_events_gate ON safety_events(gate);
"""


def get_conn() -> sqlite3.Connection:
    """每个请求一个连接；sqlite3 默认串行，靠 _lock 兜底写并发。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
                    (uuid.uuid4().hex, now, gate, kind, n, 1 if blocked else 0,
                     action, rule_version, source),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
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


def _migrate_tenant_to_department(conn: sqlite3.Connection) -> None:
    """把演示库里 tenant 口径的存量数据迁到 department 口径（幂等）。

    为什么必须有这一步：SCHEMA 用 CREATE TABLE IF NOT EXISTS，旧库不会自动改列。
    「全新库」与「含 tenant 旧列的存量库」两种输入都要能跑；另外还有第三种
    「半迁移态」（两列并存），不能让旧列静默残留成第二条部门数据来源。
    这里只改成本归集 / 限流统计维度，不动权限模型、不动冻结的 run_id。
    """
    # 用位置下标 r[1] 而不是 r["name"]：本函数不应隐式依赖调用方设置了
    # row_factory=sqlite3.Row，裸 sqlite3 连接（含单测内存库）也要能跑。
    cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_events)")}
    if "tenant" in cols:
        # 先删引用旧列的索引，否则 SQLite 会因索引依赖拒绝 DROP COLUMN。
        conn.execute("DROP INDEX IF EXISTS ix_usage_tenant")
        if "department_id" not in cols:
            conn.execute("ALTER TABLE usage_events RENAME COLUMN tenant TO department_id")
        else:
            # 半迁移态：把旧列里有效的部门快照补回新列，再删旧列。
            # 'default' 是新列的占位值，同样视为「尚未归因」，应被真实部门值回填。
            conn.execute(
                "UPDATE usage_events SET department_id=tenant "
                "WHERE (department_id IS NULL OR department_id='' OR department_id='default') "
                "AND tenant IS NOT NULL AND tenant <> '' AND tenant <> 'default'"
            )
            try:
                conn.execute("ALTER TABLE usage_events DROP COLUMN tenant")
            except sqlite3.OperationalError:
                # 更老版本 SQLite 不支持 DROP COLUMN：保留旧列不影响新查询，
                # 但它从此不再是数据来源（record_usage 只写 department_id）。
                pass
    # 新库与迁移后的旧库都在这里统一建索引，幂等。
    conn.execute("CREATE INDEX IF NOT EXISTS ix_usage_department ON usage_events(department_id)")
    # 旧库的 tenant 规则行按新口径收敛：限流只保留 user/endpoint/model 三维，
    # 部门不再是限流维度（只是成本归因标签），故不迁移成 department 规则行。
    conn.execute("DELETE FROM rate_rules WHERE dimension IN ('tenant', 'department')")
    # 评估配置键 tenant → department（展示与成本归集用），保留新键优先。
    conn.execute("UPDATE OR IGNORE eval_configs SET key='department' WHERE key='tenant'")
    conn.execute("DELETE FROM eval_configs WHERE key='tenant'")


def _migrate_users_department(conn: sqlite3.Connection) -> None:
    """给旧库补 users.department（幂等）。

    只作**队列展示标签**（「哪个部门在排队」），不参与权限判定——
    webapp 仍是单组织多用户，部门级权限不在本轮范围。
    用 ``row[1]`` 取列名，不依赖 row_factory（与既有迁移口径一致）。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "department" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN department TEXT NOT NULL DEFAULT '未分配'"
        )


def _migrate_knowledge_columns(conn: sqlite3.Connection) -> None:
    """给旧库补知识治理列（幂等）。

    和 department_id 那次一样的教训：新列**不能**在 SCHEMA 里被索引引用，
    否则旧库 executescript 会先报 no such column。这里只做 ADD COLUMN，
    索引留给真实语料规模上来之后再说。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)")}
    additions = (
        ("valid_from", "TEXT"),
        ("expires_at", "TEXT"),
        ("last_verified_at", "TEXT"),
        ("revision", "TEXT NOT NULL DEFAULT '1'"),
        ("authority", "INTEGER NOT NULL DEFAULT 1"),
        ("scope_json", "TEXT NOT NULL DEFAULT '{}'"),
    )
    for name, ddl in additions:
        if name not in cols:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {ddl}")


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.executescript(SCHEMA)
            _migrate_tenant_to_department(conn)
            _migrate_users_department(conn)
            _migrate_knowledge_columns(conn)
            _seed(conn)
            conn.commit()
        finally:
            conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    """首次启动种入演示账号与评估任务，保证开箱即可演示。"""
    cur = conn.cursor()
    # 管理员账号 admin / admin123（口令哈希口径与 security.py 完全一致）
    if not cur.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        salt = uuid.uuid4().hex
        dk = hashlib.pbkdf2_hmac(
            "sha256", "admin123".encode(), bytes.fromhex(salt), 100_000
        ).hex()
        cur.execute(
            "INSERT INTO users(id,username,email,password_salt,password_hash,role,department,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, "admin", "admin@example.com", salt, dk, "admin", "平台运营",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
    # 演示评估任务
    tasks = [
        ("ET-001", "产品说明书问答评测", "验证分块问答在说明书场景的准确率与引用完整性", "running", 20),
        ("ET-002", "客服话术合规评测", "检查回答是否符合合规与品牌口径", "running", 15),
        ("ET-003", "知识库检索召回评测", "评估检索为空/超时等边界场景的降级表现", "paused", 10),
    ]
    for tid, name, desc, status, target in tasks:
        if not cur.execute("SELECT 1 FROM eval_tasks WHERE id=?", (tid,)).fetchone():
            cur.execute(
                "INSERT INTO eval_tasks(id,name,description,status,target_count,created_by,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (tid, name, desc, status, target, "admin",
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
    # 可观测与治理的默认配置（幂等：只在缺失时插入，不覆盖 admin 后续修改）
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not cur.execute("SELECT 1 FROM budgets WHERE scope='global' AND key='default'").fetchone():
        cur.execute("INSERT INTO budgets(scope,key,limit_cny,policy,updated_at) VALUES (?,?,?,?,?)",
                    ("global", "default", 50.0, "warn", now))
    # 限流配额本体是三维；department 只作成本归因，不在此建规则行。
    # ip 是**接入层防刷附加维度**（不进 RATE_DIMENSIONS，不改变三维业务配额口径），
    # 600 次/分/IP 为演示起始值，防止未登录/匿名刷接口；可用 admin 页调小复现。
    # 数值来自模块级 RATE_RULE_DEFAULTS（唯一事实源，测试隔离也用它还原）。
    for dim, (lim, win) in RATE_RULE_DEFAULTS.items():
        if not cur.execute("SELECT 1 FROM rate_rules WHERE dimension=?", (dim,)).fetchone():
            cur.execute(
                "INSERT INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
                " VALUES (?,?,?,?,?)", (dim, lim, win, 1, now))
    cfg = {"profile": "offline_placeholder", "prompt_version": "v2_grounded",
           "params_id": "B", "samples": "all", "project": "default", "department": "default"}
    for k, v in cfg.items():
        if not cur.execute("SELECT 1 FROM eval_configs WHERE key=?", (k,)).fetchone():
            cur.execute("INSERT INTO eval_configs(key,value,updated_at) VALUES (?,?,?)", (k, v, now))
