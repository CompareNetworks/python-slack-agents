import httpx

from slack_agents.oauth.scopes import (
    OIDC_BASELINE_SCOPES,
    detect_missing_scopes,
    parse_www_auth_scope,
    replace_www_auth_scope,
    www_authenticate_to_required_scopes,
)


def test_parse_scope():
    assert parse_www_auth_scope('Bearer scope="a b"') == "a b"
    assert parse_www_auth_scope("Bearer realm=x") is None


def test_replace_scope():
    out = replace_www_auth_scope('Bearer scope="a"', "a b")
    assert out == 'Bearer scope="a b"'


def test_required_scopes_strips_oidc_baseline():
    hdr = 'Bearer scope="openid offline_access profile agent:x:read"'
    assert www_authenticate_to_required_scopes(hdr) == {"agent:x:read"}
    assert OIDC_BASELINE_SCOPES == frozenset({"openid", "offline_access", "profile"})


def test_detect_missing_scopes_finds_403_gap():
    resp = httpx.Response(403, headers={"WWW-Authenticate": 'Bearer scope="agent:x:write"'})
    err = httpx.HTTPStatusError("403", request=httpx.Request("POST", "http://x"), response=resp)
    required, missing = detect_missing_scopes(err, granted_scopes={"agent:x:read"})
    assert required == {"agent:x:write"}
    assert missing == {"agent:x:write"}


def test_detect_missing_scopes_returns_none_when_satisfied():
    resp = httpx.Response(403, headers={"WWW-Authenticate": 'Bearer scope="agent:x:read"'})
    err = httpx.HTTPStatusError("403", request=httpx.Request("POST", "http://x"), response=resp)
    assert detect_missing_scopes(err, granted_scopes={"agent:x:read"}) is None
