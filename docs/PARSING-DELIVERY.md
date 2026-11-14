# 解析能力升级 · 交付说明（可直接拿去讲）

> 面向：把项目目录直接打包上传 / 投屏给考官的场景。
> 本文只做**导航与话术**，不重复原理（原理在 `docs/parsing/01–07`）。
> 定位：开发框架骨架 + 可运行演示切片，**不是上线交付版**。

---

## 1. 一页地图：每个目录该讲什么

| 路径 | 一句话 | 考官问到就打开 |
|---|---|---|
| `parsing/README.md` | **先打开这个**。三级策略、90:10 的真正含义、目录地图、三条演示命令 | 「你这个解析是怎么设计的？」 |
| `parsing/router.py` | 质量门禁 + 确定性升级 + 预算闸门 + 逐页轨迹，核心逻辑都在这一个文件 | 「什么时候会调用贵的模型？」 |
| `parsing/contracts.py` | 数据契约：`QualitySignals` / `ParseResult` / `ParseTrace` | 「怎么保证换供应商不改下游？」 |
| `parsing/strategies/` | 三个策略实现，`base.py` 是抽象，`local.py` 能跑，`ocr_api.py` / `multimodal.py` 是契约+降级 | 「自研版具体做了什么？」 |
| `parsing/tables.py` | 表格结构语义：cells 带行列坐标/类型/单位 + 5 项稳定性检查 | 「表格为什么会读错？」 |
| `parsing/images.py` | 图片容器头 + 字节熵 + OpenAI 兼容传图请求构造 | 「没有视觉模型时怎么办？」 |
| `parsing/config.py` | `PARSING_` 前缀开关与阈值，含命名空间隔离说明 | 「阈值怎么调？」 |
| `parsing/server.py` + `Dockerfile.parse` | 独立可运行的解析服务镜像 | 「怎么单独扩容这一层？」 |
| `parsing/tests/` | 18 项离线单测，零外部调用 | 「怎么证明不是嘴上说？」 |
| `webapp/routers_parse.py` | 演示切片 HTTP 适配（4 个接口） | 「前后端怎么接？」 |
| `webapp/static/` 的「解析实验室」 | 策略下拉 + 逐页轨迹表 + 结构化预览 | 现场演示主界面 |
| `docs/parsing/01–07` | 原理学习资料（PDF/OCR、表格、图片多模态、升级预算、部门队列、API 契约） | 「讲讲原理」 |
| `docs/HARNESS.md` | **通用开发规约**，可复制到别的项目 | 「你怎么保证工程纪律？」 |
| `docs/acceptance-report.md` | 最终回归实测与可量化验收 | 「测了什么？」 |
| `interview/index.html` | 单文件面试演示页，双击即开、离线投屏 | 开场 2 分钟 |
| `docker-compose.yml` + `.env.example` | 默认 11 服务不变，`--profile parsing` 多 1 个解析服务 | 「Docker 怎么跑？」 |

---

## 2. Git 路径与提交（按逻辑版本分开，便于逐个讲解）

本轮开发版本按顺序提交，每个提交一个关注点，不塞巨型 commit：

| 版本 | 内容 | 关注点 |
|---|---|---|
| `feat(webapp): 低思考快速档、部门统计维度与统一 429 契约` | 上一轮成果 | 阈值口径、错误契约 |
| `docs: 企业级就绪复核、部署规范与独立面试演示页` | 上一轮文档 | 三态标注 |
| `chore: 记录复核结论与项目硬约束` | 项目记忆 | 纪律 |
| `a3e8e5b` | `parsing/` 子系统 + `webapp/` 接入 + 前端解析实验室 + 26 项新增单测 + 两条致命缺陷修复 | 解析架构主体 |
| `70a6977` | `docs/parsing/` 原理资料 + `docs/PARSING-DELIVERY.md` + `docs/HARNESS.md` + 并发改动收口 | 学习资料与规约 |
| `c179f3d` | `Dockerfile.parse` + compose `parser` 服务 + `.env.example` 的 `PARSING_*` 段 | 编排与路径整理 |

> 具体 hash 以 `git log --oneline -6` 为准；分支当前为 `demo/v1`，工作区干净。
> 分支策略与提交信息规范见 `docs/git-conventions.md`；冻结区 `eval/` / `demo/` / `evalbench/` 全程零 diff。

**为什么目录叫 `parsing/` 而不是塞进 `backend/services/`**：它是纯函数包，不依赖 PG / Milvus / Celery，可以单独 import、单独跑单测、单独打镜像；`backend/` 与 `webapp/` 都能接它而互不拖累。面试时这个边界很好讲。

---

## 3. 三段话术（照着念即可）

**开场（30 秒）**
> 这是一个企业内部知识库的开发框架骨架，重点是「多文件 / 大文件异步解析 → 分部门检索 → 流式问答」这条链路。这次我专门把**解析方法**抽成了可插拔的三层：自研规则层、收费 OCR 层、多模态兜底层，前端能实时切换策略并看到每一页的解析轨迹。

