"""Git 上下文采集：让每次评估 run 都能对应到具体分支与提交。"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(PROJECT), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def snapshot() -> dict:
    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "commit": _git("rev-parse", "--short", "HEAD") or "unknown",
        "commit_full": _git("rev-parse", "HEAD") or "unknown",
        "dirty": bool(_git("status", "--porcelain")),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
