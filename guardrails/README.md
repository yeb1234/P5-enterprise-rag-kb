# `guardrails/` · 内容安全与 PII 防护

> 一句话：**同一套规则，贯穿四个出口**。展示层打码不算防护——原文只要进了
> chunks、向量、缓存、日志或 CSV，就总有一条路径能把它带出去。

## 为什么单独一个包

`parsing/` 管「字节 → 结构化文本」，`knowledge/` 管「哪些证据适用」，
`cache/` 管「派生结果怎么复用」，`guardrails/` 管「哪些内容不允许以明文形式离开」。
四者都是纯函数包，互不 import webapp，方便单独测试与讲解时逐层讲。

用户诉求是「做一层，防止 ID、用户信息泄露」。但既有文档
`docs/knowledge/02-candidates.md` 的 C09 早有判断：**只在一处替换反而容易被追问成漏洞**。
所以这里的「一层」指**一层统一规则**，不是**一个替换点**：

| 出口 | 常量 | 时机 | 接入点 |
|---|---|---|---|
| 入库前 | `GATE_INGEST` | 分块之前，chunks 写入前 | `webapp/rag.py::analyze_document` |
| 进模型前 | `GATE_CONTEXT` | 检索用原文完成后、送进 LLM 前 | `webapp/routers_chat.py::chat_stream` |
| 输出前 | `GATE_OUTPUT` | SSE 逐 token 下发时 | 同上（`StreamRedactor` + `done.tail`） |
| 日志与导出前 | `GATE_AUDIT` | 审计日志写库、badcase 采集/列表/CSV/JSON 导出 | `webapp/db.py::log_action`、`webapp/routers_badcase.py` |

顺序不能反：**检索必须用原文**（脱敏后的问题会破坏词法召回），
但**进模型、落库、下发、导出一律用脱敏文本**。

## 目录

| 文件 | 职责 |
|---|---|
| `contracts.py` | 数据契约：`PIIFinding` / `RedactionPolicy` / `RedactionResult` / `SafetyReport`；规则版本 `PII-RULES@1.0` |
| `detectors.py` | 11 条规则 + 四级判定链（格式 → 校验位 → 上下文 → 置信度） |
| `redact.py` | 五种动作、可逆 token 保险库、`StreamRedactor` 流式闸 |
| `pipeline.py` | 四出口统一入口 `Guardrails`、全局策略、状态/演示/导出辅助 |
| `tests/test_guardrails.py` | 27 项离线单测 |

## 检测：为什么不能只写正则

四类真实误报，每条都对应代码里的一个防御：

- `\d{17}[\dXx]` 会把 18 位订单号当身份证 → 必须校验出生日期段 +
  **ISO 7064 MOD 11-2 校验位**（`_valid_id18`）。校验位不过的一律不报。
- `1[3-9]\d{9}` 会命中 13 位长号中间的子串 → 前后加数字边界断言。
- 16–19 位纯数字可能是银行卡也可能是订单号 → 过 **Luhn**；
  且末尾紧跟 `X/x` 说明更像身份证校验位，交给身份证规则，
  不误报成银行卡（`BANK_CARD` 的 `(?![\dXx])`）。
- 中文姓名 / 工号 / 护照没有稳定文本特征 → 只认**带字段名词**的写法
  （`姓名：`、`工号：`、`护照号：`），绝不做「看见两个汉字就当姓名」。

命中带 `kind` / 起止位置 / 置信度 / 规则版本，重叠时**长命中优先、
同长度高置信度优先**。`as_dict()` 的 `preview` 只回显首尾各 2 位，
报告本身也不泄露完整标识符。

## 动作

| 动作 | 行为 | 用途 |
|---|---|---|
| `mask` | 保留首 3 尾 4（邮箱保留首字符 + 域名） | 默认，人工可核对又不可还原 |
| `hash` | 带盐 HMAC 前 12 位 | 不可逆但跨请求稳定，便于统计去重 |
| `token` | 可逆占位符 | **默认关闭**，映射本身是新的泄漏面 |
| `remove` | 整段换成占位符 | 确认不需要核对时 |
| `block` | 命中即标记拒绝 | 默认只对 `API_KEY` 启用 |

## 流式闸：最容易漏的一层

SSE 逐 token 输出时，一个 18 位身份证可能被拆在多个 chunk 里。
只保留尾部 `HOLD=24` 个字符**不够**：如果标识符起点落在「可下发区」、
终点还在保留区，按字符切片会把前 10 位先发出去——发出去就撤不回来了。

`StreamRedactor.push()` 的做法：

1. 找出横跨下发边界的连续候选串（`[A-Za-z0-9_.@+\-:]+`，
   中文天然充当边界），把下发边界收缩到它的起点，**整串扣住**；
2. 只脱敏完整落在边界之前的命中；
3. `flush()` 处理最后一段。

为不破坏断线续传契约，webapp 侧保持「一个原始 token 对应一个 SSE 包」，
`index` 仍用原始索引，被扣住的尾部由 `done.tail` 补齐、前端在 `done` 时追加。
代价是真实断线重连时尾部可能少几个字（生产应改成基于安全输出字节偏移的游标），
这个边界在 `docs/content-safety/02-design.md` 里如实标注。

## 配置

纯包自治，读 `GUARDRAILS_*` 环境变量（不与 webapp / backend 既有键冲突）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GUARDRAILS_ENABLED` | `1` | 关掉后四出口全部放行原文，仅用于对照演示 |
| `GUARDRAILS_ACTION` | `mask` | 五种动作之一 |
| `GUARDRAILS_MIN_CONFIDENCE` | `0.60` | 低于阈值的命中跳过 |
| `GUARDRAILS_HASH_SALT` | `demo-salt-change-me` | **生产必须换成密钥管理系统里的值**；默认盐等于没脱 |
| `GUARDRAILS_REVERSIBLE_TOKEN` | `0` | 开启后映射只放内存 |

运行时可由 admin 通过 `PUT /api/safety/policy` 调整，本进程即时生效、不重启。

## 台账

`webapp` 的 `safety_events` 表只存 `gate / kind / 数量 / 是否拦截 / 动作 /
规则版本 / 调用点`，**不存原文、不存 preview、不存可逆映射**。
它是给运维看「哪个出口在漏」的，不是给回放原文用的。

## 边界，别夸大

- 这是**规则级检测**，不是模型级 NER；语义上下文、重标识、k-匿名只是设计讨论，未实现。
- 图片 / 扫描件 / 表格单元格里的 PII、OCR 前处理**未做**。
- `backend/app/agent/orchestrator.py` 骨架仍是两条正则 + `verify_node` 输出替换，
  **没有接本包**；本包只覆盖 webapp 演示切片。
- 合规审计、留存策略、法务审批、SIEM 对接、密态检索、压测与阈值标定**未做**。
- 演示样本里的证件号 / 手机号 / 卡号都是**按校验位算法合成的教学用号码**，
  不是任何真实个人的信息。
