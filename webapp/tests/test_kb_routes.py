"""三路取证在 webapp 层的集成测试：开关、融合、PG 复核、Wiki 落库与门禁、接口鉴权。

不依赖 Neo4j、不触网：图谱路由用假候选，Wiki 构建用假模型。Neo4j 连不上这条降级路径
在这里是被**真实执行**的——测试进程里没有配置连接串（见 conftest）。

## 两条隔离

- **开关**存在共享库的 app_settings 里，一个用例把图谱打开没关，后面的用例全都会受影响
  （本项目在限流上踩过同样的坑）。autouse 夹具在用例前后都复位成默认。
- **候选池是全库 ready 分块**。同一个测试库里还有别的用例留下的文档，它们也含「配网」
  这类词，会混进检索结果让断言时灵时不灵。所以本文件的造数一律带一个语料里不可能出现的
  锚词（NONCE），问句也带锚词，这样只有自己的分块会命中。
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from kgraph import store as graph_store
from webapp import db, kb_routes, rag, settings
from webapp.main import app

ADMIN = {"username": "admin", "password": "admin123"}
DEFAULT_ROUTES = {"rag_route_vector": "on", "rag_route_graph": "off", "rag_route_wiki": "off",
                  "wiki_scenario": ""}
NONCE = "耀石"
T_HIGH = f"{NONCE}设备配网说明：长按电源键开机"       # 问「设备配网」时词法命中 3（锚词+设备+配网）
T_MID = f"{NONCE}配网失败重试"                      # 命中 2
T_OFFTOPIC = f"{NONCE}账号绑定说明"                 # 只命中锚词；问「设备配网」时命中 1
T_WIKI = f"{NONCE}长按复位键3秒进入配网模式"
QUESTION = f"{NONCE}设备配网怎么做"
# 增量重建专用：两篇文档各带一个独有锚词，便于让假模型按文档分别产页
T_INC_A = f"{NONCE}阿尔法设备配网：长按电源键开机"
T_INC_B = f"{NONCE}贝塔账号绑定：扫码登录成功"
INC_SCENARIO = f"{NONCE}阿尔法贝塔"


@pytest.fixture(autouse=True)
def _isolate():
    graph_store.reset()            # 清掉探测缓存与连接单例，避免用例之间互相影响
    yield
    settings.set_many(DEFAULT_ROUTES)
    graph_store.reset()
    with db.get_conn() as conn:    # 只清本文件造的数据（doc-<随机串> 前缀）
        conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN "
                     "(SELECT id FROM chunks WHERE doc_id LIKE 'doc-%')")
        conn.execute("DELETE FROM chunks WHERE doc_id LIKE 'doc-%'")
        conn.execute("DELETE FROM documents WHERE id LIKE 'doc-%'")
        conn.execute("DELETE FROM wiki_claims")
        conn.execute("DELETE FROM wiki_pages")
        conn.execute("DELETE FROM runtime_state WHERE key IN ('wiki_build_meta', 'graph_sync_meta')")
        conn.commit()
    kb_routes.reset_jobs()


def _wait_job(name: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline and kb_routes.job_state(name)["status"] == "running":
        time.sleep(0.05)
    return kb_routes.job_state(name)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _login(client, creds):
    r = client.post("/api/auth/login", json=creds)
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def _seed(texts_by_doc: dict[str, list[str]]) -> dict[str, str]:
    """直接写 documents / chunks（绕开上传队列）。返回 {文本: 首个 chunk_id}。"""
    tag = uuid.uuid4().hex[:8]
    now = "2026-11-01T00:00:00Z"
    out: dict[str, str] = {}
    with db.get_conn() as conn:
        for n, (title, texts) in enumerate(texts_by_doc.items()):
            doc_id = f"doc-{tag}-{n}"
            conn.execute(
                "INSERT INTO documents(id, owner_id, filename, title, status, chunk_count, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (doc_id, "u-test", f"{doc_id}.txt", title, "ready", len(texts), now, now))
            for i, text in enumerate(texts):
                cid = f"chk-{tag}-{n}-{i}"
                conn.execute("INSERT INTO chunks(id, doc_id, idx, text) VALUES (?,?,?,?)",
                             (cid, doc_id, i, text))
                out.setdefault(text, cid)
        conn.commit()
    return out


def _ids_of(text: str) -> list[str]:
    with db.get_conn() as conn:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM chunks WHERE text=? AND doc_id LIKE 'doc-%' ORDER BY id", (text,)).fetchall()]


class FakeLLM:
    def __init__(self, text: str):
        self.text = text

    def complete(self, prompt, **kw):
        return self.text


# ---------------------------------------------------------------- 开关与默认路径
def _rank_of_ours(hits, ours) -> list[int]:
    """我们造的分块在结果里的名次。共享测试库里还有别的用例造的分块，
    只断言「自己的三条按词法分 3/2/1 有序且都命中」，不假设别人没命中（全套跑时会干扰）。"""
    mine = [h["chunk_id"] for h in hits if h["chunk_id"] in set(ours)]
    return [ours.index(c) for c in mine]


def test_default_is_vector_only_and_output_shape_is_additive():
    ids = _seed({"说明书": [T_HIGH, T_MID, T_OFFTOPIC]})
    ours = [ids[T_HIGH], ids[T_MID], ids[T_OFFTOPIC]]
    assert settings.routes_on() == ["vector"]
    hits, meta = rag.retrieve(QUESTION, with_meta=True)
    assert _rank_of_ours(hits, ours) == [0, 1, 2]        # 自己的三条：按词法分 3/2/1 排列
    assert all(h["routes"] == ["vector"] and h["fusion_score"] is None for h in hits)
    assert meta["fusion"] is None and set(meta["routes"]) == {"vector"}
    assert meta["retrieval_mode"] == "lexical"          # 离线：词法门禁 + 词法排序，与从前一致


def test_all_routes_off_returns_empty_instead_of_silently_falling_back():
    _seed({"说明书": [T_HIGH]})
    settings.set_many({"rag_route_vector": "off"})
    hits, meta = rag.retrieve(QUESTION, with_meta=True)
    assert hits == [] and meta["retrieval_mode"] == "off" and meta["routes"] == {}


def test_search_debug_exposes_routes_and_fusion():
    _seed({"说明书": [T_HIGH]})
    r = rag.search_debug(QUESTION)
    assert "routes" in r and "fusion" in r and r["fusion"] is None
    assert all(h["routes"] == ["vector"] for h in r["hits"])


# ---------------------------------------------------------------- 图谱路由不可用 / 出错时的降级
def test_graph_route_unavailable_leaves_vector_results_untouched():
    """Neo4j 没配置 / 没起：这一路如实报告 unavailable，结果与只开向量时完全一致。"""
    ids = _seed({"说明书": [T_HIGH, T_MID, T_OFFTOPIC]})
    ours = [ids[T_HIGH], ids[T_MID], ids[T_OFFTOPIC]]
    base, _ = rag.retrieve(QUESTION, with_meta=True)
    assert _rank_of_ours(base, ours) == [0, 1, 2]

    settings.set_many({"rag_route_graph": "on"})
    hits, meta = rag.retrieve(QUESTION, with_meta=True)
    # 「图谱不可用时与只开向量一致」的判据：自己那三条的名次和分数逐条不变。
    # 不能断言整个 hits 列表相等——共享库里别的用例的分块数会变（全套跑时实测过）。
    assert _rank_of_ours(hits, ours) == _rank_of_ours(base, ours)
    by_id = {h["chunk_id"]: h["score"] for h in base}
    assert all(h["score"] == by_id[h["chunk_id"]] for h in hits if h["chunk_id"] in by_id)
    g = meta["routes"]["graph"]
    assert g["status"] == "unavailable" and "未配置" in g["reason"]
    assert all(h["routes"] == ["vector"] for h in hits)
    assert meta["fusion"]["multi_hit_count"] == 0
    assert meta["retrieval_mode"] == "fusion(vector+graph)"


def test_graph_route_raising_is_contained(monkeypatch):
    """适配层理论上不抛错；万一抛了，融合层也必须兜住——「一路出问题只让那一路缺席」。"""
    _seed({"说明书": [T_HIGH]})
    settings.set_many({"rag_route_graph": "on"})

    def boom(question, cfg):
        raise RuntimeError("bolt 断开")

    monkeypatch.setattr(kb_routes, "graph_candidates", boom)
    hits, meta = rag.retrieve(QUESTION, with_meta=True)
    g = meta["routes"]["graph"]
    assert g["status"] == "error" and "bolt 断开" in g["reason"]
    assert hits and hits[0]["routes"] == ["vector"]        # 向量那一路照常


# ---------------------------------------------------------------- 融合与 PG 复核
def test_fusion_multi_hit_first_and_pg_verification(monkeypatch):
    ids = _seed({"说明书": [T_WIKI, T_MID], "另一份": [T_MID]})   # 第二份文档的正文与 T_MID 相同
    a = ids[T_WIKI]
    b, c = _ids_of(T_MID)[0], _ids_of(T_MID)[1]
    assert c != b

    settings.set_many({"rag_route_vector": "off", "rag_route_graph": "on", "rag_route_wiki": "on"})
    # 图谱：a、一条 PG 里不存在的 ghost、以及 T_MID 的两个副本 b / c
    # Wiki：a、以及 T_MID 的另一个副本 c——与图谱命中的 b **不是同一个 chunk_id**，但正文相同
    monkeypatch.setattr(kb_routes, "graph_candidates", lambda q, cfg: (
        [{"chunk_id": a, "score": 1.0}, {"chunk_id": "ghost", "score": 1.0},
         {"chunk_id": b, "score": 0.6}, {"chunk_id": c, "score": 0.6}],
        {"route": "graph", "status": "ok"}))
    monkeypatch.setattr(kb_routes, "wiki_candidates", lambda q, cfg: (
        [{"chunk_id": a, "score": 0.8}, {"chunk_id": c, "score": 0.8}],
        {"route": "wiki", "status": "ok"}))

    hits, meta = rag.retrieve(QUESTION, top_k=10, with_meta=True)
    g = meta["routes"]["graph"]
    assert g["dropped_missing_in_pg"] == 1                         # ghost：PG 里没有，不算数
    assert g["deduped_same_text"] == 1                             # 图谱这一路里 c 与 b 同文：只留一条
    # Wiki 命中的副本 c 按正文归并到 b 上：b 因此也是双路命中，而不是两条互不相干的记录
    assert [h["chunk_id"] for h in hits] == [a, b]
    assert hits[0]["routes"] == ["graph", "wiki"] and hits[1]["routes"] == ["graph", "wiki"]
    assert (hits[0]["score"], hits[1]["score"]) == (1.0, 0.8)      # 向量关着：展示其它路里最高的分
    assert meta["fusion"] == {"method": "multi_hit_first + rrf", "multi_hit_count": 2,
                              "multi_hit": [a, b], "pool": 2}
    assert meta["retrieval_mode"] == "fusion(graph+wiki)"


def test_fusion_never_returns_more_than_top_k(monkeypatch):
    ids = _seed({"说明书": [f"{NONCE}配网第{i}条" for i in range(8)]})
    settings.set_many({"rag_route_vector": "off", "rag_route_graph": "on"})
    monkeypatch.setattr(kb_routes, "graph_candidates", lambda q, cfg: (
        [{"chunk_id": cid, "score": 1.0} for cid in ids.values()], {"route": "graph", "status": "ok"}))
    hits, meta = rag.retrieve(QUESTION, top_k=4, with_meta=True)
    assert len(hits) == 4 and meta["fusion"]["pool"] == 8


# ---------------------------------------------------------------- Wiki：门禁、落库、检索
PAGE = {"pages": [{
    "title": "配网流程", "section": "业务流程", "markdown": "## 配网流程\n长按复位键进入配网模式。",
    "claims": [
        {"statement": "长按复位键进入配网模式", "evidence": [{"chunk_id": "__A__", "quote": "长按复位键"}]},
        {"statement": "这条证据是编的", "evidence": [{"chunk_id": "nope", "quote": "x"}]},
    ],
}]}


def _patch_fake_wiki_llm(monkeypatch, chunk_id: str) -> None:
    text = json.dumps(PAGE, ensure_ascii=False).replace("__A__", chunk_id)
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM(text))


def test_wiki_route_blocked_until_scenario_set_and_built(monkeypatch):
    ids = _seed({"说明书": [T_WIKI]})
    settings.set_many({"rag_route_vector": "off", "rag_route_wiki": "on"})
    q = f"{NONCE}复位键怎么进入配网"

    _, meta = rag.retrieve(q, with_meta=True)
    w = meta["routes"]["wiki"]
    assert w["status"] == "gate_blocked"
    assert any("未设置业务场景" in r for r in w["gate"]["reasons"])

    _patch_fake_wiki_llm(monkeypatch, ids[T_WIKI])
    settings.set_many({"wiki_scenario": "演示场景"})
    _, meta = rag.retrieve(q, with_meta=True)
    assert meta["routes"]["wiki"]["status"] == "gate_blocked"          # 场景有了，但还没构建
    assert any("尚未构建" in r for r in meta["routes"]["wiki"]["gate"]["reasons"])


def test_wiki_build_persists_claims_then_route_works_and_detects_source_change(monkeypatch):
    ids = _seed({"说明书": [T_WIKI, T_MID]})
    a = ids[T_WIKI]
    _patch_fake_wiki_llm(monkeypatch, a)
    settings.set_many({"wiki_scenario": "演示场景"})

    built = kb_routes.wiki_build()
    assert built["pages"] == 1 and built["claims"] == 1             # 编造的那条证据被拦下
    assert built["stats"]["evidence_dropped"] >= 1 and built["gate"]["allowed"] is True

    with db.get_conn() as conn:
        row = conn.execute("SELECT statement, evidence_json FROM wiki_claims").fetchone()
    ev = row["evidence_json"]
    if isinstance(ev, str):                 # psycopg3 读 jsonb 通常直接给 list；两种形态都接受
        ev = json.loads(ev)
    assert row["statement"] == "长按复位键进入配网模式"
    assert ev[0]["chunk_id"] == a

    settings.set_many({"rag_route_vector": "off", "rag_route_wiki": "on"})
    q = f"{NONCE}复位键怎么进入配网"
    hits, meta = rag.retrieve(q, with_meta=True)
    w = meta["routes"]["wiki"]
    assert w["status"] == "ok" and [h["chunk_id"] for h in hits] == [a]
    assert hits[0]["routes"] == ["wiki"]
    assert w["pages"][0]["title"] == "配网流程" and w["pages"][0]["claims_used"] == 1

    # 原文一改，信源指纹就变：门禁立刻不再放行（页面是基于旧内容整理的）
    with db.get_conn() as conn:
        conn.execute("UPDATE chunks SET text=? WHERE id=?", (T_WIKI.replace("3秒", "5秒"), a))
        conn.commit()
    _, meta = rag.retrieve(q, with_meta=True)
    assert meta["routes"]["wiki"]["status"] == "gate_blocked"
    assert any("信源在构建后发生变化" in r for r in meta["routes"]["wiki"]["gate"]["reasons"])


def test_wiki_build_scopes_sources_to_scenario(monkeypatch):
    """场景决定哪些资料算信源：与场景无关的文档不送去整理，之后改了它也不该逼着重建。"""
    t_off = "羟乙基报销流程说明：扫码后开具发票"            # 与「配网」无关，也不带锚词
    ids = _seed({"设备说明": [T_WIKI, T_MID], "财务制度": [t_off]})
    a = ids[T_WIKI]
    with db.get_conn() as conn:
        off_doc = conn.execute("SELECT doc_id FROM chunks WHERE id=?", (ids[t_off],)).fetchone()["doc_id"]
    _patch_fake_wiki_llm(monkeypatch, a)
    settings.set_many({"wiki_scenario": f"{NONCE}配网故障排查"})

    built = kb_routes.wiki_build()
    sf = built["scenario_filter"]
    # 断言锚词是强词，而不是「配网」：测试库是共享的，别的用例留下的样例文档里也大量含「配网」，
    # 它的占比可能超过区分度门槛而被正确地筛掉。锚词只出现在本文件造的数据里，才是稳的判据。
    assert sf["mode"] == "scenario" and NONCE in sf["terms"]
    assert off_doc not in built["source_doc_ids"]          # 与场景无关的文档不是信源
    assert sf["kept"] < sf["total"]                        # 送去做整理的分块确实少于全库
    g = built["gate"]
    assert g["allowed"] is True, {"ok": {k: v["ok"] for k, v in g["checks"].items()}, "reasons": g["reasons"]}

    # 改动与场景无关的文档：已有页面的信源没变，门禁仍然通过
    with db.get_conn() as conn:
        conn.execute("UPDATE chunks SET text=? WHERE id=?", (t_off + "（修订）", ids[t_off]))
        conn.commit()
    assert kb_routes.wiki_gate()["allowed"] is True

    # 改动信源文档：指纹变，门禁关闭
    with db.get_conn() as conn:
        conn.execute("UPDATE chunks SET text=? WHERE id=?", (T_WIKI.replace("3秒", "5秒"), a))
        conn.commit()
    g = kb_routes.wiki_gate()
    assert not g["allowed"] and any("信源在构建后发生变化" in r for r in g["reasons"])


def test_wiki_build_requires_model(monkeypatch):
    _seed({"说明书": [T_WIKI]})
    settings.set_many({"wiki_scenario": "演示场景"})
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: None)      # 离线占位
    with pytest.raises(kb_routes.RouteNotReady, match="离线占位"):
        kb_routes.wiki_build()


def test_wiki_tables_match_ddl():
    from webapp.tests._dbutil import columns
    with db.get_conn() as conn:
        assert {"id", "title", "section", "body_md", "summary", "scenario", "built_at"} <= columns(conn, "wiki_pages")
        assert {"id", "page_id", "statement", "evidence_json", "source_hash", "ordinal"} <= columns(conn, "wiki_claims")


# ---------------------------------------------------------------- 后台任务与接口
def test_build_job_runs_in_background_and_rejects_duplicate_clicks(monkeypatch):
    """构建要跑几分钟，不能让一个请求干等：立即返回 running，后台线程执行，状态可查。"""
    ids = _seed({"说明书": [T_WIKI]})

    class SlowLLM(FakeLLM):
        def complete(self, prompt, **kw):
            time.sleep(0.4)                       # 保证第二次点击发生在构建期间
            return self.text

    text = json.dumps(PAGE, ensure_ascii=False).replace("__A__", ids[T_WIKI])
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: SlowLLM(text))
    settings.set_many({"wiki_scenario": "演示场景"})

    job = kb_routes.wiki_build_async()
    assert job["status"] == "running" and job["result"] is None
    with pytest.raises(kb_routes.RouteNotReady, match="正在进行中"):
        kb_routes.wiki_build_async()              # 重复点击被拒：不能把活干两遍
    done = _wait_job("wiki_build")
    assert done["status"] == "done" and done["result"]["pages"] == 1 and done["progress"] == "1/1"
    assert kb_routes.status()["jobs"]["wiki_build"]["status"] == "done"
    kb_routes.wiki_build_async()                  # 完成之后可以再次启动
    assert _wait_job("wiki_build")["status"] == "done"


def test_build_job_failure_is_recorded_not_swallowed(monkeypatch):
    _seed({"说明书": [T_WIKI]})
    settings.set_many({"wiki_scenario": "演示场景"})
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM("这不是 JSON"))
    kb_routes.wiki_build_async()
    job = _wait_job("wiki_build")
    assert job["status"] == "failed" and "未落库" in job["error"] and job["finished_at"]


def test_build_endpoints_start_background_jobs_or_return_409(client, monkeypatch):
    ids = _seed({"说明书": [T_WIKI]})
    text = json.dumps(PAGE, ensure_ascii=False).replace("__A__", ids[T_WIKI])
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM(text))
    h = _login(client, ADMIN)
    assert client.post("/api/kb/graph/sync", headers=h, json={}).status_code == 409   # Neo4j 未配置
    r = client.post("/api/kb/wiki/build", headers=h)
    assert r.status_code == 409 and "业务场景" in r.text                            # 场景没填

    settings.set_many({"wiki_scenario": "演示场景"})
    r = client.post("/api/kb/wiki/build", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "running"                  # 立即返回
    assert _wait_job("wiki_build")["status"] == "done"
    state = client.get("/api/kb/routes", headers=h).json()
    assert state["jobs"]["wiki_build"]["status"] == "done"
    assert state["wiki"]["gate"]["allowed"] is True
    assert state["wiki"]["meta"]["pages"] == 1


# ---------------------------------------------------------------- 设置接口与状态接口的鉴权
def test_settings_api_accepts_and_validates_route_keys(client):
    h = _login(client, ADMIN)
    r = client.put("/api/settings", headers=h, json={"patch": {
        "rag_route_graph": "on", "rag_graph_hops": "3", "rag_graph_cypher": "llm",
        "rag_graph_extractor": "llm", "wiki_scenario": "订单排障", "wiki_stale_ratio_max": "0.2"}})
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert (cfg["rag_route_graph"], cfg["rag_graph_hops"], cfg["wiki_scenario"]) == ("on", "3", "订单排障")
    assert settings.routes_on() == ["vector", "graph"]

    for bad in ({"rag_graph_hops": "9"}, {"rag_route_graph": "maybe"}, {"rag_graph_cypher": "auto"},
                {"wiki_stale_ratio_max": "1.5"}, {"wiki_stale_ratio_max": "x"}, {"wiki_scenario": "长" * 61}):
        assert client.put("/api/settings", headers=h, json={"patch": bad}).status_code == 400, bad
    trimmed = client.put("/api/settings", headers=h, json={"patch": {"wiki_scenario": "  订单排障  "}})
    assert trimmed.json()["config"]["wiki_scenario"] == "订单排障"      # 首尾空白不算场景名的一部分


def test_routes_status_requires_auth_and_hides_infra_detail(client):
    assert client.get("/api/kb/routes").status_code in (401, 403)
    uname = "u" + uuid.uuid4().hex[:8]
    r = client.post("/api/auth/register",
                    json={"username": uname, "password": "test1234", "email": "u@demo.local"})
    uh = {"Authorization": "Bearer " + r.json()["access_token"]}
    body = client.get("/api/kb/routes", headers=uh).json()
    assert body["routes_on"] == ["vector"] and "switches" not in body
    assert body["graph"]["probe"] == {"ok": False}                 # 普通用户只看通不通
    assert body["wiki"]["gate"]["allowed"] is False
    assert set(body["wiki"]["gate"]["checks"]) == {"scenario", "sources", "freshness"}

    admin = client.get("/api/kb/routes", headers=_login(client, ADMIN)).json()
    assert "switches" in admin and admin["graph"]["probe"]["ok"] is False
    # 普通用户不能触发构建
    assert client.post("/api/kb/graph/sync", headers=uh).status_code == 403
    assert client.post("/api/kb/wiki/build", headers=uh).status_code == 403


# ---------------------------------------------------------------- 任务落库与增量重建
def test_job_state_reads_from_db_and_restart_marks_interrupted(monkeypatch):
    """状态从 PG 读回；把 worker_id 改成别人的再读，running 收敛成 interrupted。"""
    ids = _seed({"说明书": [T_WIKI]})
    text = json.dumps(PAGE, ensure_ascii=False).replace("__A__", ids[T_WIKI])
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM(text))
    settings.set_many({"wiki_scenario": "演示场景"})

    job = kb_routes.wiki_build_async()
    assert job["status"] == "running" and job["result"] is None
    assert _wait_job("wiki_build")["status"] == "done"

    with db.get_conn() as conn:
        row = conn.execute("SELECT status, worker_id FROM kb_jobs WHERE id=?",
                           (job["id"],)).fetchone()
    assert row["status"] == "done" and row["worker_id"] == kb_routes.WORKER_ID

    # 直接改库：读接口跟着变，证明不是进程内存里的副本
    with db.get_conn() as conn:
        conn.execute("UPDATE kb_jobs SET result_json=? WHERE id=?",
                     (json.dumps({"pages": 99}), job["id"]))
        conn.commit()
    assert kb_routes.job_state("wiki_build")["result"]["pages"] == 99

    # 模拟进程重启 / 换了副本：同一行重新 running 且 worker_id 不是本进程
    with db.get_conn() as conn:
        conn.execute("UPDATE kb_jobs SET status='running', worker_id='other-replica', "
                     "finished_at=NULL WHERE id=?", (job["id"],))
        conn.commit()
    st = kb_routes.job_state("wiki_build")
    assert st["status"] == "interrupted" and "进程重启" in st["error"] and st["finished_at"]
    assert kb_routes.job_state("wiki_build")["status"] == "interrupted"   # 幂等
    assert kb_routes.status()["jobs"]["wiki_build"]["status"] == "interrupted"


def test_same_name_job_rejected_when_db_already_running(monkeypatch):
    """并发拦截靠 PG 部分唯一索引：别的进程插了 running 行，本进程也要当场被拒。"""
    _seed({"说明书": [T_WIKI]})
    settings.set_many({"wiki_scenario": "演示场景"})
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM("unused"))
    now = "2026-11-27T00:00:00Z"
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO kb_jobs(id,name,worker_id,status,started_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, "wiki_build", "other-replica", "running", now, now))
        conn.commit()

    with pytest.raises(kb_routes.RouteNotReady, match="正在进行中"):
        kb_routes.wiki_build_async()
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM kb_jobs WHERE worker_id=?",
                         (kb_routes.WORKER_ID,)).fetchone()["c"]
    assert n == 0                                   # 被拒时不能留下本进程的 running 行


def test_reset_jobs_clears_db():
    now = "2026-11-27T00:00:00Z"
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO kb_jobs(id,name,worker_id,status,started_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, "wiki_build", kb_routes.WORKER_ID, "running", now, now))
        conn.commit()
    assert kb_routes.job_state("wiki_build")["status"] == "running"
    kb_routes.reset_jobs()
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM kb_jobs").fetchone()["c"] == 0
    assert kb_routes.job_state("wiki_build") == {"name": "wiki_build", "status": "idle"}


def test_incremental_skips_llm_when_sources_unchanged(monkeypatch):
    """信源无变化时一次模型调用都不发——即使 _llm_or_none 会抛异常也要成功 skip。"""
    ids = _seed({"说明书": [T_WIKI]})
    text = json.dumps(PAGE, ensure_ascii=False).replace("__A__", ids[T_WIKI])
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: FakeLLM(text))
    settings.set_many({"wiki_scenario": f"{NONCE}配网故障排查"})
    kb_routes.wiki_build()                          # 先建一次，写下 source_doc_hashes

    def boom(cfg):
        raise AssertionError("信源无变化时不许触碰模型")

    monkeypatch.setattr(kb_routes, "_llm_or_none", boom)
    out = kb_routes.wiki_build(mode="incremental")
    assert out["incremental_skipped"] is True and out["reason"] == "信源无变化"
    assert out["pages"] == 1 and out["claims"] == 1
    assert out["gate"]["allowed"] is True


class IncrementalLLM:
    """按分块正文里出现哪篇文档决定产哪一页；记录 prompt 供断言。"""

    def __init__(self, chunk_a: str, chunk_b: str):
        self.chunk_a, self.chunk_b = chunk_a, chunk_b
        self.prompts: list[str] = []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        pages = []
        if T_INC_A in prompt:                       # 场景文本不会含整句，只有分块清单会
            pages.append({
                "title": "阿尔法配网", "section": "业务流程",
                "markdown": "## 阿尔法配网\n长按电源键开机。",
                "claims": [{"statement": "阿尔法设备长按电源键开机",
                            "evidence": [{"chunk_id": self.chunk_a, "quote": "长按电源键开机"}]}],
            })
        if T_INC_B in prompt:
            pages.append({
                "title": "贝塔绑定", "section": "业务流程",
                "markdown": "## 贝塔绑定\n扫码登录成功。",
                "claims": [{"statement": "贝塔账号扫码登录成功",
                            "evidence": [{"chunk_id": self.chunk_b, "quote": "扫码登录成功"}]}],
            })
        return json.dumps({"pages": pages}, ensure_ascii=False)


def test_incremental_rebuilds_only_changed_doc_pages(monkeypatch):
    ids = _seed({"阿尔法说明": [T_INC_A], "贝塔说明": [T_INC_B]})
    a, b = ids[T_INC_A], ids[T_INC_B]
    with db.get_conn() as conn:
        doc_a = conn.execute("SELECT doc_id FROM chunks WHERE id=?", (a,)).fetchone()["doc_id"]

    def _snapshot():
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT p.title, c.source_hash FROM wiki_pages p "
                "JOIN wiki_claims c ON c.page_id=p.id WHERE p.scenario=?", (INC_SCENARIO,)).fetchall()
        return {r["title"]: r["source_hash"] for r in rows}

    llm = IncrementalLLM(a, b)
    monkeypatch.setattr(kb_routes, "_llm_or_none", lambda cfg: llm)
    settings.set_many({"wiki_scenario": INC_SCENARIO})

    built = kb_routes.wiki_build()
    assert built["pages"] == 2 and built["claims"] == 2
    before = _snapshot()
    assert set(before) == {"阿尔法配网", "贝塔绑定"}

    # 只改阿尔法那篇；贝塔那篇的 content_hash 不变
    with db.get_conn() as conn:
        conn.execute("UPDATE chunks SET text=? WHERE id=?", (T_INC_A + "（已修订）", a))
        conn.commit()

    llm.prompts.clear()
    out = kb_routes.wiki_build(mode="incremental")
    assert out["incremental"] is True and out["changed_doc_ids"] == [doc_a]
    # 只把变化文档的分块送给模型，没把没变的贝塔再送一遍（场景名里有锚词，按整句判）
    assert llm.prompts and all(T_INC_A in p and T_INC_B not in p for p in llm.prompts)
    after = _snapshot()
    assert set(after) == {"阿尔法配网", "贝塔绑定"}
    assert after["阿尔法配网"] != before["阿尔法配网"]      # 变化的页重建了
    assert after["贝塔绑定"] == before["贝塔绑定"]          # 没变的页原样保留
    assert out["pages"] == 2 and out["claims"] == 2 and out["gate"]["allowed"] is True


def test_wiki_build_rejects_invalid_mode(client):
    h = _login(client, ADMIN)
    r = client.post("/api/kb/wiki/build", headers=h, json={"mode": "bogus"})
    assert r.status_code == 400 and "mode" in r.text


def test_kb_jobs_table_matches_ddl():
    from webapp.tests._dbutil import columns
    with db.get_conn() as conn:
        assert {"id", "name", "worker_id", "status", "started_at", "finished_at",
                "error", "result_json", "progress", "updated_at"} <= columns(conn, "kb_jobs")
