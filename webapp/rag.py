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
# 检索默认参数：CmedqaRetrieval（200 题）收敛结果，明细见 docs/retrieval-param-convergence.md
# TOP_K=5：hit@k 曲线的拐点（hit@4 0.810 → hit@5 0.830 → hit@10 0.875），
#          后 5 条只多 4.5pt 命中，却让进 prompt 的上下文翻倍。
#           生成侧实测 top5 与 top10 正确率同为 1.000，说明多给没有收益。
TOP_K = 5
# RERANK_DEPTH：送进 rerank 的候选数，与 TOP_K 解耦。默认「先截断再重排」时，
# rerank 只能在最终几条里换顺序，第 11 名再好也进不来；解耦后实测
# 3000 池 R@10 +4.6pt / 12000 池 +5.2pt，代价是延迟 +14%。
RERANK_DEPTH = 20
# ADAPTIVE_RERANK_MARGIN：向量第一名「领先第二名多少」就跳过 rerank（0 = 关闭，永不跳过）。
# rerank 是检索链路上最贵的一步（外部端点，20 条约 440ms，实测占暖相 83%），但它的价值
# 不均匀：向量排序已经分得很开时，重排只能在候选内微调，几乎改不动结果；挤成一团时才值钱。
# 0.05 来自 200 题实测（3000 与 12000 池各一轮，明细见 docs/retrieval-param-convergence.md §2.5）：
#   3000 池 跳过 29% → nDCG −0.12pt、R@5 −0.62pt、R@1 +0.75pt、均值 821→616ms
#   12000 池 跳过 24% → nDCG −0.16pt、R@5 −0.42pt、R@1 +0.25pt
# 选「相对间距」而不是「绝对相似度」（如 s1≥0.72）：后者在小池上省得更多（−44%），
# 但换到 12000 池 ΔR@5 从 +0.15pt 翻成 −0.60pt——绝对分值的尺度绑定模型与语料，会漂。
ADAPTIVE_RERANK_MARGIN = float(os.environ.get("WEBAPP_ADAPTIVE_RERANK_MARGIN", "0.05"))

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


# 结构边界识别：Markdown 标题、表格行、代码围栏、句末标点。
# 为什么不用 split("\n\n")：表格与代码块内部也可能出现空行，
# 按空行切会把它们拦腰砍断，正是旧版分块最大的问题来源。
_MD_HEADING = re.compile(r"^#{1,6}\s")
_TABLE_ROW = re.compile(r"^\s*\|")
_FENCE = re.compile(r"^\s*```")
_SENT_BREAK = re.compile(r"(?<=[。！？；!?;])\s*")


def _struct_segments(text: str) -> list[str]:
    """按 Markdown 结构拆成不可再分的最小语义单元。

    优先级：代码块 > 表格 > 标题 > 段落。表格与代码块整块保留——
    表头和数据行、围栏和代码体一旦分离，后半段就失去自解释能力，
    embedding 与生成都无从判断这段在讲什么。
    """
    lines = text.split("\n")
    segs: list[str] = []
    buf: list[str] = []
    i, n = 0, len(lines)

    def flush() -> None:
        if buf:
            seg = "\n".join(buf).strip("\n")
            if seg.strip():
                segs.append(seg)
            buf.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if _FENCE.match(line):
            flush()
            j = i + 1
            while j < n and not _FENCE.match(lines[j]):
                j += 1
            segs.append("\n".join(lines[i : min(j + 1, n)]))
            i = j + 1
            continue

        if _TABLE_ROW.match(line):
            flush()
            j = i
            while j < n and _TABLE_ROW.match(lines[j]):
                j += 1
            segs.append("\n".join(lines[i:j]))
            i = j
            continue

        if _MD_HEADING.match(stripped):
            flush()
            segs.append(stripped)
            i += 1
            continue

        if not stripped:
            flush()
            i += 1
            continue

        buf.append(line)
        i += 1

    flush()

    # 标题必须与其后内容同块：Markdown 里「## 标题 + 正文」才是一个语义单元。
    # 若把标题单独切成一段，正文会失去它讲的是什么，chunk 也会以标题结尾而无法
    # 判断内容——这是把「断词」换成「断标题」，同样不可接受。
    merged: list[str] = []
    pending: list[str] = []
    for seg in segs:
        if _MD_HEADING.match(seg.strip()) and "\n" not in seg.strip():
            pending.append(seg)
            continue
        if pending:
            merged.append("\n".join(pending + [seg]))
            pending = []
        else:
            merged.append(seg)
    if pending:
        merged.append("\n".join(pending))
    return merged


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    """无结构长文本的最后手段：字符滑窗，保留 overlap，与旧实现行为一致。"""
    step = max(1, size - overlap)
    return [p for p in (text[i : i + size] for i in range(0, len(text), step)) if p.strip()]


