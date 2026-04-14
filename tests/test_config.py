"""Tests for configuration loading."""

import sys

import pytest
import yaml

from slack_agents.config import (
    CURRENT_SCHEMA,
    AgentConfig,
    _auto_extend_sys_path,
    load_agent_config,
)

ACCESS = {"type": "slack_agents.access.allow_all"}


class TestAgentConfig:
    def test_minimal_config(self):
        config = AgentConfig(
            version="1.0.0",
            slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
            llm={"type": "slack_agents.llm.anthropic", "model": "claude-sonnet-4-6"},
            storage={"type": "slack_agents.storage.sqlite"},
            access=ACCESS,
        )
        assert config.llm["type"] == "slack_agents.llm.anthropic"
        assert config.tools == {}
        assert config.version == "1.0.0"

    def test_version_required(self):
        with pytest.raises(Exception):
            AgentConfig(
                slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
                llm={"type": "slack_agents.llm.anthropic", "model": "claude-sonnet-4-6"},
                storage={"type": "slack_agents.storage.sqlite"},
                access=ACCESS,
            )

    def test_tools_default_empty(self):
        config = AgentConfig(
            version="1.0.0",
            slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
            llm={"type": "slack_agents.llm.anthropic", "model": "claude-sonnet-4-6"},
            storage={"type": "slack_agents.storage.sqlite"},
            access=ACCESS,
        )
        assert config.tools == {}

    def test_access_required(self):
        with pytest.raises(Exception):
            AgentConfig(
                version="1.0.0",
                slack={"bot_token": "xoxb-test", "app_token": "xapp-test"},
                llm={"type": "slack_agents.llm.anthropic", "model": "claude-sonnet-4-6"},
                storage={"type": "slack_agents.storage.sqlite"},
            )


