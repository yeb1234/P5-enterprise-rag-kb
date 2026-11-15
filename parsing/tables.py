"""表格专项：结构语义还原与稳定性校验。

为什么单独一个模块：RAG 里表格最容易「读起来像对的、其实全错」——
把二维关系压成一维字符串后，行列对应关系就丢了，LLM 只能靠猜。
本模块把表表示为 cells(row, col, value, type, unit, rowspan, colspan)，
再附带一组可量化的稳定性检查；下游 chunk 用 block_type=table 单独成块，
回答时引用「第 r 行第 c 列」而不是复述整张表。

边界：这是**规则级自研实现**，处理 Markdown 管道表 / CSV / TSV 这类
「已有结构线索」的输入；不等于工业级表格识别（无边框扫描表、合并单元格
需要版面模型，属于 P1/P2 层级）。
"""
from __future__ import annotations

import csv
import re
from typing import Any

_NUM = re.compile(r"^\s*[-+]?\d[\d,]*\.?\d*\s*$")
_PERCENT = re.compile(r"^\s*[-+]?\d[\d,]*\.?\d*\s*%$")
_DATE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*$")
_UNIT = re.compile(r"(元|万元|亿元|%|％|kg|g|mg|km|m|cm|mm|台|个|人|次|件|小时|天|秒|ms|MB|GB|TB)\s*$")


def infer_cell_type(value: str) -> tuple[str, str | None]:
    """返回 (类型, 单位)。单位单独抽出来，避免把 '12.5%' 当纯文本。"""
    s = (value or "").strip()
    if not s:
        return "empty", None
    if _PERCENT.match(s):
        return "percent", "%"
    if _DATE.match(s):
        return "date", None
    m = _UNIT.search(s)
    unit = m.group(1) if m else None
    bare = s[:m.start()].strip() if m else s
    if _NUM.match(bare.replace("，", ",")):
        return "number", unit
    return "text", None


def _split_row(line: str, delim: str | None) -> list[str]:
    if delim == "|":
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]
    if delim == ",":
        return [c.strip() for c in next(csv.reader([line]))]
    return [c.strip() for c in line.split(delim or "\t")]


def _is_separator(line: str) -> bool:
    """Markdown 表头分隔行：|---|---|"""
    return bool(re.fullmatch(r"[\s|:\-]+", line)) and "-" in line and "|" in line


