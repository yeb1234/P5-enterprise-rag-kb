# Phase 3 独立验收报告 · showcase 演示站点与配套文档

> 产出人：凌无缺（qa-engineer，独立复验）｜日期：2026-11-15｜交付物：**仅本文件**
> 定位：企业内部知识库「开发框架骨架 + 可运行演示切片」，非上线交付版。
> 立场：以怀疑的眼光独立复验，不照抄主理人已跑的连通性基线；只报事实、不改源码、不自行修复。
> 所有命令均为本轮真实实跑；输出按原样摘录。写盘类测试把 `showcase.api.OUT_DIR` 指向系统临时目录；`webapp` 自测用临时 DB + 离线 provider，未污染项目文件。


## 0. 验收对象、方法与基线

**交付物实查**：`showcase/`（server/api/params/llm_router/snapshot/ingest_preview/guard/make_video/verify_integration.py、adapters/、static/、samples/、tests/、README.md、Dockerfile）齐备；`docs/showcase/{01-scope.md 360 行, 02-architecture.md 449 行, 03-phase2-spec.md 251 行, aliyun-guide.html 441 行}`；`interview/{index.html 1018 行, index-standalone.html 1018 行, showcase-demo.mp4 323720 B}`；`.gitignore:24-26` 已配 `showcase/out/*` + `!showcase/out/.gitkeep`。
> 注：`interview/` 位于**项目根**，不在 `docs/showcase/` 下，任务描述路径与实际不符，按实际路径验收。

**环境与方法**：Windows / Git Bash / Python 3.13；`PARSING_OFFLINE=1`；`fastapi.testclient.TestClient(app, raise_server_exceptions=False)` 逐条打点；静态面用 `node --check`、`html.parser.HTMLParser`、正则、`ffprobe`、`ffmpeg` 抽帧 + 图像查看；回归用 `pytest`。**未发起任何真实外部 LLM / OCR / 视觉 API 调用**；`webapp` 自测显式设 `WEBAPP_LLM_PROVIDER=offline`、`WEBAPP_EMBEDDING_PROVIDER=offline`、`WEBAPP_RERANK_PROVIDER=off`。

**主理人基线（仅记录，不当作本轮结论）**：`PARSING_OFFLINE=1 python showcase/verify_integration.py` → 31/31 `INTEGRATION_READY`；`python -m pytest showcase/tests -q` → 12 passed；各包原基线 248 项。本报告不重跑这些断言，只做其未覆盖的边界与错误路径。


## 1. 错误路径与异常契约

**命令**（内联 Python，逐条打印 `status / body_keys / code / message / trace_id`）：
```bash
PARSING_OFFLINE=1 python - <<'PY'
from fastapi.testclient import TestClient; from showcase.server import app
import showcase.api as api, tempfile, json; from pathlib import Path
api.OUT_DIR = Path(tempfile.mkdtemp(prefix="qa-showcase-"))   # 隔离写盘
with TestClient(app, raise_server_exceptions=False) as c:
    c.post("/api/analyze", files={"file": ("empty.pdf", b"", "application/pdf")})        # 空文件
    c.post("/api/analyze", files={"file": ("big.pdf", b"x"*(8*1024*1024+1), "application/pdf")})  # >8MiB
    c.post("/api/analyze", files={"file": ("x.exe", b"MZ", "application/octet-stream")}) # 未知 MIME
    c.post("/api/analyze", data={"sample_id":"simple.pdf","params":"{not json"})         # 非法 params
    c.post("/api/analyze", data={"sample_id":"simple.pdf","as_of":"2026-13-99"})         # 非法 as_of
    [c.post("/api/analyze", data={"sample_id":s}) for s in ("../../.env","..%2f..%2f.env",
        "%2e%2e/.env","/etc/passwd","C:/Windows/win.ini","simple.pdf/../.env")]           # 6 种穿越
    c.post("/api/analyze", data={"sample_id":"simple.pdf","params":json.dumps({"llm_tier_override":"ultra"})})
    c.get("/api/ingest/preview?run_id=abcdef0123456789abcd&format=compact")
    c.get("/api/manifest/abcdef0123456789abcd")
    c.put("/api/params", json=[1,2,3]); c.post("/api/route/decide", json=[1,2])          # 框架级 422
    c.delete("/api/health"); c.get("/nope.html")                                         # 405 / 静态 404
PY
```

**输出（原样摘录）**：
```
A5 empty file     422 keys=['code','message','trace_id'] INVALID_PARAM      '上传文件为空'
A5 >8MiB          413 ... FILE_TOO_LARGE   '文件 8388609 字节超过上限 8388608 字节（8 MiB）'
A5 unknown MIME   415 ... UNSUPPORTED_MIME
A5 bad params     422 ... INVALID_PARAM   'params 不是合法 JSON：Expecting property name ...'
A5 bad as_of      422 ... INVALID_PARAM   'INVALID_PARAM: as_of 必须是 YYYY-MM-DDTHH:MM:SSZ...'
A5 traversal      6/6 → 422 INVALID_PARAM 'sample_id 不在白名单...不接收任意路径'
A5 bad override   422 ... INVALID_PARAM   "llm_tier_override 未知值 'ultra'"
A7 missing run_id 404 RUN_NOT_FOUND ; A8 missing run_id 404 RUN_NOT_FOUND
A9 sk-(纯字母数字) 422 PII_BLOCKED 'guardrails 命中 ACTION_BLOCK，已拒绝；不回显原文'
--- framework-level validation ---
A3/A6/A9 body=list → 422 keys=['detail'] code=None trace_id=None
405 → 405 keys=['code','message','trace_id'] code='INTERNAL_ERROR' msg='Method Not Allowed'
static 404 → 404 ... code='RUN_NOT_FOUND' msg='Not Found'
```