def _split_oversize(seg: str, size: int, overlap: int) -> list[str]:
    """单个语义单元超过 size 时的降级切分。

    表格：按行切，但每片都复制表头（前两行）——否则第二片起不自解释。
    其余：按句末标点回退；句子本身仍超长，才退到字符滑窗。
    """
    lines = seg.split("\n")
    tbl_at = next((i for i, ln in enumerate(lines) if _TABLE_ROW.match(ln)), -1)
    tbl_rows = sum(1 for ln in lines if _TABLE_ROW.match(ln))
    if tbl_at >= 0 and tbl_rows >= 2:
        # 表头 = 表格的第 1、2 行（列名行 + 分隔行）。表格之前的标题只跟第一片，
        # 否则每片都带同一个标题，top-k 会出现多个几乎相同的块互相挤占名额。
        prefix = "\n".join(lines[:tbl_at])
        tbl = lines[tbl_at:]
        header = "\n".join(tbl[:2])
        body = tbl[2:]
        if not body:
            return [(prefix + "\n" + header) if prefix else header]
        out: list[str] = []
        cur: list[str] = []
        cur_len = 0
        first = True
        for ln in body:
            head = (prefix + "\n" + header) if (first and prefix) else header
            room = max(1, size - len(head) - 1)
            if cur and cur_len + len(ln) + 1 > room:
                out.append(head + "\n" + "\n".join(cur))
                first = False
                cur, cur_len = [], 0
            cur.append(ln)
            cur_len += len(ln) + 1
        if cur:
            head = (prefix + "\n" + header) if (first and prefix) else header
            out.append(head + "\n" + "\n".join(cur))
        return out

    sents = [s for s in _SENT_BREAK.split(seg) if s]
    if len(sents) > 1:
        out2: list[str] = []
        cur_s = ""
        for s in sents:
            if len(s) > size:
                if cur_s:
                    out2.append(cur_s)
                    cur_s = ""
                out2.extend(_hard_split(s, size, overlap))
                continue
            if cur_s and len(cur_s) + len(s) > size:
                out2.append(cur_s)
                cur_s = s
            else:
                cur_s += s
        if cur_s:
            out2.append(cur_s)
        return out2

    return _hard_split(seg, size, overlap)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """结构感知分块：优先在语义边界切，字符数只作上限。

    与旧版（text[i:i+size] 纯滑窗）的差别只在切点选择：
    - 旧版 89.6% 的切点落在句子中间、58% 的 chunk 含被砍断的表格；
    - 新版按 标题/段落/表格/代码块 边界组装，表格整块不拆。
    无结构文本（如无标点的长串）仍退化为字符滑窗，保证行为可预期，
    也保证与 showcase 契约（overlap<size、末尾重叠）一致。
    """
    if size <= 0:
        raise ValueError("size must be > 0")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")
    text = (text or "").strip()
    if not text:
        return []

    # 段落级不重叠：段落本身就是语义单元，强行重叠只会让 top-k 出现近重复块，
    # 白占名额。overlap 只在「超长块硬切」这一种降级路径里生效。
    out: list[str] = []
    cur = ""
    for seg in _struct_segments(text):
        if not seg.strip():
            continue
        # 表格是原子单元，放宽到 4×size：表头与数据行一旦分家，
        # 后半段就不再自解释，而 bge-m3 的输入窗口远大于 800 字符，
        # 整块保留不会溢出。只有表格本身超过 4×size 才降级按行切。
        is_table = sum(1 for ln in seg.split("\n") if _TABLE_ROW.match(ln)) >= 2
        limit = size * 4 if is_table else size
        if len(seg) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(_split_oversize(seg, size, overlap))
            continue
        if cur and len(cur) + 1 + len(seg) > size:
            out.append(cur)
            cur = seg
        else:
            cur = seg if not cur else cur + "\n" + seg
    if cur:
        out.append(cur)
    return out


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


