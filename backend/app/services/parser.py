"""解析与语义分块（骨架版）。

设计文档里 MinerU / OCR / 多模态是「质量源头」，本骨架先把接口与页级元数据
（page_no、section_path、block_type、confidence、source_level）固定下来，
实现用 pypdf + 规则分块。后续把 Parser 换成 MinerU 时，
下游 chunks 表、Milvus schema、citation 契约都不用改 —— 这是预留扩展点的意义。

大文件原则：绝不 get() 整个文件到内存，先流式落临时文件，再按页范围读。
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.integrations.object_storage import get_object_store

HEADING_PATTERNS = [
    re.compile(r"^#{1,6}\s+(.+)$"),
    re.compile(r"^第[一二三四五六七八九十百\d]+[章节篇]\s*(.+)$"),
    re.compile(r"^\d+(\.\d+)*\s+[^\d].{0,60}$"),
]


@dataclass
class PageText:
    page_no: int
    text: str
    headings: list[str] = field(default_factory=list)


@dataclass
class ParsedDoc:
    pages: list[PageText]
    page_count: int
    source_level: str = "L1"


@dataclass
class ChunkSpec:
    seq: int
    content: str
    page_no: int | None
    section_path: str
    block_type: str
    token_count: int
    parent_seq: int | None = None
    confidence: float = 1.0
    source_level: str = "L1"


def _temp_file(object_key: str) -> Path:
    """流式写临时文件，内存占用恒定为 1MiB。"""
    store = get_object_store()
    suffix = Path(object_key).suffix or ".bin"
    tmp = Path(tempfile.gettempdir()) / f"kb_{abs(hash(object_key))}{suffix}"
    with tmp.open("wb") as out:
        for block in store.stream(object_key):
            out.write(block)
    return tmp


def probe_page_count(object_key: str, mime_type: str) -> int:
    if "pdf" in mime_type:
        from pypdf import PdfReader

        tmp = _temp_file(object_key)
        try:
            return len(PdfReader(str(tmp)).pages)
        finally:
            tmp.unlink(missing_ok=True)
    return 1


def parse_range(object_key: str, mime_type: str, start_page: int = 0, end_page: int | None = None) -> ParsedDoc:
    """按页范围解析。大文件把 [0, N) 切成多个子任务时，每个子任务只处理自己那段。"""
    store = get_object_store()
    if "pdf" in mime_type:
        from pypdf import PdfReader

        tmp = _temp_file(object_key)
        try:
            reader = PdfReader(str(tmp))
            total = len(reader.pages)
            end = min(end_page if end_page is not None else total, total)
            pages = [
                PageText(page_no=i + 1, text=reader.pages[i].extract_text() or "", headings=[])
                for i in range(start_page, end)
            ]
            return ParsedDoc(pages=pages, page_count=total)
        finally:
            tmp.unlink(missing_ok=True)

    # 纯文本 / Markdown / JSON：按固定字符数模拟「页」，保证大文件也能按范围切片
    raw = store.get(object_key)
    text = raw.decode("utf-8", errors="replace")
    page_chars = 3000
    chunks = [text[i:i + page_chars] for i in range(0, len(text), page_chars)] or [""]
    end = min(end_page if end_page is not None else len(chunks), len(chunks))
    return ParsedDoc(
        pages=[PageText(page_no=i + 1, text=chunks[i]) for i in range(start_page, end)],
        page_count=len(chunks),
    )


def _heading(line: str) -> str | None:
    stripped = line.strip()
    for pattern in HEADING_PATTERNS:
        m = pattern.match(stripped)
        if m:
            return stripped
    return None


def split_into_chunks(
    pages: list[PageText],
    max_chars: int | None = None,
    overlap_chars: int | None = None,
) -> list[ChunkSpec]:
    """按标题层级 + 段落边界切，窗口重叠；表格/代码块不切断（block_type 标出）。"""
    max_chars = max_chars or settings.CHUNK_SIZE_CHARS
    overlap = overlap_chars or settings.CHUNK_OVERLAP_CHARS

    units: list[tuple[int | None, str, str, str]] = []  # (page_no, heading_path, text, block_type)
    heading_stack: list[str] = []
    for page in pages:
        for raw_line in page.text.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            h = _heading(line)
            if h:
                depth = len(re.match(r"^#+", h).group(0)) if h.startswith("#") else 1
                heading_stack = heading_stack[: max(depth - 1, 0)]
                heading_stack.append(h.lstrip("# ").strip())
                continue
            block_type = "table" if "|" in line and line.count("|") >= 2 else (
                "code" if line.startswith(("    ", "\t", "```")) else "paragraph")
            units.append((page.page_no, " / ".join(heading_stack), line, block_type))

    chunks: list[ChunkSpec] = []
    buffer: list[str] = []
    buf_len = 0
    current_section = ""
    current_page: int | None = None
    current_block = "paragraph"
    seq = 0

    def flush() -> None:
        nonlocal buffer, buf_len, seq
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        if not content:
            buffer, buf_len = [], 0
            return
        chunks.append(ChunkSpec(
            seq=seq, content=content, page_no=current_page,
            section_path=current_section, block_type=current_block,
            token_count=max(1, len(content) // 2),  # 中文粗估，生产接 tokenizer
            parent_seq=(seq // 4) * 4,  # 每 4 个小块归一个父块，命中后回溯补上下文
            confidence=1.0,
        ))
        seq += 1
        tail = content[-overlap:] if overlap else ""
        buffer = [tail] if tail else []
        buf_len = len(tail)

    for page_no, section, line, block_type in units:
        if buffer and (section != current_section or block_type != current_block or buf_len + len(line) > max_chars):
            flush()
        current_section, current_page, current_block = section, page_no, block_type
        buffer.append(line)
        buf_len += len(line) + 1
    flush()
    return chunks
