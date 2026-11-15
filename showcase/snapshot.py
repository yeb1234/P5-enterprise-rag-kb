"""单文件 manifest 快照：一次 LLM 路由选择 ↔ 一个物理 manifest 文件。

用户原话落点：「保证 LLM、对应的快照时间版本、提示词、参数等都要同步单文件
跟着这个 LLM 走（没有就空占位）」。

「单文件」语义（架构 §6.4）：一次 ``RouteSnapshot`` 对应**一个**
``out/<run_id>.manifest.json``，与同次 ``out/<run_id>.ingest.json`` 组成一对产物；
换模型 / 换提示词 / 换参数 → ``snapshot_version`` 变 → ``run_id`` 变，
**旧 manifest 不复用**。

────────────────────────── 三态标注 ──────────────────────────
【已实现】manifest 原子落盘（tmp + os.replace）、双向摘要互引、回放校验
          （重算 ingest 摘要 / manifest 摘要 / route.snapshot_version，任一不符即抛错）。
【只设计】在线填真值的 LLM 快照字段（由 llm_router.py 占位，本模块原样透传）；
          真实外部 LLM 调用与计费——本轮一次都不发。
【未做】  真实压测 / 质量基线 / 监控 SLO；manifest 加密签名（只做 sha256 完整性互引，
          不是防篡改签名，改动可被发现但不能防抵赖）。

────────────── 双向摘要互引：为什么必须用 canonical projection ──────────────
两个文件若各自存放「对方**完整文件字节**」的 sha256，会形成循环依赖：
    ingest_digest  = sha256(ingest 完整字节)  ← ingest 内含 manifest.sha256
    manifest_digest= sha256(manifest 完整字节) ← manifest 内含 ingest_ref.sha256
先有鸡还是先有蛋，无解（这不是实现偷懒，是互签的数学不可能三角）。

本模块采用**规范化投影**打破循环，两项都可在 ``read_manifest()`` 里确定性重算：
  * ``ingest_digest``   = sha256(_dumps(剔除 manifest.sha256 后的 ingest 规范化投影))
  * ``manifest_digest`` = sha256(_dumps(完整 manifest))   ← manifest 不含自指字段
于是 manifest_digest 依赖 ingest_digest，而 ingest_digest **不**依赖 manifest_digest。
每个文件仍各自持有对方的摘要 → 双向互引成立；任何一侧内容被改，对拍即暴露：
  · 改 ingest 的业务字段 → ingest_digest 变 → 与 manifest.ingest_ref.sha256 不符 → 抛错
  · 只改 ingest.manifest.sha256 → 与 manifest_digest 不符 → 抛错（第二条校验兜住）
  · 只改 manifest.route.model 不改 snapshot_version → 重算版本不符 → 抛错
  · 只改 manifest 任意字段 → manifest_digest 变 → 与 ingest.manifest.sha256 不符 → 抛错

────────────────────────── 确定性与纪律 ──────────────────────────
* 所有落盘内容用 ``_dumps()``（sort_keys + 紧凑分隔符 + ensure_ascii=False）规范化，
  同参双跑逐字节一致；摘要永远基于规范化字节而非文件原始字节，因此换行/缩进差异
  不会误报，但**任何值改动**都会被发现（01-scope.md G5）。
* ``created_at`` 只从 ingest / route 的 ``as_of`` 取，绝不用挂钟时间。
* 缺字段一律空占位（"" / {} / []）+ warning，**绝不编造** source_sha256 / params_hash。
* ``external_request_count`` 固定 0（spec §1 裁决：演示默认离线）。
* 只 import 本成员自己的 ``llm_router.snapshot_version_for`` 与两份字段集常量
  （兄弟文件，非同伴模块），不 import ``showcase/api.py`` / ``params.py`` /
  ``ingest_preview.py`` 等同伴文件；不依赖 ``parsing/`` / ``cache/`` 的任何运行时行为
  （哈希思路参考 cache/keys.py）。
* ``snapshot_version`` 是**配置快照版本**（不含 ``reason`` / ``hysteresis`` 滞回计数），
  由 ``llm_router.snapshot_version_for()`` 作**唯一事实源**重算；本模块的镜像常量
  只用于跨文件漂移检测，不参与任何运行时哈希计算，因此不存在「第二套哈希」。

────────── 两个 id 的语义（主理人集成裁决，与 llm_router.py 一致） ──────────
* ``snapshot_version`` = 被选中的 LLM 配置版本：换模型 / 提示词 / 参数 / 档位才变，
  变则 ``run_id`` 变、旧 manifest 不复用；滞回计数属可观测层，不改配置版本，
  保证同参双跑 ``run_id`` 一致（01-scope.md V5）。
* ``route_id`` = 决策实例 id：含 ``reason`` / ``hysteresis`` 全字段，同参连续两次
  决策仍唯一。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# 兄弟文件（同属 agent-app-engineer，同时落盘）：
# 用相对 / 绝对双路径导入，兼容「作为 showcase 包」与「把 showcase/ 加进 sys.path」
# 两种自测方式；并行期其它同伴文件未落盘不影响本模块。
try:
    from .llm_router import (
        snapshot_version_for, _CONFIG_HASH_FIELDS, _DECISION_HASH_FIELDS,
    )
except ImportError:  # pragma: no cover - 顶层模块方式运行自测时走这里
    from llm_router import (  # type: ignore[no-redef]
        snapshot_version_for, _CONFIG_HASH_FIELDS, _DECISION_HASH_FIELDS,
    )


SCHEMA_VERSION = "showcase-manifest@1.0"   # 演示契约版本，非压测结论
PII_RULE_VERSION = "PII-RULES@1.0"         # 复用 guardrails/contracts.py 约定
SNAPSHOT_HASH_FIELDS_NOTE = (
    "snapshot_version=配置快照版本，由 llm_router.snapshot_version_for(route) 重算，"
    "排除 reason/hysteresis 滞回计数；route_id=决策实例 id 含全字段；"
    "两个 id 自身不参与哈希，故无循环依赖"
)

# ── 字段集的逐字镜像（仅用于跨文件漂移检测，不参与运行时哈希）──────────────
# 必须与 llm_router.py 的 _CONFIG_HASH_FIELDS / _DECISION_HASH_FIELDS 逐字一致。
# 运行时重算一律走 llm_router.snapshot_version_for（单一事实源）；本镜像只让
# hash_fields_consistent() 能在自测里发现「改了 llm_router 忘改 snapshot」。
_CONFIG_HASH_FIELDS_MIRROR = (
    "tier", "model", "model_snapshot_at", "prompt", "prompt_version",
    "params", "fallback_chain", "trigger", "cost_hint", "placeholder",
    "difficulty", "signals", "decided_at",
)
_DECISION_HASH_FIELDS_MIRROR = (
    "tier", "model", "model_snapshot_at", "prompt", "prompt_version",
    "params", "fallback_chain", "trigger", "cost_hint", "placeholder",
    "difficulty", "signals", "decided_at",
    "reason", "hysteresis",
)


def hash_fields_consistent() -> tuple[bool, dict]:
    """交叉一致性校验：本模块镜像 vs llm_router 单一事实源。

    返回 ``(是否一致, 明细)``；明细含两份字段集与差异列表，便于自测原样打印。
    纯函数、不发请求、不落盘。
    """
    config_ok = tuple(_CONFIG_HASH_FIELDS) == _CONFIG_HASH_FIELDS_MIRROR
    decision_ok = tuple(_DECISION_HASH_FIELDS) == _DECISION_HASH_FIELDS_MIRROR
    detail = {
        "config_match": config_ok,
        "decision_match": decision_ok,
        "config_fields": list(_CONFIG_HASH_FIELDS),
        "decision_fields": list(_DECISION_HASH_FIELDS),
        "config_excludes_hysteresis": "hysteresis" not in _CONFIG_HASH_FIELDS
                                      and "reason" not in _CONFIG_HASH_FIELDS,
        "decision_includes_hysteresis": "hysteresis" in _DECISION_HASH_FIELDS
                                        and "reason" in _DECISION_HASH_FIELDS,
    }
    return (config_ok and decision_ok
            and detail["config_excludes_hysteresis"]
            and detail["decision_includes_hysteresis"], detail)


class ManifestError(RuntimeError):
    """回放校验失败：摘要不符 / 文件缺失 / JSON 损坏 / route 版本不符。

    A8 捕获它映射 404 / 500，**不静默回放**（架构 §4.3 / §6.4）。
    """


# ---------------------------------------------------------------------------
# 规范化与摘要
# ---------------------------------------------------------------------------

def _dumps(obj: Any) -> str:
    """规范化 JSON 文本：键排序 + 紧凑分隔符 + 不转义中文，保证双跑逐字节一致。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ingest_projection(ingest: dict) -> dict:
    """ingest 的摘要投影：剔除互引字段 ``manifest.sha256``，打破循环依赖。

    只剔除这一个字段；其余（含 ingest.manifest.path / snapshot_version、
    chunks / trace / guardrails / llm_route 全字段）都参与摘要，
    因此「跟着这个 LLM 走」的任何内容改动都会改变 ingest_digest。
    """
    proj = copy.deepcopy(ingest)
    m = proj.get("manifest")
    if isinstance(m, dict):
        m = dict(m)
        m.pop("sha256", None)
        proj["manifest"] = m
    return proj


