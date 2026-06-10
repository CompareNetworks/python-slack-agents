import pytest

from slack_agents import PendingFlowsRegistry
from slack_agents.oauth.crypto import derive_subkeys
from slack_agents.oauth.discovery import DiscoveryResult
from slack_agents.oauth.flow import PerUserOAuth


class _FakeStorage:
    async def get_oauth_token(self, u, s):
        return None

    async def put_oauth_token(self, u, s, r):
        pass

    async def delete_oauth_token(self, u, s):
        pass

    async def get_oauth_client(self, s, r):
        return None

    async def put_oauth_client(self, s, r, row):
        pass

    async def delete_oauth_client(self, s, r):
        pass


class _Disc:
    def __init__(self, required):
        self._r = required

    async def discover(self):
        return DiscoveryResult(None, None, scope_catalog=self._r, required_scopes=self._r)


def _puo(required):
    sk, tk = derive_subkeys(b"0" * 32)
    return PerUserOAuth(
        server_url="https://rs.example.com/a2a",
        server_id="rs",
        agent_name="t",
        storage=_FakeStorage(),
        pending_flows=PendingFlowsRegistry(),
        slack_client=None,
        state_key=sk,
        token_key=tk,
        redirect_uri="https://a.example.com/oauth/callback",
        public_url="https://a.example.com",
        auth_timeout=300,
        discovery=_Disc(required),
    )


@pytest.mark.asyncio
async def test_required_scopes_seed_initial_authorize_scope():
    puo = _puo(["agent:x:read"])
    oauth = await puo.build_provider("U1", "C1", "T1")
    scope = oauth.context.client_metadata.scope or ""
    assert "agent:x:read" in scope
    assert "openid" in scope and "offline_access" in scope


@pytest.mark.asyncio
async def test_no_required_scopes_leaves_scope_unset():
    puo = _puo([])
    oauth = await puo.build_provider("U1", "C1", "T1")
    assert not (oauth.context.client_metadata.scope or "")
