# `parsing/` · 可插拔文档解析子系统

> 一句话：**同一份文件，可以走自研 / 收费 OCR / 多模态三条解析链路；质量门禁决定何时升级，预算闸门限制最贵链路最多占 10% 的页，每一页的判定依据都留成可核对的轨迹。**

## 这个目录是干什么的

`backend/app/services/parser.py` 早就固定了下游契约（`PageText` / `ParsedDoc` / `ChunkSpec` 里的 `page_no`、`section_path`、`block_type`、`confidence`、`source_level`），但「把原始字节变成结构化文本」这一步一直是 `pypdf` 单实现。本目录把这一步抽成**可插拔策略**，换任何一家 OCR 或视觉服务都不动 chunks 表、Milvus schema 与 citation 契约。

三级能力：

| 级别 | 目录实现 | 当前状态 | 成本 |
|---|---|---|---|
| P0 自研 `local` | `strategies/local.py` + `tables.py` + `images.py` | **已实现，零外部依赖可跑** | 0 |
| P1 收费 OCR `ocr_api` | `strategies/ocr_api.py` | **只设计**（适配器 + `__TBC__` 契约），未接任何服务 | `__TBC__` |
| P2 多模态 `multimodal` | `strategies/multimodal.py` | **传图请求构造已实现**；网关实查无视觉模型，端点待补，走结构化降级 | `__TBC__` |

> 已实查：Intern-AI Discovery 网关 `/models` 返回 10 个模型，**全部纯文本，没有视觉模型**；硅基流动侧只有 `BAAI/bge-m3` 嵌入与 `BAAI/bge-reranker-v2-m3` 重排。所以 P2 现在是「请求构造 + 未配置时优雅降级留痕」，**不宣称已跑通视觉理解**。

## 目录地图（每个文件一句话）

```
parsing/
├── README.md          ← 你在这里：入口、演示步骤、面试讲法
├── __init__.py        包导出（ParseRouter / VisionBudget / 契约）
├── contracts.py       数据契约：QualitySignals / ParseResult / ParseTrace / 策略常量
├── config.py          运行时开关与阈值（PARSING_ 前缀，与其它命名空间隔离）
├── images.py          图片容器头解析（PNG/JPEG/GIF/WebP/BMP）+ 字节熵 + 传图请求构造
├── tables.py          表格结构语义（cells 带行列坐标/类型/单位）+ 5 项稳定性检查
├── router.py          ★ 质量门禁 + 确定性升级 + 90:10 预算闸门 + 逐页轨迹
├── server.py          独立 Docker 服务外壳（最小 FastAPI，只依赖本包）
├── strategies/
│   ├── base.py        ParseStrategy 抽象 + 注册表
│   ├── local.py       P0 自研
│   ├── ocr_api.py     P1 收费 OCR 适配器（契约 __TBC__）
│   └── multimodal.py  P2 多模态传图 + 结构化降级
└── tests/
    └── test_parsing.py   18 项离线单测（零外部调用）
```

配套：`webapp/routers_parse.py`（演示切片 API）、`webapp/static/` 的「解析实验室」视图、`Dockerfile.parse` + compose 的 `parser` 服务、`docs/parsing/` 六份原理资料。

## 90:10 到底是什么（面试最容易问）

**不是随机把 10% 的页送去调用最贵的模型。** 随机分流既不能保证疑难页被送到对的链路，又会让本来能自研解决的页白付钱。

这里的 `0.10` 是**上限 / 熔断线**：

1. 每一页先跑 P0，产出可复算的 `QualitySignals`（可提取字符数、文本密度、图片占比、表格单元格数、结构是否自洽、置信度）。
2. 只有命中「零字符 / 无文本层 / 置信度低于阈值 / 表格结构不自洽」才逐级升级到 P1、P2。
3. P2 每次升级先向 `VisionBudget` 申请名额，`slots = max(1, ceil(页数 × 0.10))`；20 页文档就是 2 个名额。
4. 名额用完就**显式标 `escalation_budget_exceeded` 并回退上一级结果**，不静默失败、也不偷偷超支。
5. 正常情况下一份有文本层的文档应该是 **0% 触发**；10% 是防线不是配额目标。

