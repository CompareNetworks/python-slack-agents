"""Export per-conversation usage data as CSV."""

import csv
import logging
from pathlib import Path

from slack_agents.conversations import ConversationManager

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "conversation_id",
    "date",
    "started_at",
    "last_message_at",
    "agent_name",
    "channel_name",
    "thread_id",
    "user_id",
    "user_handle",
    "message_count",
    "model",
    "version",
    "total_input_tokens",
    "total_output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "peak_single_call_input_tokens",
    "estimated_cost_usd",
    "tool_call_count",
    "file_count",
]


def _build_row(conv: dict, messages: list[dict]) -> dict:
    """Aggregate messages/blocks for a single conversation into a CSV row."""
    first_created = None
    last_created = None
    user_id = ""
    user_handle = ""
    model = ""
    version = ""
    total_input_tokens = 0
    total_output_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0
    peak_single_call_input_tokens = 0
    estimated_cost_usd = 0.0
    tool_call_count = 0
    file_count = 0
    found_first_usage = False

    for msg in messages:
        created_at = msg.get("created_at")
        if created_at is not None:
            created_str = (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            )
            if first_created is None:
                first_created = created_str
            last_created = created_str

        if not user_id and msg.get("user_id"):
            user_id = msg["user_id"]
        if not user_handle and msg.get("user_handle"):
            user_handle = msg["user_handle"]

        for block in msg.get("blocks", []):
            bt = block.get("block_type", "")
            content = block.get("content", {})

            if bt == "usage":
                if not found_first_usage:
                    model = content.get("model", "")
                    version = content.get("version", "")
                    found_first_usage = True
                total_input_tokens += content.get("input_tokens", 0)
                total_output_tokens += content.get("output_tokens", 0)
                cache_creation_input_tokens += content.get("cache_creation_input_tokens", 0)
                cache_read_input_tokens += content.get("cache_read_input_tokens", 0)
                peak_val = content.get("peak_single_call_input_tokens", 0)
                if peak_val > peak_single_call_input_tokens:
                    peak_single_call_input_tokens = peak_val
                cost = content.get("estimated_cost_usd")
                if cost is not None:
                    estimated_cost_usd += cost

            elif bt == "tool_use":
                tool_call_count += 1

            elif bt == "user_file":
                file_count += 1

    # Extract date-only from first_created
    date_only = ""
    if first_created:
        date_only = first_created[:10]

    return {
        "conversation_id": conv["id"],
        "date": date_only,
        "started_at": first_created or "",
        "last_message_at": last_created or "",
        "agent_name": conv.get("agent_name", ""),
        "channel_name": conv.get("channel_name", ""),
        "thread_id": conv.get("thread_id", ""),
        "user_id": user_id,
        "user_handle": user_handle,
        "message_count": len(messages),
        "model": model,
        "version": version,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "peak_single_call_input_tokens": peak_single_call_input_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "tool_call_count": tool_call_count,
        "file_count": file_count,
    }


async def export_usage_csv(
    conversations: ConversationManager,
    agent_name: str,
    output_path: str,
    *,
    handle: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Export per-conversation usage data as CSV. Returns the number of rows written."""
    convs = await conversations.get_conversations_for_export(
        agent_name, handle=handle, date_from=date_from, date_to=date_to
    )
    if not convs:
        return 0

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for conv in convs:
            msgs = await conversations.get_messages_with_blocks(conv["id"])
            row = _build_row(conv, msgs)
            writer.writerow(row)

    logger.info("Exported %d usage rows to %s", len(convs), output_path)
    return len(convs)
