"""Tests for canvas user-level authorization."""

from unittest.mock import AsyncMock, patch

import pytest

from slack_agents.slack.canvas_auth import (
    CanvasAccessDenied,
    check_canvas_access,
    resolve_user_access,
)

# ---------------------------------------------------------------------------
# resolve_user_access
# ---------------------------------------------------------------------------


class TestResolveUserAccess:
    def test_creator_is_owner(self):
        file_info = {"user": "U_CREATOR"}
        assert resolve_user_access(file_info, "U_CREATOR") == "owner"

    def test_canvas_creator_id_fallback(self):
        file_info = {"canvas_creator_id": "U_CREATOR"}
        assert resolve_user_access(file_info, "U_CREATOR") == "owner"

    def test_explicit_write_access(self):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_WRITER", "access": "write"},
            ],
        }
        assert resolve_user_access(file_info, "U_WRITER") == "write"

    def test_explicit_read_access(self):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_READER", "access": "read"},
            ],
        }
        assert resolve_user_access(file_info, "U_READER") == "read"

    def test_org_wide_read(self):
        file_info = {
            "user": "U_CREATOR",
            "org_or_workspace_access": "read",
        }
        assert resolve_user_access(file_info, "U_ANYONE") == "read"

    def test_org_wide_write(self):
        file_info = {
            "user": "U_CREATOR",
            "org_or_workspace_access": "write",
        }
        assert resolve_user_access(file_info, "U_ANYONE") == "write"

    def test_org_access_none_means_denied(self):
        file_info = {
            "user": "U_CREATOR",
            "org_or_workspace_access": "none",
        }
        assert resolve_user_access(file_info, "U_STRANGER") is None

    def test_no_access_returns_none(self):
        file_info = {"user": "U_CREATOR"}
        assert resolve_user_access(file_info, "U_STRANGER") is None

    def test_explicit_access_overrides_org(self):
        """Per-user access takes precedence over org-level."""
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_SPECIAL", "access": "write"},
            ],
            "org_or_workspace_access": "read",
        }
        assert resolve_user_access(file_info, "U_SPECIAL") == "write"

    def test_owner_beats_explicit_access(self):
        """Creator always gets owner, even if listed in access list."""
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_CREATOR", "access": "read"},
            ],
        }
        assert resolve_user_access(file_info, "U_CREATOR") == "owner"

    def test_empty_access_list(self):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [],
        }
        assert resolve_user_access(file_info, "U_OTHER") is None


# ---------------------------------------------------------------------------
# check_canvas_access
# ---------------------------------------------------------------------------


class TestCheckCanvasAccess:
    @pytest.fixture()
    def mock_client(self):
        return AsyncMock()

    async def test_access_granted(self, mock_client):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_WRITER", "access": "write"},
            ],
        }
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            result = await check_canvas_access(
                mock_client,
                canvas_id="F123",
                user_id="U_WRITER",
                required_level="read",
            )
            assert result == file_info

    async def test_access_denied_insufficient_level(self, mock_client):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_READER", "access": "read"},
            ],
        }
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            with pytest.raises(CanvasAccessDenied, match="write access"):
                await check_canvas_access(
                    mock_client,
                    canvas_id="F123",
                    user_id="U_READER",
                    required_level="write",
                )

    async def test_access_denied_no_access(self, mock_client):
        file_info = {"user": "U_CREATOR"}
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            with pytest.raises(CanvasAccessDenied, match="read access"):
                await check_canvas_access(
                    mock_client,
                    canvas_id="F123",
                    user_id="U_STRANGER",
                    required_level="read",
                )

    async def test_owner_has_all_access(self, mock_client):
        file_info = {"user": "U_CREATOR"}
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            result = await check_canvas_access(
                mock_client,
                canvas_id="F123",
                user_id="U_CREATOR",
                required_level="owner",
            )
            assert result == file_info

    async def test_write_satisfies_read(self, mock_client):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_WRITER", "access": "write"},
            ],
        }
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            result = await check_canvas_access(
                mock_client,
                canvas_id="F123",
                user_id="U_WRITER",
                required_level="read",
            )
            assert result == file_info

    async def test_read_insufficient_for_owner(self, mock_client):
        file_info = {
            "user": "U_CREATOR",
            "dm_mpdm_users_with_file_access": [
                {"user_id": "U_READER", "access": "read"},
            ],
        }
        with patch(
            "slack_agents.slack.canvas_auth.get_canvas_info",
            new_callable=AsyncMock,
            return_value=file_info,
        ):
            with pytest.raises(CanvasAccessDenied, match="owner access"):
                await check_canvas_access(
                    mock_client,
                    canvas_id="F123",
                    user_id="U_READER",
                    required_level="owner",
                )
