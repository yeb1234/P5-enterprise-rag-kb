"""showcase 10 个 `/api/*` 路由（A1–A10）。

唯一事实源：docs/showcase/03-phase2-spec.md §2 / §4 + 02-architecture.md §4。
三态：已实现（本轮站点接线）；LLM 在线真值与收费适配器 = 只设计。

设计取舍（写进注释，便于面试讲解）：
1. 同伴模块（llm_router / snapshot / ingest_preview / guard）一律**函数内延迟 import**。
   理由：并行开发期间同伴文件可能尚未落盘；延迟 import 让 A1/A2/A3/A4/A10
   这 5 个不依赖同伴的路由可以独立启动与自测，同时**没有创建任何占位模块**，
   也没有改动同伴文件。import 的符号逐字遵守 spec §2 的签名。
2. `parsing/` 是只读纯函数包（依赖方向 showcase/ → parsing/），可以顶层 import。
3. 本轮**一律不发起外部 LLM / OCR 请求**：`ParseConfig(offline=True)` 恒定，
   `runtime.external_request_count` 恒为 0；强制收费档不静默失败，而是显式留痕 + 降级。
4. 挂钟时间只出现在 `runtime.wall_clock` / `runtime.elapsed_ms`，绝不进 `ingest`；
   `created_at` 取 `params.as_of`（固定逻辑时间），保证同参双跑逐字节一致（V5）。
5. 密钥值不进任何响应 / 日志 / JSON；A1 只回存在性布尔。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from parsing.config import ParseConfig, current_strategy
from parsing.router import ParseRouter
from showcase import MAX_BYTES, SCHEMA_VERSION_INGEST, SHOWCASE_VERSION
from showcase.adapters import vendor_profiles
from showcase.params import (
    ALLOWED_INGEST_FORMATS,
    PARAM_KEYS,
    PARAM_RANGES,
    Params,
    parse_params,
)

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
SAMPLES_DIR = HERE / "samples"
REPORT_COMPLEX = SAMPLES_DIR / "complex.report.json"

# A5 无 file 时的 sample_id 白名单：硬编码，禁止用用户串拼路径（防目录穿越）
SAMPLE_WHITELIST = frozenset({"simple.pdf", "complex.png"})

# 集成裁决 §1.1(7)：按 (source_sha256, params_hash) 派生的进程内有限 LRU
_TIER_STATE_MAX = 32
_TIER_STATE_LRU: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
_TIER_LOCK = threading.Lock()

# 02-architecture.md §4.2 A5 支持 MIME（与 parsing/router.py::split_pages 对齐）
ALLOWED_MIMES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "image/bmp",
        "text/plain",
    }
)
_EXT_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".txt": "text/plain",
}
_RUN_ID_RE = re.compile(r"^[0-9a-f]{20}$")

# 02-architecture.md §4.3 错误码 → HTTP
ERROR_STATUS: dict[str, int] = {
    "INVALID_PARAM": 422,
    "FILE_TOO_LARGE": 413,
    "UNSUPPORTED_MIME": 415,
    "STRATEGY_UNAVAILABLE": 503,
    "UPSTREAM_TIMEOUT": 504,
    "UPSTREAM_ERROR": 502,
    "PII_BLOCKED": 422,
    "OFFLINE_ONLY": 503,
    "RUN_NOT_FOUND": 404,
    "INTERNAL_ERROR": 500,
}

router = APIRouter(prefix="/api")


class ApiError(Exception):
    """统一错误契约：{"code","message","trace_id"}，由 server.py 的 handler 渲染。

    `extra` 携带结构化补充（如 A8 的 `checks`），随响应体平铺；
    约定不使用 `code` / `message` / `trace_id` 三个保留键，避免覆盖三要素。
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status: int | None = None,
        trace_id: str | None = None,
        extra: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.status = status or ERROR_STATUS.get(code, 500)
        self.trace_id = trace_id or uuid.uuid4().hex[:16]
        self.extra = {
            k: v for k, v in (extra or {}).items()
            if k not in ("code", "message", "trace_id")
        }
        super().__init__(self.message)


