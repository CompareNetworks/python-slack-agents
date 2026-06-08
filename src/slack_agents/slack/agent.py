"""Slack agent using Bolt AsyncApp with Socket Mode."""

import asyncio
import base64
import json
import logging
import os
import re

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from slack_agents import FrameworkContext, PendingFlowsRegistry, UserConversationContext
from slack_agents.access.base import AccessDenied
from slack_agents.agent_loop import run_agent_loop_streaming
from slack_agents.config import AgentConfig, load_plugin
from slack_agents.conversations import ConversationManager
from slack_agents.files import FileHandlerRegistry
from slack_agents.llm import CHARS_PER_TOKEN
from slack_agents.llm.base import Message, StreamEvent
from slack_agents.observability import observe, set_span_attrs
from slack_agents.slack.actions import register_tool_toggle_handlers
from slack_agents.slack.files import process_files_for_message, upload_file
from slack_agents.slack.streaming_formatter import StreamingFormatter
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import BaseFileImporterProvider, BaseToolProvider

_OAUTH_PROVIDER_TYPE = "slack_agents.tools.mcp_http_oauth"

logger = logging.getLogger(__name__)


# LLM provider errors (Anthropic-style — keyed by `error.type` in the response
# body) that we can classify into transient/configuration/unknown buckets and
# surface as specific user messages instead of a generic "something went wrong."
# Format: error_type → (user-facing message, log level)
_LLM_ERROR_CLASSIFICATIONS: dict[str, tuple[str, int]] = {
    "overloaded_error": (
        "The LLM provider is currently overloaded. Please try your request again in a moment.",
        logging.WARNING,
    ),
    "rate_limit_error": (
        "We've hit the LLM provider's rate limit. Please wait a moment and try again.",
        logging.WARNING,
    ),
    "api_error": (
        "The LLM provider returned an internal error. Please try again in a moment.",
        logging.WARNING,
    ),
    "timeout_error": (
        "The LLM provider took too long to respond. Please try again in a moment.",
        logging.WARNING,
    ),
    "authentication_error": (
        "The agent's LLM credentials were rejected. This is a configuration "
        "issue — please contact whoever administers this agent.",
        logging.ERROR,
    ),
    "permission_error": (
        "The LLM provider denied permission for this request. This is a "
        "configuration issue — please contact whoever administers this agent.",
        logging.ERROR,
    ),
    "invalid_request_error": (
        "The agent sent an invalid request to the LLM provider. "
        "This is a bug — please contact support.",
        logging.ERROR,
    ),
    "not_found_error": (
        "The agent's configured LLM model wasn't found. "
        "This is a configuration issue — please contact whoever administers this agent.",
        logging.ERROR,
    ),
}


def _classify_llm_error(exc: BaseException) -> tuple[str, int]:
    """Look at an exception from the LLM call and return (user_message, log_level).

    Inspects `exc.body['error']['type']` (the shape Anthropic-compatible
    providers use) — duck-typed so we don't need to import provider-specific
    exception classes. Falls back to a generic message + ERROR level when the
    type isn't recognized.
    """
    body = getattr(exc, "body", None)
    err_type: str | None = None
    if isinstance(body, dict):
        err_obj = body.get("error")
        if isinstance(err_obj, dict):
            err_type = err_obj.get("type")
    if err_type is None:
        err_type = getattr(exc, "type", None)
    if err_type and err_type in _LLM_ERROR_CLASSIFICATIONS:
        return _LLM_ERROR_CLASSIFICATIONS[err_type]
    return (
        "Sorry, I encountered an error processing your request.",
        logging.ERROR,
    )


