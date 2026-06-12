"""A2A push-notification receiving: webhook handler + per-task push records.

A push-capable agent POSTs task updates (protobuf-JSON, camelCase) to a webhook
we register inline on the first send. The body has one of three top-level keys:

    {"task": {...}}            full Task snapshot (e.g. on creation)
    {"statusUpdate": {...}}    a status message
    {"artifactUpdate": {...}}  a file/text artifact

We correlate by taskId to the originating Slack thread, validate the shared-secret
token, de-duplicate against ids we already delivered synchronously (the server
re-pushes the immediate reply, which carries the same messageId), and deliver any
genuinely-new text/files into the thread out-of-band.

Push records live in the generic KV store under namespace `a2a:push`, keyed by
task_id:  {channel_id, thread_id, user_id, token, delivered_ids: [str, ...]}.
"""

import base64
import logging

from aiohttp import web

logger = logging.getLogger(__name__)

PUSH_NS = "a2a:push"


# ---------------------------------------------------------------------------
# Body parsing (pure; unit-tested against captured real bodies)
# ---------------------------------------------------------------------------


_TERMINAL_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}
_ERROR_STATES = {"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
_INTERRUPTED_STATES = {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}
# States that warrant a single LLM wrap-up/relay turn: the task either finished
# (terminal) or is now blocked awaiting the user (interrupted). Progress states
# (submitted/working) are NOT actionable — they stream to the thread + accumulate
# as context, but never trigger an LLM turn (avoids a per-update flurry).
_ACTIONABLE_STATES = _TERMINAL_STATES | _INTERRUPTED_STATES


def _push_state(body: dict) -> str | None:
    """The TaskState string from a statusUpdate/task push body, else None.

    Push bodies use protobuf-JSON enum NAMES (e.g. "TASK_STATE_COMPLETED"), as
    captured from the live agent and mirrored in the test fixtures.
    """
    if "statusUpdate" in body:
        return body["statusUpdate"].get("status", {}).get("state")
    if "task" in body:
        return body["task"].get("status", {}).get("state")
    return None


def _push_status_message_id(body: dict) -> str | None:
    """The status message's id (the per-status dedup key for reactions), if any."""
    for kind in ("statusUpdate", "task"):
        if kind in body:
            msg = body[kind].get("status", {}).get("message") or {}
            return msg.get("messageId") or None
    return None


def push_is_terminal(body: dict) -> bool:
    """True if this push reports a terminal task state (completed/failed/...)."""
    return _push_state(body) in _TERMINAL_STATES


def push_is_actionable(body: dict) -> bool:
    """True if this push warrants one LLM turn: terminal OR interrupted (needs input)."""
    return _push_state(body) in _ACTIONABLE_STATES


def push_reaction_key(body: dict) -> str:
    """Stable key identifying the status event to react to (for per-status dedup).

    The status message id when present (unique per status), else a state-based key
    so a bare re-pushed terminal status still de-duplicates.
    """
    return _push_status_message_id(body) or f"state:{_push_state(body)}"


def push_task_id(body: dict) -> str | None:
    """The taskId from any push body kind, or None if unrecognized."""
    if "task" in body:
        return body["task"].get("id")
    for kind in ("statusUpdate", "artifactUpdate"):
        if kind in body:
            return body[kind].get("taskId")
    return None


def _parts_text(parts: list[dict]) -> str:
    return "\n".join(p["text"] for p in (parts or []) if p.get("text"))


def _message_item(message: dict | None) -> dict | None:
    """A text item from a status message, dedup-keyed by its messageId."""
    if not message:
        return None
    text = _parts_text(message.get("parts", []))
    if not text:
        return None
    return {"id": message.get("messageId", ""), "kind": "text", "text": text}


def _artifact_items(artifact: dict | None) -> list[dict]:
    """Text and file items from an artifact, dedup-keyed by artifactId(+index)."""
    if not artifact:
        return []
    art_id = artifact.get("artifactId", "")
    items: list[dict] = []
    for i, part in enumerate(artifact.get("parts", [])):
        if part.get("text"):
            items.append({"id": f"{art_id}:{i}", "kind": "text", "text": part["text"]})
        elif part.get("raw"):
            items.append(
                {
                    "id": f"{art_id}:{i}",
                    "kind": "file",
                    "file": {
                        "data": base64.b64decode(part["raw"]),
                        "filename": part.get("filename") or artifact.get("name") or "file",
                        "mimeType": part.get("mediaType") or "application/octet-stream",
                    },
                }
            )
    return items


def extract_items(body: dict) -> list[dict]:
    """Normalize a push body into a list of deliverable items.

    Each item: {"id": str, "kind": "text"|"file", "text"?: str, "file"?: {...}}.
    The `id` is stable across the synchronous response and its re-push, so it
    drives de-duplication.
    """
    if "statusUpdate" in body:
        item = _message_item(body["statusUpdate"].get("status", {}).get("message"))
        return [item] if item else []
    if "artifactUpdate" in body:
        return _artifact_items(body["artifactUpdate"].get("artifact"))
    if "task" in body:
        task = body["task"]
        items: list[dict] = []
        item = _message_item(task.get("status", {}).get("message"))
        if item:
            items.append(item)
        for artifact in task.get("artifacts", []):
            items.extend(_artifact_items(artifact))
        return items
    return []


# ---------------------------------------------------------------------------
# Push records (KV)
# ---------------------------------------------------------------------------


async def save_record(
    storage,
    task_id,
    *,
    channel_id,
    thread_id,
    user_id,
    token,
    delivered_ids=None,
    reacted_ids=None,
):
    """Persist a push record.

    `delivered_ids` seeds the de-dup set for thread delivery (the synchronous reply's
    id(s), so the server's re-push of the immediate reply isn't re-posted).
    `reacted_ids` seeds the per-status reaction de-dup set (the synchronous reply's
    status message id when it was already actionable, so push doesn't re-react to it).
    `llm_context` accumulates pushed content (status text + extracted artifact text)
    not yet surfaced to the LLM; it is flushed and cleared on each reaction.
    """
    await storage.set(
        PUSH_NS,
        task_id,
        {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "token": token,
            "delivered_ids": sorted(set(delivered_ids or [])),
            "reacted_ids": sorted(set(reacted_ids or [])),
            "llm_context": [],
        },
    )


async def mark_delivered(storage, task_id, record, ids):
    """Add ids to a record's delivered set and persist."""
    seen = set(record.get("delivered_ids", []))
    seen.update(ids)
    record["delivered_ids"] = sorted(seen)
    await storage.set(PUSH_NS, task_id, record)


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------


async def handle_push(request: web.Request) -> web.StreamResponse:
    """POST /a2a/push — validate, correlate, dedup, deliver."""
    ctx = request.app["a2a_push_ctx"]  # FrameworkContext
    storage = ctx.storage

    try:
        body = await request.json()
    except Exception:
        logger.warning("a2a push: unparseable body from %s", request.remote)
        return web.Response(status=400, text="bad request")

    task_id = push_task_id(body)
    if not task_id:
        return web.Response(status=400, text="no taskId")

    record = await storage.get(PUSH_NS, task_id)
    if record is None:
        # Unknown task — don't reveal which ids exist.
        return web.Response(status=200, text="ok")

    token = record.get("token")
    if token and request.headers.get("X-A2A-Notification-Token") != token:
        logger.warning("a2a push: token mismatch for task %s", task_id)
        return web.Response(status=401, text="unauthorized")

    delivered = set(record.get("delivered_ids", []))
    new_items = [it for it in extract_items(body) if it["id"] not in delivered]
    # An actionable push (terminal OR needs-input) must still fall through to the
    # reaction below even when it carries no NEW items (e.g. a bare COMPLETED status
    # after content streamed earlier). Delivery + persistence below no-op on empty
    # new_items, so falling through is safe.
    if not new_items and not push_is_actionable(body):
        return web.Response(status=200, text="ok")

    from slack_agents.a2a.artifacts import files_to_llm_text  # noqa: PLC0415

    client = ctx.slack_client
    channel, thread = record["channel_id"], record["thread_id"]
    registry = getattr(ctx, "file_registry", None)
    ucc = {"user_id": record["user_id"], "channel_id": channel, "thread_id": thread}

    just_delivered: list[str] = []
    new_context: list[str] = []  # LLM-facing text for items delivered this call
    for item in new_items:
        try:
            if item["kind"] == "text":
                await client.chat_postMessage(channel=channel, thread_ts=thread, text=item["text"])
                new_context.append(item["text"])
            else:
                from slack_agents.slack.files import upload_file  # noqa: PLC0415

                f = item["file"]
                await upload_file(
                    client, channel, thread, content=f["data"], filename=f["filename"]
                )
                extracted = await files_to_llm_text([f], registry, ucc, storage)
                if extracted:
                    new_context.append(extracted)
            just_delivered.append(item["id"])
        except Exception:
            logger.exception("a2a push: delivery failed for task %s item %s", task_id, item["id"])

    # Persist newly-delivered ids + accumulate LLM context for the next reaction.
    if just_delivered:
        record["delivered_ids"] = sorted(set(record.get("delivered_ids", [])) | set(just_delivered))
    if new_context:
        record["llm_context"] = list(record.get("llm_context", [])) + new_context
    if just_delivered or new_context:
        await storage.set(PUSH_NS, task_id, record)

    # React once per actionable status (de-duped by status id), flushing everything
    # accumulated since the last reaction so the LLM gets the full picture (progress +
    # result/artifact content, tagged 'already shown') and can discuss it. Terminal →
    # wrap-up; interrupted → relay the agent's request for more input.
    if push_is_actionable(body):
        react_key = push_reaction_key(body)
        reacted_ids = set(record.get("reacted_ids", []))
        deliver = getattr(ctx, "deliver_async_result", None)
        if deliver is not None and react_key not in reacted_ids:
            buf = [p for p in record.get("llm_context", []) if p]
            summary = "\n\n".join(buf)
            if not summary:
                summary = "(task complete)" if push_is_terminal(body) else "(the agent needs input)"
            try:
                await deliver(
                    channel_id=channel,
                    thread_id=thread,
                    user_id=record["user_id"],
                    text=summary,
                    is_error=_push_state(body) in _ERROR_STATES,
                    files=[],  # already uploaded by the loop above; don't re-upload
                )
                record["reacted_ids"] = sorted(reacted_ids | {react_key})
                record["llm_context"] = []  # flush: this content has been surfaced
                await storage.set(PUSH_NS, task_id, record)
            except Exception:
                logger.exception("a2a push: reaction failed for task %s", task_id)

    return web.Response(status=200, text="ok")


def add_push_route(app: web.Application, framework_ctx) -> None:
    """Register POST /a2a/push on the shared ingress app."""
    app["a2a_push_ctx"] = framework_ctx
    app.router.add_post("/a2a/push", handle_push)
