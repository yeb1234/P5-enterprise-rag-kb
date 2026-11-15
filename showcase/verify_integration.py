"""showcase 集成验证（内存级 TestClient，不监听端口、不触发外部调用）。

用途：把 Phase 1 的 V1–V15 判据与 Phase 2 的集成裁决变成可复跑的确定性断言。
复跑：
    PARSING_OFFLINE=1 python showcase/verify_integration.py
输出：逐项 PASS/FAIL + 末尾 SUMMARY；任一失败退出码 1。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PARSING_OFFLINE", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from showcase.server import app  # noqa: E402

SAMPLES = HERE / "samples"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, bool(ok), note))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" :: {note}" if note else ""))


def canonical(obj):
    """V5 投影：与 ingest_preview.canonical() 同口径（路径感知剔除）。"""
    from showcase.ingest_preview import canonical as impl
    return impl(obj)


def run() -> None:
    # raise_server_exceptions=False：并行期同伴模块缺失时让全局 handler 归一为 500，
    # 验证脚本仍能跑完所有断言并给出完整阻塞清单，而不是被首个异常栈打断。
    with TestClient(app, raise_server_exceptions=False) as c:
        # ---- A1 health ----
        r = c.get("/api/health")
        d = r.json() if r.status_code == 200 else {}
        check("A1 health 200 且 offline", r.status_code == 200 and d.get("offline") is True,
              f"status={r.status_code} offline={d.get('offline')}")
        cfg = d.get("external_configured", {})
        check("A1 密钥只回布尔", bool(cfg) and all(isinstance(v, bool) for v in cfg.values()),
              f"keys={list(cfg)}")

        # ---- A2 / A3 params ----
        r = c.get("/api/params")
        raw = r.json() if r.status_code == 200 else {}
        # A2 实际是 {"params": {...}, "params_hash", "ranges", "note"}，兼容嵌套与平铺两种
        p = dict(raw.get("params", raw))
        p["params_hash"] = raw.get("params_hash", p.get("params_hash"))
        needed = {"strategy", "confidence_min", "chunk_size_chars", "chunk_overlap_chars",
                  "vision_escalation_ratio", "vision_min_slots", "llm_tier_override",
                  "redact_enabled", "ingest_format", "as_of", "params_hash"}
        check("A2 七类参数 + params_hash 齐备", r.status_code == 200 and needed <= set(p),
              f"missing={sorted(needed - set(p))}")
        h0 = p.get("params_hash")
        r = c.put("/api/params", json={"chunk_size_chars": 400})
        p2 = r.json() if r.status_code == 200 else {}
        check("A3 改参返回 changed 且 hash 变",
              r.status_code == 200 and "chunk_size_chars" in p2.get("changed", [])
              and p2.get("params_hash") != h0)
        check("A3 越界参数 422", c.put("/api/params", json={"confidence_min": 1.7}).status_code == 422)
        c.put("/api/params", json={"chunk_size_chars": 800})

        # ---- A4 strategies ----
        r = c.get("/api/strategies")
        s = r.json() if r.status_code == 200 else {}
        levels = {x.get("level"): x for x in s.get("levels", [])}
        ocr = levels.get("ocr_api", {})
        check("A4 ocr_api 未接通且留痕", r.status_code == 200 and ocr.get("available") is False
              and bool(ocr.get("reason") or ocr.get("contract")),
              f"available={ocr.get('available')} reason={str(ocr.get('reason'))[:50]}")
        vendors = s.get("vendors", [])
        check("A4 收费 profile 全未接通", bool(vendors) and all(v.get("available") is False for v in vendors),
              f"vendors={len(vendors)}")

        # ---- A10 samples + 静态可达 ----
        r = c.get("/api/samples")
        rows = r.json().get("samples", []) if r.status_code == 200 else []
        names = {x.get("filename") for x in rows}
        check("A10 返回真实两档样本", r.status_code == 200 and {"simple.pdf", "complex.png"} <= names,
              f"names={sorted(names)}")
        complex_row = next((x for x in rows if x.get("filename") == "complex.png"), {})
        check("A10 复杂档标回放", complex_row.get("replay") is True)
        sr = c.get("/showcase/samples/simple.pdf")
        check("静态样本可达", sr.status_code == 200
              and sr.content == (SAMPLES / "simple.pdf").read_bytes(),
              f"status={sr.status_code} bytes={len(sr.content)}")

        # ---- A5 核心：sample_id 兜底 + 两区同源 + 离线 ----
        r1 = c.post("/api/analyze", data={"sample_id": "simple.pdf"})
        d1 = r1.json() if r1.status_code == 200 else {}
        ing1, ana1 = d1.get("ingest", {}), d1.get("analysis", {})
        check("A5 sample_id 无 file 可跑", r1.status_code == 200,
              f"status={r1.status_code} code={d1.get('code')}")
        rid = str(d1.get("run_id", ""))
        check("A5 run_id 20 位小写 hex", len(rid) == 20 and all(ch in "0123456789abcdef" for ch in rid), rid)
        check("A5 左右同源",
              ing1.get("run_id") == rid and ana1.get("run_id") == rid
              and ing1.get("chunks") is not None and ana1.get("pages") is not None,
              f"chunks={len(ing1.get('chunks', []))} pages={len(ana1.get('pages', []))}")
        rt = d1.get("runtime", {})
        check("A5 外部请求计数 0", rt.get("external_request_count") == 0,
              f"count={rt.get('external_request_count')}")
        check("A5 created_at 取 as_of", ing1.get("created_at") == p.get("as_of"),
              f"{ing1.get('created_at')} vs {p.get('as_of')}")
        check("A5 右侧不含挂钟时间", "wall_clock" not in json.dumps(ing1, ensure_ascii=False))

        # ---- A5 上传真实文件（multipart） ----
        up = c.post("/api/analyze",
                    files={"file": ("simple.pdf", (SAMPLES / "simple.pdf").read_bytes(),
                                    "application/pdf")})
        check("A5 multipart 上传可跑", up.status_code == 200,
              f"status={up.status_code} code={up.json().get('code')}")

        # ---- V5 同参双跑投影一致 / 改参必变 ----
        r2 = c.post("/api/analyze", data={"sample_id": "simple.pdf"})
        if r1.status_code == 200 and r2.status_code == 200:
            a = canonical(r1.json()["ingest"])
            b = canonical(r2.json()["ingest"])
            sa = json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            sb = json.dumps(b, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            check("V5 同参双跑投影逐字节一致", sa == sb, f"len={len(sa)}/{len(sb)}")
            check("V5 投影保留 trace[].reason",
                  any("reason" in t for t in a.get("trace", []) if isinstance(t, dict)),
                  f"trace={len(a.get('trace', []))}")
        else:
            check("V5 同参双跑投影逐字节一致", False, "前置 A5 失败")
            check("V5 投影保留 trace[].reason", False, "前置 A5 失败")
        r3 = c.post("/api/analyze", data={"sample_id": "simple.pdf",
                                          "params": json.dumps({"chunk_size_chars": 400})})
        if r3.status_code == 200:
            s3 = json.dumps(canonical(r3.json()["ingest"]), sort_keys=True,
                            ensure_ascii=False, separators=(",", ":"))
            check("V5 改参后投影必变", s3 != sa, f"len={len(s3)} vs {len(sa)}")
        else:
            check("V5 改参后投影必变", False, f"status={r3.status_code}")

        # ---- 复杂档滞回：1/2 → 2/2 → pro ----
        # 必须用 full 档：compact 档按 §5.4 会把 llm_route 裁成 5 字段，读不到 hysteresis。
        full_params = json.dumps({"ingest_format": "full"})
        cx1 = c.post("/api/analyze", data={"sample_id": "complex.png", "params": full_params})
        cx2 = c.post("/api/analyze", data={"sample_id": "complex.png", "params": full_params})
        if cx1.status_code == 200 and cx2.status_code == 200:
            q1, q2 = cx1.json()["ingest"]["llm_route"], cx2.json()["ingest"]["llm_route"]
            n1 = q1.get("hysteresis", {}).get("consecutive_hits")
            n2 = q2.get("hysteresis", {}).get("consecutive_hits")
            check("复杂档滞回 1/2 → 2/2 且第 2 次升 pro",
                  q1.get("tier") == "standard" and n1 == 1
                  and q2.get("tier") == "pro" and n2 == 2,
                  f"{q1.get('tier')}({n1}) -> {q2.get('tier')}({n2})")
            check("改档必变 run_id / snapshot_version",
                  cx1.json()["run_id"] != cx2.json()["run_id"]
                  and q1.get("snapshot_version") != q2.get("snapshot_version"))
            note = str(cx1.json()["ingest"].get("note", ""))
            check("复杂档标注离线回放", "回放" in note or "回放" in str(cx1.json().get("analysis", {}).get("warnings")),
                  note[:60])
        else:
            for name in ("复杂档滞回 1/2 → 2/2 且第 2 次升 pro", "改档必变 run_id / snapshot_version",
                         "复杂档标注离线回放"):
                check(name, False, f"status={cx1.status_code}/{cx2.status_code}")

        # ---- A7 / A8 / A6 / A9 ----
        rid2 = cx2.json().get("run_id", "")
        check("A7 ingest preview", c.get(f"/api/ingest/preview?run_id={rid2}&format=compact").status_code == 200)
        a8 = c.get(f"/api/manifest/{rid2}")
        if a8.status_code == 200:
            ck = a8.json().get("checks", {})
            check("A8 双向摘要校验通过", all(v is not False for v in ck.values()), str(ck))
        else:
            check("A8 双向摘要校验通过", False, f"status={a8.status_code}")
        check("A8 缺失 run_id 404", c.get("/api/manifest/deadbeef").status_code == 404)
        a6 = c.post("/api/route/decide", json={"signals": {}, "current_tier": "standard"})
        check("A6 路由预演可跑", a6.status_code in (200, 422), f"status={a6.status_code}")
        a9 = c.post("/api/guardrails/scan", json={"text": "手机 13812345678"})
        check("A9 四出口脱敏预演", a9.status_code == 200 and a9.json().get("total_hits", 0) >= 4,
              f"hits={a9.json().get('total_hits') if a9.status_code == 200 else 'NA'}")

        # ---- 错误路径 ----
        bad = c.post("/api/analyze", data={"sample_id": "../../.env"})
        check("A5 样本白名单防穿越", bad.status_code in (400, 422, 415), f"status={bad.status_code}")
        bad2 = c.post("/api/analyze", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
        check("A5 不支持 MIME 415", bad2.status_code == 415, f"status={bad2.status_code}")

        # ---- 响应体不含密钥 ----
        blob = json.dumps([d1, up.json() if up.status_code == 200 else {},
                           cx2.json() if cx2.status_code == 200 else {}], ensure_ascii=False)
        import re
        check("响应体无密钥模式", not re.search(r"sk-[A-Za-z0-9]{20,}", blob))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n=== SUMMARY ===")
    print(json.dumps({"total": len(RESULTS), "passed": passed,
                      "failed": len(RESULTS) - passed,
                      "verdict": "INTEGRATION_READY" if passed == len(RESULTS) else "FAILED"},
                     ensure_ascii=False))
    if passed != len(RESULTS):
        print("失败项:", [n for n, ok, _ in RESULTS if not ok])
        sys.exit(1)


if __name__ == "__main__":
    run()
