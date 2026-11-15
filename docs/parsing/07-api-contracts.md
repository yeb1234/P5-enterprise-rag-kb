# 07 · 外部服务契约、实测记录与仍需补齐的清单

> **不编造原则**：真实接口地址、鉴权值、请求/响应结构、配额、错误码、价格一律
> 以实测或官方契约为准；未核实的字段保持 `__TBC__`。`eval/` 冻结对象的
> `__TBC__` / `NOT_COMPARABLE` / `real_api` 阻断口径不变、不绕过。
>
> **2026-11-14 状态**：P2 多模态**已接通并实测跑通**（复用现有 Discovery 网关）；
> P1 收费 OCR **仍未接通**，契约字段全部 `__TBC__`。

---

## 1. 一次误判的纠正（留痕，别再凭名字下结论）

此前我只取了 `/models` 返回里的 `id`（模型名），看到没有 `vl` / `vision` 字样就判定
「10 个模型全为纯文本、没有视觉模型」。**这是错的**：每个模型对象其实带
`input_modalities` 字段。完整读取后：

| 输入能力 | 模型 |
|---|---|
| 支持 `image`（6 个） | `Agents-A1`、`Sage-A1.5`、`deepseek-v4.1-flash-1017`、`intern-s2`、`kimi-k3.1-pro`、`qwen3.8-27b` |
| 仅 `text`（4 个） | `Atria-Dawn`、`Atria-Dawn-Preview`、`Sage-A1.5-Taiyi`、`glm-5.3` |

目标模型 `deepseek-v4.1-flash-1017` 的 image 项写明
`supported_inputs.sources.values = ["url", "base64"]`，`is_ready: true`、`status: active`；
输出模态 `text` 的 `supported_parameters` 里列了 `max_tokens` / `temperature` /
`reasoning` / `tools` / `stop` / `structured_outputs` / `top_p`。

复跑命令（只读元数据，不发图片、不消耗生成 token）：

```bash
python -m parsing.vision_probe
```

**教训已写进 `docs/HARNESS.md`（Y16 / Y20）**：报「有问题」或「全绿」之前先证伪自己；
能力判定要用结构化元数据与实测，不能用命名启发式。

---

## 2. P2 多模态：已核实的契约（不再是 `__TBC__`）

| 字段 | 值 | 依据 |
|---|---|---|
| 端点 | `DISCOVERY_BASE_URL` 的 `/chat/completions`（值在 `.env`，不落代码） | 2026-11-14 实测 HTTP 200 |
| 鉴权 | `Bearer DISCOVERY_API_KEY`（值在 `.env`，不下发前端） | 同上 |
| 请求结构 | OpenAI 兼容 `messages[].content` = `text` + `image_url`，图片用 `data:image/png;base64,...` | 实测通过 |
| 图片来源 | `url` / `base64` | `/models` 的 `input_modalities` |
| 模型 | `deepseek-v4.1-flash-1017` | `/models` 实查 `active` |
| 响应结构 | `choices[0].message.content` + `usage`（含 `completion_tokens_details.reasoning_tokens`） | 实测解析成功 |
| 低思考 | `thinking={"type":"disabled"}` | 实测 `reasoning_tokens` 55 → 0、耗时 979 ms → 467 ms |
| 无效参数 | 布尔 `reasoning`（元数据里列为 `boolean`） | 实测 `reasoning=false` 仍 `reasoning_tokens=55`（3413 ms），`reasoning=true` 同基线 |
| 配额 / 错误码 / 价格 | `__TBC__` | 未核实，不推断 |

### 2.1 双图对照实测（排除「靠猜 / 复述提示词」）

固定 `temperature=0`、`max_tokens=256`，同一提问、两张内容不同的图：

| 图 | 实际内容 | 模型回答 | 耗时 | token |
|---|---|---|---|---|
| A | 浅蓝底 / 深蓝字 `73492` / 标签 `CODE-A` | "73492 on a light blue background with the label 'CODE-A' below." | 1406 ms | 357 |
| B | 浅黄底 / 红字 `18620` / 标签 `CODE-B` | "The image shows the number 18620 in dark red on a pale yellow background, with the label 'CODE-B' below it." | 2463 ms | 433 |

两次回答不同且都精确命中数字、背景色、标签 → 真·视觉理解。
再用 `ParseRouter.parse_document()` 走完整链路：
`reason=vision_applied`、`degraded=None`、1403 ms，回答含 `73492` 与颜色判断。

**判定边界**：这是**功能连通性实测，不是质量基线**。未做真实语料集评测、
未测分辨率/版式鲁棒性、未做成本压测；视觉回答适合版面与图表语义，
**图片里的精确数字与文字仍应以 P1 OCR 结果为准**，不要让视觉模型复述关键数值。

---

## 3. P1 收费 OCR / 版面解析：契约仍全部 `__TBC__`