class TestLoadAgentConfig:
    STORAGE = {"type": "slack_agents.storage.sqlite"}

    def _write_agent(self, agent_dir, config_data, prompt="You are helpful."):
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data.setdefault("version", "1.0.0")
        config_data.setdefault("schema", CURRENT_SCHEMA)
        config_data.setdefault("storage", self.STORAGE)
        config_data.setdefault("access", ACCESS)
        (agent_dir / "config.yaml").write_text(yaml.dump(config_data))
        (agent_dir / "system_prompt.txt").write_text(prompt)

    def test_basic_load(self, tmp_path):
        agent_dir = tmp_path / "my-agent"
        self._write_agent(
            agent_dir,
            {
                "slack": {"bot_token": "xoxb-test", "app_token": "xapp-test"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert agent_name == "my-agent"
        assert config.llm["type"] == "slack_agents.llm.anthropic"
        assert system_prompt == "You are helpful."
        assert config.storage["type"] == "slack_agents.storage.sqlite"

    def test_name_from_directory(self, tmp_path):
        agent_dir = tmp_path / "dir-name"
        self._write_agent(
            agent_dir,
            {
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.openai",
                    "model": "gpt-4.1",
                    "api_key": "sk-test",
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert agent_name == "dir-name"

    def test_env_var_in_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_BOT_TOKEN", "xoxb-from-env")
        monkeypatch.setenv("TEST_APP_TOKEN", "xapp-from-env")
        agent_dir = tmp_path / "env-agent"
        self._write_agent(
            agent_dir,
            {
                "slack": {
                    "bot_token": "{TEST_BOT_TOKEN}",
                    "app_token": "{TEST_APP_TOKEN}",
                },
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert config.slack.bot_token == "xoxb-from-env"

    def test_missing_config_raises(self, tmp_path):
        agent_dir = tmp_path / "missing"
        agent_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_agent_config(agent_dir)

    def test_tools_config(self, tmp_path):
        agent_dir = tmp_path / "with-tools"
        self._write_agent(
            agent_dir,
            {
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
                "tools": {
                    "my-server": {
                        "type": "slack_agents.tools.mcp_http",
                        "url": "https://example.com/mcp",
                        "allowed_functions": ["safe_fn", "read_.*"],
                    },
                    "export-docs": {
                        "type": "slack_agents.tools.file_exporter",
                        "allowed_functions": [".*"],
                    },
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert "my-server" in config.tools
        assert config.tools["my-server"]["url"] == "https://example.com/mcp"
        assert "export-docs" in config.tools

    def test_tools_defaults_empty(self, tmp_path):
        agent_dir = tmp_path / "no-tools"
        self._write_agent(
            agent_dir,
            {
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert config.tools == {}

    def test_schema_field_stripped(self, tmp_path):
        agent_dir = tmp_path / "with-schema"
        self._write_agent(
            agent_dir,
            {
                "schema": CURRENT_SCHEMA,
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        # Should not raise — schema is popped before AgentConfig is created
        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert config.llm["type"] == "slack_agents.llm.anthropic"

    def test_schema_too_new_raises(self, tmp_path):
        agent_dir = tmp_path / "future-schema"
        self._write_agent(
            agent_dir,
            {
                "schema": "slack-agents/v999",
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        with pytest.raises(SystemExit, match="newer than this version"):
            load_agent_config(agent_dir)

    def test_schema_missing_raises(self, tmp_path):
        """Config without schema field should fail."""
        agent_dir = tmp_path / "no-schema"
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = {
            "version": "1.0.0",
            "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
            "llm": {
                "type": "slack_agents.llm.anthropic",
                "model": "claude-sonnet-4-6",
                "api_key": "sk-test",
            },
            "storage": self.STORAGE,
            "access": ACCESS,
        }
        # Deliberately omit schema
        (agent_dir / "config.yaml").write_text(yaml.dump(config_data))
        (agent_dir / "system_prompt.txt").write_text("You are helpful.")

        with pytest.raises(SystemExit, match="Missing required 'schema'"):
            load_agent_config(agent_dir)

    def test_version_missing_raises(self, tmp_path):
        """Config without version field should fail."""
        agent_dir = tmp_path / "no-version"
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = {
            "schema": CURRENT_SCHEMA,
            "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
            "llm": {
                "type": "slack_agents.llm.anthropic",
                "model": "claude-sonnet-4-6",
                "api_key": "sk-test",
            },
            "storage": self.STORAGE,
            "access": ACCESS,
        }
        # Deliberately omit version
        (agent_dir / "config.yaml").write_text(yaml.dump(config_data))
        (agent_dir / "system_prompt.txt").write_text("You are helpful.")

        with pytest.raises(Exception):
            load_agent_config(agent_dir)

    def test_version_loaded_from_config(self, tmp_path):
        agent_dir = tmp_path / "versioned"
        self._write_agent(
            agent_dir,
            {
                "version": "3.2.1",
                "slack": {"bot_token": "xoxb-x", "app_token": "xapp-x"},
                "llm": {
                    "type": "slack_agents.llm.anthropic",
                    "model": "claude-sonnet-4-6",
                    "api_key": "sk-test",
                },
            },
        )

        config, system_prompt, agent_name = load_agent_config(agent_dir)
        assert config.version == "3.2.1"


class TestAutoExtendSysPath:
    def test_adds_src_when_present_as_sibling(self, tmp_path, monkeypatch):
        # Layout: tmp/agents/foo/ with tmp/src/
        agents_dir = tmp_path / "agents" / "foo"
        agents_dir.mkdir(parents=True)
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(sys, "path", list(sys.path))  # isolate
        _auto_extend_sys_path(agents_dir)

        assert str(src_dir) == sys.path[0]

    def test_noop_when_no_src_anywhere(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents" / "foo"
        agents_dir.mkdir(parents=True)

        original = list(sys.path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        _auto_extend_sys_path(agents_dir)

        assert sys.path == original

    def test_walks_up_multiple_levels(self, tmp_path, monkeypatch):
        # src/ is at tmp_path, config is deeper
        agents_dir = tmp_path / "a" / "b" / "c"
        agents_dir.mkdir(parents=True)
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(sys, "path", list(sys.path))
        _auto_extend_sys_path(agents_dir)

        assert str(src_dir) == sys.path[0]

    def test_ignores_file_named_src(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents" / "foo"
        agents_dir.mkdir(parents=True)
        # Create a FILE called "src" (not a directory)
        (tmp_path / "src").write_text("")

        original = list(sys.path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        _auto_extend_sys_path(agents_dir)

        assert sys.path == original

    def test_idempotent_on_repeat_call(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents" / "foo"
        agents_dir.mkdir(parents=True)
        src_dir = tmp_path / "src"
        src_dir.mkdir()

        monkeypatch.setattr(sys, "path", list(sys.path))
        _auto_extend_sys_path(agents_dir)
        _auto_extend_sys_path(agents_dir)

        # Only one entry for src_dir at position 0
        assert sys.path[0] == str(src_dir)
        assert sys.path.count(str(src_dir)) == 1