def _load_ready_chunks(terms: set[str] | None = None) -> list[dict]:
    """加载可检索 chunk，**含正文**。

    `terms` 为空 → 全池，供 nogate / 调试路径使用，行为与历史一致。
    `terms` 非空 → 顺带算出每条的词法命中数（`_lex_score`），供**没有向量时**的降级排序用。

    分工很重要：走 pgvector 时**不要**调这个函数，改用 `_gate_hit` + `_load_light_chunks`。
    向量路径只需要「有没有命中」这一个布尔，既不需要分数，也不需要把全池正文搬进进程。
    实测 3221 条：本函数约 106ms；布尔判定 0.9ms + 轻量候选 8.2ms。
    """
    if not terms:
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

    ordered = sorted(terms)  # set 迭代顺序不可依赖，排序后 SQL 与参数一一对应且稳定
    # strpos(haystack, needle)>0 与 Python 的 `t in text.lower()` 逐字等价（子串存在，不是相似度）。
    # 不用 position()：它只接受 `position(x in y)` 专用语法，`position(a, b)` 直接语法错。
    sum_expr = " + ".join(
        "CASE WHEN strpos(lower(c.text), ?) > 0 THEN 1 ELSE 0 END" for _ in ordered
    )
    sql = ("SELECT c.id, c.doc_id, c.idx, d.title, d.filename, (" + sum_expr + ") AS lex_score "
           "FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE d.status='ready' "
           "ORDER BY c.id")
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, ordered).fetchall()
    finally:
        conn.close()
    return [
        {"chunk_id": r["id"], "doc_id": r["doc_id"], "idx": r["idx"],
         "title": r["title"] or r["filename"], "_lex_score": int(r["lex_score"] or 0)}
        for r in rows
    ]


def _gate_hit(terms: set[str]) -> bool:
    """词法门禁的布尔判定：池里**有没有**任何 chunk 命中任一词。

    一条 `OR ... LIKE ... LIMIT 1`，命中即停。实测 3221 条 0.9ms；而要算出
    「每条命中几个词」得把全表扫 12 遍，106ms——差两个数量级。
    retrieve 在向量路径下只需要这个布尔：命中就把候选放宽到全池，之后排序完全交给向量，
    命中几条、哪几条都不进入后续计算。

    LIKE 里没有转义 `%` / `_` 是安全的：`_lexical_terms` 只产出 `[a-z0-9]+` 与中文二元组，
    不含通配符字符。
    """
    if not terms:
        return False
    ordered = sorted(terms)
    like = " OR ".join(["lower(c.text) LIKE ?"] * len(ordered))
    sql = ("SELECT 1 FROM chunks c JOIN documents d ON d.id=c.doc_id "
           "WHERE d.status='ready' AND (" + like + ") LIMIT 1")
    conn = db.get_conn()
    try:
        return conn.execute(sql, ["%" + t + "%" for t in ordered]).fetchone() is not None
    finally:
        conn.close()