def parse_table_lines(lines: list[str]) -> dict[str, Any] | None:
    """把连续表格行解析成结构化表。返回 None 表示不是表格。"""
    if not lines:
        return None
    first = lines[0].strip()
    if "|" in first and first.count("|") >= 2:
        delim = "|"
    elif "," in first and first.count(",") >= 1:
        delim = ","
    elif "\t" in first and first.count("\t") >= 1:
        delim = "\t"
    else:
        return None

    body_lines = [l for l in lines if not _is_separator(l)]
    rows = [_split_row(l, delim) for l in body_lines]
    # 保留**原始**行宽：cells 会把短行补成空单元格，若不单独记录，
    # 「列数守恒」检查将永远通过，参差表就检测不出来了。
    row_widths = [len(r) for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    header = rows[0] if len(rows) > 1 or delim == "|" else []

    cells: list[dict] = []
    for r, row in enumerate(rows):
        for c in range(n_cols):
            value = row[c] if c < len(row) else ""
            typ, unit = infer_cell_type(value)
            cells.append({
                "row": r, "col": c, "value": value, "type": typ, "unit": unit,
                "rowspan": 1, "colspan": 1,
                "is_header": bool(header) and r == 0 and delim == "|",
            })
    table = {
        "kind": "markdown" if delim == "|" else ("csv" if delim == "," else "tsv"),
        "n_rows": len(rows), "n_cols": n_cols,
        "header": header, "cells": cells, "row_widths": row_widths,
        "caption": "", "source_delim": delim,
    }
    checks, confidence = validate_table(table)
    table["checks"] = checks
    table["confidence"] = confidence
    return table


def validate_table(table: dict[str, Any]) -> tuple[list[dict], float]:
    """稳定性检查。每项给「检查什么 / 失败信号 / 处置」，前端可直接展示。"""
    cells = table["cells"]
    n_rows, n_cols = table["n_rows"], table["n_cols"]
    checks: list[dict] = []

    widths = table.get("row_widths") or [n_cols] * n_rows
    ragged = [i + 1 for i, w in enumerate(widths) if w != n_cols]
    checks.append({"name": "列数守恒", "passed": not ragged,
                   "detail": f"{n_rows} 行 × {n_cols} 列" if not ragged
                   else f"参差行 {ragged}（原始列宽 {widths}）"})

    missing = sum(1 for c in cells if c["type"] == "empty")
    checks.append({"name": "单元格非空率", "passed": missing <= len(cells) * 0.3,
                   "detail": f"空格 {missing}/{len(cells)}"})

    # 数值列类型一致性：每列里数值型占比应稳定，混合数值/文本视为可疑
    mixed = []
    for col in range(n_cols):
        types = [c["type"] for c in cells if c["col"] == col and not c["is_header"]]
        numeric = sum(1 for t in types if t in ("number", "percent"))
        if types and 0 < numeric < len(types):
            mixed.append(col)
    checks.append({"name": "列类型一致性", "passed": not mixed,
                   "detail": "全部一致" if not mixed else f"混合列 {mixed}"})

    # 表头重复：首行出现重复列名会让「按列名引用」产生歧义
    header = [c["value"] for c in cells if c["is_header"]]
    dup = len(header) != len(set(header))
    checks.append({"name": "表头无重复", "passed": not dup,
                   "detail": "、".join(header[:6]) if header else "无表头行"})

    checks.append({"name": "行数下界", "passed": n_rows >= 2,
                   "detail": f"{n_rows} 行"})

    passed = sum(1 for c in checks if c["passed"])
    confidence = round(0.4 + 0.6 * passed / max(1, len(checks)), 4)
    return checks, confidence


def table_to_context(table: dict[str, Any], max_rows: int = 40) -> str:
    """给 LLM 的线性化上下文：保留表头 + 行列坐标，不丢二维关系。"""
    n_cols = table["n_cols"]
    header = table["header"] or [f"列{c + 1}" for c in range(n_cols)]
    lines = ["[表格 · 结构语义]", "表头: " + " | ".join(f"[列{c + 1}]{h}" for c, h in enumerate(header))]
    grid: dict[int, list[str]] = {}
    for c in table["cells"]:
        grid.setdefault(c["row"], [""] * n_cols)
        if c["col"] < n_cols:
            grid[c["row"]][c["col"]] = c["value"]
    for r in sorted(grid)[:max_rows]:
        if table["header"] and r == 0:
            continue
        parts = [f"[行{r + 1}·列{c + 1}]{v}" for c, v in enumerate(grid[r]) if v]
        lines.append(" ".join(parts))
    if table.get("checks"):
        failed = [c["name"] for c in table["checks"] if not c["passed"]]
        if failed:
            lines.append("结构告警: " + "、".join(failed) + "（该表可能需要升级到版面解析）")
    return "\n".join(lines)


def extract_tables(text: str) -> tuple[list[dict[str, Any]], str]:
    """扫描文本，把连续表格行抽出，剩余部分作为普通段落返回。"""
    lines = text.splitlines()
    tables: list[dict] = []
    plain: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        t = parse_table_lines(list(buf))
        if t:
            tables.append(t)
        else:
            plain.extend(buf)
        buf.clear()

    for line in lines:
        candidate = ("|" in line and line.count("|") >= 2) or (
            "\t" in line and line.count("\t") >= 1)
        if candidate:
            buf.append(line)
        else:
            flush()
            plain.append(line)
    flush()
    return tables, "\n".join(plain)
