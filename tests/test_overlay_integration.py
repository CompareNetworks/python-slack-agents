"""End-to-end: scaffold an overlay, drop a custom provider, resolve it via the framework.

Also asserts the Dockerfile has the expected shape (installs from requirements.txt,
not via `pip install .`)."""

import sys
from pathlib import Path

from slack_agents.cli.init import execute as init_execute


class _Args:
    def __init__(self, project_name):
        self.project_name = project_name


def test_scaffold_then_custom_provider_resolves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_APP_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    snapshot = set(sys.modules)

    init_execute(_Args("smoke-test"))

    tools = tmp_path / "src" / "smoke_test" / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").touch()
    (tools / "hello.py").write_text(
        "from dataclasses import dataclass\n@dataclass\nclass Provider:\n    message: str = 'hi'\n"
    )

    cfg_path = tmp_path / "agents" / "hello-world" / "config.yaml"
    cfg_path.write_text(
        cfg_path.read_text().replace(
            "tools: {}",
            "tools:\n  hello:\n"
            "    type: smoke_test.tools.hello\n"
            '    message: "hello from overlay"\n',
        )
    )

    try:
        from slack_agents.config import load_agent_config, load_plugin

        config, _, name = load_agent_config(tmp_path / "agents" / "hello-world")
        tool = dict(config.tools["hello"])
        provider = load_plugin(tool.pop("type"), **tool)
        assert provider.message == "hello from overlay"
        assert name == "hello-world"
    finally:
        for mod in [m for m in sys.modules if m not in snapshot]:
            del sys.modules[mod]


def test_dockerfile_installs_from_requirements_not_package():
    dockerfile = Path(__file__).resolve().parent.parent / "src/slack_agents/Dockerfile"
    content = dockerfile.read_text()
    assert "requirements.txt" in content
    assert "pip install --no-cache-dir -r requirements.txt" in content
    assert "COPY src/" in content
    assert "pip install --no-cache-dir ." not in content
    assert "touch README.md" not in content
