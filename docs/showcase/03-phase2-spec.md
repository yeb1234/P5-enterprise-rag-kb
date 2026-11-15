# Phase 2 实现 spec · 主理人综合与裁决

> 本文件是 `showcase/` 落地的**唯一事实源**。Phase 1 的 `01-scope.md`（范围/两档/验收）
> 与 `02-architecture.md`（架构/接口/schema）结论全部采纳；本文件只做两件事：
> **① 回填架构 §10 列出的全部「待 Phase 2 回填」项；② 钉死并行实现的模块接口与文件归属。**
> 成员按本 spec 实现，不得各自另定默认值。所有数值均为**演示起始值，非压测结论、非实测指标**。

---

## 1. 裁决总表（架构 §10 必填项，逐条回填）

| 项 | 裁决值 | 理由 / 口径 |
|---|---|---|
| `chunk_size_chars` | `800` | 沿用 `webapp/rag.py::CHUNK_SIZE`，项目既有口径，便于讲解对照 |
| `chunk_overlap_chars` | `80` | 沿用 `webapp/rag.py::CHUNK_OVERLAP` |
| `confidence_min` | `0.75` | 沿用 `parsing/config.py` 默认；质量门禁阈值，非实测 |
| `vision_escalation_ratio` | `0.10` | 沿用 `parsing/config.py`；**熔断上限，不是随机分流** |
| `vision_min_slots` | `1` | 沿用 `parsing/config.py` |
| `PAGE_REF` | `20` | 页数归一化参考，演示起始值 |
| `CELLS_REF` | `100` | 表格单元格归一化参考，演示起始值 |
| 难度权重 `w_i` | `no_text_layer_ratio 0.30` / `ocr_needed 0.25` / `1−min_confidence 0.20` / `table_cells_total 0.10` / `page_count 0.10` / `1−budget_remaining_ratio 0.05` | Σ = 1.00；把「无文本层 / 需要 OCR / 低置信」设为最难信号，与 `parsing/` 质量门禁一致 |
| `θ_enter` / `θ_exit` | `0.60` / `0.40` | 带宽 δ = 0.20 ≥ 0.15，满足架构 §6.2 滞回要求 |
| `k`（连续命中次数） | `2` | 升档/降档各需连续 2 次，避免抖动 |
| Pro 档模型 id / 单价 | **空占位** | 未选定供应商、未开通凭证，按用户原话「没有就空占位」；不写猜测型号/价格 |
| `as_of` 默认 | `2026-11-15T00:00:00Z` | **固定逻辑时间常量**，绝不用 `datetime.utcnow()`；保证同参双跑逐字节一致 |
| `MAX_BYTES` | `8 MiB` | 沿用 `parsing/server.py` |
| 端口 | `8767` | 与 webapp `8765`、独立 parse 服务 `8766` 分离 |
| 离线开关 | 默认 `PARSING_OFFLINE=1` | 外部请求计数必须为 0 |

### 1.1 主理人新增裁决（架构未覆盖但与用户原话强相关）

1. **路由决策与「是否真的调用 LLM」解耦**。难度评分照常算出 `standard` / `pro` / `fallback`，
   Pro 兜底仍在复杂档真实触发；但本轮演示**一律不发起外部 LLM 调用**（离线默认）。
   因此 `RouteSnapshot` 中 `model` / `model_snapshot_at` / `prompt` / `prompt_version` / `params`
   按占位规则置空、`placeholder=true`，`cost_hint.note` 写「仅路由决策，未发起外部调用」。
   `tier` / `trigger` / `difficulty` / `signals` / `hysteresis` / `fallback_chain` / `route_id` /
   `snapshot_version` / `decided_at` 必须全是真实可复算值。
   **不得把「选出 Pro 档」讲成「调用了 Pro 模型」**。在线填真值属「只设计」，本轮不实现。