def _load_light_chunks() -> list[dict]:
    """全池候选，**不含正文、不算分数**：实测 3221 条 8.2ms（含正文要 28ms）。

    向量排序只吃 chunk_id；正文在最终候选落定时按 id 回捞（`_attach_texts`）。
    `ORDER BY c.id` 保证同分块的相对顺序稳定——原来依赖物理顺序，同一批数据两次导入
    uuid 不同就会让同分块换位，同参数跑出两个 nDCG（实测差 0.0015~0.0026）。
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT c.id, c.doc_id, c.idx, d.title, d.filename FROM chunks c "
            "JOIN documents d ON d.id=c.doc_id WHERE d.status='ready' ORDER BY c.id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"chunk_id": r["id"], "doc_id": r["doc_id"], "idx": r["idx"],
         "title": r["title"] or r["filename"]}
        for r in rows
    ]


def _attach_texts(hits: list[dict]) -> None:
    """按 id 回捞正文，只给最终候选付这份成本。

    轻量候选（SQL 门禁路径）不带 `text`；rerank 与最终输出都需要正文，
    所以在候选落定时补一次。已经是全量候选（自带 text）时是空操作。
    """
    need = [h["chunk_id"] for h in hits if not h.get("text")]
    if not need:
        return
    conn = db.get_conn()
    try:
        got: dict[str, str] = {}
        for i in range(0, len(need), 500):
            grp = need[i:i + 500]
            q = "SELECT id, text FROM chunks WHERE id IN (" + ",".join("?" * len(grp)) + ")"
            for r in conn.execute(q, grp):
                got[r["id"]] = r["text"]
    finally:
        conn.close()
    for h in hits:
        if not h.get("text"):
            h["text"] = got.get(h["chunk_id"], "")


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
        # 轻量候选（SQL 门禁路径）不带正文，补算是要正文的：先回捞，否则 embed 的是空串
        _attach_texts(missing)
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


def _ensure_embeddings(candidates: list[dict], cfg: dict) -> None:
    """保证候选都有向量，但**不把已有向量拉回 Python**。

    pgvector 排序在 SQL 里做，Python 侧根本不需要这些浮点数。历史实现调
    `_get_or_compute_embeddings` 只为「检查有没有缺」，代价却是把每个候选的
    1024 维 JSON 逐条 `json.loads` 回来——实测 3221 条 **1081ms**，占检索总耗时 75%，
    12840 条时约 4 秒。这才是深池变慢的真因，不是词法门禁。

    这里只查「哪些 id 已有向量」（同样 3221 条约 8ms），缺的才补算并双写。
    """
    model = settings.effective_embedding(cfg)["cache_key"]
    ids = [c["chunk_id"] for c in candidates]
    if not ids:
        return
    conn = db.get_conn()
    try:
        have: set[str] = set()
        for i in range(0, len(ids), 1000):
            grp = ids[i:i + 1000]
            q = ("SELECT chunk_id FROM chunk_embeddings WHERE model=? AND embedding IS NOT NULL "
                 "AND chunk_id IN (" + ",".join("?" * len(grp)) + ")")
            for r in conn.execute(q, [model] + grp):
                have.add(r["chunk_id"])
    finally:
        conn.close()

    missing = [c for c in candidates if c["chunk_id"] not in have]
    if not missing:
        return
    _attach_texts(missing)  # 轻量候选没带正文，补算要用
    vecs = providers.get_embedding(cfg).embed([c["text"] for c in missing])
    now = _now()
    conn = db.get_conn()
    try:
        for c, v in zip(missing, vecs):
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
    qv = providers.embed_query(question, cfg)
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
    # 只补缺、不加载：SQL 排序不需要 Python 侧持有向量（见 _ensure_embeddings）
    _ensure_embeddings(candidates, cfg)
    ids = [c["chunk_id"] for c in candidates]
    if not ids:
        return candidates
    # query 向量走 providers.embed_query：同一句话第二次问直接命中，不再打模型端点
    qv = providers.embed_query(question, cfg)
    qjson = json.dumps(qv)
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT chunk_id, 1 - (embedding <=> ?::vector) AS score "
            "FROM chunk_embeddings "
            "WHERE model = ? AND chunk_id = ANY(?) AND embedding IS NOT NULL "
            # chunk_id 作次级键：同分时顺序固定，否则物理顺序变化会让同一 query
            # 跑出不同的 nDCG（实测两次同参差异 0.0015~0.0026）
            "ORDER BY embedding <=> ?::vector, chunk_id LIMIT ?",
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


def _adaptive_skip_reason(hits: list[dict]) -> str | None:
    """自适应重排的判定：返回跳过原因，返回 None 表示照旧重排。

    规则只有一条：向量第一名领先第二名 ≥ `ADAPTIVE_RERANK_MARGIN` 就跳过。
    为什么只看 top1-top2：
    - rerank 的作用是把「向量排不进前几」的相关块捞上来，前提是候选分得很近；
      top1 已经大幅领先时，重排改的是第 4/5 名的次序，对 R@1/R@5 影响极小（实测两端池都在噪声内）。
    - 词法回退路径没有 `_emb_score`，此函数直接放弃判定（不猜）——宁可多花 440ms，
      也不要在没有依据的情况下省。
    """
    if ADAPTIVE_RERANK_MARGIN <= 0 or not hits:
        return None
    if len(hits) == 1:
        # 只有一条候选：重排无从改变顺序，这 440ms 纯浪费
        return "single"
    s1 = hits[0].get("_emb_score")
    s2 = hits[1].get("_emb_score")
    if s1 is None or s2 is None:
        return None
    margin = float(s1) - float(s2)
    if margin >= ADAPTIVE_RERANK_MARGIN:
        return f"margin={margin:.3f}"
    return None


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


def retrieve(question: str, top_k: int = TOP_K, with_meta: bool = False,
             rerank_depth: int | None = RERANK_DEPTH):
    """检索：**词法门禁（保证不相关不硬凑）→ 向量排序 → 可选 rerank**。

    - 无词法重叠 → 空列表（chat 触发 retrieval_empty 降级）；
    - 命中且 embedding != offline → 用当前向量模型（本地 LM Studio 或硅基流动 BAAI/bge-m3）
      的余弦相似度重排；调用失败则回退词法排序（留痕，不静默）；
    - rerank 可选 off / llm / external；不可用时优雅跳过并标注。
    """
    cfg = settings.get_all()
    terms = _lexical_terms(question)
    emb = settings.effective_embedding(cfg)
    use_vector = emb["kind"] != "offline"

    # 词法门禁的职责是「判定这批文档里有没有可能相关的」，**不是挑候选**。
    # 一旦有词法信号且向量可用，候选集放宽到全量，排序交给向量——
    # 否则跨语言/改述场景会被门禁直接掐死：中文问英文 PDF 时零词法重叠，
    # 该文档连进入向量排序的机会都没有（2026-11-16 多格式评估实测 0 召回）。
    # 词法完全无命中时仍然返回空，保留 retrieval_empty 降级语义（不硬凑）。
    #
    # 正因为只需「全放 / 全拦」这个二分，向量路径下门禁只要一个布尔：
    # `_gate_hit` 0.9ms + `_load_light_chunks` 8.2ms，替代「逐条算分 + 拉全池正文」的 106ms。
    pool: list[dict] = []
    gated: list[dict] = []
    if terms:
        if use_vector:
            if _gate_hit(terms):
                pool = _load_light_chunks()
                gated = pool
        else:
            # 没有向量时只能按词法排序，这时才需要逐条分数
            pool = _load_ready_chunks(terms)
            gated = [c for c in pool if c.get("_lex_score", 0) > 0]

    candidates = pool if (gated and use_vector) else gated

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

    # rerank_depth：送进 rerank 的候选数，默认等于 top_k。
    # 默认行为是「先截断再重排」，重排只能在最终这几条里换顺序——第 11 名再好也进不来。
    # 传 > top_k 才变成「先从 depth 条里挑，再取 top_k」，重排才真正影响入选。
    depth = max(top_k, int(rerank_depth or top_k))
    hits = candidates[:depth]
    _attach_texts(hits)  # rerank 与输出都要正文，只对这几条回捞

    rerank_note = "off"
    rp = cfg.get("rerank_provider", "off")
    scores = None
    # 自适应重排：判定在 rerank 之前做，命中则整段外部调用归零（见 _adaptive_skip_reason）
    skip_why = _adaptive_skip_reason(hits) if rp != "off" else None
    if skip_why:
        rerank_note = "skipped:" + skip_why
        rp = "off"
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

    if len(hits) > top_k:
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
        })
    meta = {
        "retrieval_mode": mode,
        "fallback_reason": fallback_reason,
        "rerank": rerank_note,
        "rerank_depth": depth,
        "embedding_model": emb["name"],
        "freshness": freshness_meta,
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
