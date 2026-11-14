"""图片解析：不依赖视觉模型时能拿到的结构化信息。

设计取舍：
- 纯标准库解析常见图片容器头（PNG/JPEG/GIF/WebP/BMP），拿尺寸、格式、
  色彩模式与字节熵。零依赖即可运行，演示环境一定能跑出结果。
- 有 Pillow 时补充 DPI / 色彩模式 / 是否近似空白页；没有就诚实标
  `probe=header_only`，绝不假装做了像素级分析。
- 输出是「结构化事实」而不是「图片内容描述」：没有视觉模型时，
  把尺寸/格式/熵/是否二值化等信号序列化成文本，交给纯文本 LLM 做**受限推断**，
  这叫结构化降级，不是视觉理解。
"""
from __future__ import annotations

import math
import struct
from typing import Any

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def shannon_entropy(data: bytes, sample: int = 65536) -> float:
    """字节级香农熵（0..8）。压缩图片接近 8，纯文本通常 4~5。

    用途：在不解码图片的情况下粗略判断「这一页是不是以位图为主」，
    是质量门禁里 image_ratio 的一个兜底信号。
    """
    if not data:
        return 0.0
    chunk = data[:sample]
    freq = [0] * 256
    for b in chunk:
        freq[b] += 1
    n = len(chunk)
    return -sum((c / n) * math.log2(c / n) for c in freq if c)


def probe_image(data: bytes) -> dict[str, Any]:
    """解析图片容器头，返回结构化事实。无法识别时 format=unknown。"""
    out: dict[str, Any] = {
        "format": "unknown", "width": None, "height": None,
        "bit_depth": None, "color_type": None,
        "bytes": len(data), "entropy": round(shannon_entropy(data), 3),
        "probe": "header_only",
    }
    if data.startswith(PNG_SIG) and len(data) >= 26 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        out.update(format="png", width=w, height=h,
                   bit_depth=data[24], color_type=data[25])
    elif data.startswith(b"\xff\xd8"):
        out["format"] = "jpeg"
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                out.update(width=w, height=h)
                break
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > len(data):
                break
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        out.update(format="gif", width=w, height=h)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        out["format"] = "webp"
        fourcc = data[12:16]
        if fourcc == b"VP8X":
            out["width"] = int.from_bytes(data[24:27], "little") + 1
            out["height"] = int.from_bytes(data[27:30], "little") + 1
        elif fourcc == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            out.update(width=w & 0x3FFF, height=h & 0x3FFF)
    elif data[:2] == b"BM" and len(data) >= 26:
        w, h = struct.unpack("<ii", data[18:26])
        out.update(format="bmp", width=abs(w), height=abs(h))

    # 可选增强：有 Pillow 才做像素级判断，没有就保持 header_only
    try:
        from PIL import Image  # type: ignore
        import io
        with Image.open(io.BytesIO(data)) as im:
            out["pillow_mode"] = im.mode
            out["dpi"] = im.info.get("dpi")
            out["probe"] = "header+pillow"
            small = im.convert("L").resize((32, 32))
            px = list(small.getdata())
            lo, hi = min(px), max(px)
            out["luma_range"] = hi - lo
            out["near_blank"] = bool(hi - lo < 12)
    except Exception:
        pass
    return out


def image_to_text_facts(facts: dict[str, Any]) -> str:
    """把结构化事实序列化成受限的文本上下文，供纯文本 LLM 做**非视觉**推断。

    明确不写「图中是什么」，只写可测事实；避免模型凭空编造图片内容。
    """
    parts = ["[图片结构事实 · 非视觉理解]"]
    parts.append(f"格式={facts.get('format')} 尺寸={facts.get('width')}x{facts.get('height')} "
                 f"字节={facts.get('bytes')} 熵={facts.get('entropy')}")
    parts.append(f"探测方式={facts.get('probe')}（header_only 表示只解析了容器头）")
    if facts.get("pillow_mode"):
        parts.append(f"色彩模式={facts['pillow_mode']} 亮度范围={facts.get('luma_range')} "
                     f"疑似空白页={facts.get('near_blank')}")
    parts.append("结论：当前无可用视觉端点，图片内容未识别；若需文字请走 OCR 层级。")
    return "\n".join(parts)


def vision_message(prompt: str, image_bytes: bytes, mime: str = "image/png") -> dict:
    """构造 OpenAI 兼容的传图消息（**只构造，不发送**）。

    这是「支持传图的多模态兜底」的请求契约：等补了 VISION_* 端点，
    把它 POST 到 /chat/completions 即可，不需要改路由代码。
    """
    import base64
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "model": "__TBC__",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "stream": False,
        "max_tokens": 1024,
    }
