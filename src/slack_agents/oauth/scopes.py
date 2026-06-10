"""Scope-string and WWW-Authenticate parsing/merging for OAuth flows."""

from __future__ import annotations

import re

import httpx

# Standard OIDC scopes the client always requests/registers, on top of any
# resource scopes. openid → OIDC + id_token; offline_access → refresh token;
# profile → identity claims (name, preferred_username, …) for userinfo. These are
# protocol/identity scopes (client<->IdP), not resource scopes the API gates on —
# so they're also stripped when computing required resource scopes below.
OIDC_BASELINE_SCOPES = frozenset({"openid", "offline_access", "profile"})
_WWW_AUTH_SCOPE_RE = re.compile(r'scope\s*=\s*"([^"]*)"')


def parse_www_auth_scope(header: str) -> str | None:
    m = _WWW_AUTH_SCOPE_RE.search(header)
    return m.group(1) if m else None


def replace_www_auth_scope(header: str, new_scope: str) -> str:
    return _WWW_AUTH_SCOPE_RE.sub(f'scope="{new_scope}"', header, count=1)


def www_authenticate_to_required_scopes(www_auth: str | None) -> set[str]:
    if not www_auth:
        return set()
    raw = parse_www_auth_scope(www_auth)
    if not raw:
        return set()
    return set(raw.split()) - OIDC_BASELINE_SCOPES


def detect_missing_scopes(
    exc: BaseException, granted_scopes: set[str]
) -> tuple[set[str], set[str]] | None:
    from slack_agents.oauth.errors import flatten_exceptions

    for leaf in flatten_exceptions(exc):
        if not isinstance(leaf, httpx.HTTPStatusError):
            continue
        if leaf.response.status_code != 403:
            continue
        www_auth_hdr = leaf.response.headers.get("WWW-Authenticate")
        required = www_authenticate_to_required_scopes(www_auth_hdr)
        if not required:
            continue
        missing = required - (granted_scopes - OIDC_BASELINE_SCOPES)
        if missing:
            return required, missing
    return None
