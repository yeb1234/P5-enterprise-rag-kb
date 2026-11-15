"""生成演示用真实样本与离线解析产物（可复跑，不摆拍、不伪造 JSON）。

用途：
- `simple.pdf`  带真实文本层的 PDF（P0 local 直接读，不升级）
- `complex.png` 模拟扫描发票图（无文本层 → 质量门禁升级 → 离线降级）
- `*.report.json`  `parsing.ParseRouter` 的真实输出，逐字段来自代码
- `*.canonical.json` 剥离非确定性字段后的可复现视图，用于「同参双跑逐字节一致」判定

为什么要 canonical：实测同一份输入连续跑两次，`summary.total_latency_ms`、
`traces[].latency_ms`、`results[].latency_ms` 会变化（44 → 0），直接比较必然不一致。
所以观测字段与可复现字段必须分开，不能靠「顺便忽略」蒙混过去。

复跑：
    PARSING_OFFLINE=1 python showcase/samples/make_samples.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PARSING_OFFLINE", "1")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # 允许 `python showcase/samples/make_samples.py` 直接复跑

from PIL import Image, ImageDraw  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (DecodedStreamObject, DictionaryObject,  # noqa: E402
                           NameObject)

# 观测字段：同一输入两次运行也会变，不进可复现视图
NON_REPLAY_FIELDS = ("latency_ms", "created_at", "run_id", "generated_at")


def build_simple_pdf() -> bytes:
    """手写 PDF 内容流造一份带文本层的说明书（本机无 reportlab）。"""
    w = PdfWriter()
    page = w.add_blank_page(width=595, height=842)
    lines = [
        "Product Manual  /  Chapter 1 Setup",
        "Press the power button for 3 seconds to start the device.",
        "Open Settings - Network, select Wi-Fi and finish pairing.",
        "Table: item=A value=42 ; item=B value=57",
        "Chapter 2 FAQ: If the answer is empty, check the uploaded documents.",
        "Contact: support@example.com  |  Staff ID: EMP-20260118",
        # 以下扩充是为了让「分块大小」参数产生可观测差异：
        # 文本长度需明显超过 800 字符，否则 800/80 与 400/80 都只切出 1 块。
        "Section 1.1 Indicator states: blue blinking means pairing mode is active.",
        "Section 1.2 Indicator states: solid green means the network is connected.",
        "Section 1.3 To reset the device, hold the button for at least 10 seconds.",
        "Section 1.4 After reset, all local settings return to factory defaults.",
        "Section 1.5 The default administrator account must be changed on first use.",
        "Section 1.6 Firmware updates are delivered over the air once per quarter.",
        "Section 1.7 Do not power off the device while a firmware update is running.",
        "Section 2.1 Upload a document from the Documents page before asking questions.",
        "Section 2.2 The parser extracts text, tables and page structure into chunks.",
        "Section 2.3 Each answer carries citations back to the original chunk ids.",
        "Section 2.4 If retrieval is empty, the assistant returns an honest fallback.",
        "Section 2.5 Badcase records can be exported by an administrator for review.",
        "Section 3.1 Sensitive identifiers are redacted before indexing and logging.",
        "Section 3.2 The four redaction gates share one rule set and one version tag.",
        "Section 3.3 Cache keys include authorization, data and policy version fields.",
        "Section 3.4 Restart recovery rebuilds volatile state from the truth layer.",
        "Section 4.1 This manual is a synthetic sample for offline demonstration only.",
        "Section 4.2 No real personal data appears in this generated document.",
    ]
    ops = ["BT /F1 13 Tf 60 770 Td 18 TL"]
    for i, s in enumerate(lines):
        esc = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj" + (" T*" if i < len(lines) - 1 else ""))
    ops.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = w._add_object(stream)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})
    })
    buf = io.BytesIO()
    w.write(buf)
    data = buf.getvalue()
    # 自检：文本层必须真的能被 pypdf 提取
    assert len(PdfReader(io.BytesIO(data)).pages[0].extract_text() or "") > 100
    return data


def build_complex_png() -> bytes:
    """造一张「扫描发票」图：P0 只能读容器头，质量门禁应判需升级。"""
    im = Image.new("RGB", (900, 1200), (248, 249, 251))
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 860, 1160], outline=(200, 205, 215), width=2)
    d.text((70, 80), "SCANNED INVOICE / 2026-11", fill=(25, 35, 55))
    d.text((70, 130), "Vendor: ACME Industrial Co., Ltd.", fill=(25, 35, 55))
    for r in range(6):
        y = 220 + r * 60
        d.rectangle([70, y, 830, y + 50], outline=(170, 178, 190), width=1)
        d.text((90, y + 16),
               f"ITEM-{r + 1:02d}    QTY {r + 2}    UNIT {12.5 + r}    TOTAL {(r + 2) * (12.5 + r):.2f}",
               fill=(25, 35, 55))
    d.text((70, 700), "Signature: ____________   Date: 2026-11-15", fill=(25, 35, 55))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def canonical(obj):
    """递归剔除观测字段，只留可复现内容。"""
    if isinstance(obj, dict):
        return {k: canonical(v) for k, v in sorted(obj.items())
                if k not in NON_REPLAY_FIELDS}
    if isinstance(obj, list):
        return [canonical(x) for x in obj]
    return obj


def main() -> None:
    from parsing.config import ParseConfig
    from parsing.router import ParseRouter

    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "simple.pdf").write_bytes(build_simple_pdf())
    (HERE / "complex.png").write_bytes(build_complex_png())

    router = ParseRouter(cfg=ParseConfig(), strategy="auto")
    cases = [
        ("simple", "simple.pdf", "application/pdf"),
        ("complex", "complex.png", "image/png"),
    ]
    for stem, filename, mime in cases:
        payload = (HERE / filename).read_bytes()
        rep = router.parse_document(payload, filename=filename, mime=mime,
                                    doc_id=f"DEMO-{stem.upper()}")
        # 只加必要元信息，且明确标注哪些字段不参与重放比对
        rep = dict(rep)
        rep["producer"] = {
            "script": "showcase/samples/make_samples.py",
            "offline": os.environ.get("PARSING_OFFLINE") == "1",
            "parser": "parsing.ParseRouter",
            "non_replay_fields": list(NON_REPLAY_FIELDS),
            "note": "本产物由真实解析链路生成；复杂档为离线降级回放，不是实时视觉识别",
        }
        (HERE / f"{stem}.report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (HERE / f"{stem}.canonical.json").write_text(
            json.dumps(canonical(rep), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

        t = rep["traces"][0]
        r = rep["results"][0]
        print(f"[{stem}] pages={rep['page_count']} level={t['level']} "
              f"upgraded_from={t['upgraded_from']} reason={t['reason']} "
              f"degraded={t['degraded']} confidence={r['signals']['confidence']} "
              f"text_chars={r['signals']['text_chars']}")

    # 自检：canonical 视图同参双跑必须逐字节一致
    a = ParseRouter(cfg=ParseConfig(), strategy="auto").parse_document(
        (HERE / "simple.pdf").read_bytes(), filename="simple.pdf",
        mime="application/pdf", doc_id="DEMO-SIMPLE")
    b = ParseRouter(cfg=ParseConfig(), strategy="auto").parse_document(
        (HERE / "simple.pdf").read_bytes(), filename="simple.pdf",
        mime="application/pdf", doc_id="DEMO-SIMPLE")
    same = (json.dumps(canonical(a), ensure_ascii=False, sort_keys=True) ==
            json.dumps(canonical(b), ensure_ascii=False, sort_keys=True))
    raw_same = (json.dumps(a, ensure_ascii=False, sort_keys=True) ==
                json.dumps(b, ensure_ascii=False, sort_keys=True))
    print(f"canonical 双跑一致={same}  原始双跑一致={raw_same}")
    print("说明：原始视图含 latency_ms 等观测字段，本次两次耗时均不足 1ms 时可能恰好一致，"
          "但耗时一变就会不一致，因此只有 canonical 视图可作为「逐字节可复现」依据。")
    assert same, "canonical 视图必须可逐字节复现"
    print("生成完毕：", ", ".join(sorted(p.name for p in HERE.iterdir() if p.is_file())))


if __name__ == "__main__":
    main()
