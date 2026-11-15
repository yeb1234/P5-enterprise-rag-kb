"""PostgreSQL 连接适配层（中立基础设施模块）：让 138 处既有调用点不用逐个手改。

**位置说明**：本文件放在项目根而不是 `webapp/` 下，是因为 `cache/` 也要用同一套
连接与占位符翻译，而 `cache/` 是纯函数包、**不允许反向 import `webapp/`**。
放在根目录后，`webapp/` 与 `cache/` 都能 import 它，依赖方向保持单向：
    webapp/ ─┐
             ├─→ pgkit
    cache/  ─┘

## 为什么需要这一层

`webapp/` 原来跑在 SQLite 上，全仓有 **138 处** `conn.execute(...)`，SQL 里用的是
SQLite 风格的 `?` 占位符。把运行时换成 PostgreSQL 时，如果逐处手改 `?` → `%s`，
就要同时处理「字符串里的 `%` 必须写成 `%%`」这个隐藏地雷——`LIKE '%' || ? || '%'`
这类写法一旦漏改就会静默变成参数错位。**在一个有 134 项测试的仓里做这种批量文本替换，
风险远大于收益。**

所以这里用一个薄适配器，在**唯一入口**做两件事：

1. `?` → `%s`（占位符翻译）
2. 有参数时把 SQL 里所有 `%` → `%%`（psycopg 的转义规则）

调用点一行都不用改，方言差异收敛在一个文件里。

## 这不是"还在用 SQLite"

运行时**没有任何 sqlite3 依赖**，连接的是真 PostgreSQL。`?` 只是一种占位符风格
（MySQL / 部分驱动也用 `?`），由本层翻译成 psycopg 的 `%s`。

**后续可选清理**：按文件把 `?` 逐个替换成 `%s` 并删掉 `_translate()`，即可去掉这层。
本次不做，是因为「138 处改文本」的回归风险不值得在换存储的同一次改动里承担。

## 连接来源：SQLAlchemy Engine（池化），SQL 仍然显式手写

`backend/` 生产骨架用 SQLAlchemy 2.0 + Alembic 做 ORM；这一层**只用 SQLAlchemy 的
Engine / 连接池 / `pool_pre_ping`**，不上 ORM、不加隐式查询——SQL 全部手写在这里，
因为 `webapp/` 的 138 处调用点是按 SQL 文本写的，改 ORM 是重写而不是接入。

方言必须是 `postgresql+psycopg://`：SQLAlchemy 的 `postgresql://` 默认走 psycopg2，
本项目装的是 psycopg3，不加 `+psycopg` 会得到 `ModuleNotFoundError: psycopg2`。

池化后有三件事必须注意（前两件踩过，第三件是实测出来的隐患）：

- **`search_path` 不能只 SET 一次**：连接回池后会被别的请求复用，一次性 `SET` 会串 schema。
  这里把它写进 `connect_args={"options": "-c search_path=..."}`，**连接建立时就带上**，
  池里每条连接都自带正确值。
- **`autocommit` 是连接级属性**：从池里取出的连接可能带着上一次的 `autocommit=True`。
  所以每次 `connect()` 都显式设置，不信上次留下的状态。
- **`options` 只在连接建立时生效，管不住「会话级 SET」**：池里的连接被借用上千次，
  只要有**一处**在 autocommit 连接上执行过 `SET search_path`，它会提交在物理连接上，
  下一个借用人拿到的是被改掉的值（实测：`SHOW search_path` 变成 `pg_catalog`）。
  所以配置了 `WEBAPP_DB_SCHEMA` 时，`connect()` 会在 checkout 处再补一条 `SET` 复核
  ——代价是两次往返（约 1.4ms），只在用 schema 隔离的场景（测试 / 分环境部署）付这个钱，
  生产不设 schema 时零开销。**在 autocommit 连接上写会话级 `SET` 是禁止操作**，
  新代码要改会话参数请走 `connect()` 参数或 `connect_args`。

## 已知取舍

- **`?` 在字符串字面量里会被误翻译**：本仓已核查，SQL 字符串内没有 ASCII `?`；
  新增 SQL 若要在字面量里写问号，请用全角 `？` 或改用 `%s` 直接写。
"""
from __future__ import annotations

import os
import pathlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_DSN = "postgresql://kb:kb_demo_only@127.0.0.1:5432/kb"

_ENV_LOADED = False