**判定**：空文件 / 超大 / 未知 MIME / 非法 params / 非法 as_of / 非法 override / 6 种路径穿越 / A7·A8 不存在 run_id / 合法形态密钥 A9，共 14 项均返回统一错误体且不回堆栈；白名单与 `_RUN_ID_RE` 有效。
- **D-02（P1）**：`server.py` 未注册 `RequestValidationError` handler → 框架级 422 返回 `{"detail":[...]}`，**无 code/message/trace_id**，违反 `02-architecture.md §4.3` / `README.md §4`；且 `detail[].input` 回显完整请求体（见 §1.4 实测密钥串出现在 body）。
- **D-09（P2）**：`server.py:74` 把 404 一律映射 `RUN_NOT_FOUND`、非 404 一律 `INTERNAL_ERROR`，实测 405 → `INTERNAL_ERROR`、静态 404 → `RUN_NOT_FOUND`，语义错配。

### 1.4 A9 `ACTION_BLOCK` 专项（**发现 P0**）

**命令**：六种 `sk-` 形态逐个 `POST /api/guardrails/scan`；再用 `text/plain` 上传含漏检密钥的 `.txt` 走 A5，检查响应与落盘文件。

```
=== A9 各形态 sk- 密钥 ===
  plain-32      sk-abcdefghijklmnopqrstuvwxyz123456          HTTP=422 echo_secret=False
  hyphen-proj   sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 HTTP=200 blocked=False hits=0 echo_secret=True
  hyphen-upper  sk-QAENGINEER-abcdefghijklmnop1234567890      HTTP=200 blocked=False hits=0 echo_secret=True
  openrouter    sk-or-v1-abcdef1234567890abcdef1234567890     HTTP=200 blocked=False hits=0 echo_secret=True
  digits-16     sk-1234567890123456                           HTTP=422 echo_secret=False
  short         sk-abc                                        HTTP=200 blocked=False hits=0 echo_secret=True
=== A5 上传 .txt 含漏检密钥 ===
  A5 status: 200 | ingest contains secret: True | written ingest file contains secret: True
  guardrails blocked: False | chunks[0].text: 凭证 sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 用于调用。...
```

根因实查：`guardrails/detectors.py:79` 正则 `sk-[A-Za-z0-9]{16,}` **字符集不含 `-`**。`sk-proj-*` / `sk-or-v1-*` 等真实供应商形态在 `sk-` 后带连字符，连续字母数字段不足 16 → 漏检。漏检时 A9 返回 200 且 `results[].text` 回显输入原文；A5 把明文写进响应 `ingest.chunks[].text` **并落盘** `out/<run_id>.ingest.json`。详见 **D-01**。


## 2. 确定性边界

**命令**：同参双跑 `simple.pdf` 比较 `full ingest` 与 `canonical_dumps`；复杂档连跑两次；逐项改 10 个参数各一次；检查 ingest 挂钟字段；读 A8 manifest。

```
=== 1. 同参双跑 (simple.pdf, 同一 LRU key) ===
run_id equal: True e6e54a4716ba2a2168ac
FULL ingest identical: False | canonical identical: True | differing top keys: ['llm_route']
=== 2. 挂钟/耗时是否混入右侧 ingest ===
  ingest contains wall_clock/elapsed_ms/latency_ms/generated_at: False (四项全 False)
  runtime keys: ['elapsed_ms','external_request_count','wall_clock'] created_at: 2026-11-15T00:00:00Z
=== 3. 复杂档第 1 / 第 2 次 ===
tier/hits/snap: standard 1 5a91b4961a -> pro 2 c21bbc5e9b
run_id equal: False | canonical identical: False | FULL identical: False
=== 4. 逐项改参（每次新 LRU key） ===
  strategy/confidence_min/chunk_size_chars/chunk_overlap_chars/vision_escalation_ratio/
  vision_min_slots/llm_tier_override/redact_enabled/ingest_format/as_of
  → 10/10 run_id_changed=True 且 canonical_changed=True
  as_of=2026-01-01T00:00:00Z 时 created_at=2026-01-01T00:00:00Z
=== 5. A8 manifest 参数快照（compact 默认档） ===
  checks: {'ingest_sha256_match': True, 'route_snapshot_match': True}
  manifest.params_hash = ''  |  manifest.config_snapshot = {}
  manifest.source_sha256 = '19fa8feaf14daa...'（真实）
  warnings: ['ingest 未提供 params_hash：空占位（不编造）', 'ingest 未提供 config_snapshot：{} 空占位（不编造）', ...]
  ingest 顶层是否含 params_hash: False | analysis.params_snapshot.params_hash: a6473d24577ef975
```