def _atomic_write(final_path: Path, text: str) -> None:
    """先写 ``<name>.tmp`` 再 ``os.replace()`` 原子替换，避免读到写一半的文件。"""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, final_path)


# ---------------------------------------------------------------------------
# manifest 组装（字段按架构 §6.4；缺失一律空占位 + warning，不编造）
# ---------------------------------------------------------------------------

def _first_present(ingest: dict, *paths: tuple[str, ...], default: Any = "") -> Any:
    """按若干候选路径依次取值，取不到返回 default（不编造）。"""
    for path in paths:
        cur: Any = ingest
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return default


def _collect_warnings(ingest: dict, *, source_sha256: str,
                      params_hash: str, config_snapshot: Any) -> list[str]:
    """确定性 warnings：只陈述真实缺口与离线事实，不编造指标。"""
    out: list[str] = []
    if not source_sha256:
        out.append("ingest 未提供 source.sha256：source_sha256 空占位（不编造）")
    if not params_hash:
        out.append("ingest 未提供 params_hash：空占位（不编造）")
    if not config_snapshot:
        out.append("ingest 未提供 config_snapshot：{} 空占位（不编造）")
    # 复杂档回放标记（spec §1.1 裁决 2）：如实透传，不自行判定
    note = str(ingest.get("note") or "")
    ingest_warns = ingest.get("warnings") or []
    blob = note + " " + " ".join(str(w) for w in ingest_warns)
    if "回放" in blob:
        out.append("复杂档视觉结果为离线回放预先留存的真实解析产物，非实时在线识别")
    out.append(
        "本轮演示一律离线，未发起外部 LLM 调用（external_request_count=0）；"
        "RouteSnapshot 的 model / prompt / params 为空占位，"
        "「选出 Pro 档」不等于「调用了 Pro 模型」"
    )
    return out


