# 本机自测清单（约 5 分钟）

> 照着走一遍，任何一步不符合「预期」就停下来查——这比看文档更能确认「能演示」。

## 0. 服务是否在跑

```bash
docker ps --filter name=kb- --format "{{.Names}} | {{.Status}}"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8767/
```

**预期**：两个容器 `Up (healthy)`；两个端口都返回 `200`。
没起来：`bash run-demo.sh`（构建 + 启动 + 等健康检查）。

## 1. 完整功能首页 `http://127.0.0.1:8765`

| 步骤 | 操作 | 预期 |
|---|---|---|
| 1.1 | 用 `admin / admin123` 登录 | 进入工作台概览，侧栏出现 15 个入口 |
| 1.2 | 文档管理 → 上传任意 `.txt` / `.pdf` | 状态从 `queued` → `analyzing` → `ready`，分块数 > 0 |
| 1.3 | 智能问答 → 问「怎么配网」 | 流式输出；回答下方出现**来源卡片**（文档/分块/得分） |
| 1.4 | 任务中心 | 4 张统计卡 + 「按用户积压 / 按部门」表；普通用户看不到文件名 |
| 1.5 | 内容安全 / PII → 运行规则演示 | 9 条样本出命中；4 条负向对照零命中；流式验证 `leaked=false` |
| 1.6 | 评估测试 → 限流规则表 | 能看到 `user / endpoint / model / ip` 四个维度，`ip` 标「接入层防刷」 |
| 1.7 | 侧栏「解析工作台 8767」 | 同标签跳到 8767（不再新开标签页） |

## 2. 解析工作台 `http://127.0.0.1:8767`

| 步骤 | 操作 | 预期 |
|---|---|---|
| 2.1 | 打开页面（自动跑简单档） | 左侧 `level=local`、`confidence=1.0`；右侧入库 JSON 有 3 个 chunk |
| 2.2 | 把分块大小 800 改成 400 重跑 | chunk 数 3 → 6，`run_id` 变化 |
| 2.3 | 切复杂档（`complex.png`） | `level=multimodal`、`degraded=vision_unavailable`，页面顶部有「离线回放」标注 |
| 2.4 | 复杂档连跑两次 | 第 1 次 `standard(1/2)`，第 2 次 `pro(2/2)`，`run_id` 不同 |
| 2.5 | 点「同参双跑对拍」 | 显示「逐字节一致」；改参后显示「已变化」 |
| 2.6 | 顶部「← 完整功能首页」 | 同标签跳回 8765 |

## 3. 讲解页（双击即可，零外部依赖）

- `interview/index.html`：三态边界、核心亮点、数据流、验证快照、评审 Q&A
- `interview/journey.html`：五段演进、8 个亮点、8 个真实缺陷、15 个常见问题
- `interview/cases.html`：九节点数据流向图 + 14 个故障点 + 评审三层讲法 + 自测清单
- `docs/showcase/aliyun-guide.html` / `tencent-cloud-guide.html`：两家云的上线指导

## 4. 回归（可选，确认没被后续改动弄坏）

```bash
# 9 个包，共 292 项
for d in webapp/tests parsing/tests knowledge/tests cache/tests guardrails/tests \
         backend/tests eval/tests evalbench/tests showcase/tests; do
  python -m pytest -q -p no:cacheprovider "$d" | tail -n 1
done

# 33 项端到端（需先起 8765，用隔离库避免污染演示库）
python webapp/selftest.py
```

## 5. 常见不符合项怎么查

| 现象 | 先看哪里 |
|---|---|
| 8765/8767 打不开 | `docker logs --tail 50 kb-webapp` / `kb-showcase` |
| 上传后一直 `queued` | 容器重启会把 `queued` 重投，看日志是否有解析报错 |
| 问答返回「没有找到」 | 文档是否 `ready`、问题是否与文档用词差异太大（词法召回） |
| 任务中心看不到别人的积压 | 普通用户只显示部门与短码；文件名明细要 admin |
| 演示数据被消费 | 跑 `demo_seed_queue.py --seed` 重新生成 |

## 6. 哪些「不符合」是正常的

- 普通用户看不到 `admin / audit / settings` 三个入口 —— **设计如此**（前端拦截 + 后端 403）；
- 复杂档没有真的调用外部视觉模型 —— **离线演示**，回放的是预先留存的真实解析产物；
- 限流 429 / 并发 503 平时不出现 —— 只有压测或规则调小才触发；
- `external_request_count` 恒为 0 —— 默认全离线，不消耗真实额度。
