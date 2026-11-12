"""极简 .env 加载器（零依赖，供 settings / providers 共用）。

只把「当前 os.environ 中不存在」的键补进去，已存在的环境变量优先（便于测试覆盖）。
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
    except FileNotFoundError:
        pass
    _LOADED = True
