"""webapp 端到端自测（标准库 urllib，零额外依赖）。

覆盖：注册登录 / 权限 / 文档上传与繁忙排队 / 分析降级 / 流式问答与断线续传 /
      badcase 采集-筛选-导出-改状态 / admin 完成度关联一致性 /
      分组导航新增能力（概览 / 检索调试 / 评测对象 / 成员 / 审计）。
产出：webapp/out/selftest_report.json（供演示与验收核对）。
"""
from __future__ import annotations

import io
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

CHECKS: list[dict] = []


def record(cid: str, name: str, passed: bool, detail: str) -> None:
    CHECKS.append({"id": cid, "name": name, "passed": bool(passed), "detail": detail})
    print(("PASS " if passed else "FAIL ") + cid + " " + name + " :: " + detail[:110])


def call(method: str, path: str, token: str = "", body=None, raw: bytes | None = None,
         ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = ctype
    req = Request(BASE + path, data=data, method=method, headers=headers)
    try:
        # 放宽到 180s：外部网关（尤其推理模型冷启动）单次可到 40–60s，
        # 40s 会把「慢」误判成「失败」。
        with urlopen(req, timeout=180) as r:
            payload = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), payload
    except HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def jcall(method: str, path: str, token: str = "", body=None):
    st, hd, txt = call(method, path, token, body)
    try:
        return st, hd, json.loads(txt)
    except Exception:
        return st, hd, txt


def upload(token: str, filename: str, content: bytes):
    boundary = "----kb" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    st, hd, txt = call("POST", "/api/docs/upload", token, raw=body,
                       ctype=f"multipart/form-data; boundary={boundary}")
    return st, json.loads(txt) if txt.startswith("{") else txt


def hd_get(headers: dict, key: str) -> str:
    """HTTP 头大小写不敏感（urllib 会规范化为 Content-disposition 等）。"""
    for k, v in headers.items():
        if k.lower() == key.lower():
            return v
    return ""


def parse_sse(text: str) -> tuple[dict, list[dict], dict]:
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
    return meta, tokens, done


def chat(token: str, question: str, resume_from: int = 0, conversation_id: str | None = None):
    st, hd, txt = call("POST", "/api/chat/stream", token,
                       {"question": question, "resume_from": resume_from,
                        "conversation_id": conversation_id})
    return st, parse_sse(txt) if st == 200 else txt


def wait_docs_ready(token: str, ids: list[str], timeout: float = 20) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, docs = jcall("GET", "/api/docs", token)
        wanted = [d for d in docs if d["id"] in ids]
        if wanted and all(d["status"] in ("ready", "error") for d in wanted):
            return wanted
        time.sleep(0.4)
    return []


