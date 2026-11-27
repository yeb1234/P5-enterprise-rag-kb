#!/usr/bin/env python3
"""切官方 DeepSeek 前的探活：一条极简请求验证 key/端点/模型名。

需先设 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL。成功退出码 0，失败 1。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evalbench.core import runner  # noqa: E402

cfg = {"id": "deepseek-flash", "timeout_s": 60}
call = runner.call_live(
    cfg, "Reply with exactly: OK",
    {"temperature": 0.0, "top_p": 1.0, "max_tokens": 32},
)
print(json.dumps({
    "endpoint": runner.active_endpoint()[0],
    "status": call["status"],
    "answer": call.get("answer", "")[:40],
    "detail": call.get("detail"),
}, ensure_ascii=False))
raise SystemExit(0 if call["status"] == "ok" else 1)
