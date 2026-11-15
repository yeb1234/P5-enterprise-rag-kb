# showcase · 离线可跑的「解析 → 入库预览」演示站点

> **A/B 档骨架 + 面试讲解用，不是上线交付版。**
> 唯一事实源：`docs/showcase/03-phase2-spec.md`（默认值与模块接口）、
> `docs/showcase/02-architecture.md §3/§4/§7`、`docs/showcase/01-scope.md §4/§7`。

一句话：上传一个 PDF / 图片，左边给人看解析轨迹（页、级别、置信度、预算、降级原因），
右边给机器看可复现的入库 JSON；两区**由同一次 `ParseRouter.parse_document()` 结果组装**，
共用同一个 `run_id` 与参数快照。全程离线，外部请求计数为 0。

---

## 1. 启动

```bash
# 项目根目录执行（Python 3.10+，需 fastapi / uvicorn / python-multipart）
python -m uvicorn showcase.server:app --host 127.0.0.1 --port 8767
# 或
python -m showcase.server

# 打开 http://127.0.0.1:8767/
# 健康检查 http://127.0.0.1:8767/api/health
```

端口 **8767**（与 webapp 8765、独立 parse 服务 8766 分离）。

Docker（可选，站点镜像）：

```bash
docker build -f showcase/Dockerfile -t kb-showcase .
docker run --rm -p 8767:8767 kb-showcase
# 需要凭证时由使用者本人注入（镜像内不含任何密钥值）：
# docker run --rm -p 8767:8767 -e PARSING_OCR_API_KEY=... -e DISCOVERY_API_KEY=... kb-showcase
```

---

## 2. 离线开关（默认开）

| 项 | 口径 |
|---|---|
| 默认 | `PARSING_OFFLINE=1`；A5 构造 `ParseConfig(offline=True)` 恒定 |
| 效果 | P0 `local` + `structured_fallback()`；`runtime.external_request_count` **恒为 0** |
| `PARSING_OFFLINE=0` | **不会**让本站点发起外部请求。实时在线档属「只设计」，本轮未实现 |
| 强制收费档 | 选 `ocr_api` → 显示「未接通，跳过」并降级为 `auto`，留痕不静默；选 `multimodal` → `OFFLINE_ONLY` 留痕 + 离线回放 |
| 密钥 | A1 只回 `external_configured` 布尔；密钥值不进响应 / manifest / 日志。**服务端不读 `.env` 的值** |

收费适配器（`showcase/adapters/`）只是静态 profile：`available=False`、未填字段 `__TBC__`、
不发起任何外部请求。价格是 **2026-11-15 调研的公开价，非账单，以控制台实时价为准**。

---

## 3. 两档操作步骤

| 档 | 样本 | 入口 | 期望看到 |
|---|---|---|---|
| 简单档 | `showcase/samples/simple.pdf`（有文本层） | `GET /api/samples` 取文件名 → 上传 | P0 `local` 直接读，不升级；`replay=false` |
| 复杂档 | `showcase/samples/complex.png`（模拟扫描发票） | 同上，或 A5 带 `replay=true` | 质量门禁判需升级 → 离线降级留痕；页头固定标注「**视觉结果为离线回放，非实时在线识别**」；`replay=true` |

**复杂档是回放**：来源为 `showcase/samples/complex.report.json`
（`make_samples.py` 从 `parsing.ParseRouter` 真实离线跑出的产物），不是实时在线识别。
A5 的 `analysis.warnings[]` 与 `ingest.note` 都会出现「回放」。
样本是已落盘的真实文件，**站点不会重新生成样例**。

改参数一键复现：在参数面板改 7 类参数（尤其 `chunk_size_chars` / `chunk_overlap_chars`），
重新 A5 → 右侧 JSON 的 `chunks[]` 与 `params_hash` 同步变化 → `run_id` 变化（改参必变，G5）。

---

## 4. 10 个 API（前缀 `/api`）