**判定**：挂钟 / `elapsed_ms` / `latency_ms` / `generated_at` 均未进入右侧 `ingest`；`created_at` 严格取 `as_of`；10 个参数逐项改动全部触发 `run_id` 与 canonical 投影变化；复杂档升档改 `run_id` 与 `README.md §7` 第 11 条声明一致。
- **D-06（P2）**：`01-scope.md` G5 / M5 / U3 / V5 / §4.3 均写「同参双跑 **入库 JSON 逐字节一致**」，实测**完整 ingest 不一致**（差异在 `llm_route` 的 `route_id`/`hysteresis`/`reason`），只有 `canonical()` 投影一致；复杂档连 `canonical` 都不一致。`README.md:154` 有「判定只基于 canonical() 视图」限定，需求文档没有 —— G5 按字面验收会失败。
- **D-07（P2）**：`manifest.params_hash` / `config_snapshot` 恒为空占位。根因是 `_build_manifest()` 从 `ingest` 取这两字段，而 `build_views()` 从未写入；参数仅通过 `run_id` 间接绑定。代码用 warnings 如实标注「空占位（不编造）」（不属隐瞒），但「LLM / 提示词 / 参数同步跟着单文件走」在字段级未落地。


## 3. 状态隔离

**命令**：40 个不同 `chunk_size_chars` 各跑一次 A5；复杂档同 key 连跑两次后换 key；`subprocess` 另起进程读 `_TIER_STATE_LRU` 长度。

```
=== 1. LRU 容量收敛 ===  after 40 distinct keys, LRU size = 32 max = 32
=== 2. 不同 key 不串档 ===
  keyA 1st/2nd: standard pro | different params 1st: standard | different sample 1st: standard
  LRU keys count: 3
=== 3. 重启进程后状态清零 ===  fresh process LRU size = 0 | in-process size = 3
```

**判定：通过**。`decide_with_lru()` 按 `(source_sha, params_hash)` 隔离；40 个不同 key 后收敛到上限 32；换参数 / 换样本第 1 次均回落 `standard`，不串档；新进程状态为 0（进程内可丢弃状态，与三态声明一致）。


## 4. 双向摘要与 A8 `checks`

**命令**：取一次 `full` 档 A5 产物，对 `out/<rid>.{ingest,manifest}.json` 做 5 类篡改，每类改完 `GET /api/manifest/{rid}` 后还原。

```
baseline (untouched)                 HTTP=200 checks={'ingest_sha256_match': True, 'route_snapshot_match': True}
1 tamper ingest business field       HTTP=500 INTERNAL_ERROR 'manifest 摘要校验失败：ingest 摘要不匹配...'
2 tamper manifest.route.model        HTTP=500 INTERNAL_ERROR 'manifest 摘要校验失败：manifest 摘要不匹配...'
3 tamper ingest.manifest.sha256      HTTP=500 INTERNAL_ERROR 'manifest 摘要校验失败：manifest 摘要不匹配...'
4 tamper manifest.ingest_ref.sha256  HTTP=500 INTERNAL_ERROR 'manifest 摘要校验失败：ingest 摘要不匹配...'
5 stale route.snapshot_version       HTTP=500 INTERNAL_ERROR 'manifest 摘要校验失败：manifest 摘要不匹配...'
```

**判定**：ingest 业务字段、`manifest.route`、双向互引字段、`snapshot_version` 过期共 5 类篡改**全部被拦**；`read_manifest()` 的 ① ingest 投影摘要 / ② manifest 摘要 / ③ 版本重算链路有效；错误信息只回摘要前缀，不含堆栈或密钥。
- **D-03（P2）**：`api.py:643-646` 的 `checks` 两项**硬编码 `True`**，`route_snapshot_match` 仅在两侧字段齐全时才做字段级比较；一旦 `read_manifest()` 抛错就直接 500、响应体不含 `checks`。因此 `checks` **在任何可观测路径下都不会是 False**，是装饰性字段，不真实反映校验结果。
- **D-04（P1）**：`README.md:109` 与 `02-architecture.md:238` 写「摘要校验失败 → `RUN_NOT_FOUND` 404」，`snapshot.py:130` 也写「映射 404 / 500」；实测 5/5 返回 **500 `INTERNAL_ERROR`**（`api.py:630`）。文档与实现不一致。


## 5. 前端静态与契约

**命令**：`node --check showcase/static/app.js`；Python 内联做 HTMLParser 标签配对、`https?://` 与 `src/href` 外链扫描、`index.html` 的 `id=` 与 `app.js` 的 `$('id')` 双向比对、`/api/*` 字面量与 `app.openapi()['paths']` 比对、诚实标注关键词计数。

