# `knowledge/` · 知识治理：入库新鲜度 + 事实冲突检测

> 一句话：**让「哪些证据现在适用」和「这些证据互相打架吗」变成可复算、可展示、能指给评审方看的正式状态**，而不是让模型在一堆片段里自己碰运气。

## 这个目录解决什么

`parsing/` 负责「原始字节 → 结构化文本」，本包负责「检索到的一堆片段里，哪些现在有效、哪些彼此矛盾」。下游 `ChunkSpec` / Milvus schema / citation 契约一律不动。

```
上传 → 解析(parsing/) → 分块 → 检索(词法+向量+rerank)
   → 新鲜度重排(knowledge/freshness.py)
   → claim 抽取(knowledge/claims.py)
   → 冲突检测与按簇裁决(knowledge/conflicts.py)
   → EvidenceRecord → 生成层 → 来源卡片
```

## 目录地图

| 文件 | 职责 |
|---|---|
| `contracts.py` | 数据契约：`FreshnessPolicy` / `DocMeta` / `FreshnessResult` / `Claim` / `Conflict` / `EvidenceRecord` |
| `freshness.py` | 时间衰减、适用性门控、过期三档处置、重排与可观测汇总 |
| `claims.py` | 规则级断言抽取、归一化、防误报、主题相似度门控 |
| `conflicts.py` | 冲突检测、冲突图连通分量、按簇裁决、结构化证据记录 |
| `samples.py` | 内置合成演示场景 `DEMO-FRESHNESS-CONFLICT-001`（不写业务库） |
| `tests/` | 23 项离线单测，零外部调用 |
| `README.md` | 本文：入口、公式、演示步骤、诚实边界 |

配套：`webapp/routers_knowledge.py`（HTTP 适配）、`webapp/static/` 的「知识治理」视图、`docs/knowledge/01–02`（调研 / 候选）、`docs/knowledge/04-design.md`（设计）。

## 两个核心公式（能手算复核）

```
衰减 decay   = max(demote_floor, 0.5 ^ (age_days / half_life_days))
有效分       = 相关分 × (1 - blend + blend × decay)
```

- `age_days` 优先取 `last_verified_at`（人工复核时间），其次 `indexed_at`，最后 `valid_from`——**发布时间不等于生效时间，也不等于「还有效」**。
- `blend=0.65` 保证新鲜度不会把一篇高度相关的老文档彻底压没；`demote_floor=0.10` 保证衰减不归零。
- 全部是演示默认经验起始值，**非本项目实测、非推荐配置**，需用真实语料校准。

## 三条容易答错的工程判断

1. **最新 ≠ 适用**。`valid_from` 在未来的文档要标 `not_yet_effective` 并排除（不是判它过期）；`scope`（地区/套餐/产品版本）不同的两份文档，同一字段取值不同也不算冲突。
2. **相似度最高 ≠ 可信**。向量分数里没有权威度、生效期、适用性；位置偏差、语气强弱、词法重合都会让模型挑错来源。所以冲突必须作为 `conflict_signal` 正式传给生成层。
3. **新版本优先只是先验，不是自动采信**。`trust_newer_with_review` 强制带 `needs_review=True`；权威性相当且无法裁决时，并列呈现两个版本 + 原文 + 转人工，**不发明 tie-breaking 规则**。

## 按簇裁决顺序

```
业务权威度(authority) 不同  → trust_higher_authority        （无需复核）
否则生效期不同              → trust_newer_with_review       （needs_review=True）
否则                        → present_both_and_escalate     （winner=None）
```

冲突图用并查集求连通分量，**每个簇独立裁决**，避免「整批只留最新一份」误删其它独立簇的有效信息。被淘汰的 claim 不删除，只在 `EvidenceRecord.excluded_claim_ids` 里留痕。

## 防误报（评审最常被追问）

- 年份 1900–2099 且无单位 → 降置信度，不作为冲突燃料；
- 裸小整数 1–9 且无单位 → 降置信度，不单独触发冲突；
- 单位、否定词、适用范围原样保留，归一化只用于比对不改原文；
- 主题相似度（中文二元组 + 英文整词 Jaccard）低于阈值不比对；
- 每条 claim 保留支持片段 `text` 与 `chunk_id`，引用可回溯。

## 三条命令

```bash
# 1) 离线单测（零外部调用、零模型流量）
python -m pytest knowledge/tests -q

# 2) 直接跑演示场景，看排序前后对照与两类裁决
python -c "from knowledge.samples import build_scenario; s=build_scenario(); \
print(s['freshness']['order_with'], s['freshness']['order_without']); \
print([(c['kind'], c['resolution'], c['needs_review']) for c in s['evidence']['conflicts']])"

# 3) 前端可视化
python -m uvicorn webapp.main:app --port 8765
#   admin / admin123 → 侧栏「知识治理」→ 改半衰期/过期处置 → 看排序翻转与裁决日志
```

内置场景输出（当前默认值）：

| 项 | 结果 |
|---|---|
| 新鲜度开 | `D2 → D4 → D3 → D1 → D6`（新文档反超旧的） |
| 仅关衰减 | `D1 → D2 → D3 → D4 → D6`（恢复按相关分） |
| 适用性门控 | `D5` 未来生效被排除、`D6` 已过期按 demote 降权 |
| 冲突簇 1 | 婚假 `10 天` vs `15 天` → `freshness` / `trust_newer_with_review` / 需复核 |
| 冲突簇 2 | 报销上限 `5000 元` vs `8000 元` → `authority` / `trust_higher_authority` |
| 负向对照 | GB 地区不算冲突、接口超时同值不算冲突、未生效/已过期不参与 claim |

## 诚实边界

- 合成样本是**功能可演示**的证明，不是质量基线；未做真实语料集评测、未做压测、未测限流与失败语义。
- 冲突检测是**规则级**（正则 + 归一化 + 图），不是 NLI 模型级矛盾识别；模型只宜作告警线索，不自动裁决。
- `authority` 目前是 `DocMeta` 里的整数，由调用方传入；**来源权威注册表的业务审批流程未做**。
- 真实检索链的新鲜度重排**默认关闭**（`WEBAPP_FRESHNESS_ENABLED=1` 开启），且只在 `top_k` 内重排；生产应在 SQL / Milvus expr 阶段先做召回前硬过滤。
- **生成层注入 EvidenceRecord 仍为只设计**：证据记录已就绪，但尚未注入 prompt，逐句 grounding 高亮未做。
- 幽灵向量、CDC 近实时增量、跨缓存版本感知失效、金标/金丝雀回归集均为未做，见 `docs/knowledge/02-candidates.md`。