| # | 路径 | 方法 | 说明 |
|---|---|---|---|
| A1 | `/api/health` | GET | 健康检查；`external_configured` 只回布尔 |
| A2 | `/api/params` | GET | 7 类参数当前值 + 取值范围 + `params_hash` |
| A3 | `/api/params` | PUT | 改参（进程内生效），返回 `changed[]` 与新哈希 |
| A4 | `/api/strategies` | GET | 复用 `ParseRouter.status()`；未接通档显示「未接通，跳过」；`vendors` 全 `available=False` |
| A5 | `/api/analyze` | POST | **核心**：`file`(≤8 MiB) 或白名单 `sample_id` 二选一 + 可选 `params` JSON 字符串 / `as_of` / `replay` |
| A6 | `/api/route/decide` | POST | 只做路由预演，不解析文件、不落盘 |
| A7 | `/api/ingest/preview` | GET | `?run_id=&format=compact\|full` 读回入库 JSON |
| A8 | `/api/manifest/{run_id}` | GET | 读回单文件 manifest + 双向摘要 `checks`；不符拒绝回放 |
| A9 | `/api/guardrails/scan` | POST | 单文本四出口脱敏预演 |
| A10 | `/api/samples` | GET | 真实样本清单（`simple.pdf` / `complex.png`） |

A5 响应：`{"run_id","schema_version","analysis","ingest","manifest_ref","runtime","errors"}`。
`runtime = {"wall_clock","elapsed_ms","external_request_count"}`——**挂钟时间只在这里，不进 `ingest`**；
`created_at` 取 `as_of`（固定逻辑时间 `2026-11-15T00:00:00Z`）。

`file` 与 `sample_id` 互斥：无 `file` 时传 `sample_id=simple.pdf` 或 `complex.png`
（**白名单硬编码**，不接受任意路径，防目录穿越），服务端从 `showcase/samples/` 读真实字节；
`complex.png` 会自然触发回放标注。浏览器两档切换走 `GET /showcase/samples/<name>` 拿字节
（`server.py` 中窄路径挂载**先于**根 `/`），再以 multipart 上传；`sample_id` 是给不便构造
multipart 的调用方用的兼容分支。两者都缺 / `sample_id` 不在白名单 → `INVALID_PARAM`。

`run_id = sha256(f"{source_sha256}:{params_hash}:{route_snapshot_version}")[:20]`
（`02-architecture.md §5.2` 的实现形式，分隔符固定为 `:`），不含挂钟时间。

错误统一 `{"code","message","trace_id"}`；框架级 422 额外带 `errors[{loc,msg,type}]` 明细，
**刻意剔除 `input` / `ctx`**（FastAPI 默认会把完整请求体回显到 `detail[].input`，含潜在密钥，是泄漏面）：

| 码 | HTTP | 触发 |
|---|---|---|
| `INVALID_PARAM` | 422 | 参数越界 / 未知策略 / `as_of` 非法 / params 非 JSON |
| `FILE_TOO_LARGE` | 413 | 文件 > 8 MiB |
| `UNSUPPORTED_MIME` | 415 | 非 PDF / 图片 / 纯文本 |
| `STRATEGY_UNAVAILABLE` | 503 | 强制 `ocr_api` 但未接通（A5 内以 `errors[]` 留痕 + 降级结果返回，见 §7 歧义 8） |
| `UPSTREAM_TIMEOUT` / `UPSTREAM_ERROR` | 504 / 502 | 在线档超时 / 上游错误（本轮离线不触发，契约保留） |
| `PII_BLOCKED` | 422 | guardrails 命中 `ACTION_BLOCK`，不回显原文 |
| `OFFLINE_ONLY` | 503 | 离线状态请求实时档（A5 内以 `errors[]` 留痕 + 降级） |
| `RUN_NOT_FOUND` | 404 | `/api/` 下资源不存在 / A8 摘要校验失败（失败响应带 `checks` 全 false）/ 样本缺失 |
| `METHOD_NOT_ALLOWED` | 405 | 方法不允许（如对只读路由发 DELETE） |
| `NOT_FOUND` | 404 | **非** `/api/` 路径的 404（静态资源等） |
| `HTTP_<status>` | 其它 | 其余框架级 HTTP 状态码兜底，仍保持三要素 |
| `INTERNAL_ERROR` | 500 | 未预期异常，只回 `trace_id`，不回堆栈 |

---

## 5. 7 类参数（演示起始值，非压测结论）