2. **复杂档视觉结果一律标「回放」**：来源是 `showcase/samples/complex.report.json`
   （`make_samples.py` 从 `parsing.ParseRouter` 真实离线跑出的产物），不是实时在线识别。
   `analysis.warnings[]` 与 `ingest.note` 都要出现「回放」。
3. **样本命名以现状为准**。架构 §3.1 的 `text_layer.pdf` / `scanned_page.png` /
   `replay_manifest.json` 是规划名，**实际以 `showcase/samples/` 已落盘文件为准**：
   `simple.pdf`、`complex.png`、`simple.report.json`、`complex.report.json`、
   `simple.canonical.json`、`complex.canonical.json`、`make_samples.py`。
   A10 `/api/samples` 返回真实文件名，**不得重复生成样例**。
4. **观测字段与可复现字段分离**（实跑发现）：`ParseRouter` 输出的
   `summary.total_latency_ms` / `traces[].latency_ms` / `results[].latency_ms` 同参双跑会变，
   `created_at` 取 `as_of`、挂钟时间只进 `runtime`。入库 JSON 不得含挂钟时间与实时耗时；
   `canonical()` 剥离 `latency_ms` / `created_at` / `run_id` / `generated_at` 之外的观测字段，
   用于 V5「同参双跑逐字节一致」。
5. **`guardrails` 四出口演示语义**：同一段文本依次走 `ingest/context/output/audit`，
   展示四次 `RedactionResult` 的命中数一致（证明规则单点），**最终文本只取第一次 `ingest` 的结果**，
   不重复打码。`SafetyReport` 汇总进右侧 `guardrails`。
6. **收费适配器只描述、不调用**：`showcase/adapters/` 为静态 profile，
   `available=False`、未填字段 `__TBC__`。Reducto / 阿里云 profile 的 `price_hint`
   可用 2026-11-15 联网调研到的公开价，但必须标「公开价、非账单、以控制台实时价为准」。
   **不得修改 `parsing/strategies/ocr_api.py::_call()`**（保持 `__TBC__`）。
7. **滞回状态与 Pro 触发（集成阶段新增裁决）**。实跑证据：
   `complex.png` 难度 `0.735 ≥ θ_enter=0.60`，但 `TierState` 要求连续 `k=2` 次命中，
   因此**每次新建状态的单次 A5 恒为 `standard`（hits=1）**；同一状态连续第 2 次才 `pro`（hits=2）。
   裁决：**保留 k=2 与双阈值不变**（滞回是防抖动的专业卖点，不得为演示好看降 k 或加首次直通），
   改为让 A5 复用**按 `(source_sha256, params_hash)` 派生的进程内有限 LRU（≤32 条）`TierState` 缓存**：
   - 同一份复杂档连续跑两次 A5 → 第 2 次真实升 `pro`，`hysteresis.consecutive_hits` 从 1 到 2；
   - 不同文件 / 不同参数 → 不同 key，**不串用户的状态**（回应成员提出的隔离顾虑）；
   - 进程重启清零，属演示切片的可丢弃状态，符合既有三态分类；
   - 两次 `route.snapshot_version` 不同 → `run_id` 不同，**改档必变**是正确行为，不是缺陷。
   A6 仍可用 `current_tier` 预演或 `llm_tier_override` 强制档位。
   前端必须显示 `hysteresis.consecutive_hits` 并提示「再跑一次同档位将触发 Pro 兜底」；
   录像与主解释页措辞统一改为「难度 0.735 连续 2 次 → pro」，**不得写成单次即 pro**。