def main() -> dict:
    t0 = time.time()
    # ---------- 1. 静态前端可达 ----------
    st, _, html = call("GET", "/")
    record("C01", "静态前端首页可访问", st == 200 and "企业级知识库" in html,
           f"status={st}, bytes={len(html)}")

    # ---------- 2. 管理员登录 ----------
    st, _, r = jcall("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"})
    admin = r.get("access_token", "") if isinstance(r, dict) else ""
    record("C02", "admin 登录成功且角色正确", st == 200 and r.get("role") == "admin",
           f"status={st}, role={r.get('role') if isinstance(r, dict) else r}")

    st, _, me = jcall("GET", "/api/auth/me", admin)
    record("C03", "Bearer 鉴权 /auth/me", st == 200 and me.get("role") == "admin",
           f"status={st}, username={me.get('username')}")

    st, _, bad = jcall("POST", "/api/auth/login", body={"username": "admin", "password": "wrong"})
    record("C04", "错误口令被拒绝且提示正确", st == 401 and "错误" in str(bad),
           f"status={st}, detail={bad}")

    # ---------- 3. 注册普通用户 ----------
    uname = "u" + uuid.uuid4().hex[:8]
    st, _, reg = jcall("POST", "/api/auth/register",
                       body={"username": uname, "password": "test1234", "email": "u@demo.local"})
    user_tk = reg.get("access_token", "") if isinstance(reg, dict) else ""
    record("C05", "普通用户注册并直接登录", st == 200 and reg.get("role") == "user" and bool(user_tk),
           f"status={st}, username={uname}")

    # ---------- 4. 评估任务 ----------
    st, _, tasks = jcall("GET", "/api/eval-tasks", admin)
    task_ids = [t["id"] for t in tasks] if isinstance(tasks, list) else []
    record("C06", "评估任务列表可用（badcase 关联基础）", st == 200 and len(task_ids) >= 3,
           f"status={st}, tasks={task_ids}")
    tid = task_ids[0] if task_ids else "ET-001"

    # ---------- 5. 上传 + 繁忙排队 ----------
    st, _, sample = jcall("POST", "/api/docs/seed-sample", admin)
    sample_id = sample.get("id", "") if isinstance(sample, dict) else ""
    files = [
        ("batch-a.txt", "设备配网说明：长按电源键 3 秒开机，进入设置选择 Wi-Fi。\n".encode()),
        ("batch-b.txt", "账号绑定说明：扫码绑定企业账号后可上传产品文档。\n".encode()),
        ("batch-c.txt", "常见故障：回答为空请检查文档是否已分析完成。\n".encode()),
    ]
    up_ids = []
    for fn, content in files:
        _, d = upload(admin, fn, content)
        if isinstance(d, dict) and d.get("id"):
            up_ids.append(d["id"])
    # 安全加固后 /api/system/busy 必须登录（此前无鉴权可探测文件名），故带 admin token
    st, _, busy = jcall("GET", "/api/system/busy", admin)
    record("C07", "批量上传触发繁忙状态与队列", st == 200 and busy.get("busy") is True
           and (busy.get("active_count", 0) > 0 or busy.get("queue_count", 0) > 0),
           f"status={st}, busy={busy.get('busy')}, active={busy.get('active_count')}, queue={busy.get('queue_count')}")
    record("C08", "4 个上传任务全部进入台账", len(up_ids) == 3 and bool(sample_id),
           f"uploaded={len(up_ids)}, sample={bool(sample_id)}")

    # ---------- 6. 分析完成 / 降级 ----------
    ready = wait_docs_ready(admin, up_ids + [sample_id])
    ok_ready = sum(1 for d in ready if d["status"] == "ready" and d["chunk_count"] > 0)
    record("C09", "文档分析落库并生成分块", ok_ready >= 4,
           f"ready={ok_ready}/{len(ready)}, chunks=" + str([d["chunk_count"] for d in ready]))

    _, bad_doc = upload(admin, "unsupported.pdf", b"%PDF-1.4 fake binary")
    if isinstance(bad_doc, dict) and bad_doc.get("id"):
        time.sleep(0.3)
        degraded_docs = wait_docs_ready(admin, [bad_doc["id"]])
        der = degraded_docs[0] if degraded_docs else {}
    else:
        der = {}
    record("C10", "不支持格式给出降级留痕（不静默）",
           der.get("status") == "error" and bool(der.get("error_msg")),
           f"status={der.get('status')}, msg={str(der.get('error_msg'))[:70]}")

    # ---------- 7. 流式问答 / 降级 / 续传 ----------
    st, parsed = chat(admin, "如何给设备配网？")
    meta, tokens, done = parsed
    full_count = len(tokens)
    record("C11", "SSE 流式输出逐 token 且正常收尾",
           st == 200 and full_count > 1 and bool(done) and bool(meta.get("conversation_id")),
           f"status={st}, tokens={full_count}, done={bool(done)}, conv={str(meta.get('conversation_id'))[:8]}")
    conv = meta.get("conversation_id", "")

    # 「检索为空」用随机探针保证词法零命中，与演示库累积的历史文档解耦。
    # 曾用「火星探测器着陆技术观测方法」，但「技术/方法/观测」是通用二元组，
    # 一旦库里有含这些词的大文档就会误召回，用例会假失败（见的不是降级行为本身）。
    probe = "probe-" + uuid.uuid4().hex[:12]
    st2, p2 = chat(admin, probe)
    m2, t2, d2 = p2
    record("C12", "检索为空时主动降级且不编造",
           st2 == 200 and m2.get("degraded") is True and m2.get("degrade_reason") == "retrieval_empty"
           and "没有找到" in d2.get("answer", ""),
           f"probe={probe}, degraded={m2.get('degraded')}, reason={m2.get('degrade_reason')}, "
           f"retrieved={m2.get('retrieved')}")

    # 断线续传：从第 30 个 token 继续。无论回答长短，续传流的索引都应从 30 起且连续——
    # 这是「断线恢复」的本质属性，不因真实模型回答长度变化而失效。
    resume_from = 30
    st3, p3 = chat(admin, "如何给设备配网？", resume_from=resume_from, conversation_id=conv)
    m3, t3, d3 = p3
    contig = (not t3) or (t3[0]["index"] == resume_from
                          and all(t3[i]["index"] == resume_from + i for i in range(len(t3))))
    index_ok = st3 == 200 and m3.get("resume_from") == resume_from and contig
    note = f"first={t3[0]['index'] if t3 else 'NA(空续传)'}, got={len(t3)}, contiguous={contig}"
    record("C13", "断线恢复 resume_from 续传且索引连续",
           index_ok, note)

    # ---------- 8. badcase 采集闭环 ----------
    q_text = "如何绑定企业账号？"
    st4, p4 = chat(user_tk, q_text)
    _, tk4, d4 = p4
    answer_text = d4.get("answer", "")
    payload = {
        "eval_task_id": tid, "question": q_text, "answer": answer_text,
        "verdict": "部分正确", "issue_desc": "引用缺失，未给出具体章节",
        "source": "chat",
    }
    st, _, bc = jcall("POST", "/api/badcases", user_tk, payload)
    bcid = bc.get("id", "") if isinstance(bc, dict) else ""
    required = ["question", "answer", "verdict", "issue_desc", "source", "created_at", "eval_task_id", "status"]
    fields_ok = isinstance(bc, dict) and all(k in bc and bc[k] not in (None, "") for k in required)
    record("C14", "badcase 字段完整（问题/回答/判定/说明/来源/时间/任务/状态）",
           st == 201 and fields_ok and bc.get("eval_task_id") == tid,
           f"status={st}, id={bcid[:8]}, task={bc.get('eval_task_id') if isinstance(bc, dict) else 'NA'}")

    st, _, bad_assoc = jcall("POST", "/api/badcases", user_tk,
                             {"eval_task_id": "ET-NOT-EXIST", "question": "x"})
    record("C15", "关联不存在的评估任务被拒绝（一致性）", st == 400,
           f"status={st}, detail={bad_assoc}")

    # 非法 eval_task 不能污染统计：先确认完成度只统计合法记录
    st, _, comp0 = jcall("GET", "/api/admin/completion", admin)
    t0row = next((t for t in comp0.get("tasks", []) if t["eval_task_id"] == tid), {})
    record("C16", "完成度与该任务关联 badcase 数一致",
           st == 200 and t0row.get("completed", -1) >= 1
           # pending 与完成率在服务端口径上都被钳制（累积记录可超过目标数）
           and t0row.get("pending") == max(0, t0row.get("target_count") - t0row.get("completed"))
           and 0 <= t0row.get("completion_rate", -1) <= 1,
           f"target={t0row.get('target_count')}, completed={t0row.get('completed')}, "
           f"pending={t0row.get('pending')}, rate={t0row.get('completion_rate')}")

    # ---------- 9. 筛选 / 检索 / 导出 ----------
    st, _, filtered = jcall("GET", "/api/badcases?eval_task_id=" + tid + "&status=pending", admin)
    record("C17", "按评估任务+状态筛选", st == 200 and any(x["id"] == bcid for x in filtered),
           f"status={st}, rows={len(filtered) if isinstance(filtered, list) else 'NA'}")

    st, _, searched = jcall("GET", "/api/badcases?q=" +
                            __import__("urllib.parse", fromlist=["quote"]).quote("引用缺失"), admin)
    record("C18", "关键字检索命中问题说明", st == 200 and any(x["id"] == bcid for x in searched),
           f"status={st}, rows={len(searched) if isinstance(searched, list) else 'NA'}")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    st, _, inrange = jcall("GET", f"/api/badcases?start={today}&end={today}", admin)
    record("C19", "按时间范围筛选", st == 200 and any(x["id"] == bcid for x in inrange),
           f"status={st}, rows={len(inrange) if isinstance(inrange, list) else 'NA'}")

    st, hd, csv_text = call("GET", f"/api/badcases/export?format=csv&eval_task_id={tid}", admin)
    record("C20", "按筛选条件下载 CSV",
           st == 200 and "attachment" in hd_get(hd, "Content-Disposition")
           and "question" in csv_text and q_text in csv_text,
           f"status={st}, disposition={hd_get(hd, 'Content-Disposition')[:40]}, bytes={len(csv_text)}")

    st, hd, json_text = call("GET", "/api/badcases/export?format=json", admin)
    try:
        exported = json.loads(json_text)
    except Exception:
        exported = []
    record("C21", "导出 JSON 且与库内记录一致",
           st == 200 and isinstance(exported, list) and any(x["id"] == bcid for x in exported),
           f"status={st}, rows={len(exported)}")

    # ---------- 10. 状态更新 ----------
    st, _, updated = jcall("PATCH", "/api/badcases/" + bcid, admin,
                           {"status": "resolved", "verdict": "错误", "issue_desc": "已确认引用缺失"})
    record("C22", "admin 可更新判定与状态", st == 200 and updated.get("status") == "resolved",
           f"status={st}, new_status={updated.get('status') if isinstance(updated, dict) else 'NA'}")

    st, _, after = jcall("GET", "/api/admin/completion", admin)
    arow = next((t for t in after.get("tasks", []) if t["eval_task_id"] == tid), {})
    record("C23", "状态改后完成度保持稳定关联", st == 200 and arow.get("completed") == t0row.get("completed"),
           f"completed={arow.get('completed')} (before={t0row.get('completed')})")

    # ---------- 11. 权限门禁 ----------
    st, _, _ = jcall("GET", "/api/badcases", user_tk)
    st2, _, _ = jcall("GET", "/api/admin/completion", user_tk)
    st3, _, _ = jcall("GET", "/api/badcases/export?format=csv", user_tk)
    record("C24", "普通用户不可查看/导出/看完成度（admin 专属）",
           st == 403 and st2 == 403 and st3 == 403,
           f"list={st}, completion={st2}, export={st3}")
    st4, _, can_create = jcall("POST", "/api/badcases", user_tk,
                               {"eval_task_id": tid, "question": "普通用户也能采集", "source": "manual"})
    record("C25", "普通用户可采集 badcase（采集与查看权限分离）", st4 == 201,
           f"status={st4}, id={str(can_create.get('id', ''))[:8]}")

    # ---------- 12. 断线/离线降级（业务侧） ----------
    # 同 C07：安全加固后该端点必须登录，带 admin token 复验「排空后回到空闲」
    st, _, idle = jcall("GET", "/api/system/busy", admin)
    record("C26", "队列排空后回到空闲（繁忙提示会自动消失）",
           st == 200 and idle.get("busy") is False,
           f"busy={idle.get('busy')}, msg={idle.get('message')}")

    # ---------- 13. 分组导航新增能力 ----------
    st, _, ov = jcall("GET", "/api/overview", admin)
    st2, _, ov_u = jcall("GET", "/api/overview", user_tk)
    fields_ok = isinstance(ov, dict) and all(
        k in ov for k in ("docs", "chunks", "chat", "badcases", "system", "admin")
    )
    record("C27", "工作台概览字段完整且按角色分级",
           st == 200 and st2 == 200 and fields_ok and isinstance(ov.get("admin"), dict)
           and ov_u.get("admin") is None and ov_u.get("scope") == "仅本人数据",
           f"scope={ov.get('scope')} / {ov_u.get('scope')}, admin_block={bool(ov.get('admin'))}")
    record("C28", "概览统计范围权限分离（不查而非隐藏）",
           isinstance(ov_u, dict) and ov_u["docs"]["total"] <= ov["docs"]["total"]
           and ov_u["chat"]["conversations"] <= ov["chat"]["conversations"],
           f"docs {ov_u['docs']['total']}<={ov['docs']['total']}, "
           f"chat {ov_u['chat']['conversations']}<={ov['chat']['conversations']}")

    st, _, sp = jcall("GET", "/api/search/preview?q=" + quote("配网"), admin)
    st2, _, sp0 = jcall("GET", "/api/search/preview?q=zzqqxx-nonexistent-9987", admin)
    record("C29", "检索调试：命中与降级判定可复现",
           st == 200 and st2 == 200 and sp.get("hit_count", 0) > 0
           and sp0.get("hit_count") == 0 and sp0.get("degraded") is True
           and sp0.get("degrade_reason") == "retrieval_empty",
           f"hit={sp.get('hit_count')}, empty_hit={sp0.get('hit_count')}, degraded={sp0.get('degraded')}")

    st, _, objs = jcall("GET", "/api/eval/objects", admin)
    record("C30", "评测中心对象清单与 eval/ 事实源一致",
           st == 200 and objs.get("count") == 14
           and objs.get("registry_id") == "REG-KBQA-EVAL"
           and objs.get("comparable") is False
           and objs.get("quality_verdict") == "NOT_COMPARABLE",
           f"count={objs.get('count')}, registry={objs.get('registry_id')}, "
           f"verdict={objs.get('quality_verdict')}")

    st, _, users = jcall("GET", "/api/admin/users", admin)
    st2, _, _ = jcall("GET", "/api/admin/users", user_tk)
    record("C31", "成员列表 admin 可见、普通用户 403",
           st == 200 and isinstance(users, list) and len(users) >= 1
           and all("role" in u and "doc_count" in u for u in users) and st2 == 403,
           f"users={len(users) if isinstance(users, list) else users}, user_status={st2}")

    st, _, logs = jcall("GET", "/api/admin/audit?action=login", admin)
    st2, _, _ = jcall("GET", "/api/admin/audit", user_tk)
    record("C32", "审计台账可按动作筛选、普通用户 403",
           st == 200 and isinstance(logs, list) and len(logs) >= 1
           and all(x["action"] == "login" and x["actor"] and x["created_at"] for x in logs)
           and st2 == 403,
           f"login_rows={len(logs) if isinstance(logs, list) else logs}, user_status={st2}")

    st, _, html2 = call("GET", "/")
    st2, _, js = call("GET", "/app.js?v=8")
    nav_views = ["home", "chat", "docs", "search", "tasks", "eval", "admin", "audit",
                 "settings", "system"]
    nav_ok = sum(('data-view="' + v + '"') in html2 and ('id="view-' + v + '"') in html2
                 for v in nav_views)
    loaders_ok = sum(('function load' + n) in js
                     for n in ["Overview", "Tasks", "Eval", "Audit", "Settings", "System"])
    record("C33", "侧栏十视图与加载器双向齐全（含模型设置）",
           st == 200 and st2 == 200 and nav_ok == 10 and loaders_ok == 6,
           f"nav={nav_ok}/10, loaders={loaders_ok}/6")

    passed = sum(1 for c in CHECKS if c["passed"])
    report = {
        "suite": "webapp-e2e",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_seconds": round(time.time() - t0, 2),
        "base_url": BASE,
        "summary": {
            "total": len(CHECKS), "passed": passed, "failed": len(CHECKS) - passed,
            "verdict": "DEMO_READY" if passed == len(CHECKS) else "NEEDS_FIX",
        },
        "checks": CHECKS,
    }
    (OUT / "selftest_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== SUMMARY ===")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print("report:", OUT / "selftest_report.json")
    return report


if __name__ == "__main__":
    main()