| 参数 | 默认 | 取值 | 改了看到什么 |
|---|---|---|---|
| `strategy` | `auto` | `auto / local_only / ocr_api / multimodal / off` | 左区解析路径与 trace 变化；`ocr_api` 显示「未接通，跳过」 |
| `confidence_min` | `0.75` | `0..1` | 低置信块增多 → 更易触发升级 |
| `chunk_size_chars` | `800` | `1..65536` | 右区 `chunks[]` 数量与 `chunk_id` 变化（录像主镜） |
| `chunk_overlap_chars` | `80` | `0..chunk_size-1` | 相邻块重叠文本变化 |
| `vision_escalation_ratio` | `0.10` | `0..1` | 复杂档预算闸门拦截时机（**熔断上限，不是随机分流**） |
| `vision_min_slots` | `1` | `0..10000` | 小文件是否至少允许 1 页升级 |
| `llm_tier_override` | `null` | `null / standard / pro / fallback` | 路由快照档位变化；**只做决策，不调用模型** |
| `redact_enabled` | `true` | bool | 四出口脱敏开关 |
| `ingest_format` | `compact` | `compact / full` | 右区字段集合变化 |
| `as_of` | `2026-11-15T00:00:00Z` | ISO-8601 UTC `Z` | 确定性逻辑时间；**绝不用挂钟时间** |

`params_hash = sha256(规范化 json(sort_keys, 无空格, UTF-8))[:16]`，同参必同、改参必变。

---

## 6. 三态标注（不得越界宣称）

| 状态 | 内容 |
|---|---|
| **已实现** | `parsing/` 三级策略与质量门禁、`guardrails/` 四出口、真实样本与离线解析产物、`showcase/` 站点本体（A1–A10 / 参数快照 / 单文件 manifest / 两档演示）、本机 ffmpeg / PIL / pypdf |
| **只设计** | 收费 API 适配器 profile（`available=False`、`__TBC__`）、在线填真值的 LLM 快照字段（`model` / `prompt` / `params` 为空占位 + `placeholder=true`）、Pro 档真实模型与单价、阿里云实际部署 |
| **未做** | 实时在线视觉调用作为默认路径、playwright 录屏、真实压测 / 质量基线 / 监控 SLO、多租户 / SSO / 逐段落 ACL |

**三条不得**：不把演示切片说成生产实现；不把离线回放说成实时识别；
不把「选出 Pro 档」说成「调用了 Pro 模型」。

---

## 7. 边界声明

- 本轮只新增 `showcase/` 下本成员独占的文件；**不改** `parsing/` `guardrails/` `cache/` `knowledge/`
  `webapp/` `backend/`，**不动冻结区** `eval/` `demo/`，不读 `.env` 的密钥值。
- 依赖方向单向：`showcase/ → parsing/ / guardrails/ / cache/ / knowledge/`；纯函数包不反向 import。
- `parsing/strategies/ocr_api.py::_call()` 保持 `__TBC__`，本轮不填充。
- 所有默认值 / 阈值 / 价格均为**演示起始值或公开调研口径**，不是实测指标、不是账单。
- 观测字段（`latency_ms` / `created_at` / `run_id` / `generated_at`）与可复现字段分离：
  同参双跑逐字节一致的判定只基于 `canonical()` 视图（`ingest_preview.py`）。

### 已知契约歧义（集成时请主理人裁定，实现已给出可执行默认）

1. **A5 内 ①/② 顺序**：`02-architecture.md §6.4` 写「先算 `DifficultySignals` → 再 `parse_document()`」，
   但 spec §2 的 `from_parse_report(rep)` 需要 parse report。本实现按主理人任务硬约束：
   **先 `parse_document()` 一次 → `from_parse_report(rep)` → `TierState.decide()` → `build_views()`**，
   同一份 `rep` 贯穿始终。
2. **`TierState` 构造器签名**未在 spec §2 给出（只钉死 `decide()`）。同伴落盘后实查为
   `__init__(tier="standard")`：A5 用 `TierState(tier="standard")`、A6 把 `current_tier`
   传进构造器；`decide()` 本身没有 `current_tier` 参数。A6 请求契约不含 `event`，
   故固定 `event=None`，避免任意字符串撞上同伴的 `FALLBACK_EVENTS` 白名单而误报 500。
3. **`read_manifest()` 返回类型**未明说，实查为 `-> dict`。它**内部已完成三项规范化投影重算**
   （ingest 投影摘要 / manifest 摘要 / `snapshot_version_for(route)`），任一不符抛 `ManifestError`。
   A8 因此**不重复实现**同伴的私有投影/哈希算法——两套规则不一致会让 A8 恒 500（假阴性）；
   `checks` 以 `read_manifest()` 成功返回为依据，另做 `llm_route.snapshot_version` 字段级交叉核对。
