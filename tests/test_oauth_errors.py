import json

from slack_agents.oauth.errors import (
    AuthSetupError,
    UserAuthorizationDenied,
    find_user_authorization_denied,
    flatten_exceptions,
    is_redirect_uri_mismatch,
    message_from_tool_error,
    user_denied_tool_result,
)


def test_flatten_walks_groups_and_causes():
    inner = ValueError("inner")
    grp = BaseExceptionGroup("g", [inner])
    assert inner in flatten_exceptions(grp)


def test_find_user_authorization_denied():
    d = UserAuthorizationDenied(code="access_denied")
    grp = BaseExceptionGroup("g", [d])
    assert find_user_authorization_denied(grp) is d
    assert find_user_authorization_denied(ValueError("x")) is None


def test_is_redirect_uri_mismatch():
    assert is_redirect_uri_mismatch(Exception("Invalid parameter: redirect_uri"))
    assert is_redirect_uri_mismatch(Exception("redirect_uri_mismatch"))
    assert not is_redirect_uri_mismatch(Exception("nope"))


def test_user_denied_tool_result_names_scopes():
    d = UserAuthorizationDenied(
        code="scope_not_granted",
        required_scopes=["agent:x:write"],
        granted_scopes=["agent:x:read"],
    )
    tr = user_denied_tool_result("srv", d, tool="t")
    payload = json.loads(tr["content"])
    assert payload["details"]["missing_scopes"] == ["agent:x:write"]


def test_message_from_tool_error_extracts_message():
    tr = {"content": json.dumps({"message": "boom"})}
    assert message_from_tool_error(tr) == "boom"
    assert AuthSetupError