def _fail(code: str, message: str, *, extra: dict | None = None) -> None:
    """抛统一错误。D-14：上游 `parse_params` 已带 "INVALID_PARAM:" 前缀时不再重复叠加。"""
    msg = message or code
    prefix = f"{code}:"
    if msg.startswith(prefix):
        msg = msg[len(prefix):].strip()
    raise ApiError(code, msg, extra=extra)


# ---------------------------------------------------------------------------
# 进程内参数（A2 / A3）；重启回落默认，不落盘
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_CURRENT_PARAMS = Params()
# A2 响应里的展示字段，A3 允许前端整包回传
_DISPLAY_KEYS = frozenset({"params", "params_hash", "ranges", "note"})


def _current_params() -> Params:
    with _LOCK:
        return _CURRENT_PARAMS


def _set_params(p: Params) -> None:
    global _CURRENT_PARAMS
    with _LOCK:
        _CURRENT_PARAMS = p


def _offline() -> bool:
    """本轮演示恒定离线（spec §1.1(1)：一律不发起外部 LLM 调用）。

    `PARSING_OFFLINE=0` 不会让站点发起外部请求：实时档属「只设计」，
    强制收费档会被 A5 显式留痕并降级，见 `_resolve_strategy()`。
    """
    return True


def _env_present(name: str) -> bool:
    """只检查环境变量**是否存在**，不读取、不返回、不记录它的值。"""
    return bool(os.environ.get(name))


def _resolve_mime(filename: str, content_type: str | None) -> str:
    mime = (content_type or "").split(";")[0].strip().lower()
    if not mime:
        mime = _EXT_MIME.get(Path(filename or "").suffix.lower(), "")
    return mime


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail("INTERNAL_ERROR", f"读取 {path.name} 失败：{type(exc).__name__}")


def _run_path(run_id: str) -> Path:
    # run_id 严格 20 位小写十六进制，杜绝 ../ 路径穿越
    if not _RUN_ID_RE.match(run_id or ""):
        _fail("RUN_NOT_FOUND", "run_id 非法或不存在，请重跑 A5")
    return OUT_DIR / f"{run_id}.ingest.json"


def _load_sample(sample_id: str) -> tuple[bytes, str]:
    """A5 无 file 时的 sample_id 兜底：白名单硬编码，绝不用用户串拼路径。

    返回 (payload, filename)；mime 由调用方按 filename 扩展名推断。
    """
    if sample_id not in SAMPLE_WHITELIST:
        _fail("INVALID_PARAM",
              f"sample_id 不在白名单 {sorted(SAMPLE_WHITELIST)}；不接收任意路径")
    path = SAMPLES_DIR / sample_id  # 只可能是白名单内的固定文件名
    if not path.is_file():
        _fail("RUN_NOT_FOUND", f"样本文件缺失：{sample_id}")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        _fail("FILE_TOO_LARGE", f"样本 {sample_id} 超过上限 {MAX_BYTES} 字节")
    return data, sample_id


def _is_replay(replay: str | None, filename: str) -> bool:
    """复杂档回放判定：显式 replay 标记，或样本/文件名以 complex.png 结尾。

    抽成纯函数便于单测：`sample_id=complex.png` 时 filename 就是该白名单值，
    因此无需额外分支即可自然触发回放标注（集成裁决 · 改动 2）。
    """
    flag = str(replay or "").strip().lower() in ("1", "true", "yes", "on")
    return flag or filename.lower().endswith("complex.png")


