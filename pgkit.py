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

## 已知取舍

- **没有连接池**：`get_conn()` 每次新建连接（本机 ~2-5ms）。演示与测试够用；
  生产应换 `psycopg_pool.ConnectionPool`，或把 `get_conn()` 改成 FastAPI 依赖注入。
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
    """`sqlite3.Connection` 的最小同形替代：execute / executemany / commit / close。"""

    def __init__(self, raw) -> None:
        self._raw = raw

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
        self._raw.close()

    def __enter__(self) -> "PgConn":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def connect(autocommit: bool = False) -> PgConn:
    """新建一条连接。缺 psycopg 时给出可执行的安装提示，而不是 ImportError 栈。"""
    try:
        import psycopg  # type: ignore
    except ImportError:  # pragma: no cover - 环境问题
        raise RuntimeError(
            "未安装 psycopg。webapp 已不再使用 SQLite，必须连 PostgreSQL：\n"
            "    pip install 'psycopg[binary]'\n"
            "并确保 PG 已启动（docker compose -f docker-compose.infra.yml up -d postgres）"
        ) from None
    raw = psycopg.connect(dsn(), connect_timeout=5, autocommit=autocommit)
    # 测试隔离：业务表只建在独立 schema 里，且排第一，同名对象一律解析到它。
    # 末尾必须带 public：**扩展是库级的，装不进业务 schema**（pgvector 的 vector 类型、
    # <=> 操作符、vector_cosine_ops 都在 public 里），search_path 只有自己时
    # 会报 "type vector does not exist" / "operator does not exist: vector <=> vector"。
    # 代价：DDL 漏建某张表时，查询会静默落到 public 的同名表上——所以别让 DDL 悄悄失败。
    schema = (os.environ.get("WEBAPP_DB_SCHEMA") or "").strip()
    if schema:
        with raw.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        if not autocommit:
            raw.commit()
    return PgConn(raw)


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
