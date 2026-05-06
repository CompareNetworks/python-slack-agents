"""Configuration models and YAML loading."""

import base64 as _b64
import importlib
import inspect
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _auto_extend_sys_path(agent_dir: Path) -> None:
    """Prepend the nearest ancestor `src/` directory to sys.path.

    Walks up from `agent_dir` through its parents; the first `src/` subdir
    found is prepended to sys.path so custom providers under `src/` can be
    imported without installing the overlay as a pip package. Idempotent —
    a repeat call won't duplicate the entry.
    """
    agent_dir = Path(agent_dir).resolve()
    for ancestor in [agent_dir, *agent_dir.parents]:
        src = ancestor / "src"
        if src.is_dir():
            src_str = str(src)
            if src_str in sys.path:
                return
            sys.path.insert(0, src_str)
            logger.info("Added %s to sys.path", src_str)
            return


CURRENT_SCHEMA = "slack-agents/v1"


def _strip_yaml_comments(text: str) -> str:
    """Blank out YAML comment lines, preserving line numbers for error messages."""
    return re.sub(r"(?m)^(\s*)#.*$", r"\1", text)


def _resolve_env_vars(text: str) -> str:
    """Replace {VAR_NAME} with os.environ[VAR_NAME]. Only matches uppercase/underscore names."""
    text = _strip_yaml_comments(text)
    return re.sub(r"\{([A-Z_][A-Z0-9_]*)\}", lambda m: os.environ[m.group(1)], text)


def load_plugin(type_path: str, *, framework_ctx=None, **kwargs):
    """Load a plugin module by import path and instantiate its Provider class.

    Each plugin module must export a `Provider` class. The type_path is a dotted
    Python import path (e.g. 'slack_agents.llm.anthropic').

    If `framework_ctx` is supplied AND the Provider's __init__ declares a
    `framework_ctx` parameter, it is injected. Providers that don't declare
    the parameter are unaffected.
    """
    mod = importlib.import_module(type_path)
    cls = mod.Provider
    if framework_ctx is not None:
        sig = inspect.signature(cls.__init__)
        if "framework_ctx" in sig.parameters:
            kwargs["framework_ctx"] = framework_ctx
    return cls(**kwargs)


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
    _auto_extend_sys_path(agent_dir)
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


_OAUTH_PROVIDER_TYPE = "slack_agents.tools.mcp_http_oauth"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _has_oauth_provider(tools_config: dict[str, dict]) -> bool:
    return any(t.get("type") == _OAUTH_PROVIDER_TYPE for t in tools_config.values())


def _validate_public_url(value: str | None) -> list[str]:
    if not value:
        return ["OAUTH_PUBLIC_URL is not set."]
    try:
        parsed = urlparse(value)
    except Exception:
        return [f"OAUTH_PUBLIC_URL is not a valid URL: {value!r}."]
    if not parsed.hostname:
        return [f"OAUTH_PUBLIC_URL has no host: {value!r}."]
    if parsed.scheme == "https":
        return []
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        if host in _LOOPBACK_HOSTS:
            return []
        return [
            f"OAUTH_PUBLIC_URL must use https:// (or http:// with a loopback host); got {value!r}."
        ]
    return [f"OAUTH_PUBLIC_URL must be http(s); got scheme {parsed.scheme!r}."]


def _validate_secret_key(value: str | None) -> list[str]:
    if not value:
        return ["OAUTH_SECRET_KEY is not set.\n    Generate one with:  openssl rand -base64 32"]
    try:
        raw = _b64.b64decode(value, validate=True)
    except Exception:
        return [
            "OAUTH_SECRET_KEY is not valid base64.\n"
            "    Generate a fresh one with:  openssl rand -base64 32"
        ]
    if len(raw) < 32:
        return [
            f"OAUTH_SECRET_KEY decodes to {len(raw)} bytes; need at least 32.\n"
            "    Generate a fresh one with:  openssl rand -base64 32"
        ]
    return []


def validate_oauth_env(tools_config: dict[str, dict]) -> None:
    """Raise SystemExit with a consolidated message if any mcp_http_oauth provider is
    configured but the OAuth env vars are missing or malformed.

    No-op if no mcp_http_oauth provider is configured.
    """
    if not _has_oauth_provider(tools_config):
        return
    problems: list[str] = []
    problems.extend(_validate_public_url(os.environ.get("OAUTH_PUBLIC_URL")))
    problems.extend(_validate_secret_key(os.environ.get("OAUTH_SECRET_KEY")))
    if not problems:
        return
    names = sorted(k for k, v in tools_config.items() if v.get("type") == _OAUTH_PROVIDER_TYPE)
    header = (
        "Configuration error: this agent has OAuth-protected MCP servers "
        f"({', '.join(names)}), but the required environment variables are "
        "missing or invalid:\n"
    )
    body = "\n".join(f"  • {p}" for p in problems)
    footer = "\nAdd these to your .env file and restart."
    raise SystemExit(header + body + footer)
