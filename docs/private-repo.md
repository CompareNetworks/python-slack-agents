# Organizing Your Agents

Agents are just directories with `config.yaml` and `system_prompt.txt`. Where you put them depends on your situation.

## Option 1: In the `agents/` directory

If you're developing the framework itself, add agents directly to `agents/`. The example agents (`hello-world`, `kitchen-sink`, `docs-assistant`) live here.

To keep private agents out of version control, put them in a gitignored directory instead — for example `agents-local/`. The CLI doesn't care where the directory is:

```bash
slack-agents run agents-local/my-private-agent
```

## Option 2: Separate private repository

For production agents with company-specific prompts, tools, and configs, create a standalone repository that depends on `python-slack-agents`:

```
my-agents/
├── agents/
│   ├── support-bot/
│   │   ├── config.yaml
│   │   └── system_prompt.txt
│   └── sales-bot/
│       ├── config.yaml
│       └── system_prompt.txt
├── src/
│   └── my_agents/
│       └── __init__.py
├── pyproject.toml
└── .env
```

The `pyproject.toml` and `src/` directory are required for `slack-agents build-docker` to work — the bundled Dockerfile runs `pip install .` in the build context.

### pyproject.toml

```toml
[project]
name = "my-agents"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-slack-agents>=0.5,<2",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Building Docker images

No custom Dockerfile needed — `python-slack-agents` bundles one:

```bash
slack-agents build-docker agents/support-bot
slack-agents build-docker agents/support-bot --push registry.example.com
```

### Custom tools

If your agents need custom tool providers, add them to your package and reference them in config:

```yaml
tools:
  internal-api:
    type: my_agents.tools.internal_api
    allowed_functions: [".*"]
    base_url: "{INTERNAL_API_URL}"
```
