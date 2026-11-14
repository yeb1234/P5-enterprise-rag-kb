"""解析实验室 API 的离线回归：零外部调用、零真实模型流量。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.main import app


@pytest.fixture()
def client():
    # 必须进 with：lifespan 才会执行 init_db()，否则 rate_limit 中间件查表会 500
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_parse_status_reports_unavailable_external_tiers(client, auth):
    r = client.get("/api/parse/status", headers=auth)
    assert r.status_code == 200
    d = r.json()
    levels = {x["level"]: x for x in d["levels"]}
    assert levels["local"]["available"] is True
    assert levels["ocr_api"]["available"] is False
    assert levels["multimodal"]["available"] is False
    assert levels["ocr_api"]["contract"]["endpoint"] == "__TBC__"
    assert d["vision_models_probe"]["has_vision"] is False
    assert len(d["vision_models_probe"]["models"]) == 10
    assert d["budget"]["vision_escalation_ratio"] == 0.10


def test_parse_probe_markdown_stays_local(client, auth):
    r = client.post("/api/parse/probe", headers=auth,
                    files={"file": ("manual.md",
                                    b"# Manual\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nLong text here.",
                                    "text/markdown")})
    assert r.status_code == 200
    d = r.json()
    assert d["page_count"] == 1
    assert d["traces"][0]["level"] == "local"
    assert d["summary"]["upgrades"] == 0
    assert d["results"][0]["signals"]["table_cells"] > 0


def test_parse_probe_no_text_layer_escalates_honestly(client, auth):
    r = client.post("/api/parse/probe", headers=auth,
                    files={"file": ("blank.txt", b"", "text/plain")})
    assert r.status_code == 200
    d = r.json()
    tr = d["traces"][0]
    assert tr["level"] == "multimodal"
    assert tr["degraded"] == "vision_unavailable"
    assert "结构化降级" in d["results"][0]["text"]


def test_parse_probe_image_does_not_claim_vision(client, auth):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    r = client.post("/api/parse/probe", headers=auth,
                    files={"file": ("p.png", png, "image/png")})
    assert r.status_code == 200
    d = r.json()
    assert d["traces"][0]["degraded"] == "vision_unavailable"
    assert "非视觉" in d["results"][0]["text"] or "未识别" in d["results"][0]["text"]


def test_parse_strategy_write_requires_admin(client, auth):
    body = {"strategy": "local_only"}
    r = client.put("/api/parse/strategy", json=body)  # 无 token
    assert r.status_code in (401, 403)
    r2 = client.put("/api/parse/strategy", headers=auth, json=body)
    assert r2.status_code == 200 and r2.json()["strategy"] == "local_only"
    # 复原，避免污染其它用例
    client.put("/api/parse/strategy", headers=auth, json={"strategy": "auto"})


def test_parse_strategy_rejects_unknown(client, auth):
    r = client.put("/api/parse/strategy", headers=auth, json={"strategy": "turbo"})
    assert r.status_code == 400


def test_parse_traces_history(client, auth):
    client.post("/api/parse/probe", headers=auth,
                files={"file": ("a.txt", b"hello world", "text/plain")})
    r = client.get("/api/parse/traces", headers=auth)
    assert r.status_code == 200
    assert len(r.json()["history"]) >= 1
    assert "策略" in r.json()["note"] or "进程内" in r.json()["note"]


def test_parse_probe_rejects_oversize(client, auth):
    big = b"x" * (8 * 1024 * 1024 + 1)
    r = client.post("/api/parse/probe", headers=auth,
                    files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413
