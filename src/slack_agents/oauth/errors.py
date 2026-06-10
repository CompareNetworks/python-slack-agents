from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from slack_agents.tools.base import (
    ERROR_PERMISSION_DENIED,
    ERROR_SYSTEM_ERROR,
    RECOVERY_CONTACT_ADMIN,
    RECOVERY_CONTACT_SUPPORT,
    RECOVERY_RETRY,
    ToolResult,
    make_tool_error,
)

logger = logging.getLogger(__name__)


def utc_timestamp() -> str:
    """ISO-8601 UTC timestamp used to correlate Slack messages with log lines."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_exception(exc: BaseException) -> str:
    """One-line summary of an exception, with HTML bodies and long messages trimmed."""
    msg = str(exc) or ""
    cut = msg.find("<")
    if cut != -1:
        msg = msg[:cut]
    msg = msg.replace("\n", " ").strip()
    if len(msg) > 200:
        msg = msg[:200].rstrip() + "…"
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def record_error(
    *,
    server_id: str,
    action_phrase: str,
    exc: BaseException,
    tool: str | None = None,
    user_id: str | None = None,
) -> ToolResult:
    """Log the full traceback with a UTC timestamp tag, return a `make_tool_error`
    result (system error, contact_support).

    The timestamp ends up in `details.timestamp_utc` and in the log line — the
    operator can grep for it.
    """
    ts = utc_timestamp()
    logger.exception(
        "oauth: failure on server=%s user=%s ts=%s action=%s",
        server_id,
        user_id or "?",
        ts,
        action_phrase,
    )
    return make_tool_error(
        error=ERROR_SYSTEM_ERROR,
        recovery=RECOVERY_CONTACT_SUPPORT,
        server=server_id,
        tool=tool,
        message=(
            f"{server_id} could not complete the request while {action_phrase}. "
            "An operator should investigate the agent logs."
        ),
        details={
            "action": action_phrase,
            "exception": short_exception(exc),
            "timestamp_utc": ts,
        },
    )


class AuthSetupError(Exception):
    """Raised by ensure_authenticated() — user-facing message describes the failure."""


class ReauthRequired(Exception):
    """Interactive re-authentication is needed but cannot be performed in this
    context (e.g. an out-of-band background poller). Raised instead of posting a
    Slack auth prompt when a non-interactive OAuth flow hits a full re-auth."""


class UserAuthorizationDenied(Exception):
    """Raised when the upstream IdP (or the resource server) rejects access
    because the user's account lacks permission for the operation. Distinct
    from `OAuthFlowError` so the caller can surface a user-level message.

    Codes can come from two layers:
      - IdP-level: "invalid_scope", "access_denied", "consent_required",
        "login_required" — set by the OAuth callback when the IdP returns an
        error parameter.
      - Resource-level: "scope_not_granted" — set by us when the user's
        post-auth token still doesn't have the scope the resource server is
        demanding (e.g. user consented but Keycloak silently dropped a scope
        their role doesn't grant).

    Attributes:
        code: classification of the denial.
        description: human-readable detail.
        required_scopes / granted_scopes: when set, used to produce a clearer
            user-facing message that names the specific scopes involved.
    """

    USER_LEVEL_CODES = frozenset(
        {
            "invalid_scope",
            "access_denied",
            "consent_required",
            "login_required",
            "scope_not_granted",
        }
    )

    def __init__(
        self,
        code: str,
        description: str | None = None,
        *,
        required_scopes: list[str] | None = None,
        granted_scopes: list[str] | None = None,
    ) -> None:
        self.code = code
        self.description = description
        self.required_scopes = required_scopes
        self.granted_scopes = granted_scopes
        super().__init__(description or code)


def flatten_exceptions(exc: BaseException) -> list[BaseException]:
    """Walk an ExceptionGroup tree (and __cause__/__context__) and return all leaves."""
    out: list[BaseException] = []
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        e = stack.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, BaseExceptionGroup):
            stack.extend(e.exceptions)
        else:
            out.append(e)
        if e.__cause__ is not None:
            stack.append(e.__cause__)
        if e.__context__ is not None and e.__cause__ is None:
            stack.append(e.__context__)
    return out


def find_user_authorization_denied(exc: BaseException) -> UserAuthorizationDenied | None:
    """Look for a UserAuthorizationDenied anywhere in the exception chain."""
    for leaf in flatten_exceptions(exc):
        if isinstance(leaf, UserAuthorizationDenied):
            return leaf
    return None


# Per-code reason text + recovery action for user-level auth denials.
# The recovery action drives what the LLM/UI should advise the user to do next.
_USER_DENIED: dict[str, tuple[str, str]] = {
    # code: (reason text, recovery action)
    "invalid_scope": (
        "your account does not have a role granting the required scope",
        RECOVERY_CONTACT_ADMIN,
    ),
    "scope_not_granted": (
        "your account does not have a role granting the required scope; "
        "the authorization server issued a token without it",
        RECOVERY_CONTACT_ADMIN,
    ),
    "access_denied": (
        "access was denied at the authorization server",
        RECOVERY_RETRY,
    ),
    "consent_required": (
        "consent is required and was not granted",
        RECOVERY_RETRY,
    ),
    "login_required": (
        "the authorization server requires re-authentication",
        RECOVERY_RETRY,
    ),
}


def user_denied_tool_result(
    server_id: str,
    denied: "UserAuthorizationDenied",
    tool: str | None = None,
) -> ToolResult:
    """Build a `make_tool_error` ToolResult for a user-level authorization rejection."""
    reason, recovery = _USER_DENIED.get(
        denied.code,
        (f"the authorization server returned `{denied.code}`", RECOVERY_CONTACT_ADMIN),
    )
    details: dict[str, Any] = {}
    if denied.description and denied.description != denied.code:
        d = denied.description.replace("\n", " ").strip()
        if len(d) > 200:
            d = d[:200].rstrip() + "…"
        details["description"] = d
    if denied.required_scopes is not None:
        details["required_scopes"] = denied.required_scopes
    if denied.granted_scopes is not None:
        details["granted_scopes"] = denied.granted_scopes
    if denied.required_scopes is not None and denied.granted_scopes is not None:
        details["missing_scopes"] = sorted(set(denied.required_scopes) - set(denied.granted_scopes))
    return make_tool_error(
        error=ERROR_PERMISSION_DENIED,
        code=denied.code,
        server=server_id,
        tool=tool,
        recovery=recovery,
        message=(
            f"This action cannot be run on {server_id}: {reason}. "
            "Ask whoever administers your account to grant the required access, "
            "then try again."
        ),
        details=details or None,
    )


def is_redirect_uri_mismatch(exc: BaseException) -> bool:
    """Detect an IdP rejection of our authorize redirect_uri so the caller can
    drop the stale cached client registration. Matches Keycloak's "Invalid
    parameter: redirect_uri" and the RFC 6749 standard `redirect_uri_mismatch`.
    """
    for leaf in flatten_exceptions(exc):
        msg = str(leaf).lower()
        if "invalid parameter: redirect_uri" in msg or "redirect_uri_mismatch" in msg:
            return True
    return False


def message_from_tool_error(tr: ToolResult) -> str:
    """Extract the human-readable `message` field from a structured tool-error
    ToolResult, for paths (like ensure_authenticated) that surface errors
    directly to Slack rather than to an LLM. Falls back to the raw content if
    the payload isn't well-formed JSON.
    """
    try:
        payload = json.loads(tr["content"])
    except (json.JSONDecodeError, TypeError):
        return tr["content"]
    return payload.get("message") or tr["content"]
