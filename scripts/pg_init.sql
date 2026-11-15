-- PostgreSQL 初始化：把 webapp/db.py 的 SQLite SCHEMA 翻译成 PG 方言。
-- 由 docker-compose.infra.yml 挂到 /docker-entrypoint-initdb.d/，首次建库时自动执行。
-- 类型映射原则见 docs/production/01-data-migration.md：
--   TEXT→text、INTEGER→integer/bigint、REAL→numeric(12,6)、
--   时间字符串→timestamptz、0/1 布尔→boolean。
-- 说明：本文件只建**结构与必要种子**；存量数据由 scripts/migrate_sqlite_to_pg.py 迁。

-- ---------- 扩展 ----------
-- pgvector：向量检索的索引层。主链路直接在这一列上做 ANN 检索 + 权限预过滤，
-- 不用 Python 逐个算余弦（实测：纯 Python 10 万候选要 25.6 秒，见 docs/production/08-vector-store-decision.md）。
-- 依赖镜像自带扩展：docker-compose.infra.yml 的 KB_PG_IMAGE 默认已指向 pgvector 版。
-- 用官方 postgres 镜像会在这里失败并明确报错 —— **这是有意的**，不做静默降级：
-- 静默跳过的结果就是「表建好了、检索永远走不到索引」，比直接炸更难查。
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- 用户与组织 ----------
CREATE TABLE IF NOT EXISTS users (
    id            text PRIMARY KEY,
    username      text UNIQUE NOT NULL,
    email         text,
    password_salt text NOT NULL,
    password_hash text NOT NULL,
    role          text NOT NULL DEFAULT 'user',
    department    text NOT NULL DEFAULT '未分配',
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------- 文档与分块 ----------
CREATE TABLE IF NOT EXISTS documents (
    id               text PRIMARY KEY,
    owner_id         text NOT NULL,
    filename         text NOT NULL,
    title            text,
    size_bytes       bigint NOT NULL DEFAULT 0,
    status           text NOT NULL DEFAULT 'queued',
    chunk_count      integer NOT NULL DEFAULT 0,
    error_msg        text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    valid_from       timestamptz,
    expires_at       timestamptz,
    last_verified_at timestamptz,
    revision         text NOT NULL DEFAULT '1',
    authority        integer NOT NULL DEFAULT 1,
    scope_json       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON documents(owner_id);
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS chunks (
    id     text PRIMARY KEY,
    doc_id text NOT NULL,
    idx    integer NOT NULL,
    text   text NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);

-- ---------- 会话与溯源 ----------
CREATE TABLE IF NOT EXISTS chat_logs (
    id              text PRIMARY KEY,
    conversation_id text NOT NULL,
    user_id         text NOT NULL,
    role            text NOT NULL,
    content         text NOT NULL,
    degraded        boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_conv ON chat_logs(conversation_id);

CREATE TABLE IF NOT EXISTS chat_sources (
    id              text PRIMARY KEY,
    conversation_id text NOT NULL,
    msg_id          text NOT NULL,
    seq             integer NOT NULL,
    doc_id          text,
    doc_title       text,
    chunk_id        text,
    chunk_idx       integer,
    snippet         text,
    score           numeric(12,6),
    retrieval_mode  text,
    rerank          text,
    chunk_text      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chat_src_conv ON chat_sources(conversation_id);
CREATE INDEX IF NOT EXISTS ix_chat_src_msg ON chat_sources(msg_id);

-- ---------- 评测与 badcase ----------
CREATE TABLE IF NOT EXISTS eval_tasks (
    id           text PRIMARY KEY,
    name         text NOT NULL,
    description  text,
    status       text NOT NULL DEFAULT 'running',
    target_count integer NOT NULL DEFAULT 0,
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS badcases (
    id           text PRIMARY KEY,
    eval_task_id text NOT NULL,
    question     text NOT NULL,
    answer       text,
    verdict      text,
    issue_desc   text,
    source       text NOT NULL DEFAULT 'chat',
    status       text NOT NULL DEFAULT 'pending',
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_badcase_task ON badcases(eval_task_id);
CREATE INDEX IF NOT EXISTS ix_badcase_status ON badcases(status);

-- ---------- 审计 ----------
CREATE TABLE IF NOT EXISTS audit_logs (
    id         text PRIMARY KEY,
    actor      text NOT NULL,
    role       text NOT NULL,
    action     text NOT NULL,
    target     text,
    detail     text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_logs(actor);

-- ---------- 配置 ----------
CREATE TABLE IF NOT EXISTS app_settings (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 向量缓存：PG 里只留元数据（chunk_id / model / dim / updated_at），
-- 向量本体迁 Milvus（见 01-data-migration.md §4）。vec 列保留是为了双写过渡期可回滚。
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id   text NOT NULL,
    model      text NOT NULL,
    dim        integer NOT NULL,
    -- vec 是历史形态（JSON 文本，1024 维约 22.2KB/行），保留用于回滚与对拍；
    -- embedding 是 binary 形态（4KB/行，小 5.5 倍），检索走它。
    -- 两列由 rag.py 双写；确认新路径稳定后再考虑删 vec。
    vec        text,
    embedding  vector(1024),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, model)
);

-- HNSW 索引：vector_cosine_ops 对应 <=> 余弦距离。
-- 注意：数据量小时 planner 会选 Seq Scan + Sort（128 行时索引反而更慢），
-- 这是正常的；不要为了「看到用了索引」去 SET enable_seqscan=off 调生产配置。
CREATE INDEX IF NOT EXISTS idx_chunk_emb_hnsw
    ON chunk_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------- 可观测与治理 ----------
CREATE TABLE IF NOT EXISTS usage_events (
    id                text PRIMARY KEY,
    ts                timestamptz NOT NULL DEFAULT now(),
    project           text NOT NULL DEFAULT 'default',
    department_id     text NOT NULL DEFAULT 'default',
    user_id           text NOT NULL DEFAULT '',
    call_type         text NOT NULL,
    model             text NOT NULL DEFAULT '',
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    total_tokens      integer NOT NULL DEFAULT 0,
    cost_cny          numeric(12,6) NOT NULL DEFAULT 0,
    estimated         boolean NOT NULL DEFAULT true,
    run_id            text,
    detail            text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS ix_usage_project ON usage_events(project);
CREATE INDEX IF NOT EXISTS ix_usage_department ON usage_events(department_id);
CREATE INDEX IF NOT EXISTS ix_usage_user ON usage_events(user_id);

CREATE TABLE IF NOT EXISTS budgets (
    scope      text NOT NULL,
    key        text NOT NULL,
    limit_cny  numeric(12,6) NOT NULL,
    policy     text NOT NULL DEFAULT 'warn',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS rate_rules (
    dimension   text PRIMARY KEY,
    limit_count integer NOT NULL,
    window_s    integer NOT NULL,
    enabled     boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rate_hits (
    id          text PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    dimension   text NOT NULL,
    key         text NOT NULL,
    path        text NOT NULL,
    limit_count integer NOT NULL,
    window_s    integer NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_hits_ts ON rate_hits(ts);
CREATE INDEX IF NOT EXISTS ix_rate_hits_dim ON rate_hits(dimension, ts);

CREATE TABLE IF NOT EXISTS eval_configs (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    run_id         text PRIMARY KEY,
    label          text NOT NULL,
    mode           text NOT NULL,
    comparable     boolean NOT NULL,
    profile        text,
    prompt_version text,
    params_id      text,
    samples        text,
    n_records      integer NOT NULL DEFAULT 0,
    status_counts  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    actor          text,
    git_branch     text,
    git_commit     text,
    source         text NOT NULL DEFAULT 'webapp'
);

CREATE TABLE IF NOT EXISTS eval_logs (
    id     text PRIMARY KEY,
    ts     timestamptz NOT NULL DEFAULT now(),
    run_id text,
    level  text NOT NULL,
    event  text NOT NULL,
    detail text,
    actor  text
);
CREATE INDEX IF NOT EXISTS ix_eval_logs_ts ON eval_logs(ts);
CREATE INDEX IF NOT EXISTS ix_eval_logs_run ON eval_logs(run_id);

CREATE TABLE IF NOT EXISTS safety_events (
    id           text PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    gate         text NOT NULL,
    kind         text NOT NULL,
    hit_count    integer NOT NULL DEFAULT 0,
    blocked      boolean NOT NULL DEFAULT false,
    action       text,
    rule_version text,
    source       text
);
CREATE INDEX IF NOT EXISTS ix_safety_events_ts ON safety_events(ts);
CREATE INDEX IF NOT EXISTS ix_safety_events_gate ON safety_events(gate);

-- ---------- 缓存与运行时状态（cache/store.py::_SCHEMA 的 PG 版本） ----------
-- 列集合必须与 SQLite 严格一致：迁移脚本按源库列名生成 INSERT，
-- 少一列就是整表迁移失败。索引单独建，不要塞进建表语句。
CREATE TABLE IF NOT EXISTS cache_entries (
    key            text PRIMARY KEY,
    namespace      text NOT NULL,
    value_json     text NOT NULL,
    data_version   text NOT NULL DEFAULT '',
    authz_snapshot text NOT NULL DEFAULT '',
    policy_version text NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,
    last_access    timestamptz NOT NULL,
    hits           integer NOT NULL DEFAULT 0,
    is_negative    boolean NOT NULL DEFAULT false,
    size_bytes     integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_cache_expires ON cache_entries(expires_at);
CREATE INDEX IF NOT EXISTS ix_cache_access ON cache_entries(last_access);

CREATE TABLE IF NOT EXISTS runtime_state (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL
);

-- ---------- 必要种子（与 webapp/db.py::_seed 逐字对齐；存量迁移时用 ON CONFLICT 跳过） ----------
-- 数值来自 db.py 的 defaults 字典，不是随手写的演示值：
-- user 1200 / endpoint 900 / model 600 / ip 600，窗口均为 60s。
-- 注意 department 只作成本归因，**不建规则行**（按部门限流是明确不做的）。
INSERT INTO rate_rules(dimension, limit_count, window_s, enabled)
VALUES ('user', 1200, 60, true),
       ('endpoint', 900, 60, true),
       ('model', 600, 60, true),
       ('ip', 600, 60, true)
ON CONFLICT (dimension) DO NOTHING;

INSERT INTO budgets(scope, key, limit_cny, policy)
VALUES ('global', 'default', 50, 'warn')
ON CONFLICT (scope, key) DO NOTHING;

-- eval_configs 的 6 个默认配置（db.py::_seed 的 cfg 字典）
INSERT INTO eval_configs(key, value)
VALUES ('profile', 'offline_placeholder'),
       ('prompt_version', 'v2_grounded'),
       ('params_id', 'B'),
       ('samples', 'all'),
       ('project', 'default'),
       ('department', 'default')
ON CONFLICT (key) DO NOTHING;