def _build_manifest(run_id: str, route: dict, ingest: dict,
                    *, ingest_path_name: str, ingest_digest: str) -> dict:
    """组装 manifest（含 ingest_ref.sha256 回填）；不改传入的 route / ingest。

    ``ingest_digest`` 由 ``write_pair`` 基于**最终写盘形态**的 ingest 骨架算好后传入，
    保证 ``read_manifest`` 重算时能命中（见 write_pair 步骤①②的循环依赖说明）。
    """
    source_sha256 = _first_present(ingest, ("source", "sha256"), ("source_sha256",),
                                   default="")
    params_hash = _first_present(ingest, ("params_hash",), ("parser", "params_hash"),
                                 ("manifest", "params_hash"), default="")
    config_snapshot = _first_present(
        ingest, ("config_snapshot",), ("params_snapshot",), ("parser", "config"),
        default={},
    )
    created_at = _first_present(ingest, ("created_at",), default="") or \
        str(route.get("decided_at") or "")

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,          # as_of 固定逻辑时间，非挂钟
        "source_sha256": source_sha256,
        "params_hash": params_hash,
        "config_snapshot": copy.deepcopy(config_snapshot),
        "route": copy.deepcopy(route),     # 完整 RouteSnapshot，与 ingest.llm_route 同源
        "ingest_ref": {
            "path": ingest_path_name,      # 相对文件名，保证产物可移植
            "sha256": ingest_digest,       # ← 回填 ingest 摘要
        },
        "pii_rule_version": PII_RULE_VERSION,
        "offline": bool(ingest.get("offline", True)),   # 本轮演示一律离线
        "external_request_count": 0,      # spec §1 裁决：外部请求必须为 0
        "warnings": _collect_warnings(
            ingest, source_sha256=str(source_sha256),
            params_hash=str(params_hash), config_snapshot=config_snapshot,
        ),
        "note": SNAPSHOT_HASH_FIELDS_NOTE,
    }
    return manifest


