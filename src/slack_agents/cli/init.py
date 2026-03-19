"""CLI subcommand: init — scaffold a new project."""

PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-slack-agents>=0.6,<2",
]

[tool.setuptools.packages.find]
where = ["src"]
"""

ENV_EXAMPLE = """\
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
"""

HELLO_WORLD_CONFIG = """\
version: "1.0.0"
schema: "slack-agents/v1"

slack:
  bot_token: "{SLACK_BOT_TOKEN}"
  app_token: "{SLACK_APP_TOKEN}"

access:
  type: slack_agents.access.allow_all

llm:
  type: slack_agents.llm.anthropic
  model: claude-sonnet-4-6
  api_key: "{ANTHROPIC_API_KEY}"
  max_tokens: 4096
  max_input_tokens: 200000

storage:
  type: slack_agents.storage.sqlite
  path: ":memory:"

tools: {}
"""

HELLO_WORLD_PROMPT = "You are a helpful assistant. Be concise and friendly.\n"


def register(subparsers):
    p = subparsers.add_parser(
        "init",
        help="Scaffold a new project in the current directory",
    )
    p.add_argument("project_name", help="Project name (used in pyproject.toml)")
    p.set_defaults(handler=execute)


def execute(args):
    import re
    from pathlib import Path

    project_name = args.project_name
    package_name = re.sub(r"[^a-z0-9]+", "_", project_name.lower()).strip("_") or "my_agents"

    files = {
        "pyproject.toml": PYPROJECT_TEMPLATE.format(
            project_name=project_name, package_name=package_name
        ),
        f"src/{package_name}/__init__.py": "",
        ".env.example": ENV_EXAMPLE,
        "agents/hello-world/config.yaml": HELLO_WORLD_CONFIG,
        "agents/hello-world/system_prompt.txt": HELLO_WORLD_PROMPT,
    }

    for rel_path, content in files.items():
        path = Path(rel_path)
        if path.exists():
            print(f"Skipping {rel_path} (already exists — remove it to regenerate)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"Created {rel_path}")

    print()
    print("Next steps:")
    print("  cp .env.example .env       # add your tokens")
    print("  pip install -e .           # install for development")
    print("  slack-agents run agents/hello-world")
