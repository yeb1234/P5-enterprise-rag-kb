"""RAG 演示核心：文本提取、分块、检索、占位流式生成。

刻意做成“离线占位”实现（不调任何外部 LLM / 向量库），对应项目离线演示基调：
- 检索用关键词重叠打分，演示“检索为空 → 降级”的边界；
- 生成用确定性占位流式输出，便于断线恢复（resume_from 可重放）；
- 生产里这里换成 Milvus 向量检索 + DeepSeek 流式接口，契约不变（见 next-version 方案）。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path

from webapp import db, providers, settings

UPLOAD_DIR = db.DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("webapp.rag")

# 演示用模拟耗时：让「分析并发上限=2 / 排队」在界面上真实可见。
# 生产里这里是真实解析 + embedding 调用，去掉该 sleep 即可。
ANALYZE_SIM_SECONDS = 1.8

CHUNK_SIZE = 800
CHUNK_OVERLAP = 80
TOP_K = 4

_TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".log", ".yaml", ".yml"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def extract_text(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_text(path)
    if ext not in _TEXT_EXT:
        return None  # 不支持的格式：返回 None 触发降级/错误提示
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return raw


def _pdf_text(path: Path) -> str | None:
    """PDF 文本层提取（pypdf，镜像里已装）。

    只取**文本层**，不做 OCR：
    - 文件损坏 / 不是真 PDF → 抛异常 → 返回 None，走「不支持」错误路径（不静默）；
    - 扫描件（页数正常但抽不出字符）→ 同样返回 None。**这是有意的**：
      OCR / 视觉链路目前只在 `parsing/` 子系统里，没有接入主链路，
      与其塞一个「[无文本层 PDF]」占位分块污染检索结果，不如明确报错。
      接入 `parsing/` 之后这里应改成调用 ParseRouter 并保留降级痕迹。
    """
    try:
        from pypdf import PdfReader  # 延迟导入：pypdf 缺失时不影响纯文本链路
    except Exception:
        return None
    try:
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except Exception:
        return None
    text = "\n\n".join(t for t in pages if t)
    return text or None


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    step = max(1, size - overlap)
    chunks = [text[i : i + size] for i in range(0, len(text), step)]
    return [c for c in chunks if c.strip()]


def analyze_document(doc_id: str) -> None:
    """由 queue 调用：读文件、提取、分块、落库，并更新文档状态（含降级标记）。"""
    time.sleep(ANALYZE_SIM_SECONDS)  # 演示排队体感
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT filename,status FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        if not row:
            return
        filepath = UPLOAD_DIR / f"{doc_id}_{row['filename']}"
        if not filepath.exists():
            _set_error(conn, doc_id, "上传文件丢失，无法分析")
            return
        text = extract_text(filepath)
        if text is None:
            _set_error(
                conn,
                doc_id,
                "演示支持 txt/md/csv/json/py/log/yaml 等纯文本与**有文本层的 PDF**；"
                "其余格式（扫描版 PDF / 图片 / docx / xlsx）暂未接入检索链路",
            )
            return
        # 入库前出口（GATE_INGEST）：分块之前一次性脱敏。晚一步做就晚了——
        # chunks 是检索、向量缓存、来源卡片、chat_sources 的共同真值源，
        # 只要原文进了 chunks，后面每一层都会把它带出去。
        try:
            from guardrails import default as guard_default
            g = guard_default()
            ingest_result = g.ingest(text)
            text = ingest_result.text
            if ingest_result.hit_count:
                db.record_safety_event(ingest_result, f"upload:{doc_id[:8]}")
        except Exception:
            # fail-closed：脱敏不可用时宁可整份不入库，也不能把原文写进知识库
            _set_error(conn, doc_id, "内容安全脱敏不可用，已阻断本次入库（fail-closed）")
            return
        chunks = chunk_text(text)
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        for idx, c in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                (uuid.uuid4().hex, doc_id, idx, c),
            )
        conn.execute(
            "UPDATE documents SET status='ready', chunk_count=?, updated_at=? WHERE id=?",
            (len(chunks), _now(), doc_id),
        )
        conn.commit()
    finally:
        conn.close()


def _set_error(conn, doc_id: str, msg: str) -> None:
    conn.execute(
        "UPDATE documents SET status='error', error_msg=?, updated_at=? WHERE id=?",
        (msg, _now(), doc_id),
    )
    conn.commit()


def _load_ready_chunks() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.doc_id, c.idx, c.text, d.title, d.filename FROM chunks c "
            "JOIN documents d ON d.id=c.doc_id WHERE d.status='ready'"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"chunk_id": r["id"], "doc_id": r["doc_id"], "idx": r["idx"],
         "text": r["text"], "title": r["title"] or r["filename"]}
        for r in rows
    ]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _get_or_compute_embeddings(candidates: list[dict], cfg: dict) -> dict[str, list[float]]:
    """按 (chunk_id, model) 缓存向量；缺失的批量计算并落库。"""
    # 缓存命名空间来自唯一事实源：切模型（本地 Bge M3 ↔ 硅基流动 BAAI/bge-m3）
    # 会自然使用另一份向量，不会复用旧模型向量导致余弦静默失真。
    model = settings.effective_embedding(cfg)["cache_key"]
    ids = [c["chunk_id"] for c in candidates]
    cached: dict[str, list[float]] = {}
    conn = db.get_conn()
    try:
        for i in range(0, len(ids), 500):
            grp = ids[i:i + 500]
            q = ("SELECT chunk_id, vec FROM chunk_embeddings WHERE model=? AND chunk_id IN ("
                 + ",".join("?" * len(grp)) + ")")
            for r in conn.execute(q, [model] + grp):
                try:
                    cached[r["chunk_id"]] = json.loads(r["vec"])
                except Exception:
                    pass
    finally:
        conn.close()
    missing = [c for c in candidates if c["chunk_id"] not in cached]
    if missing:
        emb = providers.get_embedding(cfg)
        vecs = emb.embed([c["text"] for c in missing])
        now = _now()
        conn = db.get_conn()
        try:
            for c, v in zip(missing, vecs):
                cached[c["chunk_id"]] = v
                # 双写：vec（历史文本形态，回滚与对拍用）+ embedding（binary，检索用）。
                # `?::vector` 直接吃 JSON 数组文本（`[0.1,0.2]` 是合法 vector 输入）；
                # 不写 public.vector：search_path 末尾已带 public（见 pgkit.connect）。
                # 不再多序列化一次；同一个参数传两遍是刻意的，比先 INSERT 再 UPDATE 少一次往返。
                conn.execute(
                    "INSERT INTO chunk_embeddings(chunk_id, model, dim, vec, embedding, updated_at) "
                    "VALUES (?,?,?,?,?::vector,?) "
                    "ON CONFLICT(chunk_id, model) DO UPDATE SET dim=excluded.dim, vec=excluded.vec, "
                    "embedding=excluded.embedding, updated_at=excluded.updated_at",
                    (c["chunk_id"], model, len(v), json.dumps(v), json.dumps(v), now),
                )
            conn.commit()
        finally:
            conn.close()
    return cached


def vector_backend() -> str:
    """向量排序走哪条路径：`pgvector`（默认）或 `python`（历史实现，回滚用）。

    默认切到 pgvector 的理由是实测的：同一批候选，纯 Python 逐条 `_cosine` 在 10 万条上要 25.6 秒
    （JSON 22.2KB/条 + 0.071ms/次循环），而把这件事交给 PG 是一条 SQL + 二进制列。
    回滚只需 `WEBAPP_VECTOR_BACKEND=python`，不用改代码。
    """
    v = os.environ.get("WEBAPP_VECTOR_BACKEND", "pgvector").strip().lower()
    return v if v in ("pgvector", "python") else "pgvector"


def _semantic_rank(question: str, candidates: list[dict], cfg: dict) -> tuple[list[dict], str]:
    """语义排序。返回 (排序结果, 后端说明)，后端说明会写进 `mode` 供排查与展示。"""
    emb = settings.effective_embedding(cfg)
    if vector_backend() == "pgvector":
        try:
            return _semantic_rank_pg(question, candidates, cfg), f"pgvector({emb['model']})"
        except Exception as e:  # noqa: BLE001
            # 降级**必须留痕**：mode 里会写 pgvector_fallback_python:XXX，
            # 而不是悄悄变慢（旧库没迁完、扩展没装时会走到这里）。
            ranked = _semantic_rank_py(question, candidates, cfg)
            return ranked, f"pgvector_fallback_python:{type(e).__name__}({emb['model']})"
    return _semantic_rank_py(question, candidates, cfg), f"{emb['kind']}_embedding({emb['model']})"


def _semantic_rank_py(question: str, candidates: list[dict], cfg: dict) -> list[dict]:
    """历史实现：取回候选向量 → 纯 Python 逐条算余弦 → 排序。"""
    cache = _get_or_compute_embeddings(candidates, cfg)
    emb = providers.get_embedding(cfg)
    qv = emb.embed([question])[0]
    for c in candidates:
        c["_emb_score"] = _cosine(qv, cache.get(c["chunk_id"], []))
    candidates.sort(key=lambda x: (x.get("_emb_score", 0), x.get("_lex_score", 0)), reverse=True)
    for c in candidates:
        c["score"] = round(c.get("_emb_score", 0), 4)
    return candidates


def _semantic_rank_pg(question: str, candidates: list[dict], cfg: dict) -> list[dict]:
    """用 pgvector 在库里排序：过滤与排序一条 SQL，避免把 22KB/条的 JSON 拉回 Python。

    `LIMIT len(ids)` 是刻意的——候选集已经是「词法门禁 + 权限」缩过的范围，
    在这个范围内做**全量精确排序**，召回与旧实现逐条一致（便于 A13 对拍）。
    候选集到十万级时再改成有界窗口 + `hnsw.iterative_scan`，那时才需要用近似换吞吐。
    """
    model = settings.effective_embedding(cfg)["cache_key"]
    _get_or_compute_embeddings(candidates, cfg)  # 保证候选都有向量（缺失的补算并双写）
    ids = [c["chunk_id"] for c in candidates]
    if not ids:
        return candidates
    qv = providers.get_embedding(cfg).embed([question])[0]
    qjson = json.dumps(qv)
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT chunk_id, 1 - (embedding <=> ?::vector) AS score "
            "FROM chunk_embeddings "
            "WHERE model = ? AND chunk_id = ANY(?) AND embedding IS NOT NULL "
            "ORDER BY embedding <=> ?::vector LIMIT ?",
            (qjson, model, ids, qjson, len(ids)),
        ).fetchall()
    finally:
        conn.close()
    score = {r["chunk_id"]: float(r["score"] or 0.0) for r in rows}
    # 没有向量的候选（理论上不会有）按 0 分留位，不静默丢文档
    for c in candidates:
        c["_emb_score"] = score.get(c["chunk_id"], 0.0)
        c["score"] = round(c["_emb_score"], 4)
    candidates.sort(key=lambda x: (x.get("_emb_score", 0), x.get("_lex_score", 0)), reverse=True)
    return candidates


FRESHNESS_NOTE_OFF = {"freshness": "off"}


def _freshness_enabled() -> bool:
    return os.environ.get("WEBAPP_FRESHNESS_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on")


def _apply_freshness(hits: list[dict]) -> tuple[list[dict], dict]:
    """可选叠加新鲜度重排。默认关闭，避免打断既有 33 项端到端自测。

    诚实边界：这里只在 `top_k` 之内重排，不做召回前硬过滤；
    生产应在 SQL / Milvus expr 阶段先排除「未来生效」与「已过期」文档，
    否则过期内容仍会占用候选名额。治理层任何异常都不得打断检索主链路。
    """
    if not _freshness_enabled():
        return hits, FRESHNESS_NOTE_OFF
    try:
        from knowledge import FreshnessPolicy, meta_from_row, rerank
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT * FROM documents").fetchall()
        finally:
            conn.close()
        metas = {}
        for r in rows:
            d = dict(r)
            d["doc_id"] = r["id"]
            d["scope"] = d.get("scope_json")
            metas[r["id"]] = meta_from_row(d)
        p = FreshnessPolicy(
            half_life_days=float(os.environ.get("WEBAPP_FRESHNESS_HALF_LIFE_DAYS", 90)),
            expired_action=os.environ.get("WEBAPP_FRESHNESS_EXPIRED_ACTION", "demote"),
        )
        ordered, results = rerank(hits, metas, policy=p)
        return ordered, {
            "freshness": "on", "policy": p.as_dict(),
            "excluded": [r.doc_id for r in results if r.excluded],
            "note": "仅在 top_k 内重排；生产应在召回前做硬过滤",
        }
    except Exception as e:
        return hits, {"freshness": f"unavailable:{type(e).__name__}"}


def _vector_route(question: str, cfg: dict, pool: list[dict] | None = None):
    """向量路由：**词法门禁（保证不相关不硬凑）→ 向量排序**。返回 (候选, 模式说明, 降级原因)。

    - 无词法重叠 → 空列表（chat 触发 retrieval_empty 降级）；
    - 命中且 embedding != offline → 用当前向量模型（本地 LM Studio 或硅基流动 BAAI/bge-m3）
      的余弦相似度重排；调用失败则回退词法排序（留痕，不静默）。

    这段是从原来的 retrieve() 里**逐字搬出来**的。只开向量一路时走的仍是这段逻辑，
    引入三路之前的全部回归用例覆盖的就是它，所以不做任何「顺手优化」。
    """
    terms = _lexical_terms(question)
    if pool is None:
        pool = _load_ready_chunks() if terms else []
    gated: list[dict] = []
    if terms:
        for c in pool:
            tl = (c["text"] or "").lower()
            c["_lex_score"] = sum(1 for t in terms if t in tl)
        gated = [c for c in pool if c["_lex_score"] > 0]

    emb = settings.effective_embedding(cfg)
    # 词法门禁的职责是「判定这批文档里有没有可能相关的」，**不是挑候选**。
    # 一旦有词法信号且向量可用，候选集放宽到全量，排序交给向量——
    # 否则跨语言/改述场景会被门禁直接掐死：中文问英文 PDF 时零词法重叠，
    # 该文档连进入向量排序的机会都没有（2026-11-16 多格式评估实测 0 召回）。
    # 词法完全无命中时仍然返回空，保留 retrieval_empty 降级语义（不硬凑）。
    candidates = pool if (gated and emb["kind"] != "offline") else gated

    mode = "lexical"
    fallback_reason = None
    if candidates and emb["kind"] != "offline":
        try:
            candidates, backend_note = _semantic_rank(question, candidates, cfg)
            mode = f"hybrid: lexical_gate + {backend_note}"
        except Exception as e:
            fallback_reason = f"embedding_unavailable:{type(e).__name__}"
            candidates.sort(key=lambda x: x.get("_lex_score", 0), reverse=True)
            for c in candidates:
                c["score"] = c.get("_lex_score", 0)
            mode = "lexical(fallback)"
    else:
        candidates.sort(key=lambda x: x.get("_lex_score", 0), reverse=True)
        for c in candidates:
            c["score"] = c.get("_lex_score", 0)

    return candidates, mode, fallback_reason


# 每一路最多取前 50 名进融合：融合用的是名次，第 200 名的倒数贡献没有意义。
FUSION_POOL = 50
# 多路 + 重排时先留 3 倍候选给重排挑；没有重排就不留，否则多出来的会直接漏到结果里。
MULTI_ROUTE_RERANK_POOL = 3


def _fuse(route_hits: dict[str, list[tuple[str, float]]]) -> list[dict]:
    """多路融合。排序规则两条，先后有序：

    1. **被多路同时命中的排前面**（「双路命中」）——两条独立路径都指向它，
       是比任何单路分数都强的相关性信号；
    2. 同组内按 RRF：Σ 1/(60+名次)。用名次而不是分数相加，是因为向量给的是余弦、
       图谱给的是跳数衰减分、Wiki 给的是页面匹配分，三者不同量纲，直接相加没有意义。

    返回的每一项带 routes / rrf / scores，供调试页解释「这条为什么排在这」。
    """
    by: dict[str, dict] = {}
    for route in ("vector", "graph", "wiki"):
        ranked = sorted(route_hits.get(route) or [], key=lambda x: (-x[1], x[0]))
        for rank, (chunk_id, score) in enumerate(ranked[:FUSION_POOL], 1):
            e = by.setdefault(chunk_id, {"chunk_id": chunk_id, "routes": [], "rrf": 0.0, "scores": {}})
            e["routes"].append(route)
            e["rrf"] += 1.0 / (60 + rank)
            e["scores"][route] = score
    return sorted(by.values(), key=lambda e: (-len(e["routes"]), -e["rrf"], e["chunk_id"]))


def _verified(route_cands: list[dict], by_id: dict[str, dict], report: dict) -> list[tuple[str, float]]:
    """图谱 / Wiki 给的候选回到 PG 复核：分块还在、文档仍 ready 才算数；同文只留一条。

    图和 Wiki 里的 chunk_id 是构建时写进去的，之后文档可能被删、被重新分析。
    这一步是「PG 是真值」在检索侧的落点。演示库里重复文档很多，
    同文不去重的话同一路会被同一份内容刷满名额。
    """
    out, seen_text = [], set()
    for c in route_cands:
        chunk = by_id.get(c["chunk_id"])
        if chunk is None:
            report["dropped_missing_in_pg"] = report.get("dropped_missing_in_pg", 0) + 1
            continue
        if chunk["text"] in seen_text:
            report["deduped_same_text"] = report.get("deduped_same_text", 0) + 1
            continue
        seen_text.add(chunk["text"])
        out.append((c["chunk_id"], float(c.get("score") or 0.0)))
    return out


def _align_same_text(route_hits: dict[str, list[tuple[str, float]]], by_id: dict[str, dict]) -> None:
    """图谱 / Wiki 命中的分块按**正文**归并到同一个代表分块上（不动向量那一路）。

    为什么要对齐：演示库里同一份手册传过很多遍。图谱命中的是副本 X、Wiki 的证据指向副本 Y，
    按 chunk_id 比对是两条不同的记录，「双路命中」就认不出来——明明两条独立路径指向同一段话。
    代表优先取向量那一路名次最高的同文分块，这样多路命中落在向量实际返回的那条上；
    向量没返回任何同文分块时，取 id 最小的那条。
    """
    needed = {by_id[cid]["text"] for route in ("graph", "wiki")
              for cid, _ in route_hits.get(route) or []}
    if not needed:
        return
    groups: dict[str, list[str]] = {}
    for cid, chunk in by_id.items():
        if chunk["text"] in needed:
            groups.setdefault(chunk["text"], []).append(cid)
    vector_rank = {cid: rank for rank, (cid, _) in enumerate(route_hits.get("vector") or [])}
    big = 10 ** 9
    representative = {text: min(ids, key=lambda c: (vector_rank.get(c, big), c))
                      for text, ids in groups.items()}
    for route in ("graph", "wiki"):
        merged, seen = [], set()
        for cid, score in route_hits.get(route) or []:      # 保持本路原有名次顺序
            target = representative[by_id[cid]["text"]]
            if target in seen:
                continue
            seen.add(target)
            merged.append((target, score))
        route_hits[route] = merged


def _multi_route(question: str, cfg: dict, routes: list[str]):
    """两路及以上：各路出候选 → PG 复核 → 融合。任何一路出问题只让那一路缺席。"""
    from webapp import kb_routes  # 延迟导入：只开向量时不加载 kgraph / llmwiki

    pool = _load_ready_chunks()
    by_id = {c["chunk_id"]: c for c in pool}
    route_hits: dict[str, list[tuple[str, float]]] = {}
    reports: dict[str, dict] = {}

    mode = fallback_reason = None
    if "vector" in routes:
        cands, mode, fallback_reason = _vector_route(question, cfg, pool=pool)
        route_hits["vector"] = [(c["chunk_id"], float(c.get("score") or 0.0)) for c in cands]
        reports["vector"] = {"route": "vector", "status": "ok" if cands else "empty",
                             "candidates": len(cands), "mode": mode}
    for route, fetch in (("graph", kb_routes.graph_candidates), ("wiki", kb_routes.wiki_candidates)):
        if route not in routes:
            continue
        try:
            cands, rep = fetch(question, cfg)
        except Exception as exc:  # noqa: BLE001 - 适配层内部已兜过，这里再兜一次
            # 「一路出问题只让那一路缺席」是这一层的承诺，不能靠下层永不抛错来保证
            log.warning("kb route %s raised: %s: %s", route, type(exc).__name__, exc)
            cands, rep = [], {"route": route, "status": "error",
                              "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
        route_hits[route] = _verified(cands, by_id, rep)
        reports[route] = rep

    _align_same_text(route_hits, by_id)
    fused = _fuse(route_hits)
    for entry in fused:
        c = by_id[entry["chunk_id"]]
        c["_routes"] = entry["routes"]
        c["_fusion"] = round(entry["rrf"], 6)
        if "vector" not in routes:
            # 没有向量分数可展示时，用其它几路里最高的那个分。只用于展示，排序仍看融合名次
            c["score"] = round(max(entry["scores"].values()), 4)
    candidates = [by_id[e["chunk_id"]] for e in fused]
    multi = [e["chunk_id"] for e in fused if len(e["routes"]) > 1]
    fusion_meta = {
        "method": "multi_hit_first + rrf",
        "multi_hit_count": len(multi), "multi_hit": multi[:20], "pool": len(fused),
    }
    mode = "fusion(" + "+".join(routes) + ")"
    return candidates, mode, fallback_reason, reports, fusion_meta


def retrieve(question: str, top_k: int = TOP_K, with_meta: bool = False):
    """检索：**各路召回 → （多路时）融合 → 可选 rerank → 可选新鲜度重排**。

    - 只开向量（默认）：词法门禁 → 向量排序，与引入三路之前逐字一致；
    - 多路：每路各自出候选，PG 复核后按「多路命中优先 + RRF」融合；
    - rerank 可选 off / llm / external；不可用时优雅跳过并标注；
    - 三路全关：返回空（`retrieval_mode=off`），不静默退回某一路。
    """
    cfg = settings.get_all()
    emb = settings.effective_embedding(cfg)
    routes = settings.routes_on(cfg)
    reports: dict[str, dict] = {}
    fusion_meta = None
    if not routes:
        candidates, mode, fallback_reason = [], "off", None
    elif routes == ["vector"]:
        candidates, mode, fallback_reason = _vector_route(question, cfg)
        reports["vector"] = {"route": "vector", "status": "ok" if candidates else "empty",
                             "candidates": len(candidates), "mode": mode}
    else:
        candidates, mode, fallback_reason, reports, fusion_meta = _multi_route(question, cfg, routes)

    widen = MULTI_ROUTE_RERANK_POOL if (fusion_meta and cfg.get("rerank_provider", "off") != "off") else 1
    hits = candidates[:top_k * widen]

    rerank_note = "off"
    rp = cfg.get("rerank_provider", "off")
    scores = None
    if hits and rp != "off":
        docs = [h["text"] for h in hits]
        if rp == "llm":
            scores = providers.rerank_with_llm(question, docs, providers.get_llm(cfg))
            if scores is None:
                rerank_note = "llm_unavailable(skipped)"
        else:  # external：Cohere 风格 /rerank（硅基流动 / Jina / TEI）
            scores = providers.rerank(
                cfg.get("external_rerank_base_url", ""),
                cfg.get("external_rerank_model", ""),
                question, docs, os.environ.get("RERANK_API_KEY", ""),
            )
            if scores is None:
                rerank_note = "external_unavailable(skipped)"
        if scores is not None:
            for h, s in zip(hits, scores):
                h["_rerank"] = s
            hits.sort(key=lambda x: x.get("_rerank", 0), reverse=True)
            for h in hits:
                h["score"] = round(h.get("_rerank", 0), 4)
            rerank_note = "applied:" + rp

    # 多路 + 重排时候选池放宽过；不管重排成功与否，最终都只留 top_k——
    # 否则重排不可用时放宽的候选会原样漏到结果里，返回条数比 top_k 还多。
    if fusion_meta is not None and len(hits) > top_k:
        hits = hits[:top_k]

    hits, freshness_meta = _apply_freshness(hits)
    out = []
    for c in hits:
        out.append({
            "doc_title": c["title"], "text": c["text"],
            "score": c.get("score", c.get("_lex_score", 0)),
            "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "idx": c["idx"],
            "snippet": (c["text"] or "").strip().replace("\n", " ")[:180],
            "retrieval_mode": mode, "rerank": rerank_note,
            "freshness": c.get("freshness"),
            "effective_score": c.get("effective_score"),
            # 新增字段（纯增量）：这条命中来自哪几路、融合分。只开向量时恒为 ["vector"]。
            "routes": c.get("_routes") or ["vector"],
            "fusion_score": c.get("_fusion"),
        })
    meta = {
        "retrieval_mode": mode,
        "fallback_reason": fallback_reason,
        "rerank": rerank_note,
        "embedding_model": emb["name"],
        "freshness": freshness_meta,
        # 新增（纯增量）：每一路的报告（状态 / 候选数 / 耗时 / 失败原因）与融合摘要
        "routes": reports,
        "fusion": fusion_meta,
    }
    return (out, meta) if with_meta else out


def search_debug(question: str, top_k: int = TOP_K) -> dict:
    """检索调试页：把「召回门禁 → 向量排序 → rerank/降级」摊开给人看。"""
    terms = sorted(_lexical_terms(question))
    conn = db.get_conn()
    try:
        eligible = conn.execute(
            "SELECT COUNT(*) AS c FROM chunks c JOIN documents d ON d.id=c.doc_id "
            "WHERE d.status='ready'"
        ).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
    finally:
        conn.close()
    hits, meta = retrieve(question, top_k, with_meta=True)
    degraded = len(hits) == 0
    emb_name = settings.effective_embedding()["name"]
    return {
        "question": question,
        "terms": terms,
        "top_k": top_k,
        "indexed_chunks": total,
        "eligible_chunks": eligible,
        "hit_count": len(hits),
        "hits": hits,
        "degraded": degraded,
        "degrade_reason": "retrieval_empty" if degraded else None,
        "permission_filter": "documents.status='ready'（演示切片单库；生产为部门树 + decide() 在召回前过滤）",
        "rerank": meta["rerank"],
        "retrieval_mode": meta["retrieval_mode"],
        "fallback_reason": meta["fallback_reason"],
        "embedding": meta["embedding_model"],
        "generator": providers.get_model_label() + f"（检索：词法门禁 + {emb_name} 排序）",
        # 三路取证：每一路的状态与证据（图谱子图、Wiki 命中页面与门禁结论）
        "routes": meta.get("routes"),
        "fusion": meta.get("fusion"),
    }


def get_model_label() -> str:
    return providers.get_model_label()


def _lexical_terms(text: str) -> set[str]:
    """词法检索的「分词」：英文/数字整词 + 中文相邻二元组。

    演示级方案——真实系统换 Milvus 语义向量后本函数不再用于召回打分。
    中文无空格分词，若整句作为一个 token 会完全匹配不到子串，故拆成字符二元组，
    使「配网」这类短语可被相邻命中（见 chat 检索为空 → 拒答的边界判定）。
    """
    text = (text or "").lower()
    terms: set[str] = set()
    for m in re.findall(r"[a-z0-9]+", text):
        if len(m) > 1:
            terms.add(m)
    cjk = re.findall(r"[一-鿿]", text)
    if len(cjk) == 1:
        terms.add(cjk[0])
    for i in range(len(cjk) - 1):
        terms.add(cjk[i] + cjk[i + 1])
    return terms


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|\s+|[^\s]", text)


def stream_answer(question: str, retrieved: list[dict], resume_from: int = 0, state: dict | None = None):
    """生成器：逐 token 产出，支持断线重放（resume_from 跳过已发 token）。

    - 检索为空 → 永远用离线诚实话术（不调真实模型，避免编造），degraded=retrieval_empty；
    - 检索命中 → 交给当前 LLM 提供者流式生成（离线占位或真实 DeepSeek）；
    - 真实模型抛错（429 限流 / 超时 / 5xx）→ 优雅降级为离线拼接回答，
      不中断 SSE 流，degraded=model_error；state 回传最终降级状态供落库与 done 事件使用。
    """
    if state is None:
        state = {}
    if not retrieved:
        provider = providers.OfflineLLMProvider()
        degraded, reason = True, "retrieval_empty"
    else:
        provider = providers.get_llm()
        degraded, reason = False, None
    state["degraded"] = degraded
    state["degrade_reason"] = reason
    emitted = 0
    try:
        for i, tok in enumerate(provider.stream(question, retrieved)):
            if i < resume_from:
                continue
            emitted += 1
            yield {
                "index": i,
                "token": tok,
                "degraded": degraded,
                "degrade_reason": reason,
                "retrieved": len(retrieved),
            }
    except Exception as exc:
        # 模型服务不可用：降级为离线拼接回答（仍基于已检索片段），不丢弃本次问答
        if emitted > 0:
            # 已经吐出正文了：只在尾部标记中断，绝不把降级文案拼到真答案后面
            state["degraded"] = True
            state["degrade_reason"] = "stream_interrupted"
            return
        # 额度耗尽与普通报错要分开：前者重试无用，提示也不同
        is_quota = "quota_exceeded" in str(exc).lower()
        state["degraded"] = True
        state["degrade_reason"] = "quota_exceeded" if is_quota else "model_error"
        offline_answer, _, _ = providers._offline_answer(question, retrieved)
        head = ("（当前模型额度已用尽，未自动重试，已降级为本地拼接回答。知识库内容仍基于检索片段：）\n"
                if is_quota else
                "（模型服务暂时不可用或触发限流，已自动降级为本地拼接回答。知识库内容仍基于检索片段：）\n")
        fallback = head + offline_answer
        for i, tok in enumerate(_tokenize(fallback)):
            if i < resume_from:
                continue
            yield {
                "index": i,
                "token": tok,
                "degraded": True,
                "degrade_reason": state["degrade_reason"],
                "retrieved": len(retrieved),
            }
        return

    # 模型正常收尾却一个正文 token 都没给（典型：推理模型只输出 reasoning_content，
    # 或 max_tokens 被推理过程吃光）。此前会静默返回空白回答，这里按「空输出」降级，
    # 仍用检索片段兜底，保证用户不会看到空气泡。
    if emitted == 0 and resume_from == 0:
        state["degraded"] = True
        state["degrade_reason"] = "empty_model_output"
        offline_answer, _, _ = providers._offline_answer(question, retrieved)
        fallback = (
            "（当前模型未返回正文，可能只输出了推理过程；已自动降级为本地拼接回答。"
            "知识库内容仍基于检索片段：）\n" + offline_answer
        )
        for i, tok in enumerate(_tokenize(fallback)):
            yield {
                "index": i,
                "token": tok,
                "degraded": True,
                "degrade_reason": "empty_model_output",
                "retrieved": len(retrieved),
            }


def files_for(doc_id: str, filename: str) -> Path:
    return UPLOAD_DIR / f"{doc_id}_{filename}"
