# 真实外部模型配置 · 评估测试报告（面试讲解版）

日期：2026-11-11 ｜ 对象：`webapp/` 可运行演示切片 ｜ 档位：**真实外部 API 全链路**
事实源：`webapp/out/eval_report.json`、`webapp/out/selftest_report.json`（密钥已脱敏，报告不含任何凭证）

> **一句话**：把「生成 / 向量化 / 重排」三个组件全部换成真实外部模型后，
> 检索质量、流式问答、来源追溯、降级链路与五套回归全部实测通过。
> 这是一份**工程实测记录**，不是质量基线。

---

## 0. 先说清楚这份报告的边界（面试务必先讲这句）

| 是什么 | 不是什么 |
|---|---|
| `webapp/` 演示切片在真实外部模型下的**链路与工程行为实测** | `eval/` 评测体系的质量基线 |
| 证明「三个 Provider 可切换、可观测、可降级、可追溯」 | 证明「召回准确率 / MRR / NDCG 达标」 |
| 3 份构造文档 + 1 个问答的定性三层分数 | 有统计意义的效果评估 |

**必须区分两套东西**：`docs/evaluation-plan.md` 与 `eval/` 里，真实 API 的地址、鉴权、
请求/响应、配额、模型版本一律 `__TBC__`，`real_api` 档位仍被契约门禁阻断，
占位结果 `comparable=false` / `NOT_COMPARABLE`，**本轮没有改动、没有解冻、没有据此冻结任何阈值**。
本报告写真实模型名，是因为 `webapp/` 是独立的可运行演示切片，不承担 `eval/` 的冻结口径。

**60 秒电梯讲法**：
> 「我把知识库的三个模型组件都换成了真实的外部服务：生成走 Intern-AI Discovery 网关的
> DeepSeek，向量化和重排都走硅基流动的 BGE 系列。切换不需要改代码，是设置页的三个下拉框，
> 因为我在 `providers.py` 留了 Provider 适配器接口。为了验证它不是『能跑就行』，我写了个
> 可复跑的采集脚本，把检索的三层分数、端到端问答、来源追溯和降级行为都落成结构化 JSON，
> 报告里每个数字都来自这份事实源。最后五套回归全绿。但我不会说这是质量评估——
> 它没有基线、没有压测、样本只有几份构造文档，评测体系那套仍然是占位且被门禁阻断的。」

---

## 1. 目标配置与实测连通性

用户要求：**截图里那两个模型（嵌入 `BAAI/bge-m3`、重排 `BAAI/bge-reranker-v2-m3`）都用硅基流动；
LLM 用外部 DeepSeek，走 discovery-api 网关。**

| 组件 | Provider | 模型 | 端点 | 实测延迟 | 结果 |
|---|---|---|---|---|---|
| 生成 LLM | `discovery` | `deepseek-v4.1-flash-1017` | `discovery-api.intern-ai.org.cn/v1` | 1159 ms（单次探活） | ✅ 可用 |
| Embedding | `external` | `BAAI/bge-m3` | `api.siliconflow.cn/v1` | **435 ms / 1024 维** | ✅ 可用 |
| Rerank | `external` | `BAAI/bge-reranker-v2-m3` | `api.siliconflow.cn/v1` | **205 ms** | ✅ 可用 |

- 嵌入与重排**同一账号、同一端点、同一把 key**，配置上收敛为一个外部凭证，不落库、不下发前端。
- 连通性探测走 `/api/settings/test` 同款函数；rerank 探活样本分数 `0.9668` vs `0.0014`，
  说明 cross-encoder 确实在打分，不是「请求通了但分数没用上」。
- `test_llm` 给 256 token（不是 24）：`deepseek-v4.1-flash-1017` 是推理模型，
  token 给小了会只吐 reasoning 却把正文吃光，早期因此误判过「模型可用」。

**面试可讲**：为什么生成 / 向量 / 重排是三个独立 Provider？
因为 DeepSeek 的 OpenAI 兼容接口只有 `chat/completions`，**不提供 `/embeddings`**；
而 rerank 又是另一套 Cohere 风格 `/rerank` 协议。三者协议、鉴权、限流、失败语义都不同，
硬塞进一个 client 会把「换模型」变成改代码。适配器 seam 让切换成本降到改配置。

---

## 2. 配置矩阵：不是写死，是三档可切换

| 组件 | 可选档位 | 说明 |
|---|---|---|
| LLM | `offline` / `sensenova` / `discovery` / `local` | 离线占位 / DeepSeek 代理 / Intern-AI 网关（14 模型）/ LM Studio |
| Embedding | `offline` / `local` / **`external`** | 词法 / LM Studio Bge M3 / **硅基流动 BAAI/bge-m3**（本轮新增） |
| Rerank | `off` / `llm` / **`external`** | 不重排 / LLM-as-reranker / **真 cross-encoder**（Cohere/Jina/TEI 兼容） |

