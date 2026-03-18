"""Configuration models and YAML loading."""

import importlib
import logging
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CURRENT_SCHEMA = "slack-agents/v1"


def _resolve_env_vars(text: str) -> str:
    """Replace {VAR_NAME} with os.environ[VAR_NAME]. Only matches uppercase/underscore names."""
    return re.sub(r"\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ[m.group(1)], text)


def load_plugin(type_path: str, **kwargs):
    """Load a plugin module by import path and instantiate its Provider class.

    Each plugin module must export a `Provider` class. The type_path is a dotted
    Python import path (e.g. 'slack_agents.llm.anthropic').
    """
    mod = importlib.import_module(type_path)
    return mod.Provider(**kwargs)


class SlackConfig(BaseModel):
    bot_token: str
    app_token: str


class OTLPHeaderDef(BaseModel):
    key: str
    value: str


class BasicAuthDef(BaseModel):
    user: str
    password: str


class ObservabilityEndpointDef(BaseModel):
    type: str
    endpoint: str
    headers: list[OTLPHeaderDef] = []
    basic_auth: BasicAuthDef | None = None
    attributes: dict[str, str] = {}


class ObservabilityConfig(BaseModel):
    endpoints: list[ObservabilityEndpointDef] = []


class AgentConfig(BaseModel):
    """Agent configuration loaded from config.yaml.

    The 'llm', 'storage', and 'tools' fields are raw dicts that get passed to
    load_plugin(). Each must contain a 'type' key with a dotted import path.
    """

    version: str
    slack: SlackConfig
    llm: dict
    storage: dict
    tools: dict[str, dict] = {}
    access: dict
    observability: ObservabilityConfig | None = None


def _check_schema(schema: str) -> None:
    """Check that the config schema is compatible with this version of the framework."""
    if not schema.startswith("slack-agents/v"):
        raise SystemExit(f"Unknown config schema: {schema!r}. Expected format: 'slack-agents/vN'")
    try:
        config_version = int(schema.split("/v", 1)[1])
    except ValueError:
        raise SystemExit(f"Invalid config schema version: {schema!r}")
    current_version = int(CURRENT_SCHEMA.split("/v", 1)[1])
    if config_version > current_version:
        raise SystemExit(
            f"Config schema {schema!r} is newer than this version of slack-agents"
            f" (supports up to {CURRENT_SCHEMA}). Please upgrade slack-agents."
        )


def load_agent_config(agent_dir: Path) -> tuple[AgentConfig, str, str]:
    """Load agent config from a directory containing config.yaml and system_prompt.txt.

    Returns (config, system_prompt, agent_name).
    """
    config_path = agent_dir / "config.yaml"
    prompt_path = agent_dir / "system_prompt.txt"

    with open(config_path) as f:
        text = _resolve_env_vars(f.read())
    data = yaml.safe_load(text)

    system_prompt = prompt_path.read_text().strip()
    agent_name = agent_dir.name

    schema = data.pop("schema", None)
    if not schema:
        raise SystemExit(
            f"Missing required 'schema' field in {config_path}. Add: schema: \"{CURRENT_SCHEMA}\""
        )
    _check_schema(schema)

    return AgentConfig(**data), system_prompt, agent_name