8. **`snapshot_version` 与 V5 判定口径（集成预演新增裁决）**。实跑发现
   `llm_router.py::_SNAPSHOT_HASH_FIELDS` 把 `reason` 与 `hysteresis` 也算进版本哈希，
   导致同参连续两次决策版本从 `7bd0a3faca3002ee` 变成 `cb26b89a3e04fb3f`，
   连带 `run_id`、manifest 摘要全变。但「快照版本」的语义是**被选中的 LLM 配置版本**，
   不该被瞬时滞回计数器改变。裁决：
   - `snapshot_version_for()` 改用**配置性字段**：`tier / model / model_snapshot_at / prompt /
     prompt_version / params / fallback_chain / trigger / cost_hint / placeholder /
     difficulty / signals / decided_at`；**排除 `reason` 与 `hysteresis`**。
   - `route_id` 仍用**全字段**哈希（它是「这一次决策实例」的唯一 id，每次不同是合理的）。
   - `snapshot.py` 里那份逐字一致的常量副本必须同步修改，并保留自测里的交叉一致性校验。
   - 效果：同参同档双跑 → `snapshot_version` 与 `run_id` 相同；真正改档 / 换模型 / 换提示词 /
     换参数 → 版本变 → `run_id` 变（改档必变不变）。
   - **V5「同参双跑逐字节一致」的判定对象是入库内容规范化投影 `canonical()`**，不是含决策实例
     字段的完整文档。投影剔除：`manifest`（整块）、`llm_route.route_id` /
     `llm_route.hysteresis` / `llm_route.reason`、所有 `latency_ms` / `wall_clock` / `elapsed_ms`；
     **保留** `chunks[]` / `trace[]`（含 `trace[].reason` 解析原因码）/ `source` / `parser` /
     `guardrails` / `created_at`（固定 `as_of`）。A8 回放校验仍用**完整** manifest 与 ingest，
     不受投影影响。前端对拍按钮须写明这个比较口径。

---

## 2. 模块接口契约（并行实现必须逐字遵守）

> 依赖方向单向：`showcase/` → `parsing/` / `guardrails/` / `cache/` / `knowledge/`；
> 纯函数包**禁止**反向 import。以下签名是成员之间的唯一约定，不得擅自改名或改参数。

```python
# showcase/params.py  ── 归属 backend-engineer
DEFAULT_AS_OF = "2026-11-15T00:00:00Z"
@dataclass(frozen=True)
class Params:
    strategy: str = "auto"
    confidence_min: float = 0.75
    chunk_size_chars: int = 800
    chunk_overlap_chars: int = 80
    vision_escalation_ratio: float = 0.10
    vision_min_slots: int = 1
    llm_tier_override: str | None = None
    redact_enabled: bool = True
    ingest_format: str = "compact"       # compact | full
    as_of: str = DEFAULT_AS_OF
    def as_dict(self) -> dict: ...
    @property
    def params_hash(self) -> str: ...     # 规范化 json -> sha256[:16]
def parse_params(raw: dict) -> Params: ...   # 越界/未知值抛 ValueError("INVALID_PARAM: ...")
```

```python
# showcase/llm_router.py  ── 归属 agent-app-engineer
@dataclass(frozen=True)
class DifficultySignals:
    page_count: int; no_text_layer_ratio: float; table_cells_total: int
    min_confidence: float; ocr_needed: bool; budget_remaining_ratio: float
    raw: dict; notes: tuple[str, ...]
    def as_dict(self) -> dict: ...
def from_parse_report(rep: dict) -> DifficultySignals: ...
def difficulty_score(s: DifficultySignals) -> float: ...   # Σ w_i×norm_i，权重见 §1
@dataclass(frozen=True)
class RouteSnapshot:
    route_id: str; tier: str; model: str; model_snapshot_at: str | None
    snapshot_version: str; prompt: str; prompt_version: str; params: dict
    fallback_chain: list[str]; trigger: str; cost_hint: dict; placeholder: bool
    reason: str; difficulty: float; signals: dict; hysteresis: dict; decided_at: str
    def as_dict(self) -> dict: ...
class TierState:
    def decide(self, signals: DifficultySignals, *, override: str | None,
               as_of: str, offline: bool, event: str | None = None) -> RouteSnapshot: ...
```