- 落 `app_settings` 表，**每请求读取，切换即时生效、无需重启**；`.env` 只提供首次默认值。
- 设置页有「测试连接」按钮，三档都能单独探活；当前生效配置在页面上直接可见。
- 白名单键 + 枚举校验，未知键/非法值直接拒绝，避免配置污染。

> **本轮最重要的一个设计决定**：`settings.effective_embedding()` 把
> 「端点 / 模型 / API key / **缓存命名空间**」收敛成唯一事实源。
> 向量按 `(chunk_id, model)` 缓存在 `chunk_embeddings`，如果切模型后还沿用同一个
> model 字符串，就会复用旧模型的向量 —— 维度不一致时 `_cosine` **静默返回 0**，
> 检索看起来正常、实际全错。所以 `cache_key` 必须带 provider + 模型名：
> `external:BAAI/bge-m3` 与 `local:text-embedding-bge-m3` 天然隔离。
> 这条已写进单元测试 `test_settings.py`（41 项里含该用例）。

---

## 3. 评估方法（可复跑，不是手填数字）

```bash
python webapp/eval_report.py      # → webapp/out/eval_report.json
python webapp/selftest.py        # → webapp/out/selftest_report.json
```

采集脚本的设计取舍：

- **隔离临时库**（`WEBAPP_DB_PATH` 指向临时文件），不污染演示库、不依赖历史数据；
- 用 `TestClient` 走真实 HTTP 语义（含 SSE 流解析），而不是直接调函数自说自话；
- 同时用项目自身的 `rag` / `providers` 把「词法门禁 → 向量 → rerank」三层分数摊开；
- 全部结构化落 JSON，**报告只引用这份事实源**；凭证只取「是否存在 + 脱敏前后缀」。

**语料**（3 份构造文档，专门设计成能暴露问题）：

| 文档 | 角色 | 陷阱 |
|---|---|---|
| `wifi-setup.txt` | 真相关（标准答案） | — |
| `billing.txt` | 关键词干扰 | 含「配网」但讲的是账单 |
| `asset-scrap.txt` | 无关 | 含「设备」但讲的是报废 |

---

## 4. 检索质量：三层分数（本轮最有说服力的一张表）

问题：**「设备如何配网？」** ｜ 词法门禁切出的二元组：`何配 / 备如 / 如何 / 设备 / 配网`

| 文档 | 词法命中 | 向量余弦 `BAAI/bge-m3` | **Rerank 分数** `bge-reranker-v2-m3` |
|---|---:|---:|---:|
| `wifi-setup.txt`（真相关） | 2 | **0.7296** | **0.9686** 🥇 |
| `billing.txt`（关键词干扰） | 1 | 0.6023 | **0.0168** 🥈 |
| `asset-scrap.txt`（无关） | 1 | 0.4811 | **0.0009** 🥉 |

最终顺序：`wifi-setup → billing → asset-scrap`，
`retrieval_mode = hybrid: lexical_gate + external_embedding(BAAI/bge-m3)`，
`rerank = applied:external`，`fallback_reason = null`。

**怎么讲**：光看词法，三份文档都有命中；光看向量，干扰文档的余弦 0.6023 也不算低，
排序虽然对但**区分度不够**（0.7296 vs 0.6023，差 0.13）。
真 cross-encoder 重排后差距拉到 **0.95**（0.9686 vs 0.0168），
「含关键词但语义不相关」的干扰文档被明确压下去。
这就是我为什么要单独接一个 rerank，而不是「向量检索凑合用」——
**双塔嵌入擅长召回，cross-encoder 擅长精排，两者不是替代关系。**

（诚实补充：这是 1 个问题 × 3 份文档的定性证据，**不是** MRR/NDCG 基线。）

---

## 5. 端到端问答与来源追溯

| 指标 | 实测 |
|---|---|
| HTTP 状态 | 200 |
| 生成模型 | `deepseek-v4.1-flash-1017` |
| 流式 chunk 数 | 70 |
| 答案长度 | 127 字 |
| 端到端耗时 | **59.7 s** |
| `degraded` | `false`（未降级） |
| 检索命中 / `meta` 来源 / `done` 来源 | 3 / 3 / 3 |

答案摘录（真实模型输出）：
> 设备配网步骤（参考 1）：1. 长按机身复位键 3 秒，指示灯快闪后松开；
> 2. 在 App 中选择 **2.4GHz Wi-Fi** 并输入密码；3. 听到连接成功提示音即完成配网。
> 若配网失败，请确认路由器未开启 **5GHz 独占模式**。

