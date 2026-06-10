"""Wire the shared per-user OAuth core to a per-Slack-user A2A client."""

from __future__ import annotations

import httpx

from slack_agents.a2a.client import A2AClient
from slack_agents.oauth.discovery import AgentCardDiscovery
from slack_agents.oauth.flow import PerUserOAuth


def build_per_user_oauth(
    *,
    card_oauth: dict,
    server_url: str,
    server_id: str,
    framework_ctx,
    state_key: bytes,
    token_key: bytes,
    public_url: str,
    auth_timeout: int,
) -> PerUserOAuth:
    redirect_uri = f"{public_url}/oauth/callback"
    discovery = AgentCardDiscovery(
        resource=server_url,
        metadata_url=card_oauth["metadata_url"],
        scopes=card_oauth["scopes"],
        required_scopes=card_oauth["required_scopes"],
    )
    return PerUserOAuth(
        server_url=server_url,
        server_id=server_id,
        agent_name=framework_ctx.agent_name,
        storage=framework_ctx.storage,
        pending_flows=framework_ctx.pending_flows,
        slack_client=framework_ctx.slack_client,
        state_key=state_key,
        token_key=token_key,
        redirect_uri=redirect_uri,
        public_url=public_url,
        auth_timeout=auth_timeout,
        discovery=discovery,
    )


async def build_user_a2a_client(
    *,
    oauth: PerUserOAuth,
    url: str,
    timeout: float,
    user_id: str,
    channel_id: str,
    thread_id: str | None,
    interactive: bool = True,
) -> A2AClient:
    """Per-user authed A2AClient: its httpx carries the OAuthClientProvider + scope-merge hook."""
    provider = await oauth.build_provider(user_id, channel_id, thread_id, interactive=interactive)
    httpx_client = httpx.AsyncClient(
        auth=provider,
        timeout=httpx.Timeout(timeout, read=300.0),
        follow_redirects=True,
        event_hooks={"response": [oauth.auth_response_hook(user_id)]},
    )
    client = A2AClient(url=url, timeout=timeout, httpx_client=httpx_client)
    # card fetch is public; this also builds the sdk client over the authed httpx.
    # If it fails (network, a 401, or ReauthRequired in the non-interactive poller),
    # close the client we just built so its httpx connection pool isn't leaked — the
    # caller never receives a handle to close it on this path.
    try:
        await client.resolve_card()
    except BaseException:
        await client.close()
        await httpx_client.aclose()
        raise
    return client
