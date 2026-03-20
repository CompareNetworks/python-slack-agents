"""Generic file handler registry — routes files to import handlers by MIME type."""

import logging

from slack_agents import InputFile, UserConversationContext
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseFileImporterProvider, ContentBlock, FileImportToolException

logger = logging.getLogger(__name__)


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
    ) -> ContentBlock | None:
        entry = self._mime_map.get(mimetype)
        if not entry:
            return None
        provider, handler_name, max_size = entry
        if len(file_bytes) > max_size:
            return {
                "type": "text",
                "text": (
                    f"[File '{filename}' was not processed: its size ({len(file_bytes)} bytes)"
                    f" exceeds the {max_size} byte limit for {mimetype} files."
                    " Please ask the user to provide a smaller file or extract the relevant"
                    " sections manually.]"
                ),
            }
        input_file = InputFile(file_bytes=file_bytes, mimetype=mimetype, filename=filename)
        if file_id is not None:
            input_file["file_id"] = file_id
        try:
            return await provider.call_tool(
                handler_name, input_file, user_conversation_context, storage
            )
        except FileImportToolException:
            logger.exception("File import failed for %s (%s)", filename, mimetype)
            return None