**来源追溯**（本轮之前的改造，本轮在真实模型下复验）：
`chat_sources` 表落库 3 条；`/api/chat/sources` 返回 3 行、
`/api/chat/conversations` 标记 `has_sources=true`、
`/api/chat/history` 的助手消息带 3 条来源。前端回答下方有可点击的引用卡片。

> **59.7 s 要如实解释，别糊弄**：`deepseek-v4.1-flash-1017` 是**推理模型**，
> 冷启动 + reasoning 阶段耗时波动大（探活时几次测到 0.6 s / 9.6 s / 58 s 不等）。
> 真做演示我会切 `minimax-m3`（同网关，约 1.3 s，无 reasoning），
> 要「DeepSeek」名号时才用这个模型。这个取舍我写进了配置说明 ——
> **知道它慢、知道为什么慢、知道什么时候该换**，比藏起来更有说服力。

**诚实拒答复验**：问「量子纠缠如何应用于超导材料？」（库里没有），
`degraded=true` / `reason=retrieval_empty`，
回答「抱歉，我在当前知识库中没有找到与您问题相关的内容…」——**不编造**。

---

## 6. 降级矩阵：故障不是吞掉，是留痕

| 场景 | 行为 | 留痕标记 |
|---|---|---|
| 检索为空 | 主动拒答，不编造 | `degraded=true, retrieval_empty` |
| Embedding 端点不可用 | 回退词法排序，答案照出 | `lexical(fallback)` + `embedding_unavailable:*` |
| Rerank 不可用 / 空 key / 错 key | 优雅跳过，**不假装用了** | `*_unavailable(skipped)` |
| 模型报错 | 降级为检索片段拼接 | `model_error` |
| `429 rate_limit_exceeded` | 指数退避 1/2/4 s + 抖动，尊重 `Retry-After`（上限 30 s），最多 3 次 | 退避后重试 |
| `429 quota_exceeded` | **不重试**（墨点耗尽，重试无意义），提示换模型/供应商 | `quota_exceeded` |
| 流式已出正文后中断 | 不拼接兜底文案，避免重复内容 | `stream_interrupted` |
| 零正文（推理模型吃光 token） | 不再出空白气泡，用检索片段兜底 | `empty_model_output` |

**面试可讲**：429 必须分两类 —— `rate_limit_exceeded` 是**等一等就好**，`quota_exceeded` 是**等也没用**。
把两者都当「重试三次」会白烧时间、把额度耗尽伪装成网络抖动。
另外**流式一旦开始吐字就不再重试**，否则会在客户端拼出重复答案。
上游网关还会在流末尾发 `choices: []` 的纯 usage 块，解析器必须跳过空 choices，
否则一个 `IndexError` 会把正常回答误判成 `model_error`（这是真踩过的坑）。

---

## 7. 回归结果（最终确认快照）

| 套件 | 命令 | 结果 |
|---|---|---|
| webapp 单元测试（隔离库 + 强制离线） | `python -m pytest webapp/tests -q` | **41 passed** |
| webapp 端到端（真实外部配置，82.65 s） | `python webapp/selftest.py` | **33/33 · DEMO_READY** |
| 演示物料自测 | `python demo/selftest.py` | **16/16** · `run_id=RUN-095a2a4a40c6` |
| 评测 harness 单测 | `python -m unittest discover -s eval/tests -t .` | **14 OK** |
| backend 骨架单测 | `cd backend && python -m pytest -q tests` | **16 passed** |

`demo` 的 `run_id` 未变 —— 本轮没有改动任何 `eval/` 冻结对象、数据集或评分规则，
`NOT_COMPARABLE` 口径完整。`.env` 的密钥已 gitignore，未进源码。

---

## 8. 本轮踩到并修掉的真问题（很好的面试谈资）

1. **配置命名空间污染**（最值得讲）。`webapp/` 与 `backend/` 共用根目录 `.env`，
   但两边对 `EMBEDDING_PROVIDER` 的枚举定义**互斥**：webapp 是 `offline|local|external`，
   backend 骨架是 `stub|openai_compatible`。改成 `external` 后 backend 启动直接校验失败 ——
   这个隐患**一直存在**（改之前是 `local`，同样非法），只是之前缺依赖没跑到那一步。
   修法：演示切片的 provider 开关一律加 `WEBAPP_` 前缀，把通用名还给生产骨架，backend 零改动。
   **教训**：共享扁平配置命名空间迟早撞车，子系统要有自己的作用域。
2. **向量缓存命名空间**（见 §2）：切模型不隔离缓存 → 余弦静默归零，最难查的那种 bug。
3. **测试用例脆弱**：C12 用「火星探测器着陆技术观测方法」验证「检索为空」，
   但中文二元组里「技术 / 方法 / 观测」是通用词，演示库累积 50 份文档后碰巧命中了。
   这是**用例写坏了**，不是功能坏了 —— 改成随机无意义探针，让判定与库内容解耦。