4. **`write_pair()` 的 `path`** 是绝对/相对、是否 `Path` 未定。A5 统一 `str()` 后回填 `manifest_ref`。
5. **`build_views(..., params=...)`** 类型：spec §2 docstring 写「`created_at` 取 `params.as_of`」→
   判定应传 **`Params` 对象**（本实现按此）；若按 `dict` 实现会 TypeError。
6. **A5 顶层 `schema_version`** 未定值 → 复用 §5.2 既有常量 `showcase-ingest@1.0`。
7. **复杂档回放触发条件**未定义 → 本实现用 form 字段 `replay=true` 或 `filename` 以 `complex.png` 结尾；
   「回放」标注在 API 层 `build_views()` 之后、`write_pair()` 之前追加，摘要仍一致。
8. **`STRATEGY_UNAVAILABLE` 503 vs「给降级结果不静默」**：错误码表是 503，V6 是「显示未接通跳过并给降级结果」。
   本实现取 **HTTP 200 + `errors[]` 结构化记录 + `analysis.warnings[]` + 降级后的真实解析结果**，
   两条要求都满足且前端能一屏看到留痕；若主理人要求严格 503，改 `_resolve_strategy()` 一处即可。
9. **compact 档 `ingest.note`**：§5.4 未列 `note`，但 spec §1.1(2) 要求回放标注出现在 `ingest.note`。
   A7 compact 裁剪**刻意保留 `note`**。
10. **A4 `levels[].contract`**：`ParseRouter.status()` 返回的是 `OcrApiStrategy.contract()`（值为
    `__TBC__` 字符串），本实现原样透传，不伪造对象结构。
11. **滞回状态与 Pro 触发（集成裁决 §1.1(7)，已落地）**：`TierState` 的 `k=2` 双阈值滞回
    是防抖动的专业卖点，但每次新建状态会让单次 A5 恒 `standard(hits=1)`，用户看不到
    「解析难度变大会上 Pro 兜底」。裁决：**保留 k=2 与双阈值不变**（不降 k、不加首次直通），
    改为让 A5 复用按 `(source_sha256, params_hash)` 派生的**进程内有限 LRU（≤32 条）
    `TierState` 缓存**（`api.py::decide_with_lru`，`move_to_end` 提活 + `popitem(last=False)`
    淘汰最旧）。效果：同一份复杂档连续两次 A5 → 第 2 次真实 `pro`、
    `hysteresis.consecutive_hits` 1→2；不同文件 / 不同参数不同 key，**不串用户状态**；
    进程重启清零，属演示切片的可丢弃状态。两次 `snapshot_version` 不同 → `run_id` 不同，
    **改档必变是正确行为**，不是缺陷。A6 仍可用 `current_tier` 预演或 `llm_tier_override`
    强制档位；前端显示 `consecutive_hits` 并提示「再跑一次同档位将触发 Pro 兜底」。
    录像与主解释页措辞统一为「难度 0.735 连续 2 次 → pro」，**不得写成单次即 pro**。
12. **`write_pair()` 的 digest 语义**：实查其返回 `sha256` 是 **manifest 摘要**
    （`_dumps(manifest)`），而 `manifest.ingest_ref.sha256` 是 **ingest 投影摘要**。
    A5 的 `manifest_ref.sha256` 直接取 `write_pair()` 返回值，符合 §4.2 契约。

---

## 8. 自测

```bash
# 语法编译（全部本成员 .py）
python -m py_compile showcase/__init__.py showcase/params.py showcase/server.py \
  showcase/api.py showcase/adapters/__init__.py showcase/adapters/base.py \
  showcase/adapters/reducto.py showcase/adapters/aliyun_docmind.py \
  showcase/adapters/local_free.py

# 不依赖同伴模块，可单独验证
python -c "from showcase.params import Params, parse_params; p=Params(); print(p.params_hash, p.as_dict())"
python -c "from showcase.adapters import vendor_profiles; print([(v['id'], v['available']) for v in vendor_profiles()])"
python -c "from showcase.params import parse_params; parse_params({'chunk_size_chars': 0})"
```

集成验证（同伴文件齐备后由主理人统一跑）：A1 → A2 → A4 → A10 → A5 两档 → A7/A8 对拍 → A9。
`runtime.external_request_count` 必须恒为 0；`git diff --exit-code -- eval/ demo/` 必须为空。
