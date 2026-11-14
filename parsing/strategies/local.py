"""P0 自研解析：零外部服务，纯标准库 + 可选 pypdf / Pillow。

能真实处理的输入：纯文本 / Markdown / CSV / JSON / 代码文件、带文本层的
PDF（有 pypdf 时）、图片的容器头结构化事实。表格走 `parsing.tables`
做结构语义还原与稳定性校验。

判定「需不需要升级」的关键输出是 QualitySignals，全部可复算：
- text_chars 低于阈值 + 高熵 / 图片格式 → ocr_needed=True
- 表格检查失败比例高 → table_complete=False、置信度下调
- 每一条判定都写进 notes，前端解析轨迹可逐页展示
"""
from __future__ import annotations

import json
import time
from typing import Any

from parsing import images, tables
from parsing.config import ParseConfig
from parsing.contracts import (Block, LEVEL_LOCAL, ParseResult, QualitySignals)
from parsing.strategies.base import ParseStrategy, register

TEXT_MIMES = ("text/", "application/json", "application/xml", "application/csv")
IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp")


@register
class LocalStrategy(ParseStrategy):
    name = "local"
    level = LEVEL_LOCAL

    def __init__(self, cfg: ParseConfig | None = None):
        self.cfg = cfg or ParseConfig()

    def parse(self, payload: bytes, *, mime: str, page_no: int,
              context: dict[str, Any] | None = None) -> ParseResult:
        t0 = time.time()
        ctx = context or {}
        notes: list[str] = []

        # 0) PDF 且没有可用的文本层提取器：二进制流不能当文本 decode，
        #    否则乱码会被当成正文，质量门禁永远判不出「需要 OCR」。
        if mime == "application/pdf" and not ctx.get("simulated_text_layer"):
            desc = "[无文本层 PDF · 待 OCR] 未安装 pypdf 或该 PDF 为扫描件，本页未提取到正文。"
            return ParseResult(
                text=desc,
                blocks=[Block(block_type="paragraph", text=desc, page_no=page_no,
                              confidence=0.15)],
                signals=QualitySignals(
                    text_chars=0, text_density=0.0, image_ratio=1.0,
                    ocr_needed=True, confidence=0.15,
                    notes=("PDF 无文本层提取结果", "需升级到 P1 OCR 或 P2 多模态",
                           "接口契约 __TBC__，当前未接入收费服务"),
                ),
                level=LEVEL_LOCAL, degraded="no_text_layer",
                source="local:pdf-no-text-layer",
                latency_ms=int((time.time() - t0) * 1000),
            )

        # 1) 图片：拿结构化事实，不声称做了视觉理解
        if mime.startswith("image/") or images.probe_image(payload)["format"] != "unknown":
            facts = images.probe_image(payload)
            desc = images.image_to_text_facts(facts)
            entropy = float(facts.get("entropy") or 0)
            return ParseResult(
                text=desc,
                blocks=[Block(block_type="image", text=desc, page_no=page_no,
                              facts=facts, confidence=0.35)],
                signals=QualitySignals(
                    text_chars=0, text_density=0.0, image_ratio=1.0,
                    table_cells=0, table_complete=True,
                    ocr_needed=entropy >= self.cfg.image_entropy_min,
                    confidence=0.35,
                    notes=("图片容器头解析（P0 层不调用视觉，交由质量门禁升级）",
                           f"熵={entropy}（高熵通常意味着压缩位图）",
                           "内容未识别，需 OCR 或视觉层级"),
                ),
                level=LEVEL_LOCAL, source="local:image-header",
                degraded="image_content_unrecognized",
                latency_ms=int((time.time() - t0) * 1000),
            )

        # 2) 文本类：解码 + 表格抽取
        text = payload.decode("utf-8", errors="replace")
        if ctx.get("simulated_text_layer"):
            notes.append("PDF 文本层（pypdf 逐页提取）")
        tbls, plain = tables.extract_tables(text)
        blocks: list[Block] = []
        for i, t in enumerate(tbls):
            blocks.append(Block(block_type="table",
                                text=tables.table_to_context(t), page_no=page_no,
                                section_path=f"表{i + 1}", cells=t["cells"],
                                confidence=float(t["confidence"])))
        for para in [p.strip() for p in plain.split("\n\n") if p.strip()]:
            blocks.append(Block(block_type="paragraph", text=para, page_no=page_no))

        chars = len(text.strip())
        # 粗略密度：字符 / 估计页面「单位面积」。这里用 3000 字符作为满页基准，
        # 与 backend/app/services/parser.py 的 page_chars 保持一致，便于口径对齐。
        density = min(1.0, chars / 3000.0)
        # 表格结构是否自洽：只有全部稳定性检查通过才算可信。
        # 不能只看 confidence 阈值，否则参差表也会以 0.88 的分数混过去。
        table_complete = all(c["passed"] for t in tbls for c in t["checks"])
        failed = sum(1 for t in tbls for c in t["checks"] if not c["passed"])
        total_checks = sum(len(t["checks"]) for t in tbls) or 1
        fail_ratio = failed / total_checks

        # 「无文本层」是**来源**属性，不是长度属性。
        # PDF / 图片页提取不到字符才需要 OCR；一个短的纯文本页只是内容少，
        # 有可读文本就不该被判成扫描件，否则正常文档会被整批送去最贵链路。
        binary_source = bool(ctx.get("simulated_text_layer")) or mime.startswith("image/")
        empty_page = chars == 0
        # 零字符页要靠上一级确认是「真空白」还是「扫描件没提出文字」；
        # 这也让质量门禁有一个确定可复现的升级入口，便于演示。
        ocr_needed = empty_page or (binary_source and chars < self.cfg.ocr_text_chars_min)
        if empty_page:
            notes.append("本页零字符，规则无法区分真空白与扫描件，需上一级确认")
        elif chars < self.cfg.ocr_text_chars_min and not binary_source:
            notes.append(f"短文本页（{chars} 字符，有可读文本，不判为无文本层）")
        if ocr_needed and not empty_page:
            notes.append(f"非文本来源且可提取字符 {chars} < 阈值 "
                         f"{self.cfg.ocr_text_chars_min}，疑似无文本层")
        if tbls:
            notes.append(f"识别 {len(tbls)} 张表，结构检查失败 {failed}/{total_checks}")
        if not table_complete:
            notes.append("表格结构不自洽，建议升级到版面解析")

        confidence = 1.0 if not ocr_needed else 0.2
        if not ocr_needed:
            confidence -= 0.5 * fail_ratio
        confidence = max(0.05, min(1.0, round(confidence, 4)))

        # JSON 单独试解析，结构化程度更高时给一点置信度加成
        if mime == "application/json":
            try:
                json.loads(text)
                notes.append("JSON 语法校验通过")
                confidence = min(1.0, confidence + 0.05)
            except Exception:
                notes.append("JSON 语法校验失败")
                confidence = max(0.05, confidence - 0.15)

        return ParseResult(
            text=text, blocks=blocks,
            signals=QualitySignals(
                text_chars=chars, text_density=density,
                image_ratio=0.0,
                table_cells=sum(len(t["cells"]) for t in tbls),
                table_complete=table_complete, ocr_needed=ocr_needed,
                confidence=confidence, notes=tuple(notes),
            ),
            level=LEVEL_LOCAL,
            degraded="no_text_layer" if ocr_needed else None,
            source="local:rules+pypdf" if ctx.get("simulated_text_layer") else "local:rules",
            latency_ms=int((time.time() - t0) * 1000),
        )
