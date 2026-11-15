"""parsing/ 独立的 .env 加载器（零依赖，不引用 webapp）。

为什么需要：`parsing/` 是纯函数包，可以脱离 webapp 单独跑
（`python -m parsing.vision_probe`、`uvicorn parsing.server:app`），
但 `config.py` 的端点默认值在 import 时求值，必须先把 `.env` 补进 os.environ。
规则与 webapp/envfile.py 一致：只补「当前不存在」的键，已存在的环境变量优先，
所以单测 setenv / compose 注入都能覆盖 .env。
"""
from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_env() -> None:
    global _LOADED
    if _LOADED:
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    try:
        for ln in env_path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except (FileNotFoundError, OSError):
        pass
    _LOADED = True
