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
    storage, task_id, *, channel_id, thread_id, user_id, token, delivered_ids=None
):
    """Persist a push record. Seed `delivered_ids` with the synchronous reply's id(s)
    so the server's re-push of that immediate reply is de-duplicated atomically."""
    await storage.set(
        PUSH_NS,
        task_id,
        {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "token": token,
            "delivered_ids": sorted(set(delivered_ids or [])),
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
    if not new_items:
        return web.Response(status=200, text="ok")

    just_delivered: list[str] = []
    client = ctx.slack_client
    channel, thread = record["channel_id"], record["thread_id"]
    for item in new_items:
        try:
            if item["kind"] == "text":
                await client.chat_postMessage(channel=channel, thread_ts=thread, text=item["text"])
            else:
                from slack_agents.slack.files import upload_file  # noqa: PLC0415

                f = item["file"]
                await upload_file(
                    client, channel, thread, content=f["data"], filename=f["filename"]
                )
            just_delivered.append(item["id"])
        except Exception:
            logger.exception("a2a push: delivery failed for task %s item %s", task_id, item["id"])

    if just_delivered:
        await mark_delivered(storage, task_id, record, just_delivered)
    return web.Response(status=200, text="ok")


def add_push_route(app: web.Application, framework_ctx) -> None:
    """Register POST /a2a/push on the shared ingress app."""
    app["a2a_push_ctx"] = framework_ctx
    app.router.add_post("/a2a/push", handle_push)
