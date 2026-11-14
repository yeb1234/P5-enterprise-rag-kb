"""部门维度迁移的三态回归：全新库 / tenant 旧库 / 半迁移态（两列并存）。

背景：本轮把 usage_events.tenant 改为 department_id。SCHEMA 用
CREATE TABLE IF NOT EXISTS，旧库不会自动改列；而 department_id 上的索引
如果写进 SCHEMA，旧库执行 executescript 会先报 "no such column" 导致
迁移根本跑不到（本轮实测复现）。因此索引移到迁移函数里统一幂等创建。
"""
import re
import sqlite3
from pathlib import Path

from webapp import db


_SCHEMA = re.search(r'SCHEMA\s*=\s*r?"""(.*?)"""',
                    Path(db.__file__).read_text(encoding="utf-8"), re.S).group(1)


def _cols(con):
    return {r["name"] for r in con.execute("PRAGMA table_info(usage_events)")}


def _indexes(con):
    return {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='usage_events'")}


def _fresh():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _legacy():
    """模拟 11-12 的演示库：usage_events 只有 tenant 列，且已有一条部门快照数据。"""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE usage_events (
            id TEXT PRIMARY KEY, ts TEXT, project TEXT,
            tenant TEXT NOT NULL DEFAULT 'default', user_id TEXT, call_type TEXT, model TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
            cost_cny REAL, estimated INTEGER, run_id TEXT, detail TEXT, created_at TEXT);
        CREATE INDEX ix_usage_tenant ON usage_events(tenant);
        INSERT INTO usage_events(id,tenant,call_type) VALUES ('old-1','RD','chat');
    """)
    return con


def _migrate(con):
    db._migrate_tenant_to_department(con)
    con.commit()


def test_fresh_schema_has_department_column_and_no_tenant():
    con = _fresh()
    try:
        _migrate(con)
        cols = _cols(con)
        assert "department_id" in cols and "tenant" not in cols
        assert "ix_usage_department" in _indexes(con)
        # 二次调用仍不报错（幂等）
        _migrate(con)
        assert "department_id" in _cols(con)
    finally:
        con.close()


def test_legacy_schema_does_not_break_executescript_and_keeps_data():
    """回归守卫：SCHEMA 本身不得引用 department_id 索引，否则旧库直接失败。"""
    con = _legacy()
    try:
        con.executescript(_SCHEMA)  # 这一句曾抛 no such column: department_id
        assert "tenant" in _cols(con)
        _migrate(con)
        cols = _cols(con)
        assert "department_id" in cols and "tenant" not in cols
        # 存量部门快照不丢、不回溯重写
        row = con.execute("SELECT department_id, call_type FROM usage_events WHERE id='old-1'").fetchone()
        assert row[0] == "RD" and row[1] == "chat"
        assert "ix_usage_tenant" not in _indexes(con)
        assert "ix_usage_department" in _indexes(con)
        _migrate(con)  # 幂等
    finally:
        con.close()


def test_half_migrated_state_drops_legacy_column():
    """半迁移态：两列并存时，把旧列有效值补回新列后删旧列，不留第二条数据来源。"""
    con = _legacy()
    try:
        con.executescript(_SCHEMA)
        con.execute("ALTER TABLE usage_events ADD COLUMN department_id TEXT NOT NULL DEFAULT 'default'")
        con.execute("UPDATE usage_events SET department_id='default' WHERE id='old-1'")
        con.commit()
        assert {"tenant", "department_id"} <= _cols(con)
        _migrate(con)
        cols = _cols(con)
        assert "department_id" in cols and "tenant" not in cols
        # 新列为空/default 的行应被旧列的真实部门值回填
        row = con.execute("SELECT department_id FROM usage_events WHERE id='old-1'").fetchone()
        assert row[0] == "RD"
    finally:
        con.close()


def test_rate_rules_collapse_to_three_dimensions():
    con = _fresh()
    try:
        con.execute("INSERT INTO rate_rules(dimension,limit_count,window_s,enabled,updated_at)"
                    " VALUES ('tenant',3000,60,1,'x')")
        con.commit()
        _migrate(con)
        dims = {r[0] for r in con.execute("SELECT dimension FROM rate_rules")}
        assert "tenant" not in dims and "department" not in dims
        # 旧配置键应收敛为 department
        con.execute("INSERT OR REPLACE INTO eval_configs(key,value,updated_at) VALUES ('tenant','default','x')")
        con.commit()
        _migrate(con)
        keys = {r[0] for r in con.execute("SELECT key FROM eval_configs")}
        assert "department" in keys and "tenant" not in keys
    finally:
        con.close()
