"""Slack Canvas API wrappers.

Async functions for creating, reading, editing, and managing canvases.
Each takes an ``AsyncWebClient`` as the first parameter for reuse across
the codebase (tools layer, future SlackAgent features, etc.).
"""

import logging

import aiohttp
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


class CanvasError(Exception):
    """Raised when a Slack Canvas API call fails."""


def _check(response: dict, action: str) -> dict:
    """Raise :class:`CanvasError` if the API response is not ``ok``."""
    if not response.get("ok"):
        error = response.get("error", "unknown_error")
        raise CanvasError(f"Canvas {action} failed: {error}")
    return response


async def create_canvas(
    client: AsyncWebClient,
    *,
    title: str | None = None,
    markdown: str | None = None,
) -> dict:
    """Create a new standalone canvas via ``canvases.create``."""
    kwargs: dict = {}
    if title:
        kwargs["title"] = title
    if markdown is not None:
        kwargs["document_content"] = {"type": "markdown", "markdown": markdown}
    resp = await client.api_call("canvases.create", json=kwargs)
    return _check(resp, "create")


async def get_canvas_permalink(
    client: AsyncWebClient,
    *,
    canvas_id: str,
) -> str | None:
    """Return the permalink for a canvas, or ``None`` on failure."""
    try:
        resp = await client.api_call("files.info", params={"file": canvas_id})
        if resp.get("ok"):
            return resp.get("file", {}).get("permalink")
    except Exception:
        logger.debug("Could not fetch permalink for canvas %s", canvas_id)
    return None


async def read_canvas_content(
    client: AsyncWebClient,
    *,
    canvas_id: str,
) -> dict:
    """Read the full content of a canvas via ``files.info`` + private download.

    Returns ``{"title": ..., "content": ..., "canvas_id": ...}``.
    """
    resp = await client.api_call("files.info", params={"file": canvas_id})
    _check(resp, "read (files.info)")
    file_info = resp["file"]
    title = file_info.get("title", "")

    download_url = file_info.get("url_private_download") or file_info.get("url_private")
    if not download_url:
        raise CanvasError("Canvas read failed: no download URL available")

    token = client.token
    async with aiohttp.ClientSession() as session:
        async with session.get(
            download_url, headers={"Authorization": f"Bearer {token}"}
        ) as http_resp:
            if http_resp.status != 200:
                raise CanvasError(f"Canvas read failed: download returned HTTP {http_resp.status}")
            content = await http_resp.text()

    return {"canvas_id": canvas_id, "title": title, "content": content}


async def edit_canvas(
    client: AsyncWebClient,
    *,
    canvas_id: str,
    changes: list[dict],
) -> dict:
    """Apply one or more edits to an existing canvas.

    *changes* is a list of change objects as defined by the
    ``canvases.edit`` API (operation, section_id, document_content).
    """
    resp = await client.api_call(
        "canvases.edit",
        json={"canvas_id": canvas_id, "changes": changes},
    )
    return _check(resp, "edit")


async def delete_canvas(
    client: AsyncWebClient,
    *,
    canvas_id: str,
) -> dict:
    """Permanently delete a canvas."""
    resp = await client.api_call(
        "canvases.delete",
        json={"canvas_id": canvas_id},
    )
    return _check(resp, "delete")


async def get_canvas_info(
    client: AsyncWebClient,
    *,
    canvas_id: str,
) -> dict:
    """Return the full ``files.info`` dict for a canvas."""
    resp = await client.api_call("files.info", params={"file": canvas_id})
    _check(resp, "info")
    return resp["file"]


async def rename_canvas(
    client: AsyncWebClient,
    *,
    canvas_id: str,
    title: str,
) -> dict:
    """Rename a canvas via ``canvases.edit`` with a ``rename`` change."""
    resp = await client.api_call(
        "canvases.edit",
        json={
            "canvas_id": canvas_id,
            "changes": [
                {
                    "operation": "rename",
                    "title_content": {"type": "markdown", "markdown": title},
                }
            ],
        },
    )
    return _check(resp, "rename")


async def set_canvas_access(
    client: AsyncWebClient,
    *,
    canvas_id: str,
    access_level: str,
    user_ids: list[str] | None = None,
) -> dict:
    """Grant access to a canvas for users.

    *access_level* is one of ``read``, ``write``, or ``owner``.
    """
    payload: dict = {"canvas_id": canvas_id, "access_level": access_level}
    if user_ids:
        payload["user_ids"] = user_ids
    resp = await client.api_call("canvases.access.set", json=payload)
    return _check(resp, "access.set")


async def delete_canvas_access(
    client: AsyncWebClient,
    *,
    canvas_id: str,
    user_ids: list[str] | None = None,
) -> dict:
    """Remove access to a canvas for users."""
    payload: dict = {"canvas_id": canvas_id}
    if user_ids:
        payload["user_ids"] = user_ids
    resp = await client.api_call("canvases.access.delete", json=payload)
    return _check(resp, "access.delete")