```python
# showcase/snapshot.py  ── 归属 agent-app-engineer
class ManifestError(RuntimeError): ...
def write_pair(run_id: str, route: dict, ingest: dict, out_dir: Path) -> dict:
    """manifest.tmp -> ingest.json -> manifest.json 原子替换 + 双向摘要互引；
       返回 {"path","sha256","snapshot_version"}"""
def read_manifest(run_id: str, out_dir: Path) -> dict:
    """重算 ingest sha256 与 route.snapshot_version；任一不符抛 ManifestError"""
```

```python
# showcase/ingest_preview.py  ── 归属 data-engineer
NON_REPLAY_FIELDS = ("latency_ms", "created_at", "run_id", "generated_at")
def canonical(obj): ...                  # 递归剔除观测字段 + 排序
def chunk_text(text: str, size: int, overlap: int) -> list[str]: ...
def build_views(payload: bytes, *, filename: str, mime: str, params, rep: dict,
                route: dict, pii: dict, run_id: str) -> tuple[dict, dict]:
    """返回 (AnalysisView, IngestDocument)；两者共用 run_id / params_snapshot。
       chunk_id = sha256(page_no + 序号 + 内容)[:12]；created_at 取 params.as_of。"""
```

```python
# showcase/guard.py  ── 归属 data-engineer
def protect_text(text: str, enabled: bool) -> tuple[str, dict]:
    """返回 (脱敏后文本, {"chain":[...4 个 RedactionResult.as_dict()...],
       "report": SafetyReport.as_dict(), "rule_version": "PII-RULES@1.0"})"""
```

```python
# showcase/adapters/__init__.py  ── 归属 backend-engineer
def vendor_profiles() -> list[dict]:
    """每个 profile: {"id","region","price_hint","available":False,
       "contract":{...__TBC__...},"note"}；含 reducto / aliyun_docmind / local_free"""
```

`showcase/api.py` 只能 import 上述公开符号；任何模块都不得 import 其他成员的私有函数。
成员并行开发期间出现 `ModuleNotFoundError` 属正常（同伴文件尚未落盘），
**不要因此创建占位模块或改动同伴文件**，按契约 import 即可。

---

## 3. 文件归属矩阵（写操作严格隔离，禁止交叉修改）

| 成员 | 独占文件 |
|---|---|
| `backend-engineer` | `showcase/__init__.py`、`params.py`、`server.py`、`api.py`、`README.md`、`Dockerfile`、`adapters/__init__.py`、`adapters/base.py`、`adapters/reducto.py`、`adapters/aliyun_docmind.py`、`adapters/local_free.py` |
| `agent-app-engineer` | `showcase/llm_router.py`、`showcase/snapshot.py` |
| `data-engineer` | `showcase/ingest_preview.py`、`showcase/guard.py`、`showcase/tests/test_contracts.py`、`showcase/out/.gitkeep` |
| `frontend-engineer` | `showcase/static/index.html`、`app.js`、`style.css` |
| 主理人（本人） | `showcase/samples/*`（已落盘）、录像 mp4、`interview/index.html` 新增章节、`docs/showcase/03-phase2-spec.md`、最终汇编与提交 |
| 只读/明确不动 | `parsing/`、`guardrails/`、`cache/`、`knowledge/`、`webapp/`、`backend/`、`eval/`、`demo/`、`.env`、`docs/showcase/01-scope.md`、`02-architecture.md`、`aliyun-guide.html` |

---

## 4. 10 个 API 与响应契约（实现以 `02-architecture.md` §4 为准，此处只列硬约束）

- A1 `/api/health`：只回 `external_configured` 布尔，**不回任何密钥值**。
- A2/A3 `/api/params`：GET 返回 7 类参数 + `params_hash`；PUT 返回 `changed[]` 与新哈希，进程内生效。
- A4 `/api/strategies`：复用 `ParseRouter.status()`；`ocr_api` 未接通必须显示「未接通，跳过」，
  `vendors` 来自 `vendor_profiles()`，`available` 全 False。