def load_dotenv() -> None:
    """把项目根 `.env` 里**尚未存在**的键补进 os.environ（幂等、零依赖）。

    为什么放在这里：`pgkit.dsn()` 是连接串的唯一出口，脚本（迁移/自检）和测试都走它。
    如果只在 webapp 内部读 .env，`python scripts/xxx.py` 就会因为拿不到 DSN 而连到
    标准 5432——本机 5432 是别的项目的库，报出来是「密码错误」，非常误导。
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    path = pathlib.Path(__file__).resolve().parent / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def dsn() -> str:
    """连接的唯一来源：环境变量 → 项目根 .env → 标准 5432 默认值。"""
    load_dotenv()
    return (os.environ.get("WEBAPP_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
            or DEFAULT_DSN)


def _translate(sql: str, has_params: bool) -> str:
    """`?` → `%s`；有参数时 `%` → `%%`（否则 LIKE 里的百分号会被当占位符）。"""
    if not has_params:
        return sql
    return sql.replace("%", "%%").replace("?", "%s")


# 命名占位符：SQLite 的 `sqlite3` 支持 `:name` + dict，psycopg 只支持 `%(name)s`。
# 负向后视排除 `::`（PG 的类型转换写法，如 `'{}'::jsonb`），否则 `::jsonb` 会被误当成参数。
_NAMED_RE = re.compile(r"(?<!:):(\w+)")


def _translate_named(sql: str) -> str:
    return _NAMED_RE.sub(r"%(\1)s", sql.replace("%", "%%"))


def _normalize(value: Any) -> Any:
    """把 PG 的强类型值收敛回应用层原本的「字符串 / 浮点」契约。

    为什么在这里做：SQLite 是动态类型，`timestamptz` 列读出来是 ISO 字符串、
    `numeric` 读出来是 float，全仓 138 处调用点与 Pydantic 响应模型都按这个契约写。
    PG 会老老实实返回 `datetime` / `Decimal`，于是：
    - `UserOut(created_at=...)` 要求 str，收到 datetime 直接 ValidationError；
    - `Decimal + float` 抛 TypeError，成本累加会炸。

    与其改上百处调用点（每处都可能引入新 bug），不如在**唯一的数据出口**统一转换。
    这是把「方言差异」关进一个文件的同一套逻辑，和占位符翻译是一个思路。

    代价：`numeric` 退化成 float，**不再有精确十进制**。演示与统计够用；
    真要做金额对账，应在 SQL 里 `ROUND(...)::text` 拿到字符串再自行解析，
    不能指望这一层给你精确性。这条限制写进 `docs/production/01-data-migration.md`。
    """
    if isinstance(value, datetime):
        # 统一 UTC + 'Z' 后缀，与 time.strftime("%Y-%m-%dT%H:%M:%SZ") 的输出同形
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class Row:
    """同时支持 `row["col"]` 与 `row[0]` 的行对象。

    SQLite 的 `sqlite3.Row` 两种下标都支持，既有代码大量混用（如
    `PRAGMA table_info` 用 `r[1]`、查询结果用 `r["status"]`）。psycopg 的
    `dict_row` 只支持字符串下标，`tuple_row` 只支持整数，所以这里自己包一层，
    避免为了换驱动去改一堆下标写法。
    """

    __slots__ = ("_values", "_index")

    def __init__(self, values: Sequence[Any], index: dict[str, int]) -> None:
        self._values = tuple(_normalize(v) for v in values)
        self._index = index

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._index)

    def get(self, key: str, default: Any = None) -> Any:
        i = self._index.get(key)
        return default if i is None else self._values[i]

    def as_dict(self) -> dict[str, Any]:
        return {k: self._values[i] for k, i in self._index.items()}

    def __repr__(self) -> str:  # 便于失败断言时看清内容
        return f"Row({self.as_dict()!r})"


class PgCursor:
    """包一层 `psycopg.Cursor`，fetch 出来的行统一成 `Row`。"""

    def __init__(self, cur) -> None:
        self._cur = cur
        desc = cur.description or []
        self._index = {d.name: i for i, d in enumerate(desc)}

    def _wrap(self, row):
        return None if row is None else Row(row, self._index)

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchall(self) -> list[Row]:
        return [self._wrap(r) for r in self._cur.fetchall()]

    def fetchmany(self, size: int) -> list[Row]:
        return [self._wrap(r) for r in self._cur.fetchmany(size)]

    def __iter__(self):
        for r in self._cur:
            yield self._wrap(r)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self) -> None:
        self._cur.close()


class PgConn:
    """`sqlite3.Connection` 的最小同形替代：execute / executemany / commit / close。

    `close()` 不是断开 TCP，而是**把连接还回池**——这一点很关键：
    全仓 138 处 `finally: conn.close()` 因此从「每次重连」变成「归还」，调用点零改动。
    """

    def __init__(self, fairy) -> None:
        self._fairy = fairy
        self._raw = fairy.driver_connection

    # -- 执行 -------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()) -> PgCursor:
        # 命名占位符（dict）与位置占位符（序列）走两条翻译路径。
        # 必须显式区分：`tuple(dict)` 只会拿到 key 列表，会被当成参数序列静默写错。
        if isinstance(params, Mapping):
            return PgCursor(self._raw.execute(_translate_named(sql), params))
        params = tuple(params)
        return PgCursor(self._raw.execute(_translate(sql, bool(params)), params or None))

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> PgCursor:
        rows = [tuple(p) for p in seq]
        return PgCursor(self._raw.executemany(_translate(sql, bool(rows)), rows))

    def executescript(self, script: str) -> None:
        """按语句切分执行（DDL 脚本用）。仅供 init 使用，不支持占位符。"""
        self._raw.execute(script)

    def cursor(self) -> PgCursor:
        return PgCursor(self._raw.cursor())

    # -- 事务 / 生命周期 ---------------------------------------------------
    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        # 还回池（SQLAlchemy 在归还时会做 rollback，未提交的事务不会漏到下一个借用人）
        self._fairy.close()

    def __enter__(self) -> "PgConn":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# 引擎按 (DSN, schema) 缓存：schema 不同必须分开池化，
# 否则测试的独立 schema 会和生产 public 共用同一条连接（串库）。
_ENGINES: dict[tuple[str, str], Any] = {}


def _sqlalchemy_url(url: str) -> str:
    """把 psycopg 风格的 DSN 改写成 SQLAlchemy 的 psycopg3 方言。

    `postgresql://` 在 SQLAlchemy 里默认解析成 psycopg2；本项目装的是 psycopg3，
    不改写会得到 `ModuleNotFoundError: No module named 'psycopg2'`。
    """
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _engine() -> Any:
    """取（或建）当前 DSN + schema 对应的 SQLAlchemy Engine。"""
    try:
        from sqlalchemy import create_engine  # type: ignore
    except ImportError:  # pragma: no cover - 环境问题
        raise RuntimeError(
            "未安装 SQLAlchemy。连接层现在建立在它之上：\n"
            "    pip install 'SQLAlchemy>=2.0,<2.1'"
        ) from None

    schema = (os.environ.get("WEBAPP_DB_SCHEMA") or "").strip()
    key = (dsn(), schema)
    eng = _ENGINES.get(key)
    if eng is not None:
        return eng

    connect_args: dict[str, Any] = {"connect_timeout": 5}
    if schema:
        # 测试隔离：业务表建在独立 schema 里且排第一，同名对象一律解析到它。
        # 末尾必须带 public：**扩展是库级的，装不进业务 schema**（pgvector 的 vector 类型、
        # <=> 操作符都在 public 里），只写自己会报 type/operator does not exist。
        # 写进 options 而不是 SET：连接从池里被复用时，一次性 SET 早被覆盖了。
        connect_args["options"] = f"-c search_path={schema},public"

    eng = create_engine(
        _sqlalchemy_url(dsn()),
        pool_pre_ping=True,          # PG 重启后池里的死连接会被自动剔除，不用手工重连
        pool_size=int(os.environ.get("WEBAPP_DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("WEBAPP_DB_MAX_OVERFLOW", "10")),
        pool_timeout=int(os.environ.get("WEBAPP_DB_POOL_TIMEOUT", "30")),
        pool_recycle=1800,           # 避免被中间设备掐掉的长连接
        connect_args=connect_args,
        future=True,
    )
    _ENGINES[key] = eng
    return eng


def dispose_pools() -> None:
    """关闭并清空所有连接池。测试收尾要调——池里的连接会一直占着 schema。"""
    for eng in list(_ENGINES.values()):
        try:
            eng.dispose()
        except Exception:  # noqa: BLE001 - 收尾失败不该盖住真正的失败
            pass
    _ENGINES.clear()


def connect(autocommit: bool = False) -> PgConn:
    """从连接池取一条连接。

    缺依赖时给可执行的安装提示，而不是 ImportError 栈。
    """
    try:
        import psycopg  # noqa: F401  # type: ignore
    except ImportError:  # pragma: no cover - 环境问题
        raise RuntimeError(
            "未安装 psycopg。webapp 已不再使用 SQLite，必须连 PostgreSQL：\n"
            "    pip install 'psycopg[binary]'\n"
            "并确保 PG 已启动（docker compose -f docker-compose.infra.yml up -d postgres）"
        ) from None

    fairy = _engine().raw_connection()      # _ConnectionFairy，close() 等于还回池
    raw = fairy.driver_connection          # 真正的 psycopg.Connection
    # autocommit 是连接级属性，池里取出来的连接可能带着上一次的值，必须每次显式设置。
    if raw.autocommit != autocommit:
        try:
            raw.rollback()                 # psycopg 不允许在事务中改 autocommit
        except Exception:  # noqa: BLE001 - 无事务时 rollback 是空操作
            pass
        raw.autocommit = autocommit

    # 只有在「显式指定了 schema」时才复核 search_path —— 这一步要两次往返（约 1.4ms），
    # 主链路承担不起。为什么必须复核：`connect_args` 里的 options 只在**连接建立时**生效，
    # 而池里的连接会被反复借用；一旦某处在 autocommit 连接上执行过会话级 `SET search_path`
    # （已实测：还池后下一个借用人直接拿到被改掉的 search_path），就串库了。
    # 只在本仓确实用 schema 隔离的场景（测试、按 schema 分环境）兜底。
    schema = (os.environ.get("WEBAPP_DB_SCHEMA") or "").strip()
    if schema:
        raw.execute(f'SET search_path TO "{schema}", public')
    return PgConn(fairy)


def reset_schema(schema: str, ddl: str) -> None:
    """测试夹具用：切到一个干净 schema 并重建表（不触碰业务库的表）。"""
    import psycopg  # type: ignore

    base = psycopg.connect(dsn(), connect_timeout=5, autocommit=True)
    try:
        with base.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        base.close()
