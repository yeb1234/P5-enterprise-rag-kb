"""RAG 演示核心：文本提取、分块、检索、占位流式生成。

刻意做成“离线占位”实现（不调任何外部 LLM / 向量库），对应项目离线演示基调：
- 检索用关键词重叠打分，演示“检索为空 → 降级”的边界；
- 生成用确定性占位流式输出，便于断线恢复（resume_from 可重放）；
- 生产里这里换成 Milvus 向量检索 + DeepSeek 流式接口，契约不变（见 next-version 方案）。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path

from webapp import db, providers, settings

UPLOAD_DIR = db.DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
    if ext not in _TEXT_EXT:
        return None  # 不支持的格式：返回 None 触发降级/错误提示
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return raw


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
                "演示仅支持 txt/md/csv/json/py 等纯文本提取，该格式暂未解析",
            )
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
                conn.execute(
                    "INSERT INTO chunk_embeddings(chunk_id, model, dim, vec, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(chunk_id, model) DO UPDATE SET dim=excluded.dim, vec=excluded.vec, updated_at=excluded.updated_at",
                    (c["chunk_id"], model, len(v), json.dumps(v), now),
                )
            conn.commit()
        finally:
            conn.close()
    return cached


def _semantic_rank(question: str, candidates: list[dict], cfg: dict) -> list[dict]:
    cache = _get_or_compute_embeddings(candidates, cfg)
    emb = providers.get_embedding(cfg)
    qv = emb.embed([question])[0]
    for c in candidates:
        c["_emb_score"] = _cosine(qv, cache.get(c["chunk_id"], []))
    candidates.sort(key=lambda x: (x.get("_emb_score", 0), x.get("_lex_score", 0)), reverse=True)
    for c in candidates:
        c["score"] = round(c.get("_emb_score", 0), 4)
    return candidates


def retrieve(question: str, top_k: int = TOP_K, with_meta: bool = False):
    """检索：**词法门禁（保证不相关不硬凑）→ 向量排序 → 可选 rerank**。

    - 无词法重叠 → 空列表（chat 触发 retrieval_empty 降级）；
    - 命中且 embedding != offline → 用当前向量模型（本地 LM Studio 或硅基流动 BAAI/bge-m3）
      的余弦相似度重排；调用失败则回退词法排序（留痕，不静默）；
    - rerank 可选 off / llm / external；不可用时优雅跳过并标注。
    """
    cfg = settings.get_all()
    terms = _lexical_terms(question)
    candidates = _load_ready_chunks() if terms else []
    if terms:
        for c in candidates:
            tl = (c["text"] or "").lower()
            c["_lex_score"] = sum(1 for t in terms if t in tl)
        candidates = [c for c in candidates if c["_lex_score"] > 0]

    mode = "lexical"
    fallback_reason = None
    emb = settings.effective_embedding(cfg)
    if candidates and emb["kind"] != "offline":
        try:
            candidates = _semantic_rank(question, candidates, cfg)
            mode = f"hybrid: lexical_gate + {emb['kind']}_embedding({emb['model']})"
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

    hits = candidates[:top_k]

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

    out = []
    for c in hits:
        out.append({
            "doc_title": c["title"], "text": c["text"],
            "score": c.get("score", c.get("_lex_score", 0)),
            "chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "idx": c["idx"],
            "snippet": (c["text"] or "").strip().replace("\n", " ")[:180],
            "retrieval_mode": mode, "rerank": rerank_note,
        })
    meta = {
        "retrieval_mode": mode,
        "fallback_reason": fallback_reason,
        "rerank": rerank_note,
        "embedding_model": emb["name"],
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
        "permission_filter": "documents.status='ready'（演示切片单租户；生产为 tenant_id + ACL 在召回前过滤）",
        "rerank": meta["rerank"],
        "retrieval_mode": meta["retrieval_mode"],
        "fallback_reason": meta["fallback_reason"],
        "embedding": meta["embedding_model"],
        "generator": providers.get_model_label() + f"（检索：词法门禁 + {emb_name} 排序）",
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
