"""Canvas user-level authorization.

Resolves a user's access level for a canvas using ``files.info`` metadata
from the Slack API — no additional storage or scopes required.
"""

from slack_sdk.web.async_client import AsyncWebClient

from slack_agents.slack.canvases import CanvasError, get_canvas_info

# Level hierarchy (higher index = more permissive)
_LEVEL_RANK = {"read": 1, "write": 2, "owner": 3}


class CanvasAccessDenied(CanvasError):
    """Raised when a user lacks sufficient access to a canvas."""


def resolve_user_access(file_info: dict, user_id: str) -> str | None:
    """Return the user's access level for a canvas, or ``None`` if denied.

    Returns one of ``"owner"``, ``"write"``, ``"read"``, or ``None``.
    """
    # Creator is the owner
    creator = file_info.get("user") or file_info.get("canvas_creator_id")
    if creator and creator == user_id:
        return "owner"

    # Explicit per-user access list
    for entry in file_info.get("dm_mpdm_users_with_file_access", []):
        if entry.get("user_id") == user_id:
            return entry.get("access")

    # Org/workspace-wide access
    org_access = file_info.get("org_or_workspace_access", "none")
    if org_access != "none":
        return org_access

    return None


async def check_canvas_access(
    client: AsyncWebClient,
    *,
    canvas_id: str,
    user_id: str,
    required_level: str,
) -> dict:
    """Verify that *user_id* has at least *required_level* access to *canvas_id*.

    Returns the ``file_info`` dict on success.
    Raises :class:`CanvasAccessDenied` if the user lacks sufficient access.
    """
    file_info = await get_canvas_info(client, canvas_id=canvas_id)
    access = resolve_user_access(file_info, user_id)
    if access is None or _LEVEL_RANK.get(access, 0) < _LEVEL_RANK.get(required_level, 0):
        raise CanvasAccessDenied(f"You don't have {required_level} access to canvas {canvas_id}")
    return file_info