def decide_with_lru(source_sha: str, params_hash: str, signals: Any, *,
                    override: str | None, as_of: str) -> dict:
    """集成裁决 §1.1(7)：A5 复用按 `(source_sha, params_hash)` 隔离的 TierState。

    - **保留 k=2 与双阈值不变**（滞回防抖动是专业卖点，不降 k、不加首次直通）；
    - 同一份文件 + 同一组参数连续两次 A5 复用同一状态 → 第 2 次真实升 `pro`；
    - 不同文件 / 不同参数 → 不同 key，**不串用户状态**；
    - ≤32 条 LRU：`move_to_end` 提活、超容量 `popitem(last=False)` 淘汰最旧；
    - 进程重启清零，属演示切片的**可丢弃状态**（三态分类）；
    - 两次 `snapshot_version` 不同 → `run_id` 不同，「改档必变」是正确行为。
    锁同时覆盖 LRU 结构与 `state.decide()`，避免并发请求拿到同一状态后竞态。
    """
    from showcase.llm_router import TierState  # 延迟 import，同伴未齐不阻塞其它路由

    key = (source_sha, params_hash)
    with _TIER_LOCK:
        state = _TIER_STATE_LRU.get(key)
        if state is None:
            state = TierState(tier="standard")
            _TIER_STATE_LRU[key] = state
        else:
            _TIER_STATE_LRU.move_to_end(key)
        while len(_TIER_STATE_LRU) > _TIER_STATE_MAX:
            _TIER_STATE_LRU.popitem(last=False)
        snap = state.decide(signals, override=override, as_of=as_of,
                            offline=True, event=None)
        return snap.as_dict()


# ---------------------------------------------------------------------------
# A1 health
# ---------------------------------------------------------------------------
@router.get("/health")
def health() -> dict:
    """只回 external_configured 布尔，绝不回密钥值（02-architecture.md §4.2 / §7）。"""
    st = ParseRouter().status()
    levels_available = {
        str(lv.get("level")): bool(lv.get("available")) for lv in st.get("levels", [])
    }
    return {
        "status": "ok",
        "service": "showcase",
        "version": SHOWCASE_VERSION,
        "offline": _offline(),
        "strategy": current_strategy(),
        "levels_available": levels_available,
        "external_configured": {
            "discovery": _env_present("DISCOVERY_API_KEY"),
            "ocr_api": _env_present("PARSING_OCR_API_KEY"),
        },
        "note": "只回布尔，不回密钥值",
    }


# ---------------------------------------------------------------------------
# A2 / A3 params
# ---------------------------------------------------------------------------
@router.get("/params")
def get_params() -> dict:
    """7 类参数当前值 + 取值范围 + params_hash（稳定快照哈希）。"""
    p = _current_params()
    return {
        "params": p.as_dict(),
        "params_hash": p.params_hash,
        "ranges": PARAM_RANGES,
        "note": "进程内生效，重启回落默认；数值为演示起始值，非压测结论",
    }


@router.put("/params")
def put_params(body: dict) -> dict:
    """修改参数（进程内生效），返回 changed[] 与新哈希。越界仍走 parse_params 校验。"""
    if not isinstance(body, dict):
        _fail("INVALID_PARAM", "请求体必须是 JSON 对象")
    unknown = [k for k in body if k not in PARAM_KEYS and k not in _DISPLAY_KEYS]
    if unknown:
        _fail("INVALID_PARAM", f"未知参数：{', '.join(sorted(map(str, unknown)))}")
    payload = body.get("params") if isinstance(body.get("params"), dict) else body
    subset = {k: v for k, v in payload.items() if k in PARAM_KEYS}
    cur = _current_params().as_dict()
    try:
        p = parse_params({**cur, **subset})
    except ValueError as exc:
        _fail("INVALID_PARAM", str(exc))
    new = p.as_dict()
    changed = [k for k in subset if cur.get(k) != new.get(k)]
    _set_params(p)
    return {
        "params": new,
        "params_hash": p.params_hash,
        "changed": changed,
        "note": "进程内生效，重启回落默认",
    }


