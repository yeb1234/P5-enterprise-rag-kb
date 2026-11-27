# 检索链路复核与参数收敛（2026-11-27）

> 环境：本机 PostgreSQL 16 + pgvector，容器 `p5-kb-infra-postgres-1` / `redis-1` / `neo4j-1` 在线
> 检索评估：CmedqaRetrieval，200 题，`seed=42`；池 3000 篇（3221 chunk）与 12000 篇（12840 chunk）
> 嵌入 / 重排：外部 `BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`，真实 API，不是 mock
> 生成评估：evalbench，`minimax-m3` + `v2_grounded` + params B，12 条（缓存回放，非本轮新调模型）
> 代码基线：`main` `7982b38`；本轮只改评估默认子集与文档，不改检索业务默认

## 结论

1. **全量测试 482 passed / 0 failed**，12 个套件全部通过，无 skip。
2. **公开基准 36.09% 复现**。先前看到的 35.09% 是 `--rerank-queries 300` 的另一个抽样；文档口径是 400 条。按 400 条重跑：不重排 32.79% → top20 **36.09%** → top50 36.02%，与旧报告一致。
3. **业务默认参数不改**：`top_k=5`、`rerank_depth=20`、`rerank=external`、`margin=0.05` 四项都有本轮数据支撑。
4. **自适应重排的收益是均值/吞吐，不是 p95**。3000 池均值 −14.7%，12000 池均值 −6.4%；两套池的 p95 都没有稳定下降，不能拿它当尾延迟 SLA 收益。
5. **只改一处评估默认**：`evalfmt/publicbench.py --rerank-queries` 300 → 400，避免同一条命令跑出两套 nDCG 口径。

---

## 一、全量测试

测试按 12 个目录分别起进程（`eval/` 与 `evalbench/` 都有 `tests/` 包，同进程会重名冲突），每个进程独立 schema，跑完 DROP。

| 套件 | 通过 | 主要覆盖 |
|---|---:|---|
| `webapp/tests` | 197 | API、检索、流式、限流、安全、缓存、自适应重排、三路路由、MCP、设置 |
| `backend/tests` | 38 | 权限、分块、进度、检索契约、答案质量 |
| `knowledge/tests` | 23 | 新鲜度衰减、冲突检测与裁决 |
| `cache/tests` | 19 | L1/L2、13 段 key、启动自检、重启恢复 |
| `guardrails/tests` | 34 | PII 规则、校验位、四出口链路 |
| `parsing/tests` | 22 | 页级元数据、质量门禁、预算闸门 |
| `showcase/tests` | 12 | 解析工作台契约 |
| `evalbench/tests` | 16 | 评估工作台指标与运行器 |
| `evalfmt/tests` | 22 | 格式化与冻结口径 |
| `eval/tests` | 14 | 14 个评测对象与哈希稳定性 |
| `kgraph/tests` | 60 | 术语建图、Cypher 三道防护、多跳查询 |
| `llmwiki/tests` | 25 | 三道门禁、证据链、增量构建 |
| **合计** | **482** | **0 failed，0 skipped** |

这是功能与契约层的全量回归，不等于负载/混沌测试；并发、长稳、故障注入不在这一轮。

---

## 二、检索评估

### 2.1 结果总表

所有配置均为 200 题、`top_k=5`（K10 行除外），延迟为单次端到端实测，含外部 API。

| 池 | 配置 | nDCG@10 | R@1 | R@5 | MRR@10 | mean | p50 | p95 | 跳过 rerank |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 3000 | depth20，margin 0.05 | **0.6747** | 0.4284 | 0.7419 | 0.7102 | 625ms | 702ms | 865ms | 58/200，29.0% |
| 3000 | depth20，margin 0 | 0.6735 | 0.4209 | 0.7431 | 0.7029 | 733ms | 721ms | 856ms | 0 |
| 3000 | depth10，margin 0 | 0.6760 | 0.4359 | 0.7348 | 0.7115 | 605ms | 595ms | 702ms | 0 |
| 3000 | depth20，rerank off | 0.6277 | 0.4017 | 0.6958 | 0.6692 | 323ms | 315ms | 408ms | — |
| 12000 | depth10，margin 0 | 0.5504 | 0.3505 | 0.5974 | 0.6042 | 797ms | 780ms | 927ms | 0 |
| 12000 | depth20，margin 0 | 0.5555 | 0.3405 | 0.6111 | 0.6067 | 908ms | 899ms | 1002ms | 0 |
| 12000 | depth20，margin 0.05 | 0.5547 | 0.3430 | 0.6086 | 0.6064 | 850ms | 907ms | 1097ms | 49/200，24.5% |
| 3000 | top_k=10，depth20 | 0.7124* | — | — | — | 733ms | 716ms | 888ms | 0 |

