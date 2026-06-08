"""A2A tool provider — exposes a remote A2A agent as a single free-text tool."""

import logging
import re
import secrets
import time

from slack_agents import UserConversationContext
from slack_agents.a2a.client import A2AClient, classify
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import (
    ERROR_SYSTEM_ERROR,
    RECOVERY_CONTACT_SUPPORT,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or "a2a-agent"


class Provider(BaseToolProvider):
    """Connects to a single remote A2A agent and exposes it as one tool."""

    def __init__(
        self,
        url: str,
        allowed_functions: list[str],
        *,
        name: str | None = None,
        auth: dict | None = None,
        framework_ctx=None,
        timeout: float = 300,
        poll_interval: float = 5,
        max_task_lifetime: float = 3600,
        push_notifications: bool = False,
    ):
        super().__init__(allowed_functions)
        self._client = A2AClient(url=url, auth=auth, timeout=timeout)
        self._configured_name = name
        self._server_key = name or _slug(url)
        self._framework_ctx = framework_ctx
        self._poll_interval = poll_interval
        self._max_task_lifetime = max_task_lifetime
        self._push_enabled = push_notifications
        self._tool_def: dict | None = None
        self._manager = None  # AsyncTaskManager, built in initialize()

    @property
    def name(self) -> str:
        return self._tool_def["name"] if self._tool_def else (self._configured_name or "a2a")

    def _get_all_tools(self) -> list[dict]:
        return [self._tool_def] if self._tool_def else []

    async def initialize(self) -> None:
        card = await self._client.resolve_card()
        tool_name = self._configured_name or _slug(card["name"])
        desc = card["description"] or f"Delegate to the {card['name']} A2A agent."
        if card["skills_text"]:
            desc = f"{desc}\nSkills:\n{card['skills_text']}"
        self._tool_def = {
            "name": tool_name,
            "description": desc,
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The request to send to the agent.",
                    }
                },
                "required": ["message"],
            },
        }
        logger.info("A2A %s: tool %r ready", self._server_key, tool_name)
        if self._framework_ctx is not None:
            from slack_agents.a2a.delivery import AsyncTaskManager  # noqa: PLC0415

            self._manager = AsyncTaskManager(
                server_key=self._server_key,
                client=self._client,
                storage=self._framework_ctx.storage,
                deliver=self._framework_ctx.deliver_async_result,
                poll_interval=self._poll_interval,
                max_lifetime=self._max_task_lifetime,
            )
            await self._manager.resume()

    async def call_tool(
        self,
        name: str,
        arguments: dict,
        user_conversation_context: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> ToolResult:
        ctx_ns = f"a2a:ctx:{self._server_key}"
        thread_id = user_conversation_context["thread_id"]
        stored = await storage.get(ctx_ns, thread_id) or {}
        context_id = stored.get("context_id")
        task_id = stored.get("task_id")  # set only while a multi-turn task is in progress
        uploads = self._pending_uploads(thread_id)  # files the user attached this turn
        # Register the push webhook inline on the FIRST send of a task (task_id is None).
        push_config = self._new_push_config() if (self._push_enabled and task_id is None) else None

        try:
            r = await self._client.send(
                arguments["message"], context_id, task_id, files=uploads, push_config=push_config
            )
        except Exception as e:
            logger.exception("A2A %s send failed", self._server_key)
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                tool=name,
                server=self._server_key,
                recovery=RECOVERY_CONTACT_SUPPORT,
                message=f"A2A agent {self._server_key!r} call failed: {e}",
                details={"exception": f"{type(e).__name__}: {e}"},
            )

        # Push bookkeeping: create the record on the first send; on every send record this
        # reply's messageId as delivered, so the server's re-push of it is de-duplicated.
        if self._push_enabled and r.task_id:
            await self._track_push(storage, r, user_conversation_context, push_config)

        new_context = r.context_id or context_id
        bucket = classify(r.state)

        if bucket == "interrupted":
            # input-required / auth-required: the agent wants another message. Keep BOTH
            # ids so the next turn continues the SAME task (and its server-side state).
            await storage.set(ctx_ns, thread_id, {"context_id": new_context, "task_id": r.task_id})
            return {
                "content": r.text or "(the agent needs more input)",
                "is_error": False,
                "files": r.files,
            }

        # terminal or non_terminal: this task won't continue as a turn-by-turn exchange,
        # so clear the saved taskId; keep contextId for conversational grouping. The next
        # message will start a fresh task.
        await storage.set(ctx_ns, thread_id, {"context_id": new_context})

        if bucket == "non_terminal":
            return await self._handle_non_terminal(r, name, user_conversation_context, storage)
        if r.state in ("failed", "canceled", "rejected"):
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                tool=name,
                server=self._server_key,
                recovery=RECOVERY_CONTACT_SUPPORT,
                message=r.text or f"A2A task ended in state {r.state!r}.",
            )
        return {"content": r.text or "(empty result)", "is_error": False, "files": r.files}

    def _pending_uploads(self, thread_id: str) -> list[dict]:
        """Files the user attached this turn, stashed on the FrameworkContext by `_run_turn`."""
        if self._framework_ctx is None:
            return []
        return getattr(self._framework_ctx, "pending_uploads", {}).get(thread_id, [])

    def _new_push_config(self) -> dict | None:
        """Build an inline push-webhook config {url, token}, or None if no public URL."""
        public_url = (
            getattr(self._framework_ctx, "_public_url", None) if self._framework_ctx else None
        )
        if not public_url:
            logger.warning(
                "A2A %s: push_notifications enabled but no ingress public URL is set "
                "(PUBLIC_URL). Skipping webhook registration.",
                self._server_key,
            )
            return None
        return {"url": f"{public_url}/a2a/push", "token": secrets.token_urlsafe(24)}

    async def _track_push(self, storage, r, ucc, push_config) -> None:
        """Persist/maintain the push record so later pushes correlate and dedup correctly."""
        from slack_agents.a2a.push import PUSH_NS, mark_delivered, save_record  # noqa: PLC0415

        delivered = [r.message_id] if r.message_id else []
        if push_config:  # first send — create the record bound to this webhook's token
            await save_record(
                storage,
                r.task_id,
                channel_id=ucc["channel_id"],
                thread_id=ucc["thread_id"],
                user_id=ucc["user_id"],
                token=push_config["token"],
                delivered_ids=delivered,
            )
        elif delivered:  # later send — dedup this reply's id against the existing record
            rec = await storage.get(PUSH_NS, r.task_id)
            if rec:
                await mark_delivered(storage, r.task_id, rec, delivered)

    async def _handle_non_terminal(self, r, name, ucc, storage) -> ToolResult:
        if self._manager is None or not r.task_id:
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                tool=name,
                server=self._server_key,
                recovery=RECOVERY_CONTACT_SUPPORT,
                message="A2A agent returned a long-running task but async delivery is unavailable.",
            )
        record = {
            "task_id": r.task_id,
            "context_id": r.context_id,
            "channel_id": ucc["channel_id"],
            "thread_id": ucc["thread_id"],
            "user_id": ucc["user_id"],
            "created_at": time.time(),
        }
        await self._manager.track(record)
        return {
            "content": "Started a longer task — I'll post the result here when it's ready.",
            "is_error": False,
            "files": [],
        }

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.stop()
        await self._client.close()