```
node --check app.js OK
=== 1. 外部 URL / CDN ===
  showcase/static/{index.html,app.js,style.css}  hits=0
  interview/{index.html,index-standalone.html}   hits=0
  docs/showcase/aliyun-guide.html                hits=8（均为 http://127.0.0.1:8766/8767 本机命令示例）
  src/href 非相对外链：6 个文件全部 0
=== 2. HTML 标签配对 ===  4 个 HTML 全部 unclosed=0 errors=0
=== 3. app.js /api 字面量 vs OpenAPI ===
  OpenAPI paths: 9 ['/api/analyze','/api/guardrails/scan','/api/health','/api/ingest/preview',
    '/api/manifest/{run_id}','/api/params','/api/route/decide','/api/samples','/api/strategies']
  app.js literals: ['/api/analyze','/api/health','/api/manifest/','/api/manifest/{run_id}',
    '/api/params','/api/samples','/api/strategies'] → 7/7 matched=True（前端是后端 9 路径的子集）
=== 4. DOM id 双向 === index.html ids: 50 | app.js $('id') refs: 50 | MISSING=[] | 未引用=[]
=== 5. 诚实标注 === 「空占位」「回放」「非实时在线识别」「external_request_count」
  「选出 Pro 档」「调用了 Pro 模型」「不发起外部」「未接通，跳过」
  → 在 index.html / app.js / README / 01~03 文档 / 两个 interview HTML 中均成对出现且为否定式表述
```

**判定：通过**。语法、标签配对、外部 CDN 零命中、DOM id 50/50 双向一致、API 路径为 OpenAPI 子集、诚实标注齐备；未发现把「离线回放」写成「实时识别」、把「选出 Pro 档」写成「调用了 Pro 模型」。
- **观察项（非缺陷）**：前端未接线 `/api/route/decide`、`/api/ingest/preview`、`/api/guardrails/scan` 三个端点。
- **D-08（P2）**：`app.js:118-120` 注释称「实查 server.py：仅 `app.mount("/")`，`samples/` 未挂载……需协调 backend-engineer 补一条静态挂载」，但 `server.py:98-102` **已挂载** `/showcase/samples`（集成裁决·致命项），`verify_integration.py`「静态样本可达」也 PASS。注释与实现事实不符。


## 6. 录像与页面

**命令**：
```bash
ffprobe -v error -show_entries format=duration,size -show_entries \
  stream=codec_name,codec_type,width,height -of default=noprint_wrappers=1 interview/showcase-demo.mp4
ffmpeg -v error -y -ss 8|38|70 -i interview/showcase-demo.mp4 -frames:v 1 <tmp>/frame_*.png
# Python：base64 解码 data URI 与 mp4 逐字节比对；相对链接目标存在性；录像数值复算
```

```
ffprobe: codec_name=h264 codec_type=video width=1280 height=720 duration=84.040000 size=323720
3 帧（8s/38s/70s）图像查看：中文清晰，无方框/豆腐块乱码
  8s  左 level=local confidence=1.0 text_chars=1673 trace.reason=local_ok
      右 "run_id": "DEMO-SIMPLE-…", "parser": {...}, "chunks": [ … ]
  38s 左 level=multimodal reason=vision_unavailable_fallback degraded=vision_unavailable confidence=0.35
  70s 左 PARSING_OFFLINE=1 runtime.external_request_count=0；产物由 make_samples.py 从真实解析链路生成
=== 数值与真实样本对拍 ===
  real n800/n400 = 3 6（index.html 声明 3 块 / 6 块，一致）
  real difficulty complex = 0.735（make_video.py 硬编码 0.735，一致）
  four-gate hits = [2,2,2,2]；simple.report.json text_chars=1673/confidence=1.0；
  complex.report.json confidence=0.35/vision_unavailable_fallback → 与 8s/38s 帧一致
=== standalone data URI ===
  base64 chars: 431628 | decoded bytes: 323720 | mp4 bytes: 323720
  byte-identical: True | sha256 8b126d1e64b4be55 == 8b126d1e64b4be55
=== 相对链接目标存在性 ===
  interview/index.html 4 个 missing=0；index-standalone.html 2 个 missing=0；aliyun-guide.html 0 个
```

**判定：通过**。录像 h264 / 1280x720 / 84.04 s；3 帧中文无方框；帧内数值与 `samples/*.report.json` 及 `make_video.py` 真实计算一致；`index.html:308-310` 如实标注「真实解析产物程序化渲染，不是摆拍、不是伪造 JSON」；standalone data URI 与 mp4 **逐字节一致**；两个 HTML 零外部 CDN，相对链接目标全部存在。
- **D-11（P3）**：两个 HTML `:325` 写「84.04 秒，**302 KB**」，实测 `size=323720` 字节 = 316.13 KiB / 323.72 KB，时长一致但体积标注失真。
- **D-13（P3）**：`make_video.py:260` 把 `"difficulty": 0.735` 写成字面量（当前与真实计算一致，样本一变即失真），建议改 `f"{dc:.3f}"`。
- **D-15（P3）**：`index-standalone.html:322` 仍保留「请打开同目录的 `showcase-demo.mp4`」fallback，且 `:347/:430` 保留 `../docs/showcase/aliyun-guide.html` 相对外链；宣称「无同目录依赖」时措辞不严谨（录像本身确无依赖）。