# ---------------------------------------------------------------------------
# 公开接口（spec §2 签名逐字遵守）
# ---------------------------------------------------------------------------

def write_pair(run_id: str, route: dict, ingest: dict, out_dir: Path) -> dict:
    """一次 LLM 选择 ↔ 一个物理 manifest，与同次 ingest JSON 成一对并双向摘要互引。

    顺序（架构 §6.4 步骤⑤，含循环依赖破除）：
      ① 固定最终写盘形态的 ingest 骨架（``manifest.sha256`` 留空），
         对其规范化投影算 ingest 摘要；
      ② 组装 manifest 并回填 ``ingest_ref.sha256`` → ``<run_id>.manifest.json.tmp``
         → ``os.replace()`` 原子替换出正式 manifest；
      ③ 从最终 manifest 规范化字节算 ``manifest_digest``，回填进
         ``ingest.manifest.sha256``；
      ④ 写 ``<run_id>.ingest.json``（同样走 tmp + replace，属额外稳妥）。
    落盘的两个 JSON 内部一律用**相对文件名**互引，保证产物可移植、双跑一致；
    返回值的 ``path`` 是 manifest 的绝对路径字符串（供 A5/A8 直接定位），不落盘。

    返回 ``{"path", "sha256", "snapshot_version"}``。
    """
    if not run_id:
        raise ManifestError("run_id 不能为空（一次 LLM 选择必须对应一个 run_id）")
    if not isinstance(route, dict) or not route:
        raise ManifestError("route 必须是非空 RouteSnapshot.as_dict()")
    if not isinstance(ingest, dict):
        raise ManifestError("ingest 必须是 dict")

    out_dir = Path(out_dir)
    manifest_name = f"{run_id}.manifest.json"
    ingest_name = f"{run_id}.ingest.json"
    manifest_path = out_dir / manifest_name
    ingest_path = out_dir / ingest_name

    # ① 先固定「最终写盘形态」的 ingest 骨架：互引字段 sha256 留空，
    #    path / snapshot_version 此时即可确定（都不依赖 manifest_digest）。
    #    这是打破双向互引循环的关键：ingest_digest 基于该骨架的 projection 计算，
    #    而 read_manifest 读回落盘文件后剔除 sha256 得到的正是同一个骨架，故必然命中。
    #    （若用原始 ingest 计算，其 manifest 段是空占位，与落盘骨架不同 → 回放必失败。）
    final_ingest = copy.deepcopy(ingest)
    m = dict(final_ingest.get("manifest") or {})
    m["path"] = manifest_name                     # 相对文件名
    m["sha256"] = ""                              # 互引字段，③ 再回填
    m["snapshot_version"] = str(route.get("snapshot_version") or "")
    final_ingest["manifest"] = m

    ingest_digest = _sha256(_dumps(_ingest_projection(final_ingest)))

    # ② manifest 原子落盘：tmp 落盘即完整（ingest_ref.sha256 已回填）
    manifest = _build_manifest(run_id, route, final_ingest,
                               ingest_path_name=ingest_name,
                               ingest_digest=ingest_digest)
    _atomic_write(manifest_path, _dumps(manifest))

    # ③ 从最终 manifest 算摘要，回填进 ingest 的 manifest 段（双向互引的第二向）
    manifest_digest = _sha256(_dumps(manifest))
    final_ingest["manifest"]["sha256"] = manifest_digest   # ← 指向 manifest 摘要

    # ④ ingest 原子落盘
    _atomic_write(ingest_path, _dumps(final_ingest))

    return {
        "path": str(manifest_path),
        "sha256": manifest_digest,
        "snapshot_version": str(route.get("snapshot_version") or ""),
    }


