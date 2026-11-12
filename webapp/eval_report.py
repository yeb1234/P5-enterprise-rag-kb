"""真实配置评估采集器：产出 webapp/out/eval_report.json，作为评估报告的唯一事实源。

运行：python webapp/eval_report.py
前置：.env 已配置 DISCOVERY_API_KEY / RERANK_API_KEY（硅基流动，嵌入与重排共用）。

设计取舍（面试可讲）：
- **隔离临时库**：进程启动前设置 WEBAPP_DB_PATH，避免污染演示库；app_settings 为空，
  因此生效配置直接来自 .env 的 DEFAULTS——测的就是「开箱即用的目标配置」，而非手工改过的库表。
- **走真实外部 API**：本脚本不 mock 任何模型调用。若某个外部服务不可用，按项目的降级口径
  如实记录 degraded / unavailable，而不是跳过或伪造数字。
- **TestClient 走真实 HTTP 语义**：路由、鉴权、SSE、落库全链路与浏览器一致，只是不占端口。
- **三层分数摊开**：用项目自身函数分别算词法命中、bge-m3 余弦、bge-reranker 分数，
  让「为什么需要重排」有可核对的数字，而不是一句定性描述。

密钥只检查是否配置，绝不写入报告 / JSON。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
# 直接 `python webapp/eval_report.py` 时 sys.path[0] 是 webapp/，
# 会让 webapp/queue.py 遮蔽标准库 queue，TestClient(anyio) 直接 ImportError。
# 从项目根跑 pytest 时不会有这问题，但脚本必须自己能独立跑。
sys.path = [p for p in sys.path if not p or Path(p).resolve() != _SCRIPT_DIR]
sys.path.insert(0, str(ROOT))

# 必须在 import webapp 之前设置：db.DB_PATH 在模块加载时确定
_TMP_DB = Path(tempfile.gettempdir()) / f"kb_eval_{os.getpid()}.db"
os.environ["WEBAPP_DB_PATH"] = str(_TMP_DB)

from fastapi.testclient import TestClient  # noqa: E402

from webapp import db, providers, rag, settings  # noqa: E402
from webapp.envfile import load_env  # noqa: E402
from webapp.main import app  # noqa: E402

load_env()
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

# 受控语料：三份文档都有词法重叠，但只有 A 真正回答「配网」。
# 用来证明「词法门禁只保证不跑偏，语义排序 + 重排才决定谁排第一」。
CORPUS = [
    {
        "name": "wifi-setup.txt", "label": "真相关（标准答案）",
        "text": ("设备配网步骤：长按机身复位键 3 秒，指示灯快闪后松开；"
                 "在 App 中选择 2.4GHz Wi-Fi 并输入密码，听到连接成功提示音即完成配网。"
                 "若配网失败，请确认路由器未开启 5GHz 独占模式。"),
    },
    {
        "name": "billing.txt", "label": "关键词干扰（含「配网」但讲的是账单）",
        "text": ("费用说明：本月账单包含一次性配网服务费 20 元，请在月底前完成缴费，"
                 "逾期将按日加收滞纳金。发票可在个人中心自助开具。"),
    },
    {
        "name": "asset-scrap.txt", "label": "无关（含「设备」但讲报废）",
        "text": ("设备报废流程：填写资产报废单，经部门主管与财务审批后，"
                 "交由行政统一回收处理，并在资产台账中登记注销。"),
    },
]
QUESTION = "设备如何配网？"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mask(key: str) -> str:
    if not key:
        return "<未配置>"
    return key[:6] + "…" + key[-4:] + f"（长度 {len(key)}）"


def seed_corpus() -> None:
    """直接落 ready 文档 + 分块（评估关注检索/生成，上传排队由 selftest 覆盖）。"""
    import uuid
    conn = db.get_conn()
    try:
        for d in CORPUS:
            doc_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO documents(id,owner_id,filename,title,size_bytes,status,chunk_count,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, "eval", d["name"], d["name"], len(d["text"].encode()),
                 "ready", 1, _now(), _now()),
            )
            conn.execute(
                "INSERT INTO chunks(id,doc_id,idx,text) VALUES (?,?,?,?)",
                (uuid.uuid4().hex, doc_id, 0, d["text"]),
            )
        conn.commit()
    finally:
        conn.close()


def parse_sse(text: str) -> dict:
    meta, tokens, done = {}, [], {}
    for block in text.split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        ev, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if not data:
            continue
        obj = json.loads(data)
        if ev == "meta":
            meta = obj
        elif ev == "done":
            done = obj
        else:
            tokens.append(obj)
    return {"meta": meta, "tokens": tokens, "done": done}


def collect() -> dict:
    cfg = settings.get_all()
    eff = settings.effective_embedding(cfg)
    report: dict = {
        "generated_at": _now(),
        "purpose": "真实外部模型配置下的 RAG 链路评估（面试讲解事实源）",
        "config": {
            "llm": {"provider": cfg["llm_provider"], "model": cfg["discovery_model"],
                    "base_url": os.environ.get("DISCOVERY_BASE_URL", ""),
                    "api_key": mask(os.environ.get("DISCOVERY_API_KEY", ""))},
            "embedding": {"provider": cfg["embedding_provider"], "model": eff["model"],
                          "base_url": eff["base_url"],
                          "cache_key": eff["cache_key"],
                          "api_key": mask(eff["api_key"])},
            "rerank": {"provider": cfg["rerank_provider"],
                       "model": cfg["external_rerank_model"],
                       "base_url": cfg["external_rerank_base_url"],
                       "api_key": mask(os.environ.get("RERANK_API_KEY", ""))},
        },
        "notes": [
            "嵌入与重排共用同一把硅基流动 key；密钥只取是否存在，绝不写入报告。",
            "生效配置来自 .env 的 DEFAULTS（隔离库无 app_settings），即开箱即用的目标配置。",
        ],
    }

    # ---- 1. 三组件连通性（真实 API，记录延迟与维度）----
    connectivity = {}
    t0 = time.time()
    try:
        r = providers.test_embedding(eff["base_url"], eff["model"], eff["api_key"])
        connectivity["embedding"] = {"ok": True, "dim": r["dim"], "latency_ms": r["latency_ms"]}
    except Exception as e:
        connectivity["embedding"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    t0 = time.time()
    try:
        scores = providers.rerank(
            cfg["external_rerank_base_url"], cfg["external_rerank_model"],
            "配网", ["配网步骤", "账单"], os.environ.get("RERANK_API_KEY", ""),
        )
        connectivity["rerank"] = {
            "ok": scores is not None,
            "latency_ms": int((time.time() - t0) * 1000),
            "sample_scores": scores,
        }
    except Exception as e:
        connectivity["rerank"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    t0 = time.time()
    try:
        r = providers.test_llm(
            os.environ.get("DISCOVERY_BASE_URL", ""),
            cfg["discovery_model"],
            os.environ.get("DISCOVERY_API_KEY", ""),
        )
        connectivity["llm"] = {
            "ok": True, "latency_ms": r["latency_ms"],
            "content_chars": r["content_chars"], "reasoning_only": r["reasoning_only"],
        }
    except Exception as e:
        connectivity["llm"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    report["connectivity"] = connectivity

    # ---- 2. 检索三层分数摊开（项目自身函数，不重造轮子）----
    docs = [d["text"] for d in CORPUS]
    stages: dict = {"question": QUESTION, "corpus": []}
    terms = sorted(rag._lexical_terms(QUESTION))
    stages["lexical_terms"] = terms
    for d in CORPUS:
        tl = d["text"].lower()
        stages["corpus"].append({
            "name": d["name"], "label": d["label"],
            "lexical_hits": sum(1 for t in terms if t in tl),
        })
    # 第二层：硅基流动 bge-m3 余弦
    try:
        emb = providers.get_embedding(cfg)
        vecs = emb.embed([QUESTION] + docs)
        qv, dvs = vecs[0], vecs[1:]
        for c, v in zip(stages["corpus"], dvs):
            c["embedding_cosine"] = round(rag._cosine(qv, v), 4)
        stages["embedding_model"] = emb.name
        stages["embedding_dim"] = len(qv)
    except Exception as e:
        stages["embedding_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    # 第三层：bge-reranker-v2-m3
    try:
        rs = providers.rerank(
            cfg["external_rerank_base_url"], cfg["external_rerank_model"],
            QUESTION, docs, os.environ.get("RERANK_API_KEY", ""),
        )
        if rs is None:
            stages["rerank_error"] = "rerank 不可用（已按项目口径优雅跳过）"
        else:
            for c, s in zip(stages["corpus"], rs):
                c["rerank_score"] = round(s, 6)
            stages["rerank_model"] = cfg["external_rerank_model"]
    except Exception as e:
        stages["rerank_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    # 最终实际检索结果（含 mode / rerank 标记 / 顺序）
    try:
        hits, meta = rag.retrieve(QUESTION, with_meta=True)
        stages["final_order"] = [
            {"doc": h["doc_title"], "score": h["score"], "mode": h["retrieval_mode"],
             "rerank": h["rerank"]} for h in hits
        ]
        stages["retrieval_mode"] = meta["retrieval_mode"]
        stages["rerank_note"] = meta["rerank"]
        stages["embedding_used"] = meta["embedding_model"]
        stages["fallback_reason"] = meta["fallback_reason"]
    except Exception as e:
        stages["retrieve_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    report["retrieval_stages"] = stages

    # ---- 3. 真实 HTTP 全链路：SSE 问答 + 来源追溯 + 降级 ----
    with TestClient(app) as client:
        tok = client.post("/api/auth/login",
                          json={"username": "admin", "password": "admin123"}).json()["access_token"]
        h = {"Authorization": "Bearer " + tok}

        t0 = time.time()
        resp = client.post("/api/chat/stream", headers=h, json={"question": QUESTION})
        wall = int((time.time() - t0) * 1000)
        parsed = parse_sse(resp.text)
        done, meta = parsed["done"], parsed["meta"]
        conv = (done or {}).get("conversation_id", "")
        answer = (done or {}).get("answer", "")
        report["e2e_chat"] = {
            "http_status": resp.status_code,
            "wall_ms": wall,
            "model": meta.get("model"),
            "token_chunks": len(parsed["tokens"]),
            "answer_chars": len(answer),
            "answer_excerpt": answer[:220],
            "degraded": (done or {}).get("degraded"),
            "degrade_reason": (done or {}).get("degrade_reason"),
            "retrieved": meta.get("retrieved"),
            "sources_in_meta": len(meta.get("sources") or []),
            "sources_in_done": len((done or {}).get("sources") or []),
            "conversation_id": conv,
        }

        # 来源追溯：落库后经三个回放接口取回
        src = client.get("/api/chat/sources", headers=h,
                         params={"conversation_id": conv}).json()
        convs = client.get("/api/chat/conversations", headers=h).json()
        hist = client.get("/api/chat/history", headers=h,
                          params={"conversation_id": conv}).json()
        asst = [m for m in hist.get("messages", []) if m["role"] == "assistant"]
        report["traceability"] = {
            "sources_endpoint_rows": len(src),
            "source_titles": [s.get("doc_title") for s in src],
            "conversation_marked_has_sources": any(
                c["conversation_id"] == conv and c["has_sources"] for c in convs),
            "history_assistant_sources": len(asst[0]["sources"]) if asst else 0,
        }

        # 降级：检索为空时诚实拒答，不编造、不调模型
        empty_q = "量子纠缠如何应用于超导材料？"
        e_resp = client.post("/api/chat/stream", headers=h, json={"question": empty_q})
        e_done = parse_sse(e_resp.text)["done"]
        report["degradation"] = {
            "question": empty_q,
            "degraded": (e_done or {}).get("degraded"),
            "reason": (e_done or {}).get("degrade_reason"),
            "answer_excerpt": (e_done or {}).get("answer", "")[:120],
        }

        # 检索调试页看到的口径
        sd = client.get("/api/search/preview", headers=h,
                        params={"q": QUESTION}).json()
        report["search_debug"] = {
            "hit_count": sd.get("hit_count"),
            "retrieval_mode": sd.get("retrieval_mode"),
            "rerank": sd.get("rerank"),
            "embedding": sd.get("embedding"),
            "generator": sd.get("generator"),
            "degraded": sd.get("degraded"),
        }

    return report


def main() -> None:
    db.init_db()
    seed_corpus()
    report = collect()
    path = OUT / "eval_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[report] {path}")
    # 清理隔离库
    try:
        _TMP_DB.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == "__main__":
    main()