## 7. 范围纪律与三态标注

**命令**：
```bash
git diff --exit-code -- eval/ demo/ webapp/ backend/ parsing/ guardrails/ cache/ knowledge/
git grep -n -E 'sk-[A-Za-z0-9]{20,}' -- .
git grep -n -E 'sk-[A-Za-z0-9]{20,}' -- showcase/ docs/showcase/ interview/ .gitignore
# .env 精确检查：仅取 KEY/SECRET/TOKEN 类键的值（不打印值本身），在 43 个新增交付文件中子串比对
```

```
范围纪律 git diff --exit-code → exit=0（零输出）
全仓 git grep 密钥模式（3 处）：
  guardrails/pipeline.py:51  {"label": "API 密钥（应被拦截）", "text": "请使用 sk-abcdefghijklmnop1234567890 调用接口。"}
  guardrails/tests/test_guardrails.py:129 / :132  同一假密钥 fixture
新增交付物内密钥模式 → exit=1（零命中）
.env 精确检查：secret-like keys 3 ['SENSENOVA_API_KEY','DISCOVERY_API_KEY','RERANK_API_KEY']
  value len=35/67/51（sk*** 前缀）→ in_new_files=False / False / False
最终 git status --short：M .workbuddy/memory/*、M interview/index.html、
  ?? docs/showcase/、?? interview/index-standalone.html、?? interview/showcase-demo.mp4
  （均为本轮既有在途交付物，非本验收写入；webapp/out/ 已被 .gitignore:21 忽略）
```

**判定**：范围纪律 diff 为空；`.env` 三个真实密钥值未出现在任何新增文件；新增交付物密钥模式零命中；三态标注与事实基本一致。
- **D-10（P2）**：任务要求的「**全仓** `git grep` 密钥模式零命中」不成立 —— `guardrails/` 3 处命中。经查均为测试用假密钥字面量 `sk-abcdefghijklmnop1234567890`，不是真实密钥，但验收标准需明确排除区，否则该条永远无法通过。
- **D-12（P3）**：`README.md §5` 标题「7 类参数」但表格列 **10 行**；`01-scope.md §4.1` 的「7 类」是逻辑分组（P3 分块大小/重叠算一类、P4 视觉预算算一类），实现 `Params` 有 10 个字段（另含 `as_of` 等）。口径不统一。
- **D-14（P3）**：`A5 bad params JSON` 的 message 回显 JSON 解析异常文本；`A5 bad as_of` 出现 `INVALID_PARAM: INVALID_PARAM: ...` 前缀重复（`parse_params` 的 ValueError 自带前缀被直接 `str(exc)` 透传）。不泄漏堆栈，但文案冗余。


## 8. 全量回归

**按包实测**（`PARSING_OFFLINE=1 python -m pytest -q <dir>`）：
```
webapp/tests 101 passed | parsing/tests 22 | knowledge/tests 23 | cache/tests 19
guardrails/tests 27 | backend/tests 26 | eval/tests 14 | evalbench/tests 16
showcase/tests 12  → 合计 260 passed（0 failed）
```
> 主理人基线记「各包原基线 248 项」；本轮实跑 **260**（按包汇总），差异来自后续新增用例，非回归失败。

**全仓一键回归**：
```
$ PARSING_OFFLINE=1 python -m pytest -q
ERROR evalbench/tests/test_evalbench.py
E   ModuleNotFoundError: No module named 'tests.test_evalbench'
Interrupted: 1 error during collection
$ PARSING_OFFLINE=1 python -m pytest -q --import-mode=importlib
E   ModuleNotFoundError: No module named 'app'（backend/tests 4 个文件）
Interrupted: 4 errors during collection
```
**D-05（P1）**：全仓一键 `pytest -q` 无法收集（`eval/tests` 与 `evalbench/tests` 同名 `tests` 包冲突；`--import-mode=importlib` 又导致 backend 侧 `app` 不可导入）。按包跑全绿。属**既有回归入口限制**（非本轮 showcase 引入），但影响「一键全量回归」可用性。

**webapp 端到端自测**（离线 provider + 临时 DB 起 8765，未触发外部 LLM）：
```bash
WEBAPP_DB_PATH=<tmp>/app.db WEBAPP_LLM_PROVIDER=offline \
WEBAPP_EMBEDDING_PROVIDER=offline WEBAPP_RERANK_PROVIDER=off \
PARSING_OFFLINE=1 python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
# curl → HTTP 200；python webapp/selftest.py
```
```
PASS C13 断线恢复 resume_from 续传且索引连续 :: first=30, got=270, contiguous=True
PASS C30 评测中心对象清单与 eval/ 事实源一致 :: count=14, registry=REG-KBQA-EVAL, verdict=NOT_COMPARABLE
PASS C33 侧栏十视图与加载器双向齐全 :: nav=10/10, loaders=6/6
=== SUMMARY === {"total": 33, "passed": 33, "failed": 0, "verdict": "DEMO_READY"}
```
**通过**：33/33 `DEMO_READY`，未回归；服务随后已停止；`webapp/out/` 被 `.gitignore:21` 忽略。


## 9. 缺陷清单（按严重度）

