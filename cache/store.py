"""双层缓存：L1 进程内 LRU（重启清空）+ L2 SQLite 持久化（重启可恢复）。

为什么分两层：
- L1 快但**重启必然丢**；把它当加速器，不当真值。
- L2 落在与业务库同一个 SQLite 文件里，重启后条目仍在；命中后仍要复核
  `data_version` / `authz_snapshot` / `policy_version`，过期或版本变了就当未命中。
- 生产对应「PG 为状态真值 + Redis 做实时通道」，本包是它的演示替身。

安全约束：调用方必须**先按权限过滤再写**；本层不负责授权，只在读取时做快照复核。
"""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cache.contracts import (CacheEntry, CachePolicy, CacheStats, REASON_AUTHZ,
                             REASON_CAPACITY, REASON_EXPLICIT, REASON_POLICY,
                             REASON_TTL, REASON_VERSION)
from cache.keys import key_matches

ROOT = Path(__file__).resolve().parent.parent


def default_db_path() -> Path:
    import os
    return Path(os.environ.get("WEBAPP_DB_PATH", str(ROOT / ".data" / "webapp" / "app.db")))


def utcnow(now: str | None = None) -> str:
    return now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    key             TEXT PRIMARY KEY,
    namespace       TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    data_version    TEXT NOT NULL DEFAULT '',
    authz_snapshot  TEXT NOT NULL DEFAULT '',
    policy_version  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    last_access     TEXT NOT NULL,
    hits            INTEGER NOT NULL DEFAULT 0,
    is_negative     INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_cache_expires ON cache_entries(expires_at);
