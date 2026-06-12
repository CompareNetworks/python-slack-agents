"""Generic file handler registry — routes files to import handlers by MIME type."""

import logging

from slack_agents import InputFile, UserConversationContext
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseFileImporterProvider, ContentBlock, FileImportToolException

logger = logging.getLogger(__name__)


def describe_file(filename: str, mimetype: str, size: int, reason: str) -> ContentBlock:
    """A text placeholder for a file we received but could not parse.

    Gives the LLM the file's identity + why it couldn't be read, so it can
    acknowledge the file without inventing its contents.
    """
    return {
        "type": "text",
        "text": (
            f"[File '{filename}' ({mimetype or 'unknown type'}, {size} bytes) was provided "
            f"but could not be read: {reason}. You can acknowledge it to the user but cannot "
            f"parse its contents.]"
        ),
    }


class FileHandlerRegistry:
    """Routes files to the right input handler by MIME type."""

    def __init__(self, providers: list[BaseFileImporterProvider]):
        # mime -> (provider, handler_name, max_size)
        self._mime_map: dict[str, tuple[BaseFileImporterProvider, str, int]] = {}
        for provider in providers:
            for tool in provider.tools:
                for mime in tool["mimes"]:
                    self._mime_map[mime] = (provider, tool["name"], tool["max_size"])

    @property
    def supported_mimes(self) -> set[str]:
        return set(self._mime_map.keys())

    def can_handle(self, mimetype: str) -> bool:
        return mimetype in self._mime_map

    async def process_file(
        self,
        file_bytes: bytes,
        mimetype: str,
        filename: str,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
        file_id: str | None = None,
    ) -> ContentBlock:
        entry = self._mime_map.get(mimetype)
        if entry is None:
            reason = "no import capability configured" if not self._mime_map else "unsupported type"
            return describe_file(filename, mimetype, len(file_bytes), reason)
        provider, handler_name, max_size = entry
        if len(file_bytes) > max_size:
            return describe_file(
                filename,
                mimetype,
                len(file_bytes),
                f"exceeds the {max_size}-byte limit for {mimetype}",
            )
        input_file = InputFile(file_bytes=file_bytes, mimetype=mimetype, filename=filename)
        if file_id is not None:
            input_file["file_id"] = file_id
        try:
            return await provider.call_tool(
                handler_name, input_file, user_conversation_context, storage
            )
        except FileImportToolException:
            logger.exception("File import failed for %s (%s)", filename, mimetype)
            return describe_file(filename, mimetype, len(file_bytes), "extraction failed")