| ID | 级别 | 缺陷 | 证据 | 建议修复方向（**本报告不自行修复**） |
|---|---|---|---|---|
| **D-01** | **P0** | `sk-` 密钥检测漏检含连字符形态；A9 回显原文、A5 落盘明文 | `guardrails/detectors.py:79` `sk-[A-Za-z0-9]{16,}`；实测 `sk-proj-*` / `sk-or-v1-*` / `sk-QAENGINEER-*` 全部 200、`blocked=false`、`hits=0`、`echo_secret=True`；上传 `.txt` 后 `ingest` 与 `out/*.ingest.json` 含密钥原文 | 正则补 `-`/`_`；A9 无论是否 blocked 都不回显输入原文；A5 落盘前兜底扫描。根因在 `guardrails/`（本轮范围外），需主理人分派 |
| **D-02** | **P1** | 统一错误契约在框架级 422 失效，且回显用户输入 | `PUT /api/params`、`POST /api/route/decide`、`POST /api/guardrails/scan` 传 list → `{"detail":[...]}`，无 code/message/trace_id；`detail[].input` 含完整输入（实测密钥串出现在 body） | `server.py` 注册 `RequestValidationError` handler 归一，并避免回显 `input` |
| **D-03** | P2 | A8 `checks` 恒 True，不反映校验结果 | `api.py:643-646` 硬编码 True；5 类篡改时直接 500、响应体无 checks | `checks` 由 `read_manifest()` 真实重算结果填充；失败时返回结构化 checks |
| **D-04** | **P1** | 摘要校验失败返回 500，与文档声明的 404 不符 | 实测 5/5 → `500 INTERNAL_ERROR`；`README.md:109`、`02-architecture.md:238`、`snapshot.py:130` 均写 404 `RUN_NOT_FOUND` | 统一实现与文档：改 `api.py:630` 或修订文档与 docstring |
| **D-05** | **P1** | 全仓 `pytest -q` 收集失败 | 原生 `No module named 'tests.test_evalbench'`；`--import-mode=importlib` → `No module named 'app'`（4 errors）；按包跑 260 passed | 各 `tests/` 加 `__init__.py` 或 CI 固定按包跑；回归入口文档写明 |
| **D-06** | P2 | 「同参双跑入库 JSON 逐字节一致」在需求文档层面不可达 | `01-scope.md` G5/M5/U3/V5/§4.3 写「逐字节一致」；实测完整 ingest 不一致（仅 `llm_route` 差异），复杂档连 canonical 也不一致 | 需求文档补「判定基于 `canonical()` 投影；复杂档滞回升档属改档必变，不在 V5 适用范围」 |
| **D-07** | P2 | manifest `params_hash` / `config_snapshot` 恒为空占位 | A8 实测 `''` / `{}`；`build_views()` 未写入这两字段；warnings 如实标注 | 若为硬要求，`build_views()` 把 `params_hash`/`config_snapshot` 写进 ingest；否则文档明确「参数通过 run_id 间接绑定」 |
| **D-08** | P2 | `app.js` 实现说明注释与 server.py 事实不符 | `app.js:118-120` 称 samples 未挂载；`server.py:98-102` 已挂 `/showcase/samples` | 更新注释为集成后事实 |
| **D-09** | P2 | 405 / 静态 404 错误码语义错配 | 405 → `INTERNAL_ERROR`；静态 404 → `RUN_NOT_FOUND`（`server.py:74`） | 按 status 细分映射，静态 404 用独立码 |
| **D-10** | P2 | 「全仓 git grep 密钥零命中」不成立 | `guardrails/pipeline.py:51`、`tests/test_guardrails.py:129/132` 共 3 处假密钥 fixture | 验收标准明确排除 `guardrails/` 测试 fixture；新增交付物已零命中 |
| **D-11** | P3 | 录像体积标注失真 | 两个 HTML `:325` 写 302 KB；ffprobe `size=323720`（316.13 KiB / 323.72 KB） | 改为「约 316 KiB」或写字节数 |
| **D-12** | P3 | 「7 类参数」与 10 字段口径不统一 | `README.md §5` 标题 7 类、表格 10 行；`01-scope.md §4.1` 为逻辑分组 | 标题改「10 个字段（归 7 类）」 |
| **D-13** | P3 | `make_video.py` 硬编码难度值 | `:260` `"difficulty": 0.735`；当前与真实计算一致，样本变化会失真 | 改为 `f"{dc:.3f}"` |
| **D-14** | P3 | 错误文案冗余 / 回显解析异常文本 | `INVALID_PARAM: INVALID_PARAM: as_of...`；`params 不是合法 JSON：Expecting property name...` | 去重复前缀；解析异常只回概要 |
| **D-15** | P3 | standalone 的 fallback 文案与「无同目录依赖」措辞不严谨 | `:322` 仍提示打开同目录 mp4；`:347/:430` 保留 `../docs/...` 相对外链 | 精确表述为「录像无同目录依赖」或移除 fallback |

**统计**：15 项（P0 × 1、P1 × 4、P2 × 6、P3 × 4，D-03/D-04 同源于 A8 但分别计）。


