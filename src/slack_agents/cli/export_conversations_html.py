"""Export conversations to HTML."""

import base64
import html
import json
import logging
import os
import re
from pathlib import Path

from slack_agents.conversations import ConversationManager

logger = logging.getLogger(__name__)

HIGHLIGHT_JS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1"

CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
    background: #f8f9fa;
    color: #1a1a1a;
    line-height: 1.6;
}
h1, h2 { color: #1a1a1a; border-bottom: 1px solid #dee2e6; padding-bottom: 8px; }
a { color: #0366d6; text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
table.export-meta { width: auto; }
th, td { border: 1px solid #dee2e6; padding: 8px 12px; text-align: left; }
th { background: #e9ecef; font-weight: 600; }
tr:nth-child(even) { background: #f8f9fa; }
.message { margin: 16px 0; padding: 16px; border-radius: 8px; }
.user-message { background: #e3f2fd; border-left: 4px solid #1976d2; }
.assistant-message { background: #fff; border-left: 4px solid #4caf50; }
.message-header { font-weight: 600; margin-bottom: 8px; color: #555; font-size: 0.9em; }
.text-content { white-space: pre-wrap; word-wrap: break-word; }
.text-content p { margin: 0.5em 0; }
.text-content table { white-space: normal; }
.text-content code {
    background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 0.9em;
}
.text-content pre {
    background: #272822; color: #f8f8f2; padding: 12px; border-radius: 6px;
    overflow-x: auto; white-space: pre;
}
.text-content pre code { background: none; padding: 0; color: inherit; }
details { margin: 8px 0; border: 1px solid #dee2e6; border-radius: 6px; }
details summary {
    padding: 8px 12px; cursor: pointer; background: #f1f3f5; border-radius: 6px;
    font-weight: 500;
}
details summary:hover { background: #e9ecef; }
details .tool-body { padding: 12px; }
.tool-input, .tool-output { margin: 8px 0; }
.tool-input pre, .tool-output pre {
    background: #1e1e1e; color: #d4d4d4; padding: 10px; border-radius: 4px;
    overflow-x: auto; font-size: 0.85em; max-height: 400px; overflow-y: auto;
    white-space: pre;
}
.tool-input pre .hljs-attr, .tool-output pre .hljs-attr { color: #9cdcfe; }
.tool-input pre .hljs-string, .tool-output pre .hljs-string { color: #ce9178; }
.tool-input pre .hljs-number, .tool-output pre .hljs-number { color: #b5cea8; }
.tool-input pre .hljs-literal, .tool-output pre .hljs-literal { color: #569cd6; }
.tool-input pre .hljs-punctuation, .tool-output pre .hljs-punctuation {
    color: #ffd700; font-weight: bold;
}
.tool-error summary { background: #ffebee; color: #c62828; }
.file-link {
    display: inline-block; margin: 4px 0; padding: 4px 8px;
    background: #e9ecef; border-radius: 4px;
}
.file-link .file-size { color: #888; font-size: 0.85em; margin-left: 4px; }
.file-attachment {
    display: flex; align-items: center; gap: 6px;
    margin: 8px 0; padding: 8px 12px; background: #e9ecef; border-radius: 6px;
    font-size: 0.9em; border-left: 3px solid #6c757d;
}
.file-attachment .file-icon { font-size: 1.2em; }
.file-attachment .file-size { color: #888; font-size: 0.85em; }
.usage-footer {
    margin-top: 16px; padding: 12px; background: #f1f3f5; border-radius: 6px;
    font-size: 0.85em; color: #666;
}
img.inline-image {
    max-width: 100%; max-height: 400px; border-radius: 4px; margin: 8px 0;
}
.file-extract {
    margin: 8px 0; border: 1px solid #dee2e6; border-radius: 6px; overflow: hidden;
}
.file-extract-header {
    display: flex; align-items: center; gap: 6px;
    padding: 6px 12px; background: #e9ecef; font-size: 0.85em; font-weight: 500;
}
.file-extract-header .file-size { color: #888; font-size: 0.9em; }
.file-extract-body {
    padding: 10px 12px; background: #f8f9fa; font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.82em; white-space: pre-wrap; word-wrap: break-word; color: #333;
    max-height: 500px; overflow-y: auto; line-height: 1.5;
}
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _format_size(size_bytes: int | None) -> str:
    """Format byte count as human-readable string."""
    if size_bytes is None:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _md_table_to_html(lines: list[str]) -> str:
    """Convert markdown table lines to an HTML table.

    Cell contents are NOT html-escaped here because they have already been
    processed for inline markdown (bold, italic, code, links) by the caller.
    """
    if len(lines) < 2:
        return "\n".join(lines)

    def parse_row(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [cell.strip() for cell in line.split("|")]

    # First line is header
    headers = parse_row(lines[0])
    # Second line is separator — skip it
    # Remaining lines are data rows
    data_rows = [parse_row(line) for line in lines[2:]]

    parts = ["<table>", "<thead><tr>"]
    for h in headers:
        parts.append(f"<th>{h}</th>")
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in data_rows:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{cell}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _is_table_line(line: str) -> bool:
    """Check if a line looks like part of a markdown table."""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_separator_line(line: str) -> bool:
    """Check if a line is a markdown table separator (|---|---|)."""
    stripped = line.strip().strip("|")
    return bool(re.match(r"^[\s\-:|]+$", stripped)) and "-" in stripped


def _md_to_html(text: str) -> str:
    """Convert basic markdown to HTML.

    Handles: code blocks, inline code, bold, italic, links, tables, paragraphs.
    """
    # Fenced code blocks — extract and replace with placeholders
    code_blocks: list[str] = []

    def replace_code_block(m):
        lang = m.group(1) or ""
        code = html.escape(m.group(2))
        lang_attr = f' class="language-{lang}"' if lang else ""
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code{lang_attr}>{code}</code></pre>")
        return f"\x00CODEBLOCK{idx}\x00"

    text = re.sub(r"```(\w*)\n(.*?)```", replace_code_block, text, flags=re.DOTALL)

    # Inline code
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f"<code>{html.escape(m.group(1))}</code>",
        text,
    )

    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)

    # Links
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Process line by line for tables
    lines = text.split("\n")
    result_parts: list[str] = []
    i = 0
    while i < len(lines):
        # Check for markdown table
        if _is_table_line(lines[i]) and i + 1 < len(lines) and _is_separator_line(lines[i + 1]):
            table_lines = [lines[i], lines[i + 1]]
            i += 2
            while i < len(lines) and _is_table_line(lines[i]):
                table_lines.append(lines[i])
                i += 1
            result_parts.append(_md_table_to_html(table_lines))
        else:
            result_parts.append(lines[i])
            i += 1

    text = "\n".join(result_parts)

    # Paragraphs (split on double newlines)
    paragraphs = re.split(r"\n{2,}", text.strip())
    result = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Don't wrap special blocks in <p>
        if p.startswith(("\x00CODEBLOCK", "<table>", "<pre>")):
            result.append(p)
        else:
            # Convert single newlines to <br>
            p = p.replace("\n", "<br>\n")
            result.append(f"<p>{p}</p>")

    text = "\n".join(result)

    # Restore code blocks from placeholders
    for idx, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{idx}\x00", block)

    return text


def _render_user_blocks(blocks: list[dict], files_dir: Path, conv_dir: Path) -> str:
    """Render user_text and user_file blocks to HTML."""
    file_blocks = {b["id"]: b for b in blocks if b["block_type"] == "user_file"}
    # Track which file blocks are already shown via a text block's file-extract
    referenced_file_ids = {b["source_file_id"] for b in blocks if b.get("source_file_id")}

    parts = []
    for block in blocks:
        bt = block["block_type"]
        if bt == "user_file":
            if block["id"] in referenced_file_ids:
                # Write file to disk (side effect) but don't render separately —
                # it will appear as the file-extract header on the text block.
                _render_file_block(block, files_dir, conv_dir)
            else:
                parts.append(_render_file_block(block, files_dir, conv_dir))
        elif bt == "user_text":
            source_file = file_blocks.get(block.get("source_file_id"))
            if source_file:
                parts.append(_render_file_extract(block, source_file, files_dir, conv_dir))
            else:
                parts.append(
                    f'<div class="text-content">{_md_to_html(block["content"]["text"])}</div>'
                )
    return "\n".join(parts)


def _render_file_extract(
    text_block: dict, file_block: dict, files_dir: Path, conv_dir: Path
) -> str:
    """Render extracted-from-file text as a monospace card with a download header."""
    filename = file_block.get("filename", "")
    size_str = _format_size(file_block.get("size_bytes"))
    size_html = f' <span class="file-size">({size_str})</span>' if size_str else ""

    # Build download link if the file was written to disk
    block_id = file_block.get("id", "unknown")
    content = file_block["content"]
    disk_name = f"{block_id}_{content.get('filename', filename)}"
    disk_path = files_dir / disk_name
    if disk_path.exists():
        rel = os.path.relpath(disk_path, conv_dir)
        name_html = f'<a href="{rel}">{html.escape(filename)}</a>'
    else:
        name_html = html.escape(filename)

    header = f'<div class="file-extract-header"><span>&#128196;</span>{name_html}{size_html}</div>'

    text = text_block["content"]["text"]

    body = f'<div class="file-extract-body">{html.escape(text)}</div>'
    return f'<div class="file-extract">{header}{body}</div>'


def _render_file_block(block: dict, files_dir: Path, conv_dir: Path) -> str:
    """Render a user_file or file block, writing data to files/."""
    content = block["content"]
    block_id = block.get("id", "unknown")

    size_bytes = block.get("size_bytes")

    # Handle image-type user_file blocks (Anthropic format)
    if content.get("type") == "image":
        source = content.get("source", {})
        data = source.get("data", "")
        media_type = source.get("media_type", "image/png")
        ext = media_type.split("/")[-1] if "/" in media_type else "png"
        filename = f"{block_id}_image.{ext}"
        filepath = files_dir / filename
        filepath.write_bytes(base64.b64decode(data))
        rel = os.path.relpath(filepath, conv_dir)
        size_str = _format_size(size_bytes)
        size_html = f' <span class="file-size">({size_str})</span>' if size_str else ""
        return (
            f'<div class="file-attachment">'
            f'<span class="file-icon">&#128248;</span>'
            f'<a href="{rel}">{html.escape(filename)}</a>{size_html}'
            f"</div>"
            f'<img class="inline-image" src="{rel}" alt="User upload">'
        )

    # Handle file blocks (tool output files)
    if "filename" in content and "data" in content:
        filename = f"{block_id}_{content['filename']}"
        filepath = files_dir / filename
        filepath.write_bytes(base64.b64decode(content["data"]))
        rel = os.path.relpath(filepath, conv_dir)
        size_str = _format_size(size_bytes)
        size_html = f' <span class="file-size">({size_str})</span>' if size_str else ""
        return (
            f'<a class="file-link" href="{rel}">{html.escape(content["filename"])}{size_html}</a>'
        )

    return ""


def _render_tool_block(block: dict) -> str:
    """Render a tool_use block as a collapsible <details> with JSON highlighting."""
    content = block["content"]
    tool_name = content.get("tool_name", "unknown")
    is_error = content.get("is_error", False)
    error_class = " tool-error" if is_error else ""
    status = "ERROR" if is_error else "OK"

    input_json = json.dumps(content.get("tool_input", {}), indent=2)
    output_text = content.get("tool_output", "")

    # Try to pretty-format output if it looks like JSON
    output_lang = ""
    try:
        parsed = json.loads(output_text)
        output_text = json.dumps(parsed, indent=2)
        output_lang = ' class="language-json"'
    except (json.JSONDecodeError, TypeError):
        pass

    return (
        f'<details class="{error_class.strip()}">'
        f"<summary>{html.escape(tool_name)} [{status}]</summary>"
        f'<div class="tool-body">'
        f'<div class="tool-input"><strong>Input:</strong>'
        f'<pre><code class="language-json">'
        f"{html.escape(input_json)}</code></pre></div>"
        f'<div class="tool-output"><strong>Output:</strong>'
        f"<pre><code{output_lang}>"
        f"{html.escape(output_text)}</code></pre></div>"
        f"</div></details>"
    )


def _render_usage_block(block: dict) -> str:
    """Render a usage block as a Slack-style footer.

    Format: version  |  in=N (X% cached) out=N  |  $cost
    """
    content = block["content"]
    version = content.get("version") or content.get("model", "")
    total_in = (
        content.get("input_tokens", 0)
        + content.get("cache_creation_input_tokens", 0)
        + content.get("cache_read_input_tokens", 0)
    )
    cache_read = content.get("cache_read_input_tokens", 0)
    output_tokens = content.get("output_tokens", 0)

    if cache_read and total_in:
        pct = int(100 * cache_read / total_in)
        in_part = f"in={total_in:,} ({pct}% cached)"
    else:
        in_part = f"in={total_in:,}"

    parts = [version, f"{in_part} out={output_tokens:,}"]
    cost = content.get("estimated_cost_usd")
    if cost is not None:
        parts.append(f"${cost:.4f}")
    return f'<div class="usage-footer">{"  |  ".join(parts)}</div>'


def _render_conversation_html(
    conversation: dict,
    messages: list[dict],
    files_dir: Path,
    conv_dir: Path,
) -> str:
    """Render a full conversation to HTML."""
    conv_id = conversation["id"]
    agent = html.escape(conversation.get("agent_name", "unknown"))
    channel_display = html.escape(
        conversation.get("channel_name") or conversation.get("channel_id", "")
    )

    body_parts = []
    body_parts.append(f"<h1>Conversation #{conv_id}</h1>")
    body_parts.append('<table class="export-meta">')
    body_parts.append(f"<tr><th>Agent</th><td>{agent}</td></tr>")
    body_parts.append(f"<tr><th>Channel</th><td>{channel_display}</td></tr>")
    body_parts.append("</table>")
    body_parts.append('<p><a href="../index.html">&larr; Back to index</a></p>')

    for msg in messages:
        user_handle = msg.get("user_handle") or msg.get("user_id") or ""
        created = msg.get("created_at", "")
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M:%S")

        blocks = msg.get("blocks", [])
        user_blocks = [b for b in blocks if b["block_type"] in ("user_text", "user_file")]
        assistant_blocks = [b for b in blocks if b["block_type"] in ("text", "tool_use", "file")]
        usage_blocks = [b for b in blocks if b["block_type"] == "usage"]

        if user_blocks:
            header = html.escape(user_handle) if user_handle else "User"
            body_parts.append('<div class="message user-message">')
            body_parts.append(
                f'<div class="message-header">{header} &mdash; {html.escape(str(created))}</div>'
            )
            body_parts.append(_render_user_blocks(user_blocks, files_dir, conv_dir))
            body_parts.append("</div>")

        if assistant_blocks:
            body_parts.append('<div class="message assistant-message">')
            body_parts.append('<div class="message-header">Assistant</div>')
            for block in assistant_blocks:
                bt = block["block_type"]
                if bt == "text":
                    body_parts.append(
                        f'<div class="text-content">{_md_to_html(block["content"]["text"])}</div>'
                    )
                elif bt == "tool_use":
                    body_parts.append(_render_tool_block(block))
                elif bt == "file":
                    body_parts.append(_render_file_block(block, files_dir, conv_dir))
            body_parts.append("</div>")

        for usage in usage_blocks:
            body_parts.append(_render_usage_block(usage))

    body = "\n".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conversation #{conv_id} - {agent}</title>
<style>{CSS}</style>
<link rel="stylesheet" href="{HIGHLIGHT_JS_CDN}/styles/vs2015.min.css">
<script src="{HIGHLIGHT_JS_CDN}/highlight.min.js"></script>
<script src="{HIGHLIGHT_JS_CDN}/languages/json.min.js"></script>
<script>hljs.highlightAll();</script>
</head>
<body>
{body}
</body>
</html>"""


def _render_index_html(
    conversations: list[dict],
    messages_by_conv: dict,
    user_handle: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """Render the index page listing all conversations grouped by channel."""
    # Group by channel (use channel_name if available, else channel_id)
    by_channel: dict[str, list[dict]] = {}
    for conv in conversations:
        ch = conv.get("channel_name") or conv.get("channel_id", "unknown")
        by_channel.setdefault(ch, []).append(conv)

    body_parts = []
    body_parts.append("<h1>Conversation Export</h1>")
    if user_handle or (date_from and date_to):
        body_parts.append('<table class="export-meta">')
        if user_handle:
            body_parts.append(f"<tr><th>Handle</th><td>{html.escape(user_handle)}</td></tr>")
        if date_from:
            body_parts.append(f"<tr><th>Date from</th><td>{html.escape(date_from)}</td></tr>")
        if date_to:
            body_parts.append(f"<tr><th>Date to</th><td>{html.escape(date_to)}</td></tr>")
        body_parts.append("</table>")

    for channel, convs in sorted(by_channel.items()):
        body_parts.append(f"<h2>Channel: {html.escape(channel)}</h2>")
        body_parts.append("<table>")
        body_parts.append("<tr><th>Agent</th><th>Date/Time</th><th>First Message</th></tr>")
        for conv in convs:
            conv_id = conv["id"]
            agent = html.escape(conv.get("agent_name", ""))
            msgs = messages_by_conv.get(conv_id, [])
            # Find first user text as snippet + timestamp
            snippet = ""
            time_str = ""
            for msg in msgs:
                if msg.get("created_at"):
                    ts = msg["created_at"]
                    time_str = (
                        ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
                    )
                for block in msg.get("blocks", []):
                    if block["block_type"] == "user_text" and not snippet:
                        snippet = block["content"].get("text", "")[:100]
                if snippet:
                    break

            link = f"conversations/{conv_id}.html"
            body_parts.append(
                f"<tr>"
                f"<td>{agent}</td>"
                f"<td>{html.escape(time_str)}</td>"
                f'<td><a href="{link}">{html.escape(snippet or "(no text)")}</a></td>'
                f"</tr>"
            )
        body_parts.append("</table>")

    body = "\n".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Conversation Export</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Export orchestration
# ---------------------------------------------------------------------------


async def export_conversations_html(
    conversations: ConversationManager,
    agent_name: str,
    output_dir: str,
    *,
    handle: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Export conversations to HTML files. Returns the number exported."""
    output = Path(output_dir)
    convs = await conversations.get_conversations_for_export(
        agent_name, handle=handle, date_from=date_from, date_to=date_to
    )
    if not convs:
        return 0

    conv_dir = output / "conversations"
    files_dir = output / "files"
    conv_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    messages_by_conv = {}
    for conv in convs:
        conv_id = conv["id"]
        msgs = await conversations.get_messages_with_blocks(conv_id)
        messages_by_conv[conv_id] = msgs

        conv_html = _render_conversation_html(conv, msgs, files_dir, conv_dir)
        (conv_dir / f"{conv_id}.html").write_text(conv_html, encoding="utf-8")
        logger.info("Exported conversation %s (%d messages)", conv_id, len(msgs))

    index_html = _render_index_html(
        convs,
        messages_by_conv,
        user_handle=handle or "",
        date_from=date_from or "",
        date_to=date_to or "",
    )
    (output / "index.html").write_text(index_html, encoding="utf-8")

    logger.info("Exported %d conversations to %s", len(convs), output_dir)
    return len(convs)
