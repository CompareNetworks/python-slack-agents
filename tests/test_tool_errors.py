"""Tests for the framework-level `make_tool_error` helper and the unified
tool-error schema. Every built-in tool produces errors in this shape so the
LLM consuming the result can reason about them uniformly.
"""

import json

from slack_agents.tools.base import (
    ERROR_INPUT_ERROR,
    ERROR_PERMISSION_DENIED,
    ERROR_SYSTEM_ERROR,
    RECOVERY_ABORT,
    RECOVERY_CONTACT_ADMIN,
    RECOVERY_CONTACT_SUPPORT,
    make_tool_error,
)


class TestMakeToolError:
    def test_minimal_required_fields(self):
        result = make_tool_error(
            error=ERROR_SYSTEM_ERROR,
            recovery=RECOVERY_CONTACT_SUPPORT,
            message="something broke",
        )
        assert result["is_error"] is True
        assert result["files"] == []
        payload = json.loads(result["content"])
        assert payload == {
            "error": "system_error",
            "recovery": "contact_support",
            "message": "something broke",
        }

    def test_all_optional_fields(self):
        result = make_tool_error(
            error=ERROR_PERMISSION_DENIED,
            code="scope_not_granted",
            tool="admin_thing",
            server="test-mcp",
            recovery=RECOVERY_CONTACT_ADMIN,
            message="user lacks admin scope",
            details={"missing_scopes": ["mcp:test:admin"]},
        )
        payload = json.loads(result["content"])
        assert payload["error"] == "permission_denied"
        assert payload["code"] == "scope_not_granted"
        assert payload["tool"] == "admin_thing"
        assert payload["server"] == "test-mcp"
        assert payload["recovery"] == "contact_admin"
        assert payload["message"] == "user lacks admin scope"
        assert payload["details"] == {"missing_scopes": ["mcp:test:admin"]}

    def test_empty_details_dict_is_omitted(self):
        result = make_tool_error(
            error=ERROR_INPUT_ERROR,
            recovery=RECOVERY_ABORT,
            message="bad input",
            details={},
        )
        payload = json.loads(result["content"])
        assert "details" in payload or payload.get("details") is None
        # Specifically: empty dict should be omitted (falsy).
        assert "details" not in payload

    def test_unicode_message_passes_through(self):
        result = make_tool_error(
            error=ERROR_SYSTEM_ERROR,
            recovery=RECOVERY_CONTACT_SUPPORT,
            message="déjà vu — server returned ❌",
        )
        payload = json.loads(result["content"])
        assert payload["message"] == "déjà vu — server returned ❌"


class TestSchemaConstants:
    def test_known_error_types(self):
        # Make sure the public constants exist and have stable values
        # (downstream tools depend on these).
        assert ERROR_SYSTEM_ERROR == "system_error"
        assert ERROR_PERMISSION_DENIED == "permission_denied"
        assert ERROR_INPUT_ERROR == "input_error"

    def test_known_recovery_actions(self):
        assert RECOVERY_RETRY == "retry"  # noqa: F821
        assert RECOVERY_CONTACT_ADMIN == "contact_admin"
        assert RECOVERY_CONTACT_SUPPORT == "contact_support"
        assert RECOVERY_ABORT == "abort"


# Ensure the import in the assertion above works at module-load time.
from slack_agents.tools.base import RECOVERY_RETRY  # noqa: E402,F401
