"""`evalfmt` · 多格式文档评估（解析保真 / 检索召回 / 引用准确 / 幻觉与拒答）。

与 `eval/`（冻结的离线占位评测）和 `evalbench/`（真实 API 工作台）平级但独立：
- `eval/` 是**冻结口径**，不改、不覆盖；
- `evalbench/` 测的是「同一批文本样本在不同模型/提示词下的表现」；
- `evalfmt/` 测的是「**格式**换掉之后，解析、召回、生成三层各掉多少」。

入口：`python -m evalfmt.run_eval --mode lexical --answer off`
"""
