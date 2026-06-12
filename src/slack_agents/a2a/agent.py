"""A2A tool provider — exposes a remote A2A agent as a single free-text tool."""

import base64
import logging
import os
import re
import secrets
import time

from slack_agents import UserConversationContext
from slack_agents.a2a.client import A2AClient, A2AResult, classify
from slack_agents.a2a.oauth import build_user_a2a_client
from slack_agents.storage.base import BaseStorageProvider
from slack_agents.tools.base import (
    ERROR_SYSTEM_ERROR,
    RECOVERY_CONTACT_SUPPORT,
    RECOVERY_RETRY,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)

logger = logging.getLogger(__name__)


def _derive_oauth_subkeys() -> tuple[bytes, bytes]:
    from slack_agents.oauth.crypto import derive_subkeys  # noqa: PLC0415

    env = os.environ.get("OAUTH_SECRET_KEY")
    if env:
        root = base64.b64decode(env, validate=True)
    else:
        root = secrets.token_bytes(32)
    return derive_subkeys(root)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or "a2a-agent"


class Provider(BaseToolProvider):
    """Connects to a single remote A2A agent and exposes it as one tool."""

    def __init__(
        self,
        url: str,
        *,
        server_id: str | None = None,
        auth: dict | None = None,
        framework_ctx=None,
        timeout: float = 300,
        poll_interval: float = 5,
        max_task_lifetime: float = 3600,
        push_notifications: bool = False,
        auth_timeout: int = 300,
    ):
        # An A2A agent is a single opaque tool — there is nothing to filter, so it has
        # no `allowed_functions`; the base class always exposes the one tool.
        super().__init__([".*"])
        self._oauth_mode = bool(auth) and auth.get("type") == "oauth2"
        # Static A2AClient must NOT receive an oauth2 auth dict (build_auth_headers would raise).
        client_auth = None if self._oauth_mode else auth
        self._client = A2AClient(url=url, auth=client_auth, timeout=timeout)
        # `server_id` is the config key under `tools:` (injected by the framework). It
        # names this agent's single tool (slugified, for the LLM tool-name rules) and
        # namespaces its storage/push/oauth state. Falls back to a slug of the url when
        # constructed directly (e.g. in tests) without a key.
        self._server_key = server_id or _slug(url)
        self._framework_ctx = framework_ctx
        self._poll_interval = poll_interval
        self._max_task_lifetime = max_task_lifetime
        self._push_enabled = push_notifications
        self._tool_def: dict | None = None
        self._manager = None  # AsyncTaskManager, built in initialize()
        # OAuth state (populated in _setup_oauth if _oauth_mode)
        self._url = url
        self._timeout = timeout
        self._auth_timeout = auth_timeout
        self._auth = auth
        self._oauth = None
        self._user_clients: dict[str, A2AClient] = {}

    @property
    def name(self) -> str:
        return self._tool_def["name"] if self._tool_def else _slug(self._server_key)

    def _get_all_tools(self) -> list[dict]:
        return [self._tool_def] if self._tool_def else []

    async def initialize(self) -> None:
        card = await self._client.resolve_card()
        # The single tool is named after the config key (slugified to satisfy LLM
        # tool-name rules); the Agent Card supplies the human-readable description.
        tool_name = _slug(self._server_key)
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
        if self._oauth_mode:
            self._setup_oauth()
        if self._framework_ctx is not None:
            from slack_agents.a2a.delivery import AsyncTaskManager  # noqa: PLC0415

            manager_kwargs = dict(
                server_key=self._server_key,
                storage=self._framework_ctx.storage,
                deliver=self._framework_ctx.deliver_async_result,
                poll_interval=self._poll_interval,
                max_lifetime=self._max_task_lifetime,
                framework_ctx=self._framework_ctx,
            )
            if self._oauth_mode:
                manager_kwargs["client"] = None
                manager_kwargs["client_factory"] = self._build_poll_client
            else:
                manager_kwargs["client"] = self._client
            self._manager = AsyncTaskManager(**manager_kwargs)
            await self._manager.resume()

    def _setup_oauth(self) -> None:
        from slack_agents.a2a.client import extract_card_oauth  # noqa: PLC0415
        from slack_agents.a2a.oauth import build_per_user_oauth  # noqa: PLC0415

        card_oauth = extract_card_oauth(self._client.card)
        if card_oauth is None:
            raise ValueError(
                f"a2a.agent {self._server_key!r}: auth.type is 'oauth2' but the Agent Card "
                f"advertises no oauth2 authorization_code security scheme."
            )
        state_key, token_key = _derive_oauth_subkeys()
        public_url = (
            getattr(self._framework_ctx, "_public_url", None) or os.environ.get("PUBLIC_URL") or ""
        ).rstrip("/")
        if not public_url:
            raise ValueError(
                f"a2a.agent {self._server_key!r}: oauth2 requires PUBLIC_URL (ingress) to be set."
            )
        self._oauth = build_per_user_oauth(
            card_oauth=card_oauth,
            server_url=self._url,
            server_id=self._server_key,
            framework_ctx=self._framework_ctx,
            state_key=state_key,
            token_key=token_key,
            public_url=public_url,
            auth_timeout=self._auth_timeout,
        )
        logger.info("A2A %s: per-user OAuth enabled (card-discovered)", self._server_key)

    async def _build_poll_client(self, record: dict) -> A2AClient:
        """Non-interactive per-user client for out-of-band polling (no auth prompt)."""
        from slack_agents.a2a.oauth import build_user_a2a_client  # noqa: PLC0415

        return await build_user_a2a_client(
            oauth=self._oauth,
            url=self._url,
            timeout=self._timeout,
            user_id=record["user_id"],
            channel_id=record["channel_id"],
            thread_id=record.get("thread_id"),
            interactive=False,
        )

    async def _get_client(self, ucc) -> A2AClient:
        """Static auth → shared client. OAuth → per-user authed client (cached)."""
        if not self._oauth_mode:
            return self._client
        user_id = ucc["user_id"]
        client = self._user_clients.get(user_id)
        if client is None:
            client = await build_user_a2a_client(
                oauth=self._oauth,
                url=self._url,
                timeout=self._timeout,
                user_id=user_id,
                channel_id=ucc["channel_id"],
                thread_id=ucc.get("thread_id"),
            )
            self._user_clients[user_id] = client
        return client

    async def _handle_oauth_error(self, exc, name, ucc) -> ToolResult | None:
        """Map OAuth-specific send failures to actionable results (oauth mode only).

        Mirrors the MCP provider: a user-level authorization denial becomes a
        permission-denied result naming the missing scope (ask your admin), and an
        IdP redirect_uri rejection clears the stale client registration so the next
        attempt self-heals. Returns None for anything else (generic system error).
        """
        from slack_agents.oauth.errors import (  # noqa: PLC0415
            find_user_authorization_denied,
            is_redirect_uri_mismatch,
            user_denied_tool_result,
        )

        denied = find_user_authorization_denied(exc)
        if denied is not None:
            logger.info(
                "A2A %s: user %s denied access (code=%s)",
                self._server_key,
                ucc["user_id"],
                denied.code,
            )
            return user_denied_tool_result(self._server_key, denied, tool=name)

        if is_redirect_uri_mismatch(exc):
            await self._oauth.handle_redirect_uri_mismatch(ucc["user_id"])
            self._user_clients.pop(ucc["user_id"], None)  # rebuild with fresh registration
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                code="redirect_uri_mismatch",
                tool=name,
                server=self._server_key,
                recovery=RECOVERY_RETRY,
                message=(
                    "The agent's PUBLIC_URL doesn't match the redirect_uri registered with "
                    "the OAuth client at the IdP. The cached client registration has been "
                    "cleared — the next call will register a fresh client and prompt for re-auth."
                ),
            )
        return None

    def _unconfigured_auth_hint(self, exc, name) -> ToolResult | None:
        """When a static (non-oauth) send gets a 401/403 and the remote Agent Card
        advertises a security scheme, return an actionable "configure auth" error
        instead of a raw system failure. Returns None otherwise (oauth mode, no
        401/403, or a card that requires no auth — a real server fault).
        """
        if self._oauth_mode:
            return None
        import httpx  # noqa: PLC0415

        from slack_agents.a2a.client import extract_card_oauth  # noqa: PLC0415
        from slack_agents.oauth.errors import flatten_exceptions  # noqa: PLC0415

        is_auth = any(
            isinstance(x, httpx.HTTPStatusError) and x.response.status_code in (401, 403)
            for x in flatten_exceptions(exc)
        )
        if not is_auth:
            return None
        card = getattr(self._client, "card", None)
        if card is None or not (getattr(card, "security_schemes", {}) or {}):
            return None  # card requires no auth → a 401 is a genuine server fault
        forms = []
        if extract_card_oauth(card) is not None:
            forms.append("`auth: { type: oauth2 }` for per-user OAuth")
        forms.append(
            '`auth: { type: apiKey, name: "Authorization", value: "{A2A_API_KEY}" }` '
            "(or bearer/header) for a static credential"
        )
        return make_tool_error(
            error=ERROR_SYSTEM_ERROR,
            code="auth_required",
            tool=name,
            server=self._server_key,
            recovery=RECOVERY_CONTACT_SUPPORT,
            message=(
                f"The A2A agent {self._server_key!r} requires authentication — it returned "
                f"401/403 and its Agent Card advertises a security scheme, but no matching "
                f"`auth:` is configured for this tool. Add one in config.yaml: "
                + " or ".join(forms)
                + "."
            ),
        )

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
        # If this user has no token yet, the send below triggers the OAuth flow — log
        # their identity once it completes.
        fresh_auth = self._oauth_mode and not await self._oauth.has_token(
            user_conversation_context["user_id"]
        )

        try:
            client = await self._get_client(user_conversation_context)
            r = await client.send(
                arguments["message"], context_id, task_id, files=uploads, push_config=push_config
            )
        except Exception as e:
            if self._oauth_mode:
                handled = await self._handle_oauth_error(e, name, user_conversation_context)
                if handled is not None:
                    return handled
            hint = self._unconfigured_auth_hint(e, name)
            if hint is not None:
                logger.warning(
                    "A2A %s: 401/403 and no matching `auth:` configured (Agent Card "
                    "advertises a security scheme)",
                    self._server_key,
                )
                return hint
            logger.exception("A2A %s send failed", self._server_key)
            return make_tool_error(
                error=ERROR_SYSTEM_ERROR,
                tool=name,
                server=self._server_key,
                recovery=RECOVERY_CONTACT_SUPPORT,
                message=f"A2A agent {self._server_key!r} call failed: {e}",
                details={"exception": f"{type(e).__name__}: {e}"},
            )
        finally:
            # OAuth runs inside send(); the token is persisted even if the A2A call
            # itself later errors. Log the user's identity once, on first-time auth.
            if fresh_auth and await self._oauth.has_token(user_conversation_context["user_id"]):
                await self._oauth.log_user_info(user_conversation_context["user_id"])

        # Push bookkeeping: create the record on the first send; on every send record this
        # reply's messageId as delivered, so the server's re-push of it is de-duplicated.
        if self._push_enabled and r.task_id:
            await self._track_push(
                storage,
                r,
                user_conversation_context,
                push_config,
                reacted_id=(
                    r.message_id if classify(r.state) in ("terminal", "interrupted") else None
                ),
            )

        new_context = r.context_id or context_id
        bucket = classify(r.state)

        if bucket == "interrupted":
            # input-required / auth-required: the agent wants another message. Keep BOTH
            # ids so the next turn continues the SAME task (and its server-side state).
            await storage.set(ctx_ns, thread_id, {"context_id": new_context, "task_id": r.task_id})
            content = await self._result_content(r, user_conversation_context, storage)
            return {
                "content": content or "(the agent needs more input)",
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
        content = await self._result_content(r, user_conversation_context, storage)
        return {"content": content or "(empty result)", "is_error": False, "files": r.files}

    async def _result_content(
        self,
        r: A2AResult,
        ucc: UserConversationContext,
        storage: BaseStorageProvider,
    ) -> str:
        """Combine the agent's text with tagged, extracted artifact content.

        Files are always uploaded to the thread, so artifacts are surfaced to the
        LLM as 'already shown' context (reference, don't reproduce). Returns "" when
        the reply has neither text nor files, so each caller supplies its own default.
        """
        from slack_agents.a2a.artifacts import files_to_llm_text  # noqa: PLC0415

        registry = getattr(self._framework_ctx, "file_registry", None)
        extra = await files_to_llm_text(r.files, registry, ucc, storage) if r.files else ""
        if r.text and extra:
            return f"{r.text}\n\n{extra}"
        return extra or r.text or ""

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

    async def _track_push(
        self, storage, r, ucc, push_config, *, reacted_id: str | None = None
    ) -> None:
        """Persist/maintain the push record so later pushes correlate and dedup correctly.

        `reacted_id` is the sync reply's status message id when that reply was already
        actionable (terminal/interrupted) — seeding it prevents push from re-reacting to
        the status the synchronous path already handled.
        """
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
                reacted_ids=[reacted_id] if reacted_id else [],
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
        # Push-preferred: when a push webhook is registered for this task, the agent
        # delivers updates inbound (no token needed at delivery), so the result arrives
        # even if the user's OAuth token later expires or is revoked. Skip the
        # token-dependent poller in that case — running it too would be redundant
        # (potential double-delivery) and, on an OAuth agent, could post a spurious
        # "session expired" notice while push delivers fine. The poller remains the
        # sole delivery path (and is tracked here) only when push is NOT registered.
        if not await self._push_active(storage, r.task_id):
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

    async def _push_active(self, storage, task_id: str) -> bool:
        """True if a push webhook is registered for this task (so it'll deliver inbound).

        Keyed on the persisted push record rather than `_push_enabled` alone, so a
        push-enabled agent with no reachable PUBLIC_URL (no record was saved) still
        falls back to polling.
        """
        if not self._push_enabled:
            return False
        from slack_agents.a2a.push import PUSH_NS  # noqa: PLC0415

        return await storage.get(PUSH_NS, task_id) is not None

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.stop()
        for client in self._user_clients.values():
            await client.close()
        self._user_clients.clear()
        await self._client.close()
