"""Block Kit builders for collapsible tool call messages."""

import json

_MAX_SECTION_TEXT = 3000

ICON_CALLING = "\u25b8"  # ▸
ICON_SUCCESS = "\u2713"  # ✓
ICON_ERROR = "\u2717"  # ✗


_TRUNCATION_SUFFIX = "\n... (truncated)"


def _truncate(text: str, *, max_len: int = _MAX_SECTION_TEXT) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def build_calling_blocks(tool_name: str) -> list[dict]:
    """Calling state: section block with processing indicator."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{ICON_CALLING} tool: _{tool_name}_ (processing...)",
            },
        }
    ]


def _tool_value(tool_id: str, tool_name: str) -> str:
    return json.dumps({"tool_id": tool_id, "tool_name": tool_name})


def _tool_header(icon: str, tool_name: str, action_id: str, label: str, value: str):
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"{icon} tool: _{tool_name}_"},
        "accessory": {
            "type": "overflow",
            "action_id": action_id,
            "options": [
                {
                    "text": {"type": "plain_text", "text": label},
                    "value": value,
                }
            ],
        },
    }


def build_collapsed_blocks(tool_name: str, is_error: bool, tool_id: str) -> list[dict]:
    icon = ICON_ERROR if is_error else ICON_SUCCESS
    return [
        _tool_header(
            icon,
            tool_name,
            f"tool_expand_{tool_id}",
            "Show Details",
            _tool_value(tool_id, tool_name),
        )
    ]


def _wrap_code_block(label: str, content: str) -> dict:
    """Build a section block with a labelled code fence, respecting Slack's 3000-char limit."""
    # The wrapper adds: "*Label:*\n```\n" + content + "\n```"
    # Reserve space for the wrapper so the total stays under the limit.
    wrapper_len = len(f"*{label}:*\n```\n\n```")
    truncated = _truncate(content, max_len=_MAX_SECTION_TEXT - wrapper_len)
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{label}:*\n```\n{truncated}\n```",
        },
    }


def build_expanded_blocks(
    tool_name: str, is_error: bool, tool_id: str, input_json: str, output_json: str
) -> list[dict]:
    icon = ICON_ERROR if is_error else ICON_SUCCESS
    return [
        _tool_header(
            icon,
            tool_name,
            f"tool_collapse_{tool_id}",
            "Hide Details",
            _tool_value(tool_id, tool_name),
        ),
        _wrap_code_block("Input", input_json),
        _wrap_code_block("Output", output_json),
    ]