- A5 `/api/analyze`：`multipart/form-data`（`file` / 可选 `params` JSON 字符串 / 可选 `as_of`）。
  响应 `{"run_id","schema_version","analysis","ingest","manifest_ref","runtime","errors"}`。
  `analysis` 与 `ingest` 必须由**同一次** `ParseRouter.parse_document()` 结果组装。
  外部请求计数 `runtime.external_request_count` 必须为 `0`。
- A6 `/api/route/decide`：只做路由预演，不解析文件、不落盘。
- A7 `/api/ingest/preview?run_id=&format=compact|full`：读回入库 JSON。
- A8 `/api/manifest/{run_id}`：读回并校验双向摘要，失败不静默回放。
- A9 `/api/guardrails/scan`：四出口脱敏预演。
- A10 `/api/samples`：返回 `showcase/samples/` 真实文件清单，两档各一条，复杂档 `replay=true`。
- 错误码严格按 `02-architecture.md` §4.3，统一 `{"code","message","trace_id"}`。

---

## 5. 前端硬约束

- 左右分栏同屏，左 `AnalysisView`、右 `IngestDocument`，共用同一个 `run_id` 显示在页头。
- 参数面板 7 类全部可改，改完重新 A5；**参数快照要在右侧 JSON 可见**。
- 两档一键切换：简单档载入 `simple.pdf`、复杂档载入 `complex.png`（从 A10 取文件名）。
- 右侧 JSON 可折叠/复制；「快照单文件」可展开显示 `RouteSnapshot` 全部字段，
  **空占位字段要显式显示「空占位」而不是隐藏或填假值**。
- GitHub 风格浅色（IDE 是 light）、零外部 CDN/字体、单页同源、可离线打开。
- 复杂档页面顶部固定标注「视觉结果为离线回放，非实时在线识别」。
- 显示 `runtime.external_request_count`，让「离线可跑」可当场核对。

---

## 6. 验收责任映射（`01-scope.md` V1–V15）

| 判据 | 责任 |
|---|---|
| V1 离线两档、V2 左右同源、V3 两类文件、V4 七类参数可观测 | `backend-engineer` + `frontend-engineer`（实跑 A5） |
| V5 同参双跑逐字节一致 / 改参必变 | `data-engineer`（`canonical()` + `test_contracts.py`） |
| V6 `ocr_api` 降级不静默 | `backend-engineer`（A4 + 错误码） |
| V7 快照单文件、空占位 | `agent-app-engineer`（`RouteSnapshot` + `snapshot.py`） |
| V8 录像 / V9 主解释页 / V11 三态标注 / V10 上线指导 | 主理人 + `devops-engineer`（后者已完成） |
| V12 冻结区零 diff、V13 `webapp/` 零 diff、V14 改动面、V15 无编造数字 | 主理人最终核验 |

成员自测下限：`python -m py_compile <自己文件>` 全过；
不要求成员启动完整服务（同伴文件未齐时必然 ImportError），集成测试由主理人汇总后跑，
再交 `qa-engineer` 独立复验。

---

## 7. 三态纪律（写进代码注释与 README）

- `已实现`：`parsing/` 三级策略与质量门禁、`guardrails/` 四出口、真实样本与解析产物、
  `docs/showcase/01–02`、`aliyun-guide.html`、本机 ffmpeg/PIL/pypdf。
- `只设计`：收费 API 适配器 profile（`available=False`、`__TBC__`）、在线填真值的 LLM 快照字段、
  Pro 档真实模型与单价、阿里云实际部署。
- `未做`：`showcase/` 站点本体（本轮实现中）、实时在线视觉调用作为默认路径、
  playwright 录屏、真实压测 / 质量基线 / 监控 SLO。
- **不得把演示切片说成生产实现，不得把离线回放说成实时识别，不得把「选出 Pro 档」说成「调用了 Pro 模型」。**