\* top10 的 nDCG@10 不能与 top5 直接横比：top10 多带了 5 条上下文，指标口径不同。可比的是 hit@k。

### 2.2 配对读法

同一批 200 题逐题配对，避免只看聚合均值。

**自适应重排，3000 池：margin 0.05 vs 0**

- nDCG +0.11pt、R@1 +0.75pt、R@5 −0.12pt、MRR +0.72pt。
- 跳过 58 题，跳过组均值 333ms，未跳组 745ms，整体均值 625ms vs 733ms，省 **14.7%**。
- 跳过组 R@1 涨 2 / 跌 0，R@5 涨 1 / 跌 5；没有任何一题丢掉唯一 gold。

**自适应重排，12000 池：margin 0.05 vs 0**

- nDCG −0.09pt、R@1 +0.25pt、R@5 −0.25pt、MRR −0.02pt，全部在一题（0.5pt）量级的噪声内。
- 跳过 49 题，整体均值 850ms vs 908ms，省 **6.4%**；但 p50 907ms vs 899ms、p95 1097ms vs 1002ms，尾延迟没有变好。
- 跳过组 R@5 涨 1 / 跌 2，丢的是多 gold 题的部分召回；逐题核对后，没有唯一 gold 题被清零。

**深度，3000 池：depth10 vs depth20（都 margin 0）**

- depth10 均值 605ms vs 733ms，快 128ms。
- nDCG +0.25pt、R@1 +1.50pt、R@5 −0.83pt、MRR +0.86pt；小池上浅深度更划算。

**深度，12000 池：depth10 vs depth20（都 margin 0）**

- depth10 均值 797ms vs 908ms，快 111ms。
- nDCG −0.51pt、R@1 +1.00pt、R@5 **−1.37pt**、MRR −0.25pt；大池上 depth20 的 R@5 优势明确。
- R@1 的反向差主要来自多 gold 题的标注口径，不能当作 depth10 更准的证据；R@5 的差距更可信。

**rerank 开关，3000 池：off vs 全开（depth20，margin 0）**

- off 省约 410ms，但 nDCG −4.58pt、R@5 −4.73pt、R@1 −1.92pt。
- 这不是“可关”的优化，是拿召回换延迟，默认必须开。

**top_k 成本，3000 池**

从 top10 记录重算 hit@k：

| k | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hit@k | 0.625 | 0.720 | 0.785 | 0.800 | **0.825** | 0.850 | 0.870 | 0.885 | 0.895 |

top5 相对 top10 少一半上下文，hit 只低 7pt；生成侧旧对照 G5/G10 正确率同为 1.000，F1 0.365 vs 0.373。**top_k=5 是成本决策，不是质量提升。**

### 2.3 公开基准：36.09 与 35.09 的口径问题

本轮先按 CLI 默认 300 条子集跑出 35.09% / 35.13%，与仓库里的 36.09% / 36.02% 不一致；核对后发现旧报告固定用 **400 条子集**，300 条是另一个样本。

按 400 条重跑（100k passage、1570 test query）：

| 配置 | nDCG@10 | R@1 | R@10 |
|---|---:|---:|---:|
| BM25 基线 | 16.29% | 9.23% | 19.73% |
| bge-m3 向量 | 33.93% | 18.96% | 42.93% |
| 同子集不重排 | 32.79% | 16.48% | 43.21% |
| 重排 top20 | **36.09%** | 20.42% | 45.42% |
| 重排 top50 | 36.02% | 19.55% | 46.80% |

结论：**旧文案成立，35.09 不是回归，是 300 条抽样。** 为避免下次再踩，已把 `evalfmt/publicbench.py` 的 `--rerank-queries` 默认从 300 改成 400。

### 2.4 生成侧

`evalbench` run `RUN-9fa455b54f35`：

- `minimax-m3` + `v2_grounded` + params B，12/12 `ok`，`comparable=true`。
- 四个类别（grounded_extraction / information_integration / negative_rejection / noise_robustness）全部 1.0，overall 1.0。
- `avg_latency_ms=1163.2`，`skipped_records=0`。
- **12 条记录的 `from_cache` 全为 true**：这是之前真实调用的缓存回放，不是本轮重新请求模型。

这 12 条只能证明“评分链路、配置快照与缓存复用是一致的”，**不能当生成质量的统计结论，也不能证明模型当前输出仍为 1.0**；样本太小，也没有重复采样。要做生成质量评估，需要清空 response cache 后重跑，并接受 12 条仍然不足的统计限制。

---

## 三、参数收敛