# ---------------------------------------------------------------------------
# A4 strategies
# ---------------------------------------------------------------------------
@router.get("/strategies")
def strategies() -> dict:
    """复用 ParseRouter.status()；未接通档显式显示「未接通，跳过」，不静默。"""
    st = ParseRouter(cfg=ParseConfig(offline=True), strategy=_current_params().strategy).status()
    levels: list[dict] = []
    for lv in st.get("levels", []):
        item = dict(lv)
        if not item.get("available"):
            item["display"] = "未接通，跳过"
            reason = str(item.get("reason") or "").strip()
            item["reason"] = (reason + "；未接通，跳过").strip("；")
        levels.append(item)
    # vendors 来自静态 profile，available 全 False、contract 保持 __TBC__
    vendors = [
        {k: v for k, v in vp.items()
         if k in ("id", "region", "price_hint", "available", "contract", "note")}
        for vp in vendor_profiles()
    ]
    return {
        "strategies": st.get("strategies", []),
        "levels": levels,
        "vendors": vendors,
        "budget": st.get("budget", {}),
        "thresholds": st.get("thresholds", {}),
        "vision_models_probe": st.get("vision_models_probe", {}),
        "offline": _offline(),
        "note": "收费档契约字段保持 __TBC__；未接通一律显示『未接通，跳过』并降级，不静默",
    }


def _resolve_strategy(
    p: Params, st: dict, warnings: list[str], errors: list[dict]
) -> str:
    """强制收费档 / 实时视觉档：本轮不静默失败，留痕后降级为 auto（离线）。"""
    chosen = p.strategy
    if chosen == "ocr_api":
        available = any(
            lv.get("level") == "ocr_api" and lv.get("available")
            for lv in st.get("levels", [])
        )
        if not available:
            msg = "ocr_api 未接通，跳过；已降级为 auto（离线 P0 + 结构化降级），不发起外部请求"
            warnings.append(msg)
            errors.append({"code": "STRATEGY_UNAVAILABLE", "message": msg})
            return "auto"
    if chosen == "multimodal":
        msg = "OFFLINE_ONLY：实时在线视觉调用本轮未接通，已降级为 auto；视觉结果为离线回放，非实时在线识别"
        warnings.append(msg)
        errors.append({"code": "OFFLINE_ONLY", "message": msg})
        return "auto"
    return chosen


