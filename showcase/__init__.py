"""showcase：离线可跑的「文档解析 → 入库预览」演示站点（A/B 档骨架）。

三态标注（docs/showcase/03-phase2-spec.md §7）：
- 已实现：parsing/ 三级策略与质量门禁、guardrails/ 四出口、真实样本与离线解析产物。
- 只设计：收费 API 适配器 profile、在线填真值的 LLM 快照字段、Pro 档真实模型与单价。
- 未做（本轮实现中）：showcase/ 站点本体、实时在线视觉调用作为默认路径、压测与监控 SLO。

边界：不改 webapp/ backend/ parsing/ guardrails/；不动冻结区 eval/ demo/；
      不读 .env 的密钥值（只检查环境变量存在性）；不发起任何外部请求。
"""
from __future__ import annotations

SHOWCASE_VERSION = "0.1.0-showcase"
SCHEMA_VERSION_INGEST = "showcase-ingest@1.0"
SCHEMA_VERSION_MANIFEST = "showcase-manifest@1.0"
DEFAULT_PORT = 8767
# 与 parsing/server.py::MAX_BYTES 对齐（8 MiB）
MAX_BYTES = 8 * 1024 * 1024