为什么不是 80:20：P2 是最慢最贵的一层，20% 的页走它，尾延迟和成本都会翻倍；10% 足够覆盖扫描件疑难页、复杂表格、纯图片这些长尾。阈值是演示默认经验起始值，需用真实语料校准，不是压测结论。

## 三条命令跑起来（零外部依赖）

```bash
# 1) 纯 Python 自检（不启服务、不发任何网络请求）
python -m pytest parsing/tests -q

# 2) 在演示切片里看轨迹
python -m uvicorn webapp.main:app --port 8765
#    浏览器打开 http://127.0.0.1:8765 → admin / admin123 → 侧栏「解析实验室」
#    选策略 → 上传 .md / .csv / .png / .pdf → 看逐页轨迹表

# 3) 独立 Docker 解析服务（默认 compose 不受影响）
docker compose --profile parsing up -d --build parser
curl http://127.0.0.1:8766/health/live
```

## 前端能选什么

| 策略 | 行为 |
|---|---|
| `auto` | 默认。质量门禁逐级升级，演示推荐 |
| `local_only` | 强制只用 P0，用于展示「自研层能做到哪」 |
| `ocr_api` | 强制先走 P1；未配置时标 `ocr_api_unavailable` 并回退 P0 |
| `multimodal` | 强制先走 P2；当前无视觉端点，展示结构化降级与预算拦截 |
| `off` | 只登记不解析，用于对照 |

「解析实验室」把**同一文件在不同策略下的轨迹差异**并排展示，这是把技术深度讲清楚的关键：每一页用了哪级、置信度多少、耗时多少、为什么升级、有没有被预算拦下、降级原因是什么，全部可见。

## 三级之外还要知道的两件事

- **大文件 / 多文件**：`split_pages()` 用 3000 字符为「页」切纯文本（与 `backend/app/services/parser.py` 的 `page_chars` 对齐），PDF 逐页提取，绝不整块进内存；生产链路的分片上传、`ingest`/`ingest_heavy` 双队列、按页范围并行与 Redis 计数合并见 `docs/parsing/06-department-queue.md`。
- **分部门**：部门是**成本归因与配额标签**，不是隔离边界；进度 key 不带部门（转岗后 SSE 不断连），成本按事件发生时的部门快照记录、不回溯。当前 `backend/` 的 `tenant_id` 是历史命名技术债，完整 13 步数据模型改造见 `docs/org-model-change-plan.md`（方案已定、代码尚未改造）。

## 诚实边界（面试别越线）

- P0 的表格是**规则级**结构还原，处理 Markdown 管道表 / CSV / TSV；不等于工业级无边框表格识别。
- P1 未接入任何收费服务，所有接口字段 `__TBC__`，`available()` 返回 `False` 时路由器降级留痕。
- P2 的 `vision_message()` 能构造标准 OpenAI 兼容 `image_url` + base64 data URI 请求，但**当前没有端点可发**；`structured_fallback()` 输出的是「图片结构事实」，明确标注**非视觉理解**，不编造图片内容。
- 阈值不是实测基线；真实压测、监控告警 SLO、按部门公平调度的工程实现都留在本期之外。

## 原理资料索引

`docs/parsing/` 下按编号阅读：

| 编号 | 主题 |
|---|---|
| 01 | 架构总览与面试讲法（含三态图例对照） |
| 02 | PDF 文本层 / 扫描件 / OCR 原理 |
| 03 | 表格结构语义与稳定性 |
| 04 | 图片与多模态（含无视觉端点时怎么诚实降级） |
| 05 | 升级决策与 90:10 预算闸门 |
| 06 | 多部门 worker 队列与排队机制 |
| 07 | 收费 API 契约槽位（全部 `__TBC__`） |