CREATE INDEX IF NOT EXISTS ix_cache_access ON cache_entries(last_access);
CREATE TABLE IF NOT EXISTS runtime_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class CacheStore:
    def __init__(self, db_path: Path | None = None, policy: CachePolicy | None = None):
        self.db_path = Path(db_path or default_db_path())
        self.policy = policy or CachePolicy()
        self._lock = threading.RLock()
        self._l1: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = CacheStats()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    # -------------------------------------------------------------- 连接
    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def ensure_schema(self) -> None:
        con = self._conn()
        try:
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()

    # -------------------------------------------------------------- runtime_state
    def state_get(self, key: str, default: str = "") -> str:
        con = self._conn()
        try:
            r = con.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default
        finally:
            con.close()

    def state_set(self, key: str, value: str, *, now: str | None = None) -> None:
        con = self._conn()
        try:
            con.execute(
                "INSERT INTO runtime_state(key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(value), utcnow(now)))
            con.commit()
        finally:
            con.close()

    def state_incr(self, key: str, *, now: str | None = None) -> int:
        with self._lock:
            cur = int(self.state_get(key, "0") or 0) + 1
            self.state_set(key, str(cur), now=now)
            return cur

    # -------------------------------------------------------------- 读写
    def _ttl_for(self, is_negative: bool, jitter: float | None = None) -> int:
        base = (self.policy.negative_ttl_seconds if is_negative
                else self.policy.ttl_seconds)
        j = self.policy.ttl_jitter if jitter is None else jitter
        return max(1, int(base * (1 + random.uniform(-j, j)))) if j else max(1, base)

    def put(self, key: str, parts: dict[str, str], value: Any, *,
            is_negative: bool = False, now: str | None = None,
            jitter: float | None = None) -> CacheEntry:
        """写缓存。**调用方负责先做权限过滤**；这里只做存储与版本标记。"""
        stamp = utcnow(now)
        ttl = self._ttl_for(is_negative, jitter)
        exp = (datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc) + timedelta(seconds=ttl)
               ).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = json.dumps(value, ensure_ascii=False, default=str)
        entry = CacheEntry(
            key=key, namespace=parts.get("kind", "retrieval"), value=value,
            data_version=parts.get("data_version", ""),
            authz_snapshot=parts.get("authz", ""),
            policy_version=parts.get("policy_version", ""),
            created_at=stamp, expires_at=exp, last_access=stamp,
            is_negative=is_negative, size_bytes=len(payload.encode("utf-8")),
        )
        with self._lock:
            self._l1[key] = entry
            self._l1.move_to_end(key)
            self._trim_l1()
            con = self._conn()
            try:
                con.execute(
                    "INSERT INTO cache_entries(key,namespace,value_json,data_version,authz_snapshot,"
                    "policy_version,created_at,expires_at,last_access,hits,is_negative,size_bytes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "value_json=excluded.value_json,data_version=excluded.data_version,"
                    "authz_snapshot=excluded.authz_snapshot,policy_version=excluded.policy_version,"
                    "created_at=excluded.created_at,expires_at=excluded.expires_at,"
                    "last_access=excluded.last_access,is_negative=excluded.is_negative,"
                    "size_bytes=excluded.size_bytes",
                    (key, entry.namespace, payload, entry.data_version, entry.authz_snapshot,
                     entry.policy_version, stamp, exp, stamp, 0, int(is_negative), entry.size_bytes))
                con.commit()
            finally:
                con.close()
            self.stats.writes += 1
        return entry

    def get(self, key: str, *, data_version: str, authz: str, policy_ver: str,
            now: str | None = None) -> tuple[Any | None, str | None]:
        """读缓存。返回 (值, 命中层级)；未命中返回 (None, None)。

        命中后仍复核三个版本字段——**TTL 不是权限控制**。
        """
        stamp = utcnow(now)
        with self._lock:
            e = self._l1.get(key)
            if e:
                if e.expires_at < stamp:
                    self._drop(key, REASON_TTL)
                    return None, None
                if not key_matches(e.as_dict(), data_version=data_version, authz=authz,
                                   policy_ver=policy_ver):
                    self._drop(key, self._mismatch_reason(e.as_dict(), data_version,
                                                          authz, policy_ver))
                    return None, None
                e.hits += 1
                e.last_access = stamp
                self._l1.move_to_end(key)
                self.stats.l1_hits += 1
                self._touch_l2(key, stamp, e.hits)
                return e.value, "l1"

            con = self._conn()
            try:
                r = con.execute("SELECT * FROM cache_entries WHERE key=?", (key,)).fetchone()
            finally:
                con.close()
            if not r:
                self.stats.misses += 1
                return None, None
            if r["expires_at"] < stamp:
                self._drop(key, REASON_TTL)
                self.stats.misses += 1
                return None, None
            if not key_matches(dict(r), data_version=data_version, authz=authz,
                               policy_ver=policy_ver):
                self._drop(key, self._mismatch_reason(dict(r), data_version, authz, policy_ver))
                self.stats.misses += 1
                return None, None
            value = json.loads(r["value_json"])
            entry = CacheEntry(
                key=key, namespace=r["namespace"], value=value,
                data_version=r["data_version"], authz_snapshot=r["authz_snapshot"],
                policy_version=r["policy_version"], created_at=r["created_at"],
                expires_at=r["expires_at"], last_access=stamp, hits=int(r["hits"]) + 1,
                is_negative=bool(r["is_negative"]), size_bytes=int(r["size_bytes"]),
            )
            self._l1[key] = entry
            self._l1.move_to_end(key)
            self._trim_l1()
            self.stats.l2_hits += 1
            self._touch_l2(key, stamp, entry.hits)
            return value, "l2"

    @staticmethod
    def _mismatch_reason(entry: dict[str, Any], data_version: str,
                         authz: str, policy_ver: str) -> str:
        if entry.get("data_version") != data_version:
            return REASON_VERSION
        if entry.get("authz_snapshot") != authz:
            return REASON_AUTHZ
        if entry.get("policy_version") != policy_ver:
            return REASON_POLICY
        return REASON_EXPLICIT

    def _touch_l2(self, key: str, stamp: str, hits: int) -> None:
        con = self._conn()
        try:
            con.execute("UPDATE cache_entries SET last_access=?, hits=? WHERE key=?",
                        (stamp, hits, key))
            con.commit()
        finally:
            con.close()

    def _drop(self, key: str, reason: str) -> None:
        self._l1.pop(key, None)
        con = self._conn()
        try:
            con.execute("DELETE FROM cache_entries WHERE key=?", (key,))
            con.commit()
        finally:
            con.close()
        self.stats.invalidations[reason] = self.stats.invalidations.get(reason, 0) + 1

    def _trim_l1(self) -> None:
        while len(self._l1) > self.policy.l1_capacity:
            self._l1.popitem(last=False)
            self.stats.evictions += 1

    # -------------------------------------------------------------- 失效与清理
    def invalidate_all(self, reason: str = REASON_EXPLICIT) -> int:
        with self._lock:
            n = len(self._l1)
            self._l1.clear()
            con = self._conn()
            try:
                cur = con.execute("DELETE FROM cache_entries")
                n = cur.rowcount
                con.commit()
            finally:
                con.close()
            self.stats.invalidations[reason] = self.stats.invalidations.get(reason, 0) + n
            return n

    def purge_expired(self, *, now: str | None = None) -> int:
        stamp = utcnow(now)
        with self._lock:
            for k in [k for k, e in self._l1.items() if e.expires_at < stamp]:
                self._l1.pop(k, None)
            con = self._conn()
            try:
                cur = con.execute("DELETE FROM cache_entries WHERE expires_at < ?", (stamp,))
                n = cur.rowcount
                con.commit()
            finally:
                con.close()
            self.stats.expired_purged += n
            self.stats.invalidations[REASON_TTL] = self.stats.invalidations.get(REASON_TTL, 0) + n
            return n

    def enforce_capacity(self) -> int:
        """L2 超容量时按 last_access 淘汰最久未用的条目。"""
        con = self._conn()
        try:
            row = con.execute("SELECT COUNT(*) AS c FROM cache_entries").fetchone()
            total = int(row["c"])
            excess = max(0, total - self.policy.l2_capacity)
            if excess:
                con.execute(
                    "DELETE FROM cache_entries WHERE key IN "
                    "(SELECT key FROM cache_entries ORDER BY last_access ASC LIMIT ?)",
                    (excess,))
                con.commit()
        finally:
            con.close()
        self.stats.evictions += excess
        self.stats.invalidations[REASON_CAPACITY] = (
            self.stats.invalidations.get(REASON_CAPACITY, 0) + excess)
        return excess

    def count(self) -> int:
        con = self._conn()
        try:
            return int(con.execute("SELECT COUNT(*) AS c FROM cache_entries").fetchone()["c"])
        finally:
            con.close()

    def clear_memory(self) -> None:
        """模拟重启：只清 L1，保留 L2。用于验证「重启后缓存仍可恢复」。"""
        with self._lock:
            self._l1.clear()
            self.stats.l1_hits = 0