## 10. 未验证项（如实说明）

| 项 | 原因 |
|---|---|
| 浏览器内实际播放 mp4 / 页面交互渲染 | 本机无 playwright / selenium / 浏览器自动化（只读探测确认）；以 `ffprobe` + 3 帧图像查看 + `node --check` + HTMLParser + data URI 逐字节比对替代 |
| 真实外部 LLM / OCR / 视觉 API 链路 | 本轮离线纪律（用户上轮流量耗尽），且 `showcase` 明确 `offline=True`；不验证也不应验证 |
| 真实线上部署 / 阿里云可用性 | 项目定位为骨架 + 演示切片，`aliyun-guide.html` 仅为指导文档；不在本次验收范围 |
| 压测 / QPS / P95 / 准确率 | 按 `01-scope.md` W6 与三态标注属「未做」，无基线可验，不编造数值 |


## 11. 放行结论

| 检查组 | 结论 |
|---|---|
| 1 错误路径与异常 | 14 项通过；**D-01（P0，密钥漏检+回显+落盘）**、D-02（P1）、D-09（P2） |
| 2 确定性边界 | 挂钟隔离 / 10 参数改必变 / as_of 生效通过；D-06、D-07（P2） |
| 3 状态隔离 | **全部通过**（40→32、不串档、重启清零） |
| 4 双向摘要 | 5 类篡改全拦；D-03、D-04（P2/P1，`checks` 恒 True 与码值不符） |
| 5 前端静态与契约 | **全部通过**（node / 标签 / CDN / DOM 50:50 / API 子集 / 诚实标注）；D-08（P2） |
| 6 录像与页面 | **全部通过**（编码时长 / 中文无方框 / 数值真实 / data URI 逐字节 / 链接齐全）；D-11、D-13、D-15（P3） |
| 7 范围纪律 | diff 为空、`.env` 三密钥未泄漏、新增文件零命中；D-10、D-12、D-14（P2/P3） |
| 8 回归 | 按包 **260 passed**；`webapp/selftest.py` **33/33 DEMO_READY**；全仓一键收集失败（D-05，P1） |

### 放行判定：**No-Go（阻断）**

1. **D-01 为 P0 安全缺陷且可无前置条件复现**：真实供应商密钥形态（`sk-proj-*` / `sk-or-v1-*`）100% 漏检，A9 回显原文、A5 把明文写进响应与落盘产物，与 `README.md §2`「密钥值不进响应 / manifest / 日志」及 A9「不回显原文」契约直接冲突。
2. **D-02 为 P1 契约缺陷**：统一错误体在框架级 422 失效，并把请求体（含可能的密钥）回显到 `detail[].input`，与 D-01 叠加放大泄漏面。
3. **D-04 / D-05 影响判定与回归可用性**：A8 摘要校验失败码值与文档不符；全仓一键回归无法收集。

功能连通性（31/31 集成、12 项 showcase 单测、按包 260 passed、33/33 selftest）**不构成放行依据** —— 这些恰是主理人已跑的基线，未覆盖上述边界。

### 复验建议（最小集合，修复后执行）

1. 修复 D-01 后重跑 §1.4 的 6 形态矩阵 + A5 `.txt` 落盘检查，要求 6/6 拦截、响应与产物均不含原文。
2. 修复 D-02 后重跑框架级 422 三项，要求全部 `{"code","message","trace_id"}` 且 `detail[].input` 不回显。
3. 对齐 D-04 码值并重跑 §4 五类篡改；修复 D-03 后确认 `checks` 能出现 `False`。
4. 明确 D-05 回归入口（按包跑或修包结构），同步 D-06 / D-07 / D-08 文档口径。
5. P3 项同批顺带修订；修订后复跑 `git diff --exit-code -- eval/ demo/ webapp/ backend/ parsing/ guardrails/ cache/ knowledge/` 与新增文件密钥 grep。


> 本报告只写入 `docs/showcase/04-acceptance.md`；未修改任何源码或其它文件；未运行真实外部 LLM 调用；所有数值均来自本轮实跑或既有事实源，未编造。修复由主理人分派，本报告不自行改动。

---

## 12. 修复回填（主理人复核 · 2026-11-15 晚）

> §11 的 **No-Go 是验收时点判定，保留不回改**。本节记录 15 条缺陷的处置与复验证据；
> 最终放行以本节为准。

### 12.1 逐条处置

