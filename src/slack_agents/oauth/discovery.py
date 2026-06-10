"""Pluggable OAuth discovery: where the authorization-server metadata and the
scope catalog come from. MCP uses RFC 9728 PRM (PrmDiscovery); A2A (a later
plan) will use the Agent Card (AgentCardDiscovery)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlparse

import httpx
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    authorization_server_metadata: OAuthMetadata | None
    resource_metadata: ProtectedResourceMetadata | None
    scope_catalog: list[str] = field(default_factory=list)  # full set for DCR registration
    required_scopes: list[str] = field(default_factory=list)  # minimum for initial authorize
    userinfo_endpoint: str | None = None  # OIDC userinfo (not in OAuthMetadata model)


class DiscoveryStrategy(Protocol):
    async def discover(self) -> DiscoveryResult: ...


def _default_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True)


def _userinfo_endpoint(resp: httpx.Response) -> str | None:
    """Pull `userinfo_endpoint` from a metadata JSON doc (OAuthMetadata drops it)."""
    try:
        return resp.json().get("userinfo_endpoint")
    except Exception:
        return None


class PrmDiscovery:
    """RFC 9728: fetch /.well-known/oauth-protected-resource then the AS metadata.
    Reproduces the previous mcp_http_oauth._ensure_metadata_cached / _derive_dcr_scopes.
    """

    def __init__(
        self,
        *,
        resource_url: str,
        http_factory: Callable[[], httpx.AsyncClient] = _default_http,
    ) -> None:
        self._resource_url = resource_url
        self._http_factory = http_factory

    async def discover(self) -> DiscoveryResult:
        parsed = urlparse(self._resource_url)
        resource_root = f"{parsed.scheme}://{parsed.netloc}"
        prm: ProtectedResourceMetadata | None = None
        asm: OAuthMetadata | None = None
        userinfo: str | None = None
        async with self._http_factory() as http:
            prm_resp = await http.get(f"{resource_root}/.well-known/oauth-protected-resource")
            prm_resp.raise_for_status()
            prm = ProtectedResourceMetadata.model_validate_json(prm_resp.content)
            if prm.authorization_servers:
                asm, userinfo = await self._fetch_asm(http, str(prm.authorization_servers[0]))
        catalog = sorted(prm.scopes_supported) if prm and prm.scopes_supported else []
        return DiscoveryResult(
            authorization_server_metadata=asm,
            resource_metadata=prm,
            scope_catalog=catalog,
            required_scopes=[],  # PRM mode derives runtime scopes from 401/403 hints
            userinfo_endpoint=userinfo,
        )

    async def _fetch_asm(
        self, http: httpx.AsyncClient, auth_server_url: str
    ) -> tuple[OAuthMetadata | None, str | None]:
        as_parsed = urlparse(auth_server_url)
        candidates = []
        if as_parsed.path and as_parsed.path != "/":
            base = f"{as_parsed.scheme}://{as_parsed.netloc}"
            candidates.append(f"{base}/.well-known/oauth-authorization-server{as_parsed.path}")
        candidates.append(f"{auth_server_url.rstrip('/')}/.well-known/oauth-authorization-server")
        for url in candidates:
            try:
                resp = await http.get(url)
                if resp.status_code == 200:
                    return OAuthMetadata.model_validate_json(resp.content), _userinfo_endpoint(resp)
            except httpx.HTTPError:
                continue
        logger.warning("oauth: could not fetch AS metadata from any of %s", candidates)
        return None, None


class AgentCardDiscovery:
    """A2A discovery: OAuth metadata comes from the Agent Card's oauth2 scheme.

    Plain-value constructor (no a2a-sdk dependency). Fetches the OIDC/AS metadata
    document at `metadata_url` for the registration endpoint, and synthesizes a
    PRM from the resource URL + the card's declared scopes (the reference A2A
    server's RFC 9728 PRM endpoint is unreliable, so we never depend on it).
    """

    def __init__(
        self,
        *,
        resource: str,
        metadata_url: str,
        scopes: list[str],
        required_scopes: list[str],
        http_factory: Callable[[], httpx.AsyncClient] = _default_http,
    ) -> None:
        self._resource = resource
        self._metadata_url = metadata_url
        self._scopes = list(scopes)
        self._required_scopes = list(required_scopes)
        self._http_factory = http_factory

    async def discover(self) -> DiscoveryResult:
        asm: OAuthMetadata | None = None
        userinfo: str | None = None
        if self._metadata_url:
            async with self._http_factory() as http:
                try:
                    resp = await http.get(self._metadata_url)
                    if resp.status_code == 200:
                        asm = OAuthMetadata.model_validate_json(resp.content)
                        userinfo = _userinfo_endpoint(resp)
                except httpx.HTTPError:
                    logger.warning(
                        "oauth: AgentCardDiscovery could not fetch %s", self._metadata_url
                    )
        prm = None
        if asm is not None:
            prm = ProtectedResourceMetadata(
                resource=self._resource,
                authorization_servers=[str(asm.issuer)] if asm.issuer else [],
                scopes_supported=sorted(self._scopes),
            )
        return DiscoveryResult(
            authorization_server_metadata=asm,
            resource_metadata=prm,
            scope_catalog=sorted(self._scopes),
            required_scopes=list(self._required_scopes),
            userinfo_endpoint=userinfo,
        )
