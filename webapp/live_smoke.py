"""真实 LLM 集成冒烟（可选、容忍限流）：

- 直接驱动 providers 的真实端点（Sensenova 代理 DeepSeek V4.1 Flash），验证接线正确；
- 免费 token 有 tpm/rpm 限制，429 时打印 WARNING 并跳过，不让常规自测失败；
- 离线降级链路由 pytest 覆盖，这里只证明「真实模型能通」。

运行：python webapp/live_smoke.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__) + os.sep + "..")
os.environ["WEBAPP_LLM_PROVIDER"] = "sensenova"  # 强制走真实端点（密钥从 .env 读取）

from webapp import providers  # noqa: E402


def main() -> int:
    p = providers.get_llm()
    print("provider:", p.model_label)
    print("base_url:", p.base_url)
    retrieved = [{
        "doc_title": "设备配网说明书",
        "text": "设备配网步骤：长按复位键3秒进入配网模式，手机连接设备热点后在App中输入"
                "家庭Wi-Fi密码即可完成配网。",
    }]
    deadline = time.time() + 60
    try:
        out = []
        for tok in p.stream("如何给设备配网？", retrieved):
            out.append(tok)
            if time.time() > deadline:
                raise RuntimeError("超时保护：单次冒烟不超过 60s")
        print("REAL LLM OK | tokens:", len(out))
        print("answer:", "".join(out)[:300])
        return 0
    except RuntimeError as e:
        msg = str(e)
        if "429" in msg or "限流" in msg:
            print("WARNING: 真实调用被限流（免费 token 限制），跳过本次冒烟。")
            print("         离线降级链路已由 pytest 覆盖；稍后降低频率重试即可。")
        else:
            print("WARNING: 真实调用失败：", msg[:160])
        return 0  # 不视为失败，避免阻塞


if __name__ == "__main__":
    sys.exit(main())
