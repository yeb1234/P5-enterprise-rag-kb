"""测试隔离配置（PostgreSQL）。

**SQLite 已从运行时移除**，所以这里不再有「临时 .db 文件」，改成：
- 连真 PostgreSQL（`WEBAPP_DATABASE_URL`，默认走项目标准 DSN）；
- 每个 pytest 进程用一个**独立 schema**（`kb_test_<uuid8>`），`search_path` 指过去，
  业务库的 `public` 表完全看不见 —— 隔离强度与原来看临时文件等价，但跑的是真库；
- 会话结束时 `DROP SCHEMA ... CASCADE`，不留垃圾。

代价与收益都很明确：`pytest` 现在**要求 PG 起着**。这是「彻底不用 SQLite」的必然结果，
不是配置疏漏。起库命令见 `docs/production/01-data-migration.md` §8.1。
"""
import atexit
import os
import uuid

import pytest

TEST_SCHEMA = "kb_test_" + uuid.uuid4().hex[:8]

# DSN 只在这里兜底一次：外部可用 WEBAPP_DATABASE_URL 覆盖（CI / 换端口时用）。
# DSN 解析顺序：已有环境变量 → 项目根的 .env（机器本地，gitignored）→ 标准 5432 默认值。
# **不要把本机端口写进仓库**：换台机器会因为这个硬编码直接跑不起来。
from webapp.envfile import load_env  # noqa: E402

load_env()
os.environ.setdefault(
    "WEBAPP_DATABASE_URL", "postgresql://kb:kb_demo_only@127.0.0.1:5432/kb"
)
os.environ["WEBAPP_DB_SCHEMA"] = TEST_SCHEMA
os.environ["WEBAPP_LLM_PROVIDER"] = "offline"
os.environ["WEBAPP_EMBEDDING_PROVIDER"] = "offline"
os.environ["WEBAPP_RERANK_PROVIDER"] = "off"
# P2 多模态已接通真实网关，单测必须显式关掉，避免图片用例消耗真实流量
os.environ["PARSING_OFFLINE"] = "1"


def _admin_conn():
    """建/删 schema 用：不带 search_path 的裸连接。"""
    import psycopg

    return psycopg.connect(os.environ["WEBAPP_DATABASE_URL"], connect_timeout=5, autocommit=True)


@pytest.fixture(scope="session", autouse=True)
def _prepare_schema():
    """整轮测试开始前建空 schema 并建表；结束后删掉。

    必须在任何 webapp 导入/连接之前建好，否则 pgkit 的 `SET search_path` 会指向
    一个不存在的 schema 直接报错。
    """
    with _admin_conn() as raw:
        with raw.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{TEST_SCHEMA}"')

    from webapp import db

    db.init_db()
    yield
    with _admin_conn() as raw:
        with raw.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')


@atexit.register
def _cleanup():
    """兜底：pytest 被强杀时 fixture 的收尾不会执行，这里再删一次（幂等）。"""
    try:
        with _admin_conn() as raw:
            with raw.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
    except Exception:
        pass


def _reset_rate_state() -> None:
    """把限流规则与滑动窗口复位成默认值。

    限流状态有两份，都要清：
    1. **持久层**：PG 的 `rate_rules` 表（同一个 schema 内跨用例留存）；
    2. **进程层**：`observability._WINDOWS`（`MemoryLimiter` 刻意复用它，所以清一份等于清两份）。

    `db.RATE_RULE_DEFAULTS` 是默认值的唯一事实源，`_seed()` 与这里共用，
    避免「测试里写死 1200、种子改成 1200、某处还是 120」这类三方漂移。
    """
    from webapp import db, observability as obs, ratelimit_dist

    try:
        conn = db.get_conn()
        try:
            for dim, (lim, win) in db.RATE_RULE_DEFAULTS.items():
                conn.execute(
                    "UPDATE rate_rules SET limit_count=?, window_s=?, enabled=true"
                    " WHERE dimension=?", (lim, win, dim),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # 首次用例可能在建表前跑到这里；此时没有需要复位的东西
        pass

    with obs._rate_lock:
        obs._WINDOWS.clear()
    # 丢掉后端实例：万一某个用例改过 WEBAPP_REDIS_URL，下一个用例要重新决策后端
    ratelimit_dist._LIMITER = None
    ratelimit_dist._LIMITER_KEY = None


@pytest.fixture(autouse=True)
def _isolate_rate_state():
    """每个用例**开始前**复位限流状态。

    真实故障（已在全量跑里复现）：`test_http_path_does_not_share_model_bucket`
    把 `model` 维度配额改成 1 用来验证 429，改完没还原；由于同一个 schema 跨用例共享，
    后续 `test_safety_api.py` 的所有 chat 请求全被 429 打死，报「2 failed」。
    业务代码没错，是测试之间没有隔离。

    选「开始前」而不是「结束后」：上一个用例崩在中间没走到收尾，下一个用例仍是干净起点。
    """
    _reset_rate_state()
    yield