class SlackAgent:
    def __init__(self, config: AgentConfig, system_prompt: str, agent_name: str):
        self.config = config
        self.system_prompt = system_prompt
        self.agent_name = agent_name
        self.llm = self._init_llm()
        self._access_provider = self._init_access()
        self.app = AsyncApp(token=config.slack.bot_token)
        # The bot's Slack Web API client. Bolt also injects this as `client` into
        # event handlers; we keep a direct reference so non-event code paths
        # (e.g. async A2A delivery) can post without a Bolt-provided client/say.
        self._slack_client = self.app.client
        self.bot_user_id: str | None = None
        self.team_id: str | None = None
        self._tool_providers: list = []
        self._input_file_providers: list[BaseFileImporterProvider] = []
        self._file_registry: FileHandlerRegistry = FileHandlerRegistry([])
        self.conversations: ConversationManager  # set in start()
        self._framework_ctx: FrameworkContext | None = None  # set in start()
        self._oauth_runner = None  # set in start() if mcp_http_oauth configured
        self._user_name_cache: dict[str, str] = {}
        self._channel_name_cache: dict[str, str] = {}
        self._register_handlers()

    def _init_llm(self):
        """Initialize the LLM provider via the plugin system."""
        llm_config = dict(self.config.llm)
        type_path = llm_config.pop("type")
        return load_plugin(type_path, **llm_config)

    def _init_access(self):
        """Initialize the access-control provider via the plugin system."""
        access_config = dict(self.config.access)
        type_path = access_config.pop("type")
        return load_plugin(type_path, **access_config)

    def _register_handlers(self) -> None:
        register_tool_toggle_handlers(self.app, lambda: self.conversations)

        @self.app.action("oauth_authenticate")
        async def _ack_oauth_authenticate(ack) -> None:
            # No-op handler. The button is a URL link — the click opens the
            # browser. We just ack() so Slack doesn't log "unhandled request".
            await ack()

        @self.app.event("assistant_thread_started")
        async def handle_assistant_thread_started(body, logger) -> None:
            logger.debug("assistant_thread_started event (ignored)")

        @self.app.event("assistant_thread_context_changed")
        async def handle_assistant_thread_context_changed(body, logger) -> None:
            logger.debug("assistant_thread_context_changed event (ignored)")

        @self.app.event("app_mention")
        async def handle_mention(event: dict, say, client) -> None:
            logger.debug(
                "app_mention event: channel=%s thread_ts=%s text=%r",
                event.get("channel"),
                event.get("thread_ts"),
                event.get("text", "")[:80],
            )
            await self._ensure_bot_user_id(client)
            text = self._strip_mention(event.get("text", ""))
            if not text.strip():
                return
            thread_ts = event.get("thread_ts") or event.get("ts")
            channel = event["channel"]
            files = event.get("files", [])
            user_id = event.get("user")
            await self._handle_message(text, channel, thread_ts, files, say, client, user_id)

        @self.app.event("message")
        async def handle_message(event: dict, say, client) -> None:
            logger.debug(
                "message event: channel_type=%s channel=%s thread_ts=%s subtype=%s "
                "bot_id=%s user=%s text=%r",
                event.get("channel_type"),
                event.get("channel"),
                event.get("thread_ts"),
                event.get("subtype"),
                event.get("bot_id"),
                event.get("user"),
                event.get("text", "")[:80],
            )

            subtype = event.get("subtype")
            if event.get("bot_id") or (subtype and subtype != "file_share"):
                logger.debug("Skipping: bot_id=%s subtype=%s", event.get("bot_id"), subtype)
                return

            await self._ensure_bot_user_id(client)
            channel_type = event.get("channel_type")
            thread_ts = event.get("thread_ts")

            if channel_type == "im":
                text = event.get("text", "")
                if not text.strip() and not event.get("files"):
                    return
                ts = thread_ts or event.get("ts")
                channel = event["channel"]
                files = event.get("files", [])
                user_id = event.get("user")
                await self._handle_message(text, channel, ts, files, say, client, user_id)
                return

            if thread_ts:
                in_thread = await self._agent_in_thread(event["channel"], thread_ts)
                logger.debug(
                    "Thread reply check: channel=%s thread_ts=%s agent_in_thread=%s",
                    event["channel"],
                    thread_ts,
                    in_thread,
                )
                if in_thread:
                    text = self._strip_mention(event.get("text", ""))
                    if not text.strip() and not event.get("files"):
                        return
                    channel = event["channel"]
                    files = event.get("files", [])
                    user_id = event.get("user")
                    await self._handle_message(
                        text, channel, thread_ts, files, say, client, user_id
                    )
            else:
                logger.debug(
                    "Ignoring non-DM, non-thread message in channel %s",
                    event.get("channel"),
                )

    async def _agent_in_thread(self, channel: str, thread_ts: str) -> bool:
        """Check if the agent has already replied in this thread."""
        return await self.conversations.has_conversation(self.agent_name, channel, thread_ts)

    async def _ensure_bot_user_id(self, client) -> None:
        if self.bot_user_id is None:
            auth = await client.auth_test()
            self.bot_user_id = auth["user_id"]
            self.team_id = auth["team_id"]

    def _strip_mention(self, text: str) -> str:
        if self.bot_user_id:
            text = re.sub(rf"<@{self.bot_user_id}>", "", text)
        return text.strip()

    async def _resolve_user_name(self, client, user_id: str) -> str:
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        try:
            resp = await client.users_info(user=user_id)
            user = resp["user"]
            profile = user.get("profile", {})
            name = (
                (profile.get("display_name") or "").strip()
                or (profile.get("real_name") or "").strip()
                or (user.get("name") or "").strip()
                or user_id
            )
            self._user_name_cache[user_id] = name
            return name
        except Exception:
            logger.warning("Could not resolve user name for %s", user_id, exc_info=True)
            return user_id

    async def _resolve_channel_name(self, client, channel_id: str) -> str:
        if channel_id in self._channel_name_cache:
            return self._channel_name_cache[channel_id]
        try:
            resp = await client.conversations_info(channel=channel_id)
            channel = resp["channel"]
            if channel.get("is_im"):
                dm_user = channel.get("user")
                name = await self._resolve_user_name(client, dm_user) if dm_user else channel_id
                name = f"dm-{name}"
            else:
                name = channel.get("name") or channel_id
            self._channel_name_cache[channel_id] = name
            return name
        except Exception:
            logger.warning("Could not resolve channel name for %s", channel_id, exc_info=True)
            return channel_id

    @observe(name="handle_message", capture_input=False, capture_output=False)
    async def _handle_message(
        self,
        text: str,
        channel: str,
        thread_ts: str,
        files: list[dict],
        say,
        client,
        user_id: str,
    ) -> None:
        """Process a message and respond via streaming agent loop."""
        display_name = await self._resolve_user_name(client, user_id)
        channel_name = await self._resolve_channel_name(client, channel)
        user_conversation_context = UserConversationContext(
            user_id=user_id,
            user_name=display_name,
            user_handle=display_name,
            channel_id=channel,
            channel_name=channel_name,
            thread_id=thread_ts,
        )

        try:
            await self._access_provider.check_access(context=user_conversation_context)
        except AccessDenied as exc:
            try:
                await client.chat_postEphemeral(
                    channel=channel,
                    thread_ts=thread_ts,
                    user=user_id,
                    text=str(exc),
                )
            except Exception:
                logger.debug("Failed to post access denial", exc_info=True)
            return

        try:
            await client.assistant_threads_setStatus(
                channel_id=channel, thread_ts=thread_ts, status="is thinking..."
            )
        except Exception:
            pass

        await self._run_turn(channel, thread_ts, user_id, text, files)

    async def _run_turn(
        self,
        channel: str,
        thread_ts: str,
        user_id: str,
        text: str,
        files: list | None = None,
    ) -> None:
        """Run one agent turn from an arbitrary trigger (Slack event OR async delivery).

        Owns the history-load → run_agent_loop_streaming → stream/format → persist
        body. Event-specific concerns (access check, "is thinking" status,
        file extraction) live in the caller. The `say`/`client` used here are
        reconstructed from the Slack client so this works without a Bolt-provided
        `say` (e.g. for out-of-band async A2A delivery).
        """
        files = files or []
        client = self._slack_client

        async def say(**kw):
            kw.setdefault("channel", channel)
            return await client.chat_postMessage(**kw)

        display_name = await self._resolve_user_name(client, user_id)
        channel_name = await self._resolve_channel_name(client, channel)
        user_conversation_context = UserConversationContext(
            user_id=user_id,
            user_name=display_name,
            user_handle=display_name,
            channel_id=channel,
            channel_name=channel_name,
            thread_id=thread_ts,
        )

        try:
            storage = self.conversations._storage
            set_span_attrs(
                trace_name=self.agent_name,
                user_id=display_name,
                session_id=f"{channel_name}.{thread_ts}",
                version=self.config.version,
                input=text,
            )

            store = self.conversations
            conversation_id = await store.get_or_create_conversation(
                self.agent_name, channel, thread_ts, channel_name=channel_name
            )
            messages = await store.get_messages(conversation_id)

            # Check for unprocessable files before starting the agent loop
            if files:
                unhandled = []
                for f in files:
                    mime = f.get("mimetype", "")
                    if mime and not self._file_registry.can_handle(mime):
                        unhandled.append(mime)
                if unhandled:
                    supported = sorted(self._file_registry.supported_mimes)
                    unsupported_str = ", ".join(sorted(set(unhandled)))
                    if supported:
                        supported_str = ", ".join(supported)
                        msg = (
                            f"I can't process files of type {unsupported_str}."
                            f" Supported types: {supported_str}"
                        )
                    else:
                        msg = (
                            "I can't process file attachments"
                            " (no file import handlers are configured)."
                        )
                    await say(text=msg, thread_ts=thread_ts)
                    return

            user_content, file_meta = await self._build_user_content(
                text, files, user_conversation_context, storage
            )
            messages.append(Message(role="user", content=user_content))

            # Make this turn's raw uploads available to tools (e.g. a2a.agent forwards
            # them as attachments). Non-image files carry raw_bytes; cleared in `finally`.
            if self._framework_ctx is not None:
                uploads = [
                    {
                        "data": m["raw_bytes"],
                        "filename": m.get("filename", "file"),
                        "mimeType": m.get("mimetype", "application/octet-stream"),
                    }
                    for m in file_meta
                    if m and m.get("raw_bytes")
                ]
                if uploads:
                    self._framework_ctx.pending_uploads[thread_ts] = uploads

            message_id = await store.create_message(
                conversation_id,
                user_id=user_conversation_context["user_id"],
                user_name=user_conversation_context["user_name"],
                user_handle=user_conversation_context["user_handle"],
            )
            if user_content:
                if isinstance(user_content, str):
                    await store.append_text_block(message_id, user_content, is_user=True)
                else:
                    for block, meta in zip(user_content, file_meta):
                        if block.get("type") == "image":
                            await store.append_file_block(
                                message_id,
                                block,
                                is_user=True,
                                filename=meta["filename"],
                                mimetype=meta["mimetype"],
                                size_bytes=meta["size_bytes"],
                            )
                        elif meta:
                            raw = meta.get("raw_bytes")
                            if raw:
                                raw_b64 = base64.b64encode(raw).decode()
                                file_block_id = await store.append_file_block(
                                    message_id,
                                    {
                                        "data": raw_b64,
                                        "filename": meta["filename"],
                                        "mimeType": meta["mimetype"],
                                    },
                                    is_user=True,
                                    filename=meta["filename"],
                                    mimetype=meta["mimetype"],
                                    size_bytes=meta["size_bytes"],
                                )
                            else:
                                file_block_id = None
                            await store.append_text_block(
                                message_id,
                                block["text"],
                                is_user=True,
                                source_file_id=file_block_id,
                            )
                        else:
                            await store.append_text_block(message_id, block["text"], is_user=True)

            # Eager OAuth setup: for any tool provider that needs per-user auth,
            # make sure this user has a token and the provider has discovered tools
            # before we let the LLM see the conversation. ensure_authenticated() is
            # cheap when already done (one DB lookup); when not, it posts an auth
            # ephemeral and blocks for up to `auth_timeout` on the user's click.
            for _provider in self._tool_providers:
                _ensure = getattr(_provider, "ensure_authenticated", None)
                if _ensure is None:
                    continue
                try:
                    await _ensure(user_conversation_context)
                except Exception as _auth_err:
                    # The provider already produced a sanitized message in the
                    # exception text (with a support reference). Surface it as-is.
                    msg = str(_auth_err) or (
                        "Something went wrong setting up authentication. Please contact support."
                    )
                    await say(text=msg, thread_ts=thread_ts)
                    return

            formatter = StreamingFormatter(client, channel, thread_ts, self.team_id, user_id)
            formatter.set_status("is thinking...")
            collected_text = ""
            current_iteration_text = ""
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_creation = 0
            total_cache_read = 0
            peak_single_call_input = 0

            async for event in run_agent_loop_streaming(
                llm=self.llm,
                messages=messages,
                system_prompt=self.system_prompt,
                tool_providers=self._tool_providers,
                user_conversation_context=user_conversation_context,
                storage=storage,
            ):
                if isinstance(event, StreamEvent):
                    if event.type == "text_delta":
                        collected_text += event.text
                        current_iteration_text += event.text
                        await formatter.send_delta(event.text)
                    elif event.type == "message_end":
                        total_input_tokens = event.input_tokens
                        total_output_tokens = event.output_tokens
                        total_cache_creation = event.cache_creation_input_tokens
                        total_cache_read = event.cache_read_input_tokens
                        peak_single_call_input = event.peak_single_call_input_tokens
                        if current_iteration_text:
                            await store.append_text_block(message_id, current_iteration_text)
                            current_iteration_text = ""
                        await formatter.stop()
                elif isinstance(event, dict) and event.get("type") == "tool_status":
                    tool_name = event["tool_name"]
                    tool_id = event["tool_id"]
                    status = event["status"]
                    if status == "calling":
                        if current_iteration_text:
                            await store.append_text_block(message_id, current_iteration_text)
                            current_iteration_text = ""
                        await formatter.post_tool_calling(
                            tool_id, tool_name, event.get("tool_input", {})
                        )
                    elif status == "done":
                        tool_input = event.get("tool_input", {})
                        tool_result = event.get("tool_result", {})
                        await formatter.update_tool_done(
                            tool_id,
                            tool_name,
                            tool_input,
                            tool_result,
                        )
                        output_text = tool_result.get("content", "")
                        if not isinstance(output_text, str):
                            output_text = json.dumps(output_text, indent=2, default=str)
                        tool_block_id = await store.append_tool_block(
                            message_id,
                            tool_call_id=tool_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_output=output_text,
                            is_error=tool_result.get("is_error", False),
                        )
                        for file_entry in tool_result.get("files", []):
                            await upload_file(
                                client,
                                channel,
                                thread_ts,
                                content=file_entry["data"],
                                filename=file_entry["filename"],
                            )
                            file_data = file_entry["data"]
                            if isinstance(file_data, bytes):
                                file_data = base64.b64encode(file_data).decode()
                            file_mimetype = file_entry.get("mimeType", "application/octet-stream")
                            await store.append_file_block(
                                message_id,
                                {
                                    "filename": file_entry["filename"],
                                    "data": file_data,
                                    "mimeType": file_mimetype,
                                },
                                is_user=False,
                                filename=file_entry["filename"],
                                mimetype=file_mimetype,
                                size_bytes=len(file_entry["data"]),
                                tool_block_id=tool_block_id,
                            )

            # Post usage footer and persist usage block
            model = self.llm.model
            agent_version = self.config.version
            version_label = agent_version
            has_usage = (
                total_input_tokens
                or total_output_tokens
                or total_cache_read
                or total_cache_creation
            )
            if has_usage:
                total_in = total_input_tokens + total_cache_creation + total_cache_read
                if total_cache_read and total_in:
                    pct = int(100 * total_cache_read / total_in)
                    in_part = f"in={total_in:,} ({pct}% cached)"
                else:
                    in_part = f"in={total_in:,}"
                parts = [version_label, f"{in_part} out={total_output_tokens:,}"]
                cost = self.llm.estimate_cost(
                    total_input_tokens,
                    total_output_tokens,
                    total_cache_creation,
                    total_cache_read,
                )
                if cost is not None:
                    parts.append(f"${cost:.4f}")
                footer_text = "  |  ".join(parts)
                footer_blocks = [
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": footer_text}],
                    }
                ]
                try:
                    await client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=footer_text,
                        blocks=footer_blocks,
                    )
                except Exception:
                    logger.debug("Failed to post usage footer", exc_info=True)

                if peak_single_call_input >= self.llm.max_input_tokens * 0.75:
                    pct_used = int(100 * peak_single_call_input / self.llm.max_input_tokens)
                    warn_text = (
                        f":warning: Input context is {pct_used}% full"
                        " — please start a new conversation soon."
                    )
                    try:
                        await client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=" ",
                            attachments=[
                                {
                                    "color": "#ff0000",
                                    "text": warn_text,
                                    "mrkdwn_in": ["text"],
                                }
                            ],
                        )
                    except Exception:
                        logger.debug("Failed to post context warning", exc_info=True)

                await store.append_usage_block(
                    message_id,
                    model=model,
                    version=agent_version or model,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cache_creation_input_tokens=total_cache_creation,
                    cache_read_input_tokens=total_cache_read,
                    peak_single_call_input_tokens=peak_single_call_input,
                    estimated_cost_usd=cost,
                )

            if not formatter.has_output:
                await say(text="(No response)", thread_ts=thread_ts)

            set_span_attrs(output=collected_text)

        except Exception as exc:
            user_msg, log_level = _classify_llm_error(exc)
            if log_level >= logging.ERROR:
                # Unknown / configuration / bug — full traceback for ops.
                logger.exception("Error handling message")
            else:
                # Known transient — log as a single warning line, no traceback.
                logger.warning(
                    "Error handling message (transient): %s: %s",
                    type(exc).__name__,
                    exc,
                )
            await say(text=user_msg, thread_ts=thread_ts)
        finally:
            if self._framework_ctx is not None:
                self._framework_ctx.pending_uploads.pop(thread_ts, None)
            try:
                await client.assistant_threads_setStatus(
                    channel_id=channel, thread_ts=thread_ts, status=""
                )
            except Exception:
                pass

    async def deliver_async_result(
        self,
        *,
        channel_id: str,
        thread_id: str,
        user_id: str,
        text: str,
        is_error: bool,
    ) -> None:
        """Surface a long-running A2A task's result into its Slack thread.

        Routes by LLM type: the dumb a2a.proxy (which advertises
        ``relays_async_raw = True``) just posts the result text raw — re-entering
        the loop would bounce it back to the A2A agent. A real LLM re-enters the
        loop via ``_run_turn`` so it can react to / synthesize the result, and
        the turn persists naturally.
        """
        if getattr(self.llm, "relays_async_raw", False):
            await self._slack_client.chat_postMessage(
                channel=channel_id, thread_ts=thread_id, text=text
            )
            return
        prefix = "A long-running task failed: " if is_error else "A delegated task finished:\n\n"
        await self._run_turn(channel_id, thread_id, user_id, f"{prefix}{text}")

    async def _build_user_content(
        self,
        text: str,
        files: list[dict],
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> tuple[str | list[dict], list[dict | None]]:
        if not files:
            return text, []

        file_results = await process_files_for_message(
            files,
            self.config.slack.bot_token,
            self._file_registry,
            user_conversation_context,
            storage,
        )
        if not file_results:
            return text, []

        content: list[dict] = []
        block_meta: list[dict | None] = []
        if text:
            content.append({"type": "text", "text": text})
            block_meta.append(None)
        for block, meta in file_results:
            content.append(block)
            block_meta.append(meta)
        return content, block_meta

    async def _init_tools(self) -> None:
        """Initialize all tool/input-file providers from config using the plugin system."""
        self._tool_providers = []
        self._input_file_providers = []

        # Create all providers first, then initialize them in parallel.
        named_providers: list[tuple[str, object]] = []
        failed_create: list[str] = []
        for name, tool_config in self.config.tools.items():
            try:
                cfg = dict(tool_config)
                type_path = cfg.pop("type")
                if type_path == _OAUTH_PROVIDER_TYPE:
                    cfg.setdefault("server_id", name)
                provider = load_plugin(type_path, framework_ctx=self._framework_ctx, **cfg)
                named_providers.append((name, provider))
            except Exception:
                logger.exception("Failed to create provider: %s", name)
                failed_create.append(name)

        async def _init_one(name: str, provider: object) -> tuple[str, object]:
            await provider.initialize()
            return (name, provider)

        results = await asyncio.gather(
            *(_init_one(name, prov) for name, prov in named_providers),
            return_exceptions=True,
        )

        failed_init: list[str] = []
        for (name, _prov), result in zip(named_providers, results):
            if isinstance(result, BaseException):
                logger.error("Failed to initialize provider: %s: %s", name, result)
                failed_init.append(name)
                continue
            name, provider = result
            if isinstance(provider, BaseFileImporterProvider):
                self._input_file_providers.append(provider)
                handler_count = len(provider.tools)
                mimes = set()
                for h in provider.tools:
                    mimes |= h["mimes"]
                logger.info(
                    "Loaded input file provider %s: %d handlers, mimes=%s",
                    name,
                    handler_count,
                    sorted(mimes),
                )
            elif isinstance(provider, BaseToolProvider):
                self._tool_providers.append(provider)
                tool_count = len(provider.tools)
                tool_tokens = sum(len(json.dumps(t)) // CHARS_PER_TOKEN for t in provider.tools)
                logger.info(
                    "Loaded tool provider %s: %d tools, ~%d tokens",
                    name,
                    tool_count,
                    tool_tokens,
                )
                for t in provider.tools:
                    tok = len(json.dumps(t)) // CHARS_PER_TOKEN
                    logger.info("  %s/%s (~%d tokens)", name, t["name"], tok)
            else:
                logger.warning(
                    "Provider %s is neither a BaseToolProvider nor BaseFileImporterProvider"
                    " — skipping",
                    name,
                )

        all_failed = failed_create + failed_init
        if all_failed:
            raise RuntimeError(f"Failed to initialize tool providers: {', '.join(all_failed)}")

        self._file_registry = FileHandlerRegistry(self._input_file_providers)

    async def _init_storage(self) -> None:
        """Initialize storage and conversation manager."""
        storage_config = dict(self.config.storage)
        type_path = storage_config.pop("type")
        try:
            storage = load_plugin(type_path, **storage_config)
            await storage.initialize()
            self.conversations = ConversationManager(storage)
            logger.info("Storage initialized: %s", type_path)
        except OSError as exc:
            raise SystemExit(
                f"\n[ERROR] Could not connect to storage: {exc}\n"
                "Make sure the storage backend is running."
            )
        except Exception as exc:
            try:
                import asyncpg

                if isinstance(exc, asyncpg.InvalidPasswordError):
                    raise SystemExit(
                        "\n[ERROR] Database authentication failed.\n"
                        "Check that storage.url in your agent config.yaml is correct.\n"
                    ) from exc
            except ImportError:
                pass
            raise

    async def _heartbeat_loop(self, client) -> None:
        """Write heartbeats to storage every 10s when Socket Mode ping/pong is healthy."""
        while True:
            await asyncio.sleep(10)
            if (
                client.current_session is not None
                and not client.current_session.closed
                and client.last_ping_pong_time is not None
                and not await client.is_ping_pong_failing()
            ):
                try:
                    await self.conversations.upsert_heartbeat(
                        self.agent_name, client.last_ping_pong_time
                    )
                except Exception:
                    logger.warning("Heartbeat write failed", exc_info=True)

    def _build_framework_context(self) -> FrameworkContext:
        """Build the FrameworkContext shared across providers that need framework
        services (Slack client, storage, pending OAuth flows).
        """
        return FrameworkContext(
            bot_token=self.config.slack.bot_token,
            agent_name=self.agent_name,
            slack_client=self.app.client,
            storage=self.conversations._storage,
            pending_flows=PendingFlowsRegistry(),
            deliver_async_result=self.deliver_async_result,
        )

    async def _start_ingress_if_needed(self) -> None:
        """Start the shared in-process HTTP listener when OAuth or A2A push needs it,
        register both feature's routes on one app/port, and stash the public URL.
        """
        from aiohttp import web

        from slack_agents.config import (
            ingress_needed,
            push_a2a_agent_names,
            resolve_bind_host,
            resolve_bind_port,
            resolve_public_url,
        )

        if not ingress_needed(self.config.tools):
            return

        has_oauth = any(t.get("type") == _OAUTH_PROVIDER_TYPE for t in self.config.tools.values())
        if has_oauth:
            from slack_agents.oauth.crypto import derive_subkeys
            from slack_agents.oauth.server import build_app
            from slack_agents.oauth.state import NonceReplayCache

            state_key, _ = derive_subkeys(base64.b64decode(os.environ["OAUTH_SECRET_KEY"], True))
            app = build_app(
                state_key=state_key,
                nonce_cache=NonceReplayCache(),
                pending_flows=self._framework_ctx.pending_flows,
            )
        else:
            app = web.Application()

        push_names = push_a2a_agent_names(self.config.tools)
        if push_names:
            from slack_agents.a2a.push import add_push_route

            add_push_route(app, self._framework_ctx)

        host, port = resolve_bind_host(), resolve_bind_port()
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host=host, port=port).start()
        self._oauth_runner = runner
        self._framework_ctx._public_url = resolve_public_url().rstrip("/")
        logger.info(
            "ingress: HTTP listener on %s:%s (public %s)",
            host,
            port,
            self._framework_ctx._public_url,
        )
        if has_oauth:
            logger.info(
                "ingress: OAuth callback = %s/oauth/callback", self._framework_ctx._public_url
            )
        if push_names:
            logger.info(
                "ingress: A2A push webhook = %s/a2a/push (%s)",
                self._framework_ctx._public_url,
                ", ".join(push_names),
            )

    async def start(self) -> None:
        """Start the agent in Socket Mode."""
        await self._init_storage()
        # Build framework context (needs storage + Slack client).
        self._framework_ctx = self._build_framework_context()
        # Conditionally start the in-process HTTP ingress (OAuth callbacks + A2A push).
        # validate_ingress_env() is called earlier in main.py, before observability
        # spins up background threads — moving it here would let OTEL keep the
        # process alive after SystemExit.
        await self._start_ingress_if_needed()
        try:
            await self._init_tools()

            prompt_tokens = len(self.system_prompt) // CHARS_PER_TOKEN
            tools_tokens = sum(
                sum(len(json.dumps(t)) // CHARS_PER_TOKEN for t in p.tools)
                for p in self._tool_providers
            )
            total = prompt_tokens + tools_tokens
            logger.info(
                "Context budget: instructions ~%d tokens + tools ~%d tokens = ~%d tokens",
                prompt_tokens,
                tools_tokens,
                total,
            )

            handler = AsyncSocketModeHandler(self.app, self.config.slack.app_token)
            asyncio.create_task(self._heartbeat_loop(handler.client))
            logger.info("Starting %s in Socket Mode...", self.agent_name)
            await handler.start_async()
        finally:
            if self._oauth_runner is not None:
                try:
                    await self._oauth_runner.cleanup()
                except Exception:
                    logger.warning("oauth: listener cleanup failed", exc_info=True)
