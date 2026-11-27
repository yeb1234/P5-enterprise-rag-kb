"""三路取证的适配层：把 kgraph / llmwiki 两个纯函数包接到 PG、模型、配置上。

## 分工

- 纯函数包负责「怎么算」：抽词、写 Cypher、校验证据、判门禁；
- 本模块负责「从哪取、往哪放」：读 PG、取模型、脱敏、落库、记元信息。

分开的好处是算法能用假 store / 假模型做单测，而这里只有数据搬运。

## 三条不能破的约束

1. **PG 是真值**：图和 Wiki 都建在 PG 的分块之上。图谱检索回来的 chunk_id 必须回到
   PG 复核（分块还在、文档仍 ready）才算数——复核在 `rag.py` 的融合处做，因为那里有全量池。
2. **模型输出也要过出口闸**：分块入库前已脱敏，但模型生成的 Wiki 页面是**新文本**，
   落库前必须再过一遍；脱敏链路不可用时 fail-closed，宁可这次不落库。
3. **降级必须留痕**：Neo4j 挂了、模型是离线占位、门禁没过，都写成路由报告里的
   `status` + `reason`，不抛到用户脸上，也不静默。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from kgraph import builder as graph_builder
from kgraph import extract_llm
from kgraph import retriever as graph_retriever
from kgraph import store as graph_store
from llmwiki import builder as wiki_builder
from llmwiki import gate as wiki_gate_mod
from llmwiki import relevancy
from llmwiki import retriever as wiki_retriever
from webapp import db, providers, settings

log = logging.getLogger("webapp.kb_routes")

GRAPH_META_KEY = "graph_sync_meta"
WIKI_META_KEY = "wiki_build_meta"


class RouteNotReady(RuntimeError):
    """管理员触发的构建动作做不了（依赖没就绪 / 配置不全）。路由层转成 409。"""


def _precheck_graph_sync() -> None:
    probe = graph_store.probe_cached()
    if not probe.get("ok"):
        raise RouteNotReady(
            f"Neo4j 不可用：{probe.get('error')}。先启动："
            "docker compose -f docker-compose.infra.yml --profile graphrag up -d neo4j")


def _precheck_wiki_build() -> None:
    """场景与模型在**启动前**就查：让管理员当场得到明确的「缺什么」，而不是等几分钟后失败。"""
    cfg = settings.get_all()
    if not str(cfg.get("wiki_scenario") or "").strip():
        raise RouteNotReady("请先填写业务场景：没有确定的整理口径，Wiki 无从构建")
    if _llm_or_none(cfg) is None:
        raise RouteNotReady("当前 LLM 为离线占位，无法构建 Wiki；请先在模型设置里切到可用模型")


# ---------------------------------------------------------------- 后台任务
# 构建类动作要跑几分钟（Wiki 构建是十几批模型调用），不能让一个 HTTP 请求干等。
# 做法是「先同步做前置检查 → 立刻返回 → 后台线程执行 → 状态接口轮询」。
# 状态只存在进程内存里：单进程演示够用；多副本时要落到 PG，否则另一副本看不到进度，
# 进程重启也会让「进行中」的任务无声消失。这两点是已知边界，不是疏漏。
_jobs: dict[str, dict] = {}
_job_locks: dict[str, threading.Lock] = {}


def start_job(name: str, fn, precheck) -> dict:
    """启动一个后台构建任务。同名任务正在跑时拒绝（防重复点击把活干两遍）。"""
    with threading.Lock():                       # 只保护锁表的创建，不跨越任务执行期
        lock = _job_locks.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        raise RouteNotReady(f"{name} 正在进行中，请等它完成后再试")
    try:
        precheck()                               # 前置检查同步做：缺什么当场说
    except Exception:
        lock.release()
        raise
    job = {"name": name, "status": "running", "started_at": _now(), "finished_at": None,
           "error": None, "result": None, "progress": None}
    _jobs[name] = job

    def run() -> None:
        try:
            result = fn()
            job.update(status="done", finished_at=_now(), result=result)
        except Exception as exc:  # noqa: BLE001 - 后台线程里的异常只能记在任务状态里
            job.update(status="failed", finished_at=_now(),
                        error=f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            lock.release()

    threading.Thread(target=run, name=f"kb-{name}", daemon=True).start()
    return dict(job)


def job_state(name: str) -> dict:
    return dict(_jobs.get(name, {"name": name, "status": "idle"}))


def reset_jobs() -> None:
    """测试用。"""
    _jobs.clear()
    _job_locks.clear()


def graph_sync_async(doc_ids: list[str] | None = None) -> dict:
    return start_job("graph_sync", lambda: graph_sync(doc_ids), _precheck_graph_sync)


def wiki_build_async() -> dict:
    def run() -> dict:
        return wiki_build(on_batch=lambda i, n: _jobs["wiki_build"].update(progress=f"{i}/{n}"))

    return start_job("wiki_build", run, _precheck_wiki_build)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _llm_or_none(cfg: dict):
    """当前配置下有没有可用模型。离线占位返回 None——纯函数包不必认识 webapp 的类型。"""
    llm = providers.get_llm(cfg)
    return None if getattr(llm, "model_label", "") == "offline-stub" else llm


# ---------------------------------------------------------------- 读 PG
def _ready_chunks() -> list[dict]:
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT c.id AS chunk_id, c.doc_id, c.idx, c.text, d.title "
            "FROM chunks c JOIN documents d ON d.id=c.doc_id "
            "WHERE d.status='ready' ORDER BY c.doc_id, c.idx").fetchall()]
    finally:
        conn.close()


def _ready_docs() -> list[dict]:
    """门禁与指纹要的文档行。`content_hash` 由分块正文算出——documents 表没有 checksum 列，
    而 Wiki 是从分块整理出来的，「正文变没变」只能看分块。"""
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT d.id AS doc_id, d.title, d.revision, d.updated_at, d.valid_from, d.expires_at, "
            "d.last_verified_at, d.created_at, "
            "(SELECT md5(string_agg(c.text, E'\\n' ORDER BY c.idx)) FROM chunks c "
            " WHERE c.doc_id = d.id) AS content_hash "
            "FROM documents d WHERE d.status='ready' ORDER BY d.id").fetchall()]
    finally:
        conn.close()


def _meta_get(key: str) -> dict | None:
    conn = db.get_conn()
    try:
        r = conn.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    try:
        return json.loads(r["value"])
    except (TypeError, ValueError):
        return None


def _meta_set(key: str, value: dict) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO runtime_state(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now()))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- Wiki：门禁与构建
def wiki_gate(cfg: dict | None = None) -> dict:
    cfg = cfg or settings.get_all()
    try:
        meta = _meta_get(WIKI_META_KEY)
        docs = _ready_docs()
        ids = (meta or {}).get("source_doc_ids")
        if ids:
            # 信源 = 这次构建**实际用到**的那批文档。库外的文档增删改不影响已有页面，
            # 不该逼着重建；而这批文档里任何一篇改了、或被删了（从结果里消失），指纹都会变。
            keep = set(ids)
            docs = [d for d in docs if d["doc_id"] in keep]
        return wiki_gate_mod.evaluate_gate(cfg, docs, meta)
    except Exception as exc:  # noqa: BLE001
        # 门禁算不出来时按「不通过」处理并写明原因：这一路缺席好过拿不确定的知识回答
        log.warning("wiki gate unavailable: %s", type(exc).__name__)
        return {"allowed": False, "checks": {}, "reasons": [f"gate_unavailable:{type(exc).__name__}"],
                "fingerprint": ""}


def _redact(text: str, source: str) -> tuple[str, int]:
    from guardrails import default as guard_default

    result = guard_default().ingest(text or "")
    if result.hit_count:
        db.record_safety_event(result, source)
    return result.text, int(result.hit_count or 0)


def wiki_build(on_batch=None) -> dict:
    """把 ready 文档的分块交给模型整理成 Wiki，校验证据链后全量替换旧页面。"""
    _precheck_wiki_build()
    cfg = settings.get_all()
    scenario = str(cfg.get("wiki_scenario") or "").strip()

    chunks = _ready_chunks()
    # 先按场景筛信源：场景决定哪些资料算信源，不能把全库都送去整理
    picked = relevancy.select_by_scenario(scenario, chunks)
    source_doc_ids = sorted({c["doc_id"] for c in picked["chunks"]})
    source_ids = set(source_doc_ids)
    fingerprint = wiki_gate_mod.source_fingerprint(
        [d for d in _ready_docs() if d["doc_id"] in source_ids])
    started = time.perf_counter()
    result = wiki_builder.build(scenario, picked["chunks"], _llm_or_none(cfg), on_batch=on_batch)
    if not result["available"]:
        raise RouteNotReady("模型没有返回可用结果，本次未落库")
    redaction_hits = 0
    try:
        pages = []
        for p in result["pages"]:
            body, n1 = _redact(p["body_md"], "wiki:body")
            summary, n2 = _redact(p.get("summary") or "", "wiki:summary")
            redaction_hits += n1 + n2
            claims = []
            for c in p["claims"]:
                statement, n3 = _redact(c["statement"], "wiki:statement")
                redaction_hits += n3
                claims.append({**c, "statement": statement})
            pages.append({**p, "body_md": body, "summary": summary, "claims": claims})
    except Exception as exc:  # noqa: BLE001
        # fail-closed：脱敏不可用时宁可整次不落库，也不能把可能的原文写进知识库
        raise RouteNotReady(f"内容安全脱敏不可用，已阻断本次构建（fail-closed）：{type(exc).__name__}") from exc

    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM wiki_claims")        # 先删证据链再删页面（无外键，顺序靠代码）
        conn.execute("DELETE FROM wiki_pages")
        built_at = _now()
        for p in pages:
            conn.execute(
                "INSERT INTO wiki_pages(id,title,section,body_md,summary,scenario,built_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (p["page_id"], p["title"], p["section"], p["body_md"], p.get("summary") or "",
                 scenario, built_at))
            for i, c in enumerate(p["claims"]):
                conn.execute(
                    "INSERT INTO wiki_claims(id,page_id,statement,evidence_json,source_hash,ordinal) "
                    "VALUES (?,?,?,?,?,?)",
                    (uuid.uuid4().hex, p["page_id"], c["statement"],
                     json.dumps(c["evidence"], ensure_ascii=False), c["source_hash"], i))
        conn.commit()
    finally:
        conn.close()

    # 信源指纹只算「这次构建实际用到的那批文档」（见函数开头 fingerprint 的计算处）。
    # 不要在这里再按全库算一遍覆盖它——两处算法不一致时，构建完门禁立刻报「信源已变化」。
    meta = {
        "scenario": scenario, "built_at": built_at,
        "source_fingerprint": fingerprint,
        "source_doc_ids": source_doc_ids,
        "scenario_filter": {k: picked[k] for k in ("mode", "terms", "kept", "total", "reason")},
        "pages": len(pages), "claims": sum(len(p["claims"]) for p in pages),
        "stats": result["stats"], "model": providers.get_model_label(cfg),
        "redaction_hits": redaction_hits,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }
    _meta_set(WIKI_META_KEY, meta)
    # 门禁必须在元信息落库**之后**再算：门禁要拿「构建时的指纹」与当前信源比对，
    # 元信息没写就等于「尚未构建」。返回给调用方的应该是构建完成后的真实门禁状态。
    return {"ok": True, **meta, "gate": wiki_gate(cfg)}


def _wiki_rows() -> tuple[list[dict], list[dict]]:
    conn = db.get_conn()
    try:
        pages = [dict(r) for r in conn.execute(
            "SELECT id, title, section, body_md, summary FROM wiki_pages "
            "WHERE scenario <> '' ORDER BY section, id").fetchall()]
        claims = [dict(r) for r in conn.execute(
            "SELECT id, page_id, statement, evidence_json, source_hash FROM wiki_claims "
            "ORDER BY page_id, ordinal").fetchall()]
    finally:
        conn.close()
    for c in claims:
        ev = c.get("evidence_json")
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except ValueError:
                ev = []
        c["evidence"] = ev or []
    return pages, claims


# ---------------------------------------------------------------- 图谱：同步
def graph_sync(doc_ids: list[str] | None = None) -> dict:
    """把 ready 文档的分块写进 Neo4j。`doc_ids` 给了就只重写这些文档的节点。"""
    _precheck_graph_sync()
    cfg = settings.get_all()
    store = graph_store.get_store()
    docs = _ready_docs()
    chunks = _ready_chunks()
    if doc_ids:
        unknown = set(doc_ids) - {c["doc_id"] for c in chunks}
        if unknown:
            raise RouteNotReady(f"这些文档没有可同步的 ready 分块：{sorted(unknown)[:5]}")

    llm_extra = None
    if cfg.get("rag_graph_extractor") == "llm":
        target = chunks if not doc_ids else [c for c in chunks if c["doc_id"] in set(doc_ids)]
        llm_extra = extract_llm.extract_llm(target, _llm_or_none(cfg))
    # plan 永远看全量：共现权重是跨文档的全局量；apply 才按 doc_ids 限定要重写的节点
    plan = graph_builder.plan(
        chunks, [{"doc_id": d["doc_id"], "title": d.get("title") or ""} for d in docs],
        llm_extra=llm_extra)
    result = graph_builder.apply(store, plan, doc_ids=doc_ids)
    meta = {
        "synced_at": _now(), "mode": result["mode"], "extractor": cfg.get("rag_graph_extractor"),
        "docs": plan["stats"]["docs"], "chunks": plan["stats"]["chunks"],
        "entities": plan["stats"]["entities"], "co_mentions": plan["stats"]["co_mentions"],
        "typed_rels": plan["stats"]["typed_rels"],
        "llm": (llm_extra or {}).get("stats"),
        "nodes": result["nodes"], "rels": result["rels"],
    }
    _meta_set(GRAPH_META_KEY, meta)
    return {"ok": True, **meta}


# ---------------------------------------------------------------- 检索时：各路给候选
def graph_candidates(question: str, cfg: dict) -> tuple[list[dict], dict]:
    """图谱路由的原始候选。PG 复核与同文去重在 rag.py 的融合处做（那里才有全量池）。"""
    started = time.perf_counter()
    report: dict = {"route": "graph", "status": "ok"}
    try:
        probe = graph_store.probe_cached()
        if not probe.get("ok"):
            report.update(status="unavailable", reason=probe.get("error", "Neo4j 不可用"))
            return [], _finish(report, started)
        mode = cfg.get("rag_graph_cypher", "template")
        cands, rep = graph_retriever.search(
            question, graph_store.get_store(),
            hops=int(cfg.get("rag_graph_hops", "2")), mode=mode,
            llm=_llm_or_none(cfg) if mode == "llm" else None)
        report.update(rep)
        if not cands and report["status"] == "ok":
            report["status"] = "empty"
        return [c.as_dict() for c in cands], _finish(report, started)
    except Exception as exc:  # noqa: BLE001 - 这一路出错不能带崩整个检索
        log.warning("graph route failed: %s: %s", type(exc).__name__, exc)
        report.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return [], _finish(report, started)


def wiki_candidates(question: str, cfg: dict) -> tuple[list[dict], dict]:
    """Wiki 路由的原始候选：先过门禁，再把命中页面的证据分块送出去。"""
    started = time.perf_counter()
    report: dict = {"route": "wiki", "status": "ok"}
    try:
        gate = wiki_gate(cfg)
        report["gate"] = gate
        if not gate.get("allowed"):
            report.update(status="gate_blocked", reason="；".join(gate.get("reasons") or []))
            return [], _finish(report, started)
        pages, claims = _wiki_rows()
        if not pages:
            report.update(status="empty", reason="Wiki 没有页面")
            return [], _finish(report, started)
        chunk_index = {c["chunk_id"]: c["text"] for c in _ready_chunks()}
        cands, rep = wiki_retriever.search(question, pages, claims, chunk_index)
        report.update(rep)
        return cands, _finish(report, started)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki route failed: %s: %s", type(exc).__name__, exc)
        report.update(status="error", reason=f"{type(exc).__name__}: {str(exc)[:160]}")
        return [], _finish(report, started)


def _finish(report: dict, started: float) -> dict:
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return report


# ---------------------------------------------------------------- 状态（给设置页与调试页）
def status(*, detail: bool = False) -> dict:
    """`detail=False` 给普通用户：只看「通不通」，不看连接串等基础设施细节。"""
    cfg = settings.get_all()
    on = settings.routes_on(cfg)
    probe = graph_store.probe_cached()
    graph: dict = {"enabled": "graph" in on,
                   "probe": probe if detail else {"ok": bool(probe.get("ok"))}}
    if probe.get("ok"):
        try:
            graph["counts"] = graph_builder.counts(graph_store.get_store())
        except Exception:  # noqa: BLE001
            graph["counts"] = None
    graph["meta"] = _meta_get(GRAPH_META_KEY)

    conn = db.get_conn()
    try:
        wiki_pages = conn.execute("SELECT COUNT(*) AS c FROM wiki_pages").fetchone()["c"]
    finally:
        conn.close()
    wiki = {"enabled": "wiki" in on, "gate": wiki_gate(cfg),
            "meta": _meta_get(WIKI_META_KEY), "pages": int(wiki_pages or 0)}
    out = {"routes_on": on, "graph": graph, "wiki": wiki,
           "jobs": {"graph_sync": job_state("graph_sync"), "wiki_build": job_state("wiki_build")}}
    if detail:
        out["switches"] = {k: cfg[k] for k in (
            "rag_route_vector", "rag_route_graph", "rag_route_wiki", "rag_graph_hops",
            "rag_graph_cypher", "rag_graph_extractor", "wiki_scenario", "wiki_stale_ratio_max")}
    return out
