"""Thin wrapper over the a2a-sdk exposing a small, stable interface.

This is the ONLY module that imports raw a2a-sdk types. Everything else in
the a2a package depends on A2AResult + A2AClient below, insulating the rest
of the codebase from SDK version churn.

Targets a2a-sdk 1.x (protobuf-based API).  Key API facts:
- A2ACardResolver(httpx_client, base_url).get_agent_card() -> AgentCard
- create_client(agent=card, client_config=ClientConfig(...)) -> Client   (async)
- ClientConfig(httpx_client=..., streaming=...) — httpx_client carries auth headers
- Client.send_message(SendMessageRequest) -> AsyncIterator[StreamResponse]
  StreamResponse is a oneof: task | message | status_update | artifact_update
- Client.get_task(GetTaskRequest(id=...)) -> Task
- new_text_message(text, context_id=..., role=Role.ROLE_USER) -> Message
- Task.status.state is a TaskState enum; TaskState.Name(...) -> "TASK_STATE_COMPLETED"
"""

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

TERMINAL = {"completed", "failed", "canceled", "rejected"}
INTERRUPTED = {"input-required", "auth-required"}
NONTERMINAL = {"submitted", "working"}


def classify(state: str) -> str:
    """Map an A2A TaskState string to a delivery bucket.

    'message' (a direct Message reply) and any unknown state are treated as
    'terminal' so the client never hangs waiting on a state it can't progress.
    """
    if state in NONTERMINAL:
        return "non_terminal"
    if state in INTERRUPTED:
        return "interrupted"
    return "terminal"


@dataclass
class A2AResult:
    state: str  # TaskState string, or "message" for a direct reply
    text: str  # extracted reply / prompt / error text
    context_id: str | None
    task_id: str | None
    files: list[dict] = field(default_factory=list)  # {data: bytes, filename, mimeType}
    message_id: str | None = None  # id of the status message (for push dedup)


def build_auth_headers(auth: dict | None) -> dict:
    """Translate the `auth:` config block into HTTP headers."""
    if not auth or auth.get("type") in (None, "none"):
        return {}
    t = auth["type"]
    if t == "bearer":
        return {"Authorization": f"Bearer {auth['token']}"}
    if t == "header":
        return {auth["name"]: auth["value"]}
    raise ValueError(f"Unknown a2a auth type: {t!r}")


def _state_name(state_int) -> str:
    """Map a TaskState enum int to our hyphenated bucket string.

    e.g. TaskState.Name(6) -> "TASK_STATE_INPUT_REQUIRED" -> "input-required".
    """
    from a2a.types.a2a_pb2 import TaskState  # noqa: PLC0415

    name = TaskState.Name(state_int)  # "TASK_STATE_COMPLETED"
    return name.replace("TASK_STATE_", "").replace("_", "-").lower()


def _parts_text(parts) -> str:
    """Join the text of any text-bearing protobuf Parts (file/data parts contribute nothing)."""
    out = []
    for p in parts or []:
        text = getattr(p, "text", "")
        if text:
            out.append(text)
    return "\n".join(out)


def _parts_files(parts) -> list[dict]:
    """Extract raw (bytes) protobuf Parts as {data, filename, mimeType} dicts."""
    out = []
    for p in parts or []:
        if p.HasField("raw"):
            out.append(
                {
                    "data": p.raw,
                    "filename": p.filename or "file",
                    "mimeType": p.media_type or "application/octet-stream",
                }
            )
    return out