| 参数 | 结论 | 依据 | 是否改默认 |
|---|---|---|---|
| `top_k` | **5** | hit@5 82.5% vs hit@10 89.5%，上下文少一半；生成侧 top5/top10 正确率持平 | 否 |
| `rerank_depth` | **20** | 3000 池 depth10 快 128ms、R@5 低 0.83pt；12000 池 depth10 的 R@5 低 1.37pt。大池必须留 20 | 否 |
| `rerank` | **external，开** | 关掉省 410ms，nDCG −4.58pt、R@5 −4.73pt | 否 |
| `ADAPTIVE_RERANK_MARGIN` | **0.05** | 3000 池跳过 29%、均值省 14.7%；12000 池跳过 24.5%、均值省 6.4%；质量差在噪声内，无唯一 gold 丢失 | 否 |
| 查询向量缓存 | **开** | 本轮未重跑冷/暖对照；沿用 v1.3.3 的 325ms 实测，不动的理由是它只缓存向量、不缓存结果，权限与门禁仍每次执行 | 否 |
| 公开基准子集 | **400** | 与历史报告、README、`evalfmt/README` 的 36.09/36.02 对齐 | **是**（只改评估 CLI） |

**没有把 depth10 设成默认。** 它只在 3000 池上有“快 128ms、R@5 只低 0.83pt”的账；到了 12000 池 R@5 掉 1.37pt，不能用一个默认覆盖两种规模。若线上确实是小池且延迟优先，可以在调用层显式传 `rerank_depth=10`，但不要改全局默认。

**没有把 margin 的收益写成 p95 收益。** 本次两套池的 p95 分别是 865 vs 856（3000）和 1097 vs 1002（12000），都没有稳定变好；它的价值是降低平均外部调用次数。对尾延迟有 SLA 的场景，应另做并发限流、超时与降级，不能靠这个开关。

---

## 四、局限

1. 200 题单次抽样，1 题约 0.5pt；0.1~0.3pt 的差只能当噪声，不能当提升。
2. 外部 embedding / rerank 有抖动；同一配置跨天复跑会有小幅漂移。
3. Cmedqa 医疗语料模板化，口语 query 与 passage 的分布不能代表所有企业语料；换嵌入模型必须重标定 margin。
4. 公开基准的 36.09% 是 400 条子集，不是 1570 条全测试集；全测试集的 bge-m3 是 33.93%。
5. 生成侧只有 12 条，不能用于模型/提示词排名。
6. 本轮没测权限过滤、PII 脱敏、缓存一致性、并发与长稳；这些是另一套实验。

---

## 五、产物与复现

事实源（`webapp/out/` 被 gitignore，不提交）：

- `cmedqa_NOV27_AD05_3000.json` / `_records.jsonl`
- `cmedqa_NOV27_AD0_3000.json` / `_records.jsonl`
- `cmedqa_NOV27_D10_3000.json` / `_records.jsonl`
- `cmedqa_NOV27_D10_12K.json` / `_records.jsonl`
- `cmedqa_NOV27_D20_12K.json` / `_records.jsonl`
- `cmedqa_NOV27_AD05_12K.json` / `_records.jsonl`
- `cmedqa_NOV27_RR_OFF_3000.json` / `_records.jsonl`
- `cmedqa_NOV27_K10_3000.json` / `_records.jsonl`

公开基准（已 tracked）：

- `evalfmt/out/publicbench.json`
- `evalfmt/out/publicbench.html`

生成评估：

- `evalbench/runs/RUN-9fa455b54f35/{config_snapshot.json,records.jsonl,summary.json}`

复现命令：

```bash
# 全量测试：按 README「测试」节的 12 个目录逐套跑
python -m pytest webapp/tests -q
# ... 其余 11 个目录

# 检索：3000 池，margin 0.05
WEBAPP_ADAPTIVE_RERANK_MARGIN=0.05 python webapp/eval_retrieval_cmedqa.py \
  --label NOV27_AD05_3000 --schema kb_depth --reuse --doc-pool 3000 --cases 200 \
  --top-k 5 --rerank-depth 20 --rerank external --limit 200 --no-resume

# 检索：12000 池，margin 0.05
WEBAPP_ADAPTIVE_RERANK_MARGIN=0.05 python webapp/eval_retrieval_cmedqa.py \
  --label NOV27_AD05_12K --schema kb_depth12k --reuse --doc-pool 12000 --cases 200 \
  --top-k 5 --rerank-depth 20 --rerank external --limit 200 --no-resume

# 公开基准：固定 400 条子集
python evalfmt/publicbench.py run --top-k 100 --rerank-queries 400 --latency-probes 20
```