| 缺陷 | 级别 | 处置 | 证据（实跑） |
|---|---|---|---|
| D-01 密钥漏检 | P0 | **已修**：`guardrails/detectors.py` 正则字符集纳入 `-`/`_`；A9 不再回显输入（`echo_suppressed=true` 且 `results[].text` 恒空）；A5 落盘前兜底扫描，命中即 422 `PII_BLOCKED` 且不写文件 | 5 种真实形态（`sk-proj-*` / `sk-or-v1-*` / `sk-<TENANT>-*` / 纯数字 16 位 / 无连字符）全部 `blocked=True`、`回显原文=False`；`sk-abc` 不误报；guardrails 单测 **33 passed**（新增 6 项防回归） |
| D-02 框架级 422 | P1 | **已修**：`server.py` 注册 `RequestValidationError` handler，明细只留 `loc/msg/type`，剔除 `input`/`ctx` | 三接口传错类型均 422 `INVALID_PARAM`，响应含 `code/message/trace_id`（框架级路径另带 `errors`），`json.dumps(响应)` 不含提交的密钥串 |
| D-04 A8 码值 | P1 | **已修**：`ManifestError` → 404 `RUN_NOT_FOUND` | 五类篡改（manifest.route / 互引字段 / ingest 业务字段 / manifest.sha256 / stale 版本）全部 404 |
| D-05 一键回归 | P1 | **不修（如实标注）**：属既有回归入口限制（多个同名 `tests` 包 + backend 的 `app` 包路径），非本轮引入 | 按包回归 **260 passed**；README 与交付说明写清「按包跑」 |
| D-03 A8 checks | P2 | **已修**：成功时三项 checks（新增 `manifest_sha256_match`）；失败时错误体带 `checks` 全 false + `checks_note` | 基线 200 三项 True；五类篡改 404 且全 false；恢复后复验 200 三项 True |
| D-06 文档口径 | P2 | **已修**：`01-scope.md` 新增 §0.0 回填，V5 判定改为「入库内容规范化投影逐字节一致」，并说明原因 | 投影剔除决策实例字段、保留 `trace[].reason` 与 `source.sha256`；同参投影一致、改参必变已实测 |
| D-07 manifest 参数空占位 | P2 | **已修**：`ingest_preview.py` 顶层补 `params_hash` / `config_snapshot` / `params_snapshot` | 实测 `manifest.params_hash=58f6c050565383a4`，`config_snapshot` 7 个键齐全 |
| D-08 前端过时注释 | P2 | **已修**：`app.js` / `style.css` 注释改为「样本由 `/showcase/samples` 窄路径提供，保留两条兜底」 | 前端自测 `node --check` / 标签配对 / 外部资源零命中 / DOM id 50/50 |
| D-09 错误码语义 | P2 | **已修**：405 → `METHOD_NOT_ALLOWED`；`/api/` 下 404 → `RUN_NOT_FOUND`；其余 404 → `NOT_FOUND` | `DELETE /api/health` → 405；`/api/unknown` → 404 RUN_NOT_FOUND；`/no-such-static.txt` → 404 NOT_FOUND |
| D-10 全仓密钥 grep | P2 | **不修（已证伪）**：3 处命中是 `guardrails/` 的**假密钥 fixture**，用于单测，非真实凭证 | 新增交付物 43 个文件密钥 grep 零命中；`.env` 三个真实值未出现在任何新增文件 |
| D-11 录像大小 | P3 | **已修**：主页面写「约 316 KiB」（实测 323,720 B） | `ffprobe` 实测 84.04 s / 323,720 B |
| D-12 参数计数 | P3 | **已修**：改为「7 类 / 10 项配置」并列出 `as_of` | 与 `/api/params` 实际字段一致 |
| D-13 录像硬编码 | P3 | **已修**：`make_video.py` 改用实算 `dc` 插值 | 重录输出 `difficulty=0.735` 由代码算出 |
| D-14 文案前缀重复 | P3 | **已修**：`_fail` 检测 `INVALID_PARAM:` 前缀后剥离 | 实跑错误文案不再出现双前缀 |
| D-15 绝对表述 | P3 | **已修**：改为「录像已 data URI 内嵌，无需同目录 mp4；若浏览器不支持 data URI 视频，请直接打开 mp4 文件」 | 主页面与 standalone 同步（重跑 `make_video.py`） |

### 12.2 复验结果（主理人实跑）

```
PARSING_OFFLINE=1 python showcase/verify_integration.py
  → {"total": 31, "passed": 31, "failed": 0, "verdict": "INTEGRATION_READY"}

python -m pytest -q showcase/tests guardrails/tests
  → 45 passed（showcase 12 + guardrails 33）

P0 六形态矩阵：6/6 拦截；A5 上传含密钥 txt → 422 PII_BLOCKED 且 out/ 无新增文件
A8 五类篡改 → 404 RUN_NOT_FOUND + checks 全 false
D-07 manifest → params_hash 非空、config_snapshot 7 键
```

### 12.3 最终放行判定：**Go（本轮范围，附条件）**

- 阻断项 D-01/D-02/D-04 已修并有实跑证据；D-03/D-06/D-07/D-08/D-09 已修；
  D-05/D-10 经复核为「既有入口限制」与「已证伪的假阳性」，已如实标注而非掩盖。
- **附条件（沿用既有三态纪律）**：① 按包回归而非一键收集；② 真实 PG/Milvus/Redis/Celery 未接入、
  未做集成测；③ 模型级 NER、图片/表格内 PII、压测与 SLO 未做；④ 阿里云仅交付指导，未实际部署。
- 已知限制（有意取舍）：A5 落盘前兜底扫描会把「文档里本来就有的 `sk-…` 示例串」也阻断，
  宁可阻断不可漏检；前端文案已改为「疑似含密钥，已阻断落盘」，不写成系统错误。
