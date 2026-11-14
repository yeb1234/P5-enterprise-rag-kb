"""重新探测网关的视觉能力：`python -m parsing.vision_probe`

只读 `/models` 元数据里的 `input_modalities`，不发图片、不消耗生成 token。
`parsing/router.py::status()` 里展示的是 2026-11-14 的实测静态快照，
模型清单变动时用本脚本重新核对，再把结果同步到 `parsing/config.py`。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from parsing.config import TEXT_ONLY_MODELS, VISION_CAPABLE_MODELS, VISION_PROBE_NOTE


def probe(base_url: str | None = None, api_key: str | None = None,
          timeout: float = 8.0) -> dict:
    base = (base_url or os.environ.get("PARSING_VISION_BASE_URL")
            or os.environ.get("DISCOVERY_BASE_URL", "")).rstrip("/")
    key = (api_key or os.environ.get("PARSING_VISION_API_KEY")
           or os.environ.get("DISCOVERY_API_KEY", ""))
    if not base or not key:
        return {"ok": False, "error": "未配置 DISCOVERY_BASE_URL / DISCOVERY_API_KEY"}
    try:
        req = urllib.request.Request(base + "/models",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    capable, text_only = [], []
    for m in data.get("data", []):
        mods = {x.get("type") for x in m.get("input_modalities", [])}
        (capable if "image" in mods else text_only).append(str(m.get("id")))
    return {"ok": True, "total": len(capable) + len(text_only),
            "vision_capable": capable, "text_only": text_only,
            "match_snapshot": set(capable) == set(VISION_CAPABLE_MODELS)
            and set(text_only) == set(TEXT_ONLY_MODELS)}


def main() -> int:
    r = probe()
    if not r["ok"]:
        print("探测失败:", r["error"])
        return 1
    print("网关 /models 视觉能力探测（只读，无生成调用）")
    print(f"模型总数: {r['total']}")
    print(f"支持 image ({len(r['vision_capable'])}): " + ", ".join(r["vision_capable"]))
    print(f"仅文本 ({len(r['text_only'])}): " + ", ".join(r["text_only"]))
    print("与 2026-11-14 快照一致:", r["match_snapshot"])
    print("说明:", VISION_PROBE_NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