| 字段 | 值 |
|---|---|
| 接口地址 / endpoint | `__TBC__` |
| 鉴权方式 | `__TBC__` |
| 请求结构（图片编码、参数名、页码、语言） | `__TBC__` |
| 响应结构（文本、坐标格式、置信度字段、区块类型枚举） | `__TBC__` |
| 配额 / 限流 / 并发 | `__TBC__` |
| 错误码与重试语义 | `__TBC__` |
| 单次 / 批量上限 | `__TBC__` |
| 模型 / 能力版本号 | `__TBC__` |
| 价格与计费单位 | `__TBC__` |

代码落点 `parsing/strategies/ocr_api.py`：`contract()` 返回同样槽位，
`_call()` 是当前唯一真实调用点，**故意抛 `StrategyUnavailable`**——
没有凭证就写一个假成功返回，会污染解析轨迹与后续所有检索结论。

能力类别（公开划分，不涉及具体厂商接口）：通用文字 OCR / 版面解析 /
表格还原 / 公式与手写专项。选型时要问清四件事：是否支持批量异步、
是否返回坐标及坐标格式、是否给逐块置信度、计费单位是什么。

---

## 4. 现在还需要补什么

| # | 需要的东西 | 用途 | 状态 |
|---|---|---|---|
| — | 支持 `image_url` 的视觉端点 | P2 多模态兜底 | **不再需要**：已复用现有网关并实测跑通 |
| 1 | 一个 **OCR / 版面解析服务**（base_url / key / 可选 model） | 无字 PDF、扫描件、复杂表格出正文与坐标 | 待补 |
| 2 | 上述服务的**配额与价格说明** | 校准 90:10 与预算闸门 | 待补 |
| 3 | **真实语料**（扫描 PDF、含合并单元格的表、纯图页各 5–10 份） | 质量门禁阈值从演示值调成可复现基线 | 待补 |
| 4 | 视觉端的**质量评测集**（版式、手写、低分辨率） | 把「能连通」升级为「准确率有数据」 | 未做，属 A/B 档 |

当前已有能力：`deepseek-v4.1-flash-1017`（纯文本 + 图像输入，低思考档已配）、
`BAAI/bge-m3` 嵌入、`BAAI/bge-reranker-v2-m3` 重排。

---

## 5. 环境变量（`.env.example` 有完整注释）

```
# P2：留空即回退到 DISCOVERY_*，无需额外凭证
PARSING_VISION_BASE_URL=
PARSING_VISION_API_KEY=
PARSING_VISION_MODEL=
PARSING_VISION_ENABLED=1
PARSING_VISION_LOW_THINKING=1
PARSING_VISION_TIMEOUT_S=60
# 一键全离线：P1/P2 全部跳过、零外部请求
PARSING_OFFLINE=0
# P1：仍未接通
PARSING_OCR_BASE_URL=
PARSING_OCR_API_KEY=
PARSING_OCR_MODEL=
```

密钥只进 `.env`（已 gitignore），不进源码、不进镜像、不下发前端。
compose 的 `parser` 服务通过 `environment` 注入 `DISCOVERY_*` 与 `PARSING_*`。

---

## 6. 补 P1 端点时的接入顺序

1. 在 `.env` 填 `PARSING_OCR_*` → `available()` 变 `True`，前端三级可用性卡从「未接通」变「可用」。
2. 按真实响应实现 `OcrApiStrategy._call()`，把文本 / 坐标 / 置信度映射成统一的 `ParseResult`。
3. 补一份**带脱敏响应样本**的契约文档，替换本文 §3 对应的 `__TBC__` 槽位（只改本文，不回改 `eval/` 冻结对象）。
4. 用真实语料跑解析轨迹，按命中率调整 `PARSING_CONFIDENCE_MIN` / `PARSING_OCR_TEXT_CHARS_MIN` / `PARSING_VISION_ESCALATION_RATIO`，把调整前后对照写进 `docs/CHANGELOG.md` 新增条目。
5. 补单测：mock 外部响应，断言 `level=ocr_api` 且 `degraded=None`；`PARSING_OFFLINE=1` 时断言零请求。

---

## 7. 诚实边界

- P2 的实测只覆盖**两张自造的清晰图片**，不是准确率结论；未做真实语料评测、未做压测、未测失败与限流语义。
- 「请求结构已确定」指项目代码里写死的 OpenAI 兼容形状在本网关实测通过，不代表任意第三方网关都支持；换端点时请用 `PARSING_VISION_LOW_THINKING=0` 去掉私有的 `thinking` 字段再验证。
- P1 没有任何凭证，全部字段 `__TBC__`；解析轨迹里出现 `ocr_api` 级别时必然伴随 `degraded` 标记，不得对外表述为「已跑通 OCR」。
- 收费与配额数字一律不推断填写；`eval/` 冻结对象的阻断口径不受本文件影响。
