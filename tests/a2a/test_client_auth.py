from slack_agents.a2a.client import build_auth_headers


def test_apikey_auth_builds_raw_header():
    h = build_auth_headers({"type": "apiKey", "name": "Authorization", "value": "k_id-secret"})
    assert h == {"Authorization": "k_id-secret"}


def test_bearer_and_header_unchanged():
    assert build_auth_headers({"type": "bearer", "token": "t"}) == {"Authorization": "Bearer t"}
    assert build_auth_headers({"type": "header", "name": "X-API-Key", "value": "v"}) == {
        "X-API-Key": "v"
    }


def test_none_and_missing():
    assert build_auth_headers(None) == {}
    assert build_auth_headers({"type": "none"}) == {}


def test_extract_card_oauth_reads_scheme_and_requirements():
    from a2a.types.a2a_pb2 import (
        AgentCard,
        AuthorizationCodeOAuthFlow,
        OAuth2SecurityScheme,
        OAuthFlows,
        SecurityRequirement,
        SecurityScheme,
        StringList,
    )

    from slack_agents.a2a.client import extract_card_oauth

    ac = AuthorizationCodeOAuthFlow(
        authorization_url="https://as/auth",
        token_url="https://as/token",
        scopes={"agent:x:read": "r", "agent:x:write": "w"},
    )
    scheme = SecurityScheme(
        oauth2_security_scheme=OAuth2SecurityScheme(
            flows=OAuthFlows(authorization_code=ac),
            oauth2_metadata_url="https://as/.well-known/openid-configuration",
        )
    )
    card = AgentCard(name="x")
    card.security_schemes["oauth2"].CopyFrom(scheme)
    req = SecurityRequirement()
    req.schemes["oauth2"].CopyFrom(StringList(list=["agent:x:read"]))
    card.security_requirements.append(req)

    info = extract_card_oauth(card)
    assert info["authorization_url"] == "https://as/auth"
    assert sorted(info["scopes"]) == ["agent:x:read", "agent:x:write"]
    assert info["required_scopes"] == ["agent:x:read"]
    assert "openid-configuration" in info["metadata_url"]


def test_extract_card_oauth_returns_none_when_no_oauth_scheme():
    from a2a.types.a2a_pb2 import AgentCard

    from slack_agents.a2a.client import extract_card_oauth

    assert extract_card_oauth(AgentCard(name="x")) is None