class A2AClient:
    """Stable interface used by the rest of the a2a package."""

    def __init__(self, url: str, auth: dict | None = None, timeout: float = 300):
        self._url = url.rstrip("/")
        self._headers = build_auth_headers(auth)
        self._timeout = timeout
        self._httpx: httpx.AsyncClient | None = None
        self._card = None
        self._sdk_client = None

    async def resolve_card(self) -> dict:
        """Fetch the Agent Card and return {name, description, skills_text}."""
        from a2a.client import A2ACardResolver  # noqa: PLC0415

        self._httpx = httpx.AsyncClient(headers=self._headers, timeout=self._timeout)
        resolver = A2ACardResolver(httpx_client=self._httpx, base_url=self._url)
        self._card = await resolver.get_agent_card()
        self._sdk_client = await self._make_client(self._card)
        skills = getattr(self._card, "skills", []) or []
        skills_text = "\n".join(
            f"- {getattr(s, 'name', '')}: {getattr(s, 'description', '')}" for s in skills
        )
        return {
            "name": getattr(self._card, "name", "") or "",
            "description": getattr(self._card, "description", "") or "",
            "skills_text": skills_text,
        }

    async def _make_client(self, card):
        """Build the raw SDK client (async). Our httpx client carries auth headers.

        streaming=False makes send_message resolve to the task's final state; we
        still drain the returned async iterator in `_collect`.
        """
        from a2a.client import ClientConfig, create_client  # noqa: PLC0415

        config = ClientConfig(httpx_client=self._httpx, streaming=False)
        return await create_client(agent=card, client_config=config)

    async def send(
        self,
        message: str,
        context_id: str | None,
        task_id: str | None = None,
        files: list[dict] | None = None,
        push_config: dict | None = None,
    ) -> A2AResult:
        """Send `message` (+ optional `files`) on `context_id`/`task_id`; return an A2AResult.

        Pass `task_id` to continue an existing multi-turn Task (so the server keeps
        its per-task state); pass None to let the server create a fresh Task.
        `files` are {data: bytes, filename, mimeType} dicts sent as raw A2A parts.
        `push_config` is {url, token}; when given, registers a push webhook inline
        on this send so the server binds it to the (new) task.
        """
        from a2a.types.a2a_pb2 import SendMessageRequest  # noqa: PLC0415

        msg = self._build_message(message, context_id, task_id, files)
        if push_config:
            from a2a.types.a2a_pb2 import (  # noqa: PLC0415
                SendMessageConfiguration,
                TaskPushNotificationConfig,
            )

            cfg = SendMessageConfiguration(
                task_push_notification_config=TaskPushNotificationConfig(
                    url=push_config["url"], token=push_config["token"]
                )
            )
            request = SendMessageRequest(message=msg, configuration=cfg)
        else:
            request = SendMessageRequest(message=msg)
        last = None
        async for resp in self._sdk_client.send_message(request):
            last = resp
        return self._normalize(last)

    def _build_message(self, message, context_id, task_id, files):
        """Build a protobuf Message with a text part plus a raw part per file."""
        from a2a.helpers import new_message, new_raw_part, new_text_part  # noqa: PLC0415
        from a2a.types.a2a_pb2 import Role  # noqa: PLC0415

        parts = [new_text_part(message)]
        for f in files or []:
            parts.append(
                new_raw_part(
                    raw=f["data"], media_type=f.get("mimeType"), filename=f.get("filename")
                )
            )
        return new_message(parts, context_id=context_id, task_id=task_id, role=Role.ROLE_USER)

    async def get_task(self, task_id: str) -> A2AResult:
        """Fetch current task state/result for polling."""
        from a2a.types.a2a_pb2 import GetTaskRequest  # noqa: PLC0415

        task = await self._sdk_client.get_task(GetTaskRequest(id=task_id))
        return self._task_to_result(task)

    def _normalize(self, resp) -> A2AResult:
        """Reduce a StreamResponse to an A2AResult.

        StreamResponse is a oneof: task | message | status_update | artifact_update.
        """
        if resp is None:
            return A2AResult(state="completed", text="", context_id=None, task_id=None)
        if resp.HasField("task"):
            return self._task_to_result(resp.task)
        if resp.HasField("message"):
            m = resp.message
            return A2AResult(
                state="message",
                text=_parts_text(m.parts),
                context_id=m.context_id or None,
                task_id=m.task_id or None,
                files=_parts_files(m.parts),
            )
        if resp.HasField("status_update"):
            su = resp.status_update
            text = _parts_text(su.status.message.parts) if su.status.HasField("message") else ""
            return A2AResult(
                state=_state_name(su.status.state),
                text=text,
                context_id=su.context_id or None,
                task_id=su.task_id or None,
            )
        if resp.HasField("artifact_update"):
            au = resp.artifact_update
            text = _parts_text(au.artifact.parts) if au.HasField("artifact") else ""
            return A2AResult(
                state="working",
                text=text,
                context_id=au.context_id or None,
                task_id=au.task_id or None,
            )
        return A2AResult(state="completed", text="", context_id=None, task_id=None)

    def _task_to_result(self, task) -> A2AResult:
        status = task.status
        # Artifacts are the task's deliverables. Pull their text parts as the reply
        # (preferred over the status message, which is often an ack), and their raw
        # parts as files. Fall back to the status message text when no artifact text.
        text = ""
        files: list[dict] = []
        if task.artifacts:
            text = "\n".join(t for a in task.artifacts if (t := _parts_text(a.parts)))
            for a in task.artifacts:
                files.extend(_parts_files(a.parts))
        message_id = None
        if status.HasField("message"):
            files.extend(_parts_files(status.message.parts))
            message_id = status.message.message_id or None
            if not text:
                text = _parts_text(status.message.parts)
        return A2AResult(
            state=_state_name(status.state),
            text=text,
            context_id=task.context_id or None,
            task_id=task.id or None,
            files=files,
            message_id=message_id,
        )

    async def close(self) -> None:
        if self._sdk_client is not None:
            try:
                await self._sdk_client.close()
            except Exception:
                logger.debug("A2A client close failed", exc_info=True)
            self._sdk_client = None
        if self._httpx is not None:
            try:
                await self._httpx.aclose()
            except Exception:
                logger.debug("A2A httpx close failed", exc_info=True)
            self._httpx = None
