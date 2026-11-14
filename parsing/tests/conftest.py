"""解析子系统单测隔离：全程零外部请求。

P2 多模态已接通真实网关，因此单测必须显式关掉视觉层，
否则一次图片用例就会消耗真实流量。`MultimodalStrategy.available()`
每次调用动态读 `PARSING_OFFLINE`，这里在收集用例前设好即可。
需要测「真实调用路径」的用例用 monkeypatch 临时打开并 mock urllib。
"""
import os

os.environ["PARSING_OFFLINE"] = "1"
