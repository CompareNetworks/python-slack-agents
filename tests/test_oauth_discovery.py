import httpx
import pytest

from slack_agents.oauth.discovery import AgentCardDiscovery, DiscoveryResult, PrmDiscovery

PRM = {
    "resource": "https://rs.example.com",
    "authorization_servers": ["https://as.example.com/realms/r"],
    "scopes_supported": ["agent:x:read", "agent:x:write"],
}
ASM = {
    "issuer": "https://as.example.com/realms/r",
    "authorization_endpoint": "https://as.example.com/realms/r/auth",
    "token_endpoint": "https://as.example.com/realms/r/token",
    "registration_endpoint": "https://as.example.com/realms/r/reg",
    "response_types_supported": ["code"],
}


def _handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p.endswith("/.well-known/oauth-protected-resource"):
        return httpx.Response(200, json=PRM)
    if "oauth-authorization-server" in p:
        return httpx.Response(200, json=ASM)
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_prm_discovery_returns_metadata_and_catalog():
    transport = httpx.MockTransport(_handler)
    disc = PrmDiscovery(
        resource_url="https://rs.example.com/mcp",
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    result = await disc.discover()
    assert isinstance(result, DiscoveryResult)
    assert sorted(result.scope_catalog) == ["agent:x:read", "agent:x:write"]
    assert result.resource_metadata is not None
    assert result.authorization_server_metadata is not None
    reg = str(result.authorization_server_metadata.registration_endpoint)
    assert reg.rstrip("/").endswith("/reg")
    assert result.required_scopes == []


@pytest.mark.asyncio
async def test_prm_discovery_handles_missing_asm_gracefully():
    def handler(request):
        if request.url.path.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json=PRM)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    disc = PrmDiscovery(
        resource_url="https://rs.example.com/mcp",
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    result = await disc.discover()
    assert result.resource_metadata is not None
    assert result.authorization_server_metadata is None
    assert sorted(result.scope_catalog) == ["agent:x:read", "agent:x:write"]


OIDC = {
    "issuer": "https://as.example.com/realms/r",
    "authorization_endpoint": "https://as.example.com/realms/r/auth",
    "token_endpoint": "https://as.example.com/realms/r/token",
    "registration_endpoint": "https://as.example.com/realms/r/reg",
    "response_types_supported": ["code"],
}


@pytest.mark.asyncio
async def test_agent_card_discovery_from_oidc_metadata_url():
    def handler(request):
        path = request.url.path
        if "openid-configuration" in path or "oauth-authorization-server" in path:
            return httpx.Response(200, json=OIDC)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    disc = AgentCardDiscovery(
        resource="https://agent.example.com",
        metadata_url="https://as.example.com/realms/r/.well-known/openid-configuration",
        scopes=["agent:x:read", "agent:x:write", "agent:x:admin"],
        required_scopes=["agent:x:read"],
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    r = await disc.discover()
    assert sorted(r.scope_catalog) == ["agent:x:admin", "agent:x:read", "agent:x:write"]
    assert r.required_scopes == ["agent:x:read"]
    assert str(r.authorization_server_metadata.registration_endpoint).rstrip("/").endswith("/reg")
    assert r.resource_metadata is not None
    assert sorted(r.resource_metadata.scopes_supported) == [
        "agent:x:admin",
        "agent:x:read",
        "agent:x:write",
    ]
