"""锁定 `pgkit` 的连接池行为（SQLAlchemy Engine 接入后新增）。

为什么值得单独一个测试文件：池化是**不可见的行为**——跑出来的 SQL 完全一样，
功能测试全绿也证明不了池真的在复用。而池化一旦悄悄失效（比如有人把 `connect()`
改回 `psycopg.connect()`，或 `close()` 改成 `_raw.close()`），表现只是「变慢」，
没有任何用例会红。所以这里直接查 `pg_backend_pid()`：同一后端进程 = 同一条物理连接。

第二个用例守的是池化带来的**新风险**：连接从池里被复用时，会带着上一个借用人的状态。
`autocommit` 是连接级属性，如果 `connect()` 不显式设置，一处写 `connect(autocommit=True)`
之后的每一次 `connect()` 都会是自动提交，事务静默失效。
"""
from __future__ import annotations

import pgkit


def _pid(conn) -> int:
    return conn.execute("SELECT pg_backend_pid() AS p").fetchone()["p"]


def test_connect_close_reuses_pooled_connections():
    """`close()` 是还池不是断连：N 次借还只应看到不超过池大小的后端进程。

    为什么不断言「连续两次同一个 pid」：池里有好几条连接，取哪条由池决定，
    单跑这个文件时池里只有一条所以恰好相同，跑全量时就是不同的——那种断言会
    **单跑绿、全量红**。真正要守的性质是「借还不会持续新建连接」，
    所以看**不同 pid 的数量上界**。
    """
    import os

    pool_size = int(os.environ.get("WEBAPP_DB_POOL_SIZE", "5"))
    rounds = pool_size * 4          # 借还次数远多于池容量

    pids = []
    for _ in range(rounds):
        conn = pgkit.connect()
        try:
            pids.append(_pid(conn))
        finally:
            conn.close()

    distinct = len(set(pids))
    assert distinct <= pool_size, (
        f"{rounds} 次借还出现了 {distinct} 个不同后端连接（池上限 {pool_size}），池没起到限流作用"
    )
    assert distinct < rounds, (
        f"{rounds} 次借还出现了 {rounds} 个不同后端连接 = 每次都在新建连接，池完全没生效"
        "（检查 close() 是不是在断 TCP 而不是还池）"
    )


def test_autocommit_is_reset_per_checkout():
    """autocommit 是连接级状态，必须每次 checkout 显式设置，不能顺着上一次的值。"""
    c = pgkit.connect(autocommit=True)
    try:
        assert c._raw.autocommit is True
    finally:
        c.close()

    # 同一条物理连接被还池后再借出来，默认应是**非**自动提交。
    d = pgkit.connect()
    try:
        assert d._raw.autocommit is False, "池里带出了上一次的 autocommit=True，事务会静默失效"
    finally:
        d.close()

    # 显式 True 也要真的生效（不是只在第一次设得上）。
    e = pgkit.connect(autocommit=True)
    try:
        assert e._raw.autocommit is True
    finally:
        e.close()


def test_sqlalchemy_url_uses_psycopg3_dialect():
    """`postgresql://` 在 SQLAlchemy 里默认走 psycopg2，会 ImportError；必须改写成 +psycopg。"""
    assert pgkit._sqlalchemy_url("postgresql://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    # 已经带方言的不动（避免重复前缀）
    assert pgkit._sqlalchemy_url("postgresql+psycopg://u:p@h/d") == "postgresql+psycopg://u:p@h/d"


def test_search_path_survives_pool_reuse():
    """search_path 必须在**连接建立时**就带上，而不是靠一次性 `SET`。

    这条最容易埋雷：如果实现写成 `conn.execute("SET search_path ...")`，第一次借出
    是好的，还池后第二次借出时那条 `SET` 早被下一个借用人覆盖或回滚了 —— 表现是
    「跑一遍绿、跑两遍花」。所以这里**连续借两次**，每次都要看到正确的 search_path。
    """
    import os

    schema = os.environ["WEBAPP_DB_SCHEMA"]

    for round_no in (1, 2, 3):
        conn = pgkit.connect()
        try:
            path = conn.execute("SHOW search_path").fetchone()[0]
        finally:
            conn.close()
        assert path.split(",")[0].strip() == schema, (
            f"第 {round_no} 次借出时 search_path 首位不是测试 schema（实得 {path!r}），"
            "说明 search_path 是靠一次性 SET 设的，池复用后会串库"
        )
        # 末尾必须带 public：**扩展是库级的装不进 schema**，pgvector 的 vector 类型
        # 与 `<=>` 操作符都在 public 里，漏了会报 type/operator does not exist。
        assert "public" in [p.strip() for p in path.split(",")], f"search_path 少了 public（{path!r}）"


def test_session_level_set_cannot_poison_the_pool():
    """模拟最坏情况：有人在 autocommit 连接上 `SET search_path`，还池后污染下一个借用人。

    这个场景在**换池化之前根本不存在**——那时每次 `connect()` 都是新建 TCP + 新会话，
    一条恶意的 `SET` 随连接一起消失。有了池，会话状态会跨借用人存活。已实测确认过
    污染真的会发生（下一个借用人 `SHOW search_path` 拿到 `pg_catalog`），所以
    `connect()` 在配置了 `WEBAPP_DB_SCHEMA` 时必须于 checkout 处复核一次。

    这条用例的价值在于：如果哪天有人删掉那次复核（觉得「多一次往返没用」），
    它会红，而功能测试仍然全绿——串库是最难靠功能测试发现的那类 bug。
    """
    import os

    schema = os.environ["WEBAPP_DB_SCHEMA"]

    dirty = pgkit.connect(autocommit=True)
    try:
        dirty.execute("SET search_path TO pg_catalog")   # 立即提交在物理连接上
    finally:
        dirty.close()

    clean = pgkit.connect()
    try:
        path = clean.execute("SHOW search_path").fetchone()[0]
    finally:
        clean.close()

    assert path.split(",")[0].strip() == schema, (
        f"上一条连接留下的 search_path 污染了下一个借用人（实得 {path!r}）。"
        "checkout 处的 search_path 复核被绕过或删掉了。"
    )
