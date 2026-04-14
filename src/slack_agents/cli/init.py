"""CLI subcommand: init — scaffold a new project."""

REQUIREMENTS_TEMPLATE = "python-slack-agents{pin}\n"

ENV_EXAMPLE = """\
# Full setup guide:
# https://github.com/CompareNetworks/python-slack-agents/blob/main/docs/setup.md

SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# LLM provider
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
"""

GITIGNORE = """\
.env
.venv/
__pycache__/
*.pyc
*.db
.DS_Store
.idea/
.vscode/
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
    p.add_argument("project_name", help="Project name (used to derive the src/ package name)")
    p.set_defaults(handler=execute)


def _framework_pin() -> str:
    """Return a pip version specifier pinning the current framework version.

    Falls back to '<2' if the installed version can't be determined."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return f"=={version('python-slack-agents')}"
    except (PackageNotFoundError, ImportError):
        return "<2"


def execute(args):
    import re
    from pathlib import Path

    project_name = args.project_name
    package_name = re.sub(r"[^a-z0-9]+", "_", project_name.lower()).strip("_") or "my_agents"

    files = {
        "requirements.txt": REQUIREMENTS_TEMPLATE.format(pin=_framework_pin()),
        f"src/{package_name}/__init__.py": "",
        ".env.example": ENV_EXAMPLE,
        ".gitignore": GITIGNORE,
        "agents/hello-world/config.yaml": HELLO_WORLD_CONFIG,
        "agents/hello-world/system_prompt.txt": HELLO_WORLD_PROMPT,
    }

    for rel_path, content in files.items():
        path = Path(rel_path)
        if path.exists():
            print(f"Skipping {rel_path} (already exists — remove it to regenerate)")
            print("  Proposed content:\n")
            for line in content.splitlines():
                print(f"    {line}")
            print()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"Created {rel_path}")

    print("Next steps:")
    print("  cp .env.example .env                      # add your tokens")
    print("  pip install -r requirements.txt           # install the framework")
    print("  slack-agents run agents/hello-world       # run the example agent")
