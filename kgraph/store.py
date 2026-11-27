"""Neo4j 存储层：全项目唯一的图数据库出入口。

## 取舍

- **延迟导入驱动**：没装 `neo4j` 时不影响其它功能，只在真正用到图谱时报
  `GraphUnavailable`，调用方按「这一路缺席」处理。
- **进程内单例**：驱动本身线程安全（会话不安全，每次查询各开一个）。FastAPI 的同步
  端点跑在线程池里，所以初始化要加锁。
- **探测结果短缓存**：图库挂掉时不能每个检索请求都等一次连接超时——失败结果缓存 15 秒，
  成功结果缓存 60 秒。宁可 15 秒内这一路一直显示「不可用」，也不拖慢主链路。
- **读写分离在会话层**：`read()` 用 READ 会话。这是只读校验之外的最后一道防线——
  即使有语句绕过了关键词校验，驱动层也会拒绝写入。
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from importlib import metadata

from kgraph.contracts import GRAPH_NAMESPACE

_PROBE_TTL_OK = 60.0
_PROBE_TTL_FAIL = 15.0


class GraphUnavailable(RuntimeError):
    """图数据库不可用（驱动未装 / 未配置 / 连不上）。调用方按「这一路缺席」处理，不中断检索。"""


def driver_version() -> str | None:
    """用包的元数据判断驱动是否安装，而不是靠「import 试试看」的命名启发式。"""
    try:
        return metadata.version("neo4j")
    except Exception:  # noqa: BLE001 - 未安装时 metadata 抛 PackageNotFoundError
        return None


class Neo4jStore:
    def __init__(self, uri: str, auth: tuple[str, str], *,
                 namespace: str = GRAPH_NAMESPACE, connection_timeout: float = 3.0) -> None:
        if driver_version() is None:
            raise GraphUnavailable("未安装 neo4j 驱动：pip install 'neo4j>=6.3,<7'")
        try:
            import neo4j
            self._neo4j = neo4j
            self._driver = neo4j.GraphDatabase.driver(
                uri, auth=tuple(auth), connection_timeout=connection_timeout)
        except Exception as exc:  # noqa: BLE001 - 统一收敛成一种「不可用」
            raise GraphUnavailable(f"Neo4j 驱动初始化失败：{type(exc).__name__}: {exc}") from exc
        self.uri, self.namespace = uri, namespace
        self._read_mode = getattr(neo4j, "READ_ACCESS", "READ")
        self._write_mode = getattr(neo4j, "WRITE_ACCESS", "WRITE")

    @classmethod
    def from_env(cls) -> "Neo4jStore | None":
        """从环境变量取连接信息。**未配置返回 None**——图谱路由没启用不算错误。

        连接串与口令只从环境变量来，不进 app_settings、不进仓库：
          WEBAPP_NEO4J_URI=bolt://kb-neo4j:7687   WEBAPP_NEO4J_AUTH=neo4j/<口令>
        `WEBAPP_NEO4J_AUTH` 与 Neo4j 官方的 NEO4J_AUTH 同形（用户名/口令）。
        """
        uri = (os.environ.get("WEBAPP_NEO4J_URI") or "").strip()
        user, _, pwd = (os.environ.get("WEBAPP_NEO4J_AUTH") or "").strip().partition("/")
        if not uri or not user or not pwd:
            return None
        return cls(uri, (user, pwd))

    @contextmanager
    def _session(self, write: bool = False):
        session = self._driver.session(
            default_access_mode=self._write_mode if write else self._read_mode)
        try:
            yield session
        finally:
            session.close()

    def read(self, query: str, params: dict | None = None) -> list[dict]:
        """只读查询。参数永远走 dict，不拼进语句。"""
        with self._session() as s:
            return [r.data() for r in s.run(query, params or {})]

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        """写查询（建图专用）。"""
        with self._session(write=True) as s:
            return [r.data() for r in s.run(query, params or {})]

    def explain(self, query: str, params: dict | None = None) -> None:
        """只让 Neo4j 编译一遍，不执行。语法错误、未知函数在这里抛出来。"""
        with self._session() as s:
            s.run("EXPLAIN " + query, params or {}).consume()

    def ensure_indexes(self) -> None:
        """建两个查询会用到的索引。旧版本若不支持复合索引就退化为全扫——演示规模可接受。"""
        for q in (
            "CREATE INDEX kgraph_entity_kb_name IF NOT EXISTS FOR (e:Entity) ON (e.kb, e.name)",
            "CREATE INDEX kgraph_chunk_kb_id IF NOT EXISTS FOR (c:Chunk) ON (c.kb, c.chunk_id)",
        ):
            try:
                self.run(q)
            except Exception:  # noqa: BLE001
                pass

    def probe(self) -> dict:
        """真连一次并取服务端版本。可用性只认实测，不认「配置看起来对」。"""
        try:
            self._driver.verify_connectivity()
            rows = self.read(
                "CALL dbms.components() YIELD name, versions, edition "
                "RETURN name AS name, versions[0] AS version, edition AS edition")
            info = rows[0] if rows else {}
            return {"ok": True, "uri": self.uri, "namespace": self.namespace,
                    "server": info.get("version"), "edition": info.get("edition"),
                    "driver": driver_version()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "uri": self.uri, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:  # noqa: BLE001
            pass


# 必须是可重入锁：probe_cached() 持锁期间会调 get_store()，后者也要拿锁。
# 用普通 Lock 会自锁——实测表现为请求永远不返回、也没有任何报错（卡住不抛异常最难查）。
_lock = threading.RLock()
_store: Neo4jStore | None = None
_probe_cache: dict = {"at": 0.0, "result": None}


def get_store() -> Neo4jStore | None:
    """进程内单例。未配置返回 None；驱动装不上抛 GraphUnavailable。"""
    global _store
    with _lock:
        if _store is None:
            _store = Neo4jStore.from_env()
        return _store


def probe_cached() -> dict:
    """带短缓存的可用性探测（结果直接进检索报告，给前端和排查看）。"""
    global _probe_cache
    with _lock:
        age = time.monotonic() - _probe_cache["at"]
        if _probe_cache["result"] is not None:
            ttl = _PROBE_TTL_OK if _probe_cache["result"].get("ok") else _PROBE_TTL_FAIL
            if age < ttl:
                return dict(_probe_cache["result"])
        result: dict
        try:
            store = get_store()
            result = store.probe() if store else {"ok": False, "error": "未配置 WEBAPP_NEO4J_URI"}
        except GraphUnavailable as exc:
            result = {"ok": False, "error": str(exc)}
        _probe_cache.update(at=time.monotonic(), result=result)
        return dict(result)


def reset() -> None:
    """测试用：关掉连接并清空缓存。"""
    global _store
    with _lock:
        if _store is not None:
            _store.close()
        _store = None
        _probe_cache.update(at=0.0, result=None)