def read_manifest(run_id: str, out_dir: Path) -> dict:
    """读回并校验双向摘要互引；任一不符抛 ``ManifestError``，**不静默回放**。

    三项确定性重算（全部基于规范化字节，同参双跑一致；主理人裁决后逻辑不变）：
      1. ingest 投影摘要  == ``manifest.ingest_ref.sha256``
      2. manifest 摘要    == ``ingest.manifest.sha256``
      3. ``snapshot_version_for(manifest.route)`` == ``manifest.route.snapshot_version``
         （复核**配置版本**：换 model / prompt / params / tier / 难度 / 信号才变，
           ``reason`` / ``hysteresis`` 滞回计数不参与，故同参双跑仍命中）
    第 3 项即「LLM / 提示词 / 参数跟着单文件走」的可验证落点：只改
    ``route.model``（或 params / prompt / tier）而不换 run_id，重算版本就不符 → 抛错。
    """
    out_dir = Path(out_dir)
    manifest_path = out_dir / f"{run_id}.manifest.json"
    ingest_path = out_dir / f"{run_id}.ingest.json"

    for label, path in (("manifest", manifest_path), ("ingest", ingest_path)):
        if not path.exists():
            raise ManifestError(f"{label} 文件不存在：{path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ingest = json.loads(ingest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"快照文件读取/解析失败：{type(exc).__name__}: {exc}") from exc

    if not isinstance(manifest, dict) or not isinstance(ingest, dict):
        raise ManifestError("快照文件顶层必须是 JSON 对象")

    # ① 重算 ingest 摘要
    try:
        expected_ingest = manifest["ingest_ref"]["sha256"]
    except (KeyError, TypeError) as exc:
        raise ManifestError("manifest 缺少 ingest_ref.sha256") from exc
    actual_ingest = _sha256(_dumps(_ingest_projection(ingest)))
    if actual_ingest != expected_ingest:
        raise ManifestError(
            "ingest 摘要不匹配：入库 JSON 已被改动或不属于该 manifest"
            f"（expected={expected_ingest[:12]}… actual={actual_ingest[:12]}…）"
        )

    # ② 重算 manifest 摘要（manifest 不含自指字段，直接规范化整个对象）
    try:
        expected_manifest = ingest["manifest"]["sha256"]
    except (KeyError, TypeError) as exc:
        raise ManifestError("ingest 缺少 manifest.sha256") from exc
    actual_manifest = _sha256(_dumps(manifest))
    if actual_manifest != expected_manifest:
        raise ManifestError(
            "manifest 摘要不匹配：单文件 manifest 已被改动"
            f"（expected={expected_manifest[:12]}… actual={actual_manifest[:12]}…）"
        )

    # ③ 重算 route.snapshot_version
    route = manifest.get("route")
    if not isinstance(route, dict):
        raise ManifestError("manifest 缺少完整 route 快照")
    declared = route.get("snapshot_version")
    recomputed = snapshot_version_for(route)
    if declared != recomputed:
        raise ManifestError(
            "route.snapshot_version 不匹配：模型 / 提示词 / 参数 / 档位改动后"
            "必须换 run_id、旧 manifest 不复用"
            f"（declared={declared} recomputed={recomputed}）"
        )

    # ④ ingestion 侧 llm_route 的版本交叉校验（架构 §6.4「跟着这个 LLM 走」）。
    # 注意：compact 档 ingest.llm_route 只有 5 个字段（架构 §5.4），不能用逐字段全等，
    # 否则会误伤同伴的精简档产物。这里只比「版本」这一真正需要绑定的字段：
    # ingest 若带了 llm_route.snapshot_version，就必须与 manifest.route 的同款值一致。
    # 其余完整性已由 ① ingest 摘要（含 llm_route 全字段）与 ② manifest 摘要覆盖。
    llm_route = ingest.get("llm_route")
    if isinstance(llm_route, dict) and "snapshot_version" in llm_route:
        if llm_route["snapshot_version"] != declared:
            raise ManifestError(
                "ingest.llm_route.snapshot_version 与 manifest.route 不一致："
                "「只改模型名不改 manifest」或「只改 manifest 不改入库 JSON」在此暴露"
            )

    return manifest
