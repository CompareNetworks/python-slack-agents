"""Turn A2A file artifacts into LLM-facing text.

A2A artifacts are always uploaded to the Slack thread, so the user has already
seen them. We surface their *content* to the LLM as context, tagged so the model
references the file rather than reproducing it. Extraction reuses the agent's
configured file-import handlers (FileHandlerRegistry); unparseable or oversized
files become a metadata placeholder.
"""

import logging

from slack_agents.files import describe_file

logger = logging.getLogger(__name__)

_SHOWN_TAG = (
    "NOTE: the file '{filename}' has already been delivered to the user in this Slack "
    "thread and is visible to them. Do NOT repeat, paste, or reformat its contents "
    "(for example as a table or list) — that is redundant. You may briefly synthesize it "
    "or draw attention to one specific point only if it genuinely helps. The content "
    "below is included only so you can answer follow-up questions about it:"
)


async def files_to_llm_text(files, registry, ucc, storage, *, already_shown: bool = True) -> str:
    """Render A2A artifacts as text for the LLM's context.

    `files` are {data: bytes, filename, mimeType} dicts. `registry` is the agent's
    FileHandlerRegistry (or None — then every file becomes a descriptor). When
    `already_shown`, each section is prefixed with the "already delivered" tag.
    Returns "" when there are no files.
    """
    sections: list[str] = []
    for f in files or []:
        filename = f.get("filename") or "file"
        mimetype = f.get("mimeType") or "application/octet-stream"
        data = f.get("data") or b""
        if registry is not None:
            block = await registry.process_file(data, mimetype, filename, ucc, storage)
            if block.get("type") == "text":
                body = block["text"]
            else:
                logger.debug(
                    "artifact %s (%s) returned a non-text block; using a descriptor",
                    filename,
                    mimetype,
                )
                body = describe_file(filename, mimetype, len(data), "binary/image content")["text"]
        else:
            body = describe_file(filename, mimetype, len(data), "no import capability configured")[
                "text"
            ]
        header = _SHOWN_TAG.format(filename=filename) + "\n" if already_shown else ""
        sections.append(f"{header}--- {filename} ({mimetype}) ---\n{body}")
    return "\n\n".join(sections)
