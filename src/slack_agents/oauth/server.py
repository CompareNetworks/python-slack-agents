"""In-process aiohttp listener for OAuth start/callback routes.

Two routes only:
    GET /oauth/start/{signed_state}  → 302 to the IdP authorize URL
    GET /oauth/callback?code&state    → resolves the matching pending Future

Anything else: 404. No introspection, no debug routes.
"""

from __future__ import annotations

import logging

from aiohttp import web

from slack_agents import OAuthCallbackResult, PendingFlowsRegistry
from slack_agents.oauth.state import NonceReplayCache, decode

logger = logging.getLogger(__name__)


_EXPIRED_HTML = """<!doctype html><html><body style="font-family: sans-serif">
<h2>This link has expired.</h2>
<p>Please return to Slack and ask the bot again.</p>
</body></html>"""

_SUCCESS_HTML = (
    '<!doctype html><html><body style="font-family: sans-serif; '
    'max-width: 32rem; margin: 3rem auto;">'
    "<h2>Authentication completed.</h2>"
    "<p>You can close this tab and return to Slack — the bot will tell you "
    "whether your account has the access required for what you asked. If a "
    "scope you requested wasn't granted (because your role doesn't include "
    "it), Slack will say so.</p></body></html>"
)


def _error_html(error: str, description: str | None) -> str:
    """Render an error page when the IdP redirects back with ?error=..."""
    import html as _html

    safe_error = _html.escape(error)
    safe_description = _html.escape(description) if description else ""
    desc_block = f"<p style='color: #555'>{safe_description}</p>" if safe_description else ""
    return (
        '<!doctype html><html><body style="font-family: sans-serif; '
        'max-width: 32rem; margin: 3rem auto;">'
        "<h2 style='color: #b91c1c;'>Authentication failed</h2>"
        f"<p>The authorization server returned: <code>{safe_error}</code>.</p>"
        f"{desc_block}"
        "<p>You can close this tab. Return to Slack — the bot has the details.</p>"
        "</body></html>"
    )


def build_app(
    *,
    state_key: bytes,
    nonce_cache: NonceReplayCache,
    pending_flows: PendingFlowsRegistry,
) -> web.Application:
    app = web.Application()
    app["state_key"] = state_key
    app["nonce_cache"] = nonce_cache
    app["pending_flows"] = pending_flows
    app.router.add_get("/oauth/start/{signed}", _start)
    app.router.add_get("/oauth/callback", _callback)
    return app


async def _start(request: web.Request) -> web.StreamResponse:
    signed = request.match_info["signed"]
    payload = decode(signed, request.app["state_key"], request.app["nonce_cache"])
    if payload is None:
        logger.warning("oauth: invalid state on /oauth/start from %s", request.remote)
        return web.Response(status=400, text=_EXPIRED_HTML, content_type="text/html")
    return web.HTTPFound(payload.authorize_url)


async def _callback(request: web.Request) -> web.StreamResponse:
    state = request.query.get("state")
    code = request.query.get("code")
    err = request.query.get("error")
    err_desc = request.query.get("error_description")
    if not state:
        return web.Response(status=400, text="missing state")
    pending: PendingFlowsRegistry = request.app["pending_flows"]
    result = OAuthCallbackResult(code=code, state=state, error=err, error_description=err_desc)
    if pending.resolve(state, result):
        # Distinguish success and error cases — Keycloak (and many others) redirect
        # back to the callback with ?error=... when the user denies, when the
        # requested scope is invalid for the user, etc. The browser should reflect
        # which one happened, not always say "authenticated."
        if err:
            body = _error_html(err, err_desc)
        else:
            body = _SUCCESS_HTML
        return web.Response(status=200, text=body, content_type="text/html")
    return web.Response(
        status=200,
        text=(
            "<!doctype html><html><body style='font-family: sans-serif'>"
            "<h2>This authentication has expired.</h2>"
            "<p>Please return to Slack and ask the bot again.</p></body></html>"
        ),
        content_type="text/html",
    )


async def start_listener(
    *,
    host: str,
    port: int,
    state_key: bytes,
    nonce_cache: NonceReplayCache,
    pending_flows: PendingFlowsRegistry,
) -> tuple[web.AppRunner, web.TCPSite]:
    app = build_app(state_key=state_key, nonce_cache=nonce_cache, pending_flows=pending_flows)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("oauth: in-process HTTP listener bound to %s:%s", host, port)
    return runner, site
