"""Markdown table detection and conversion to Slack TableBlock."""

import re


def is_table_line(line: str) -> bool:
    """Return True if the line looks like a markdown table row (starts with |)."""
    return line.strip().startswith("|")


def is_separator_line(line: str) -> bool:
    """Return True for markdown table separator rows like | --- | :---: |."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = stripped.strip("|").split("|")
    return all(re.fullmatch(r"[\s:\-]+", cell) for cell in cells)


def _strip_inline_markdown(text: str) -> str:
    """Remove basic inline markdown formatting from text."""
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    return text


def table_lines_to_blocks(lines: list[str]) -> dict:
    """Convert markdown table lines to a Slack TableBlock dict."""
    data_rows: list[list[str]] = []
    for line in lines:
        if is_separator_line(line):
            continue
        cells = line.strip().strip("|").split("|")
        cells = [_strip_inline_markdown(c.strip()) for c in cells]
        data_rows.append(cells)

    if not data_rows:
        return {"type": "table", "rows": []}

    max_cols = max(len(row) for row in data_rows)

    rows = []
    for row_cells in data_rows:
        while len(row_cells) < max_cols:
            row_cells.append("")
        row = [{"type": "raw_text", "text": cell or " "} for cell in row_cells]
        rows.append(row)

    return {"type": "table", "rows": rows}