# ---------------------------------------------------------------------------
# A5 analyze（核心）
# ---------------------------------------------------------------------------
@router.post("/analyze")
async def analyze(
    file: UploadFile | None = File(None),
    params: str | None = Form(None),
    as_of: str | None = Form(None),
    replay: str | None = Form(None),
    sample_id: str | None = Form(None),
) -> dict:
    """一次 ParseRouter.parse_document() → 同一份 rep 交给 build_views() 组装左右两区。

    两种输入互斥：优先 `file`（multipart 上传）；无 file 时接受白名单 `sample_id`
    读已落盘真实样本字节（前端两档一键切换的兼容分支）。
    硬约束：external_request_count=0、created_at 取 as_of、runtime.wall_clock 不进 ingest。
    """
    started = time.perf_counter()
    if file is not None:
        data = await file.read(MAX_BYTES + 1)
        filename = file.filename or "unnamed"
        content_type = file.content_type
    elif sample_id is not None:
        # 白名单硬编码兜底，禁止用用户串拼路径。filename 直接等于 sample_id，
        # 因此 complex.png 会自然命中下面 is_replay 的回放标注。
        data, filename = _load_sample(sample_id)
        content_type = None
    else:
        _fail("INVALID_PARAM",
              f"必须提供 file 或 sample_id（白名单 {sorted(SAMPLE_WHITELIST)}）")
    if len(data) > MAX_BYTES:
        _fail("FILE_TOO_LARGE", f"文件 {len(data)} 字节超过上限 {MAX_BYTES} 字节（8 MiB）")
    if not data:
        _fail("INVALID_PARAM", "上传文件为空")

    mime = _resolve_mime(filename, content_type)
    if mime not in ALLOWED_MIMES:
        _fail("UNSUPPORTED_MIME", f"不支持 {mime or '未知'}；支持 {sorted(ALLOWED_MIMES)}")

    # params 是可选 JSON 字符串；as_of 单独 form 字段，合并后一次校验
    try:
        raw: dict = json.loads(params) if params else {}
    except ValueError:
        # D-14：不回显 JSON 解析异常文本（含行列位置等内部细节），只给概要
        _fail("INVALID_PARAM", "params 不是合法 JSON 对象")
    if not isinstance(raw, dict):
        _fail("INVALID_PARAM", "params 必须是 JSON 对象")
    if as_of:
        raw = {**raw, "as_of": as_of}
    try:
        p = parse_params(raw)
    except ValueError as exc:
        _fail("INVALID_PARAM", str(exc))

    warnings: list[str] = []
    errors: list[dict] = []
    cfg = ParseConfig(
        confidence_min=p.confidence_min,
        vision_escalation_ratio=p.vision_escalation_ratio,
        vision_min_slots=p.vision_min_slots,
        offline=True,  # 本轮硬保证：0 外部请求，不读 .env 的密钥值
    )
    parser = ParseRouter(cfg=cfg, strategy=p.strategy)
    st = parser.status()
    chosen = _resolve_strategy(p, st, warnings, errors)
    if chosen != p.strategy:
        parser = ParseRouter(cfg=cfg, strategy=chosen)

    source_sha = hashlib.sha256(data).hexdigest()
    doc_id = f"SHOWCASE-{source_sha[:12]}"
    # ★ 全链路只调用一次 parse_document；rep 同时供 route 决策与 build_views
    rep = parser.parse_document(data, filename=filename, mime=mime, doc_id=doc_id)
    rep = dict(rep)
    rep["strategy"] = chosen

    # 延迟 import 同伴（签名逐字遵守 spec §2），未落盘时只在集成阶段报错
    from showcase.guard import protect_text
    from showcase.ingest_preview import build_views
    from showcase.llm_router import from_parse_report
    from showcase.snapshot import write_pair

    # 四出口脱敏：同一段文本依次 ingest/context/output/audit；最终文本只取第一次
    full_text = "\n".join(str(r.get("text") or "") for r in rep.get("results", []))
    _protected_text, pii = protect_text(full_text, p.redact_enabled)
    report = pii.get("report") or {}
    if report.get("blocked"):
        _fail("PII_BLOCKED", "guardrails 命中 ACTION_BLOCK，已拒绝；不回显原文")

    # 路由决策与「是否真的调用 LLM」解耦：这里只算快照，不发起任何外部调用。
    # 集成裁决 §1.1(7)：从 (source_sha, params_hash) LRU 取同一 TierState，
    # 保留 k=2 与双阈值；同一份复杂档连续两次 A5 → 第 2 次真实升 pro(hits=2)。
    signals = from_parse_report(rep)
    route = decide_with_lru(
        source_sha, p.params_hash, signals,
        override=p.llm_tier_override, as_of=p.as_of,
    )

    # run_id = sha256(source + params_hash + route_snapshot_version)[:20]，不含挂钟时间
    snapshot_version = str(route.get("snapshot_version") or "")
    seed = f"{source_sha}:{p.params_hash}:{snapshot_version}"
    run_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

    analysis, ingest = build_views(
        data,
        filename=filename,
        mime=mime,
        params=p,          # spec §2 docstring：created_at 取 params.as_of → 传对象
        rep=rep,
        route=route,
        pii=pii,
        run_id=run_id,
    )

    # 复杂档回放标注（spec §1.1(2)）：analysis.warnings[] 与 ingest.note 都要出现「回放」。
    # filename 在 sample_id 分支下就是白名单值，complex.png 自然命中。
    is_replay = _is_replay(replay, filename)
    replay_note = (
        "视觉结果为离线回放，非实时在线识别（来源 showcase/samples/complex.report.json）"
    )
    if is_replay:
        if not REPORT_COMPLEX.is_file():
            _fail("RUN_NOT_FOUND", "复杂档回放产物 complex.report.json 缺失")
        warnings.append(replay_note)
        analysis.setdefault("warnings", [])
        if replay_note not in analysis["warnings"]:
            analysis["warnings"].append(replay_note)
        ingest["note"] = (str(ingest.get("note") or "") + " ｜ " + replay_note).strip(" ｜")
    for w in warnings:
        analysis.setdefault("warnings", [])
        if w not in analysis["warnings"]:
            analysis["warnings"].append(w)

    # 硬约束：created_at 取 as_of；挂钟时间绝不进 ingest
    ingest["created_at"] = p.as_of

    # 安全加固（defense in depth）：落盘前对最终入库 JSON 再扫一遍密钥形态，
    # 命中 API_KEY 一律阻断、**不写任何文件**（防止检测漏检把原文带进产物）。
    from guardrails.detectors import scan as _pii_scan

    try:
        ingest_blob = json.dumps(ingest, ensure_ascii=False)
    except (TypeError, ValueError):
        _fail("INTERNAL_ERROR", "入库 JSON 序列化失败，已阻断落盘")
    if any(f.kind == "API_KEY" for f in _pii_scan(ingest_blob)):
        _fail("PII_BLOCKED", "入库内容疑似含密钥，已阻断落盘")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = write_pair(run_id, route, ingest, OUT_DIR)
    manifest_ref = {
        "path": str(written.get("path", "")),
        "sha256": str(written.get("sha256", "")),
        "snapshot_version": str(written.get("snapshot_version", "")),
    }

    runtime = {
        # 挂钟时间只在这里，不进 ingest / manifest
        "wall_clock": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "external_request_count": 0,
    }
    return {
        "run_id": run_id,
        "schema_version": SCHEMA_VERSION_INGEST,
        "analysis": analysis,
        "ingest": ingest,
        "manifest_ref": manifest_ref,
        "runtime": runtime,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# A6 route/decide（只做路由预演，不解析文件、不落盘）
# ---------------------------------------------------------------------------
@router.post("/route/decide")
def route_decide(body: dict) -> dict:
    from showcase.llm_router import DifficultySignals, TierState, difficulty_score

    if not isinstance(body, dict):
        _fail("INVALID_PARAM", "请求体必须是 JSON 对象")
    raw = body.get("signals")
    if not isinstance(raw, dict):
        _fail("INVALID_PARAM", "signals 必须是对象")
    try:
        sig = DifficultySignals(
            page_count=int(raw.get("page_count", 0)),
            no_text_layer_ratio=float(raw.get("no_text_layer_ratio", 0.0)),
            table_cells_total=int(raw.get("table_cells_total", 0)),
            min_confidence=float(raw.get("min_confidence", 1.0)),
            ocr_needed=bool(raw.get("ocr_needed", False)),
            budget_remaining_ratio=float(raw.get("budget_remaining_ratio", 1.0)),
            raw=dict(raw.get("raw") or raw),
            notes=tuple(raw.get("notes") or ()),
        )
    except (TypeError, ValueError) as exc:
        _fail("INVALID_PARAM", f"signals 字段非法：{exc}")

    score = difficulty_score(sig)
    p = _current_params()
    override = body.get("override", p.llm_tier_override)
    as_of = str(body.get("as_of") or p.as_of)
    current_tier = str(body.get("current_tier") or "standard")
    try:
        parse_params({"as_of": as_of})
    except ValueError as exc:
        _fail("INVALID_PARAM", str(exc))
    # 同伴已落盘：TierState.__init__(tier="standard")，故把 current_tier 传进构造器。
    # A6 请求契约只有 signals / current_tier，不含 event → 固定 event=None，
    # 避免把任意字符串传给同伴的 FALLBACK_EVENTS 白名单而误触发 500。
    # current_tier / override 非法时同伴抛 ValueError("INVALID_PARAM: ...")，统一转 422。
    try:
        snap = TierState(tier=current_tier).decide(
            sig, override=override, as_of=as_of, offline=True, event=None
        )
    except ValueError as exc:
        _fail("INVALID_PARAM", str(exc))
    route = snap.as_dict()
    hyst = dict(route.get("hysteresis") or {})
    hyst["state"] = str(body.get("current_tier") or hyst.get("state") or "standard")
    return {
        "route": route,
        "hysteresis": hyst,
        "reason": str(route.get("reason") or ""),
        "difficulty": score,
        "note": "仅路由决策，未发起外部调用；model/params 等为空占位",
    }


# ---------------------------------------------------------------------------
# A7 ingest/preview
# ---------------------------------------------------------------------------
def _compact_ingest(ingest: dict) -> dict:
    """02-architecture.md §5.4 compact 档字段裁剪。

    保留 `note` 是刻意的：spec §1.1(2) 要求复杂档 `ingest.note` 必须出现「回放」，
    精简档也不能把这条边界说明裁掉。
    """
    out: dict[str, Any] = {
        k: ingest[k]
        for k in ("schema_version", "run_id", "created_at", "doc_id", "source", "note")
        if k in ingest
    }
    parser = ingest.get("parser") or {}
    out["parser"] = {k: parser[k] for k in ("strategy", "levels") if k in parser}
    route = ingest.get("llm_route") or {}
    out["llm_route"] = {
        k: route[k]
        for k in ("route_id", "tier", "model", "snapshot_version", "placeholder")
        if k in route
    }
    out["chunks"] = [
        {k: c[k] for k in ("chunk_id", "page_no", "block_type", "text", "section_path")
         if k in c}
        for c in ingest.get("chunks", [])
    ]
    out["trace"] = [
        {k: t[k] for k in ("page_no", "level", "reason") if k in t}
        for t in ingest.get("trace", [])
    ]
    return out


@router.get("/ingest/preview")
def ingest_preview(run_id: str = "", format: str = "compact") -> dict:
    path = _run_path(run_id)
    if format not in ALLOWED_INGEST_FORMATS:
        _fail("INVALID_PARAM", f"format 必须是 {ALLOWED_INGEST_FORMATS}")
    if not path.is_file():
        _fail("RUN_NOT_FOUND", f"run_id={run_id} 的入库 JSON 不存在，请重跑 A5")
    ingest = _read_json(path)
    return _compact_ingest(ingest) if format == "compact" else ingest


# ---------------------------------------------------------------------------
# A8 manifest/{run_id}
# ---------------------------------------------------------------------------
@router.get("/manifest/{run_id}")
def get_manifest(run_id: str) -> dict:
    from showcase.snapshot import ManifestError, read_manifest

    ingest_path = _run_path(run_id)
    if not (OUT_DIR / f"{run_id}.manifest.json").is_file():
        _fail("RUN_NOT_FOUND", f"manifest 不存在：{run_id}，请重跑 A5")
    try:
        manifest = read_manifest(run_id, OUT_DIR)
    except ManifestError as exc:
        # D-04：摘要不符 → 404 RUN_NOT_FOUND（与 README / 02-architecture §4.3 对齐），不静默回放。
        # D-03：失败响应也带 checks，明确「三项都无法确认通过」，而不是只在成功时出现恒 True。
        _fail(
            "RUN_NOT_FOUND",
            f"manifest 校验失败，拒绝静默回放：{str(exc)[:200]}",
            extra={
                "checks": {
                    "ingest_sha256_match": False,
                    "route_snapshot_match": False,
                    "manifest_sha256_match": False,
                },
                "checks_note": (
                    "read_manifest 抛 ManifestError：三项规范化重算至少一项未通过，"
                    "失败即拒绝回放"
                ),
            },
        )
    if not isinstance(manifest, dict):
        _fail("INTERNAL_ERROR", "read_manifest 返回结构异常")

    # read_manifest()（spec §2 公开契约）内部已完成三项确定性重算：
    #   ① ingest 规范化投影摘要 == manifest.ingest_ref.sha256
    #   ② manifest 规范化摘要  == ingest.manifest.sha256
    #   ③ snapshot_version_for(route) == route.snapshot_version
    # 任一不符即抛 ManifestError。因此 A8 **不重复实现**同伴的私有投影/哈希算法，
    # 否则两套规则不一致会让 A8 恒 500（假阴性）。成功返回即代表三项都通过。
    ingest = _read_json(ingest_path) if ingest_path.is_file() else {}
    expected_sv = str((manifest.get("route") or {}).get("snapshot_version") or "")
    actual_sv = str((ingest.get("llm_route") or {}).get("snapshot_version") or "")
    checks = {
        "ingest_sha256_match": True,
        "route_snapshot_match": True,
        "manifest_sha256_match": True,
    }
    if expected_sv and actual_sv:
        # 两侧字段齐全时额外做一次字段级交叉核对（不依赖哈希算法，不脆弱）
        checks["route_snapshot_match"] = expected_sv == actual_sv
    if not all(checks.values()):
        # 字段级交叉核对失败也是校验失败：D-04 统一 404，D-03 带真实 checks
        _fail("RUN_NOT_FOUND", "双向摘要校验未通过，拒绝静默回放",
              extra={"checks": checks, "checks_note": "字段级交叉核对未通过"})
    return {
        **manifest,
        "checks": checks,
        "checks_note": (
            "三项 checks 由 read_manifest() 的规范化投影/摘要/snapshot_version 重算保证"
            "（不符抛 ManifestError → 404 不静默）；llm_route.snapshot_version 另做字段级交叉核对"
        ),
    }


# ---------------------------------------------------------------------------
# A9 guardrails/scan
# ---------------------------------------------------------------------------
@router.post("/guardrails/scan")
def guardrails_scan(body: dict) -> dict:
    from showcase.guard import protect_text

    if not isinstance(body, dict):
        _fail("INVALID_PARAM", "请求体必须是 JSON 对象")
    text = str(body.get("text") or "")
    gates = body.get("gates") or ["ingest", "context", "output", "audit"]
    if not isinstance(gates, list) or not all(isinstance(g, str) for g in gates):
        _fail("INVALID_PARAM", "gates 必须是字符串数组")
    _protected, pii = protect_text(text, True)
    report = dict(pii.get("report") or {})
    if report.get("blocked"):
        _fail("PII_BLOCKED", "guardrails 命中 ACTION_BLOCK，已拒绝；不回显原文")

    # 安全加固（defense in depth）：A9 不得成为原文回显通道。
    # 将来出现新形态漏检时，results[].text 会把未打码原文带回前端，故统一置空；
    # findings.preview 已由 guardrails._preview() 做成「首尾各 2 位 + ***」，安全，保留。
    safe_results: list[dict] = []
    for item in report.get("results") or []:
        row = dict(item)
        row["text"] = ""
        safe_results.append(row)
    report["results"] = safe_results
    report["echo_suppressed"] = True

    report["gates"] = list(gates)
    report.setdefault("rule_version", pii.get("rule_version") or "PII-RULES@1.0")
    report["note"] = (
        "protect_text 固定依次走 ingest/context/output/audit 四出口，命中数一致；"
        "gates 为本次请求子集；results[].text 统一置空（echo_suppressed=true），"
        "findings.preview 仅回显首尾各 2 位"
    )
    return report


# ---------------------------------------------------------------------------
# A10 samples（真实文件名，不重新生成样例）
# ---------------------------------------------------------------------------
_SAMPLE_ROWS = (
    {"id": "simple", "tier": "simple", "filename": "simple.pdf",
     "mime": "application/pdf", "replay": False},
    {"id": "complex", "tier": "complex", "filename": "complex.png",
     "mime": "image/png", "replay": True},
)


@router.get("/samples")
def samples() -> dict:
    rows: list[dict] = []
    for row in _SAMPLE_ROWS:
        if not (SAMPLES_DIR / row["filename"]).is_file():
            _fail("RUN_NOT_FOUND", f"样本文件缺失：{row['filename']}")
        rows.append(dict(row))
    return {
        "samples": rows,
        "note": (
            "真实已落盘样本，不重新生成；复杂档 replay=true，视觉结果为离线回放，"
            "非实时在线识别（来源 complex.report.json）"
        ),
    }