4. **路径遮蔽**：脚本放 `webapp/` 下直接跑时，`webapp/queue.py` 遮蔽标准库 `queue`，
   `TestClient` 起不来；pytest 从项目根跑所以没暴露。修 `sys.path` 顺序。
5. **Python 3.13 移除 `HTTPResponse.makefile`**：SSE 读取改走 `resp.fp.readline()`。
6. **中文检索把整句当 token**：改成英文整词 + 中文相邻二元组，否则永远空检。
7. **LM Studio 不暴露 `/v1/rerank`**（把 reranker 归类到 embeddings），
   所以设计里**没有 `local` 重排档**，只有 `off / llm / external`；本地要跑用 TEI 起 `/rerank` 选 `external`。
8. **backend 骨架依赖缺失**：`sqlalchemy` / `pydantic_settings` / `minio` / `psycopg` 等未装，
   按 `backend/requirements.txt` 一次装齐，16 项用例才真正跑起来（**之前「没报错」只是没跑到**）。

---

## 9. 面试 Q&A 卡片

| 可能被问 | 怎么答 |
|---|---|
| 「为什么不全用一家？」 | DeepSeek 只提供 chat/completions，没有 embeddings；rerank 又是 Cohere 风格协议。按能力选服务，用 Provider 接口隔离协议差异。 |
| 「换模型要改代码吗？」 | 不改。设置页三个下拉框，落 `app_settings`，每请求读取即时生效；新增一家只需实现一个 Provider 类。 |
| 「向量和重排都收费，怎么控成本？」 | 向量按 `(chunk_id, model)` 缓存，同一文档不重复算；rerank 只对词法门禁后的 top-k 候选打分；不可用时跳过而不是失败。 |
| 「限流怎么处理？」 | 先分类：`rate_limit_exceeded` 退避重试（含 `Retry-After` 与抖动），`quota_exceeded` 不重试直接提示换模型。**目前没做全局并发闸/令牌桶**，生产应在网关层按 RPM 限流 + 队列吸收突发。 |
| 「答案怎么保证不瞎编？」 | 词法门禁保证不相关不硬凑；检索为空主动拒答；答案带来源卡片并落 `chat_sources` 表可追溯。但**逐句 grounding / 引用高亮仍是设计，切片未做**。 |
| 「这是上线系统吗？」 | 不是。`webapp/` 是演示替身：SQLite 代 PG、进程内信号量代 Celery、词法+外部向量代 Milvus。生产骨架在 `backend/`，两者刻意分开，契约与 `docs/next-version-messaging-and-plugins.md` 对齐。 |

---

## 10. 能讲 / 不能讲（诚实边界）

| ✅ 能讲 | ❌ 不能讲 |
|---|---|
| 三个 Provider 真实接通、可切换、可探活、即时生效 | 召回准确率 / MRR / NDCG 质量基线 |
| 检索三层分数、干扰文档被 rerank 压下去的定性证据 | 有统计意义的效果提升（样本 1 问 × 3 文档） |
| 来源追溯落库可回放、流式问答、断线续传、八类降级留痕 | 答案逐句 grounding / 引用高亮 |
| 五套回归全绿、配置隔离与缓存隔离有单元测试覆盖 | 压测、并发闸、高可用、容灾演练 |
| 429 分类退避、密钥不落库不下发前端 | 生产级限流（令牌桶/全局 RPM 闸）未做 |
| Milvus 真实向量库、多租户行级权限 | 已在切片落地（仅架构预留 / 骨架设计） |

**未做但已在路线图上**：Milvus 真实向量库、全局并发闸/令牌桶、压测、
React UI 框架、多租户行级权限、`eval/` 质量基线与阈值冻结。

---

## 11. 复跑清单

```bash
# 1) 启动（首次默认值即本轮目标配置，密钥在 .env，已 gitignore）
python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8765
#    预览：http://127.0.0.1:8765/?demo=admin

# 2) 采集真实链路事实源
python webapp/eval_report.py            # → webapp/out/eval_report.json
python webapp/selftest.py               # → webapp/out/selftest_report.json

# 3) 回归（本地套件不消耗外部额度）
python -m pytest webapp/tests -q
python demo/selftest.py
python -m unittest discover -s eval/tests -t .
cd backend && python -m pytest -q tests
```

相关文档：`webapp/README.md`（切片功能与 API）、`docs/evaluation-plan.md`（占位评测方案）、
`eval/README.md`（离线 harness）、`docs/next-version-messaging-and-plugins.md`（消息层演进）。