**技术深度（90 秒）**
> 主链路是纯标准库自研：PDF 文本层逐页提取、图片容器头与字节熵、表格还原成带行列坐标和单位的结构化 cells，还带 5 项稳定性检查。每一页产出可复算的质量信号，只有判到「零字符 / 无文本层 / 置信度不足 / 表格结构不自洽」才逐级升级。最贵的多模态层有 10% 的页数熔断线——注意这是**上限**不是随机分流，正常文档 0% 触发，超了就显式留痕并回退，不静默失败。下游 `ChunkSpec` 契约完全不动，换任何一家供应商只改一个策略类。

**诚实边界（30 秒）**
> 当前手上只有纯文本 LLM：网关 `/models` 实查 10 个模型全部没有视觉能力，也没接收费 OCR。所以后两层是**契约已就绪、端点待补**，传图请求构造是真实代码但一次都没发出去过；未接通时会输出「图片结构事实」并明确标注不是视觉理解。我不把设计说成落地——降级路径本身就是可讲的技术深度。要补的端点清单在 `docs/parsing/07-api-contracts.md`。

---

## 4. 现场演示命令（离线可跑，不消耗模型流量）

```bash
# 1) 解析子系统单测（零网络、零模型调用）
python -m pytest parsing/tests webapp/tests -q

# 2) 演示切片：侧栏「解析实验室」
python -m uvicorn webapp.main:app --port 8765
#   浏览器 http://127.0.0.1:8765  →  admin / admin123

# 3) 独立解析服务（默认 compose 的 11 个服务不受影响）
docker compose --profile parsing up -d --build parser
curl http://127.0.0.1:8766/health/live

# 4) 面试演示页：双击 interview/index.html 即可，无需服务器
```

演示脚本（约 3 分钟）：三级可用性卡立边界 → 上传带表格的 Markdown 看 `local / 0 次升级` 与 `[行2·列1]` 坐标 → 上传 PNG 看 `vision_unavailable_fallback` → 切换 `off / local_only / multimodal` 对比同一文件的轨迹 → 切回 `auto`。详见 `docs/parsing/01-overview.md` §5。

---

## 5. 需要你补的东西（缺 API 就叫你）

| 优先级 | 缺什么 | 补了之后 |
|---|---|---|
| 1 | 一个支持 `image_url` 的**视觉模型端点**（base_url / key / model） | P2 从「结构化降级」变成真·传图兜底 |
| 2 | 一个 **OCR / 版面解析服务**（base_url / key / 可选 model） | 无字 PDF / 扫描件 / 复杂表格链路闭环 |
| 3 | 上述服务的**配额与价格说明** | 把 90:10 从经验起始值校准成有依据的阈值 |
| 4 | 真实语料（扫描 PDF、合并单元格表、纯图页各 5–10 份） | 阈值可复现、轨迹有说服力 |

填入 `.env` 的 `PARSING_*` 键即可点亮，接口字段保持 `__TBC__` 直到拿到可核实的契约文档。

---

## 6. 回归结果（本轮实跑）

| 项 | 结果 |
|---|---|
| `pytest webapp/tests parsing/tests` | **92 passed**（webapp 74 + parsing 18，含解析 API 8 项） |
| `backend / eval / evalbench / demo` | 16 passed / 14 OK / 16 OK / 16 与 16（run_id 未变） |
| `python webapp/selftest.py` | 33/33 `DEMO_READY`（隔离库 + 离线 provider 补跑，原报告按字节还原） |
| `node --check` ×3 + `npm run build` | 通过 |
| `docker compose config -q` | 通过；默认 11 服务、`--profile parsing` 12 服务 |
| `git diff --exit-code -- eval/ demo/` | 返回 0（冻结区零改动） |
| 密钥扫描 | 零命中，`.env` 未入库 |

**唯一一次真实外部调用（用户授权的轻量自测）**：
`deepseek-v4.1-flash-1017` 带 `thinking={"type":"disabled"}` 发一次 `max_tokens=256` 的探活，
返回 **HTTP 200 / 448 ms / 正文「你好」/ `reasoning_tokens=0` / 11 tokens**。
结论只写到这个程度：**字段被网关接受（未报 400）、本次未观察到推理 token**；
单次请求不足以断言「网关据此关闭了思考」，所以界面与代码仍标「未确认生效」。
除此之外全程零真实模型流量——解析子系统 18 项单测、解析 API 8 项单测与 33 项端到端自测全部离线。

---

## 7. 明确不做（别被问住）

真实压测与容灾演练、监控告警 SLO、SSO/OIDC、真实供应商账单校准、屏幕阅读器实测、质量基线冻结、逐段落 ACL 全量实现、真实 Milvus 集群、`backend/` 的 13 步去 tenant 数据模型改造、多租户隔离（**仅作为未来研发方向写入文档**）、真实腾讯云上线。这些在 `docs/kb-capability-checklist.md` 与 `docs/capability-priority.md` 里都标着「未做」，不夸大。
