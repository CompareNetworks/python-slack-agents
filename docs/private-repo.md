# Organizing Your Agents

Agents are just directories with `config.yaml` and `system_prompt.txt`. Where you put them depends on your situation.

## Option 1: In the framework repo

If you're developing the framework itself, add agents directly to `agents/`. To keep private agents out of version control, use a gitignored directory instead:

```bash
slack-agents run agents-local/my-agent
```

## Option 2: Separate repository

For production agents with company-specific prompts, tools, and configs, create a standalone repository:

```bash
mkdir my-agents && cd my-agents
python3 -m venv .venv
source .venv/bin/activate
pip install python-slack-agents
slack-agents init my-agents
pip install -e .
```

This scaffolds:

```
my-agents/
├── pyproject.toml
├── src/
│   └── my_agents/
│       └── __init__.py
├── agents/
│   └── hello-world/
│       ├── config.yaml
│       └── system_prompt.txt
└── .env.example
```

The `pyproject.toml` and `src/` directory are required so that:

- **`slack-agents run`** can import custom providers under `src/` (via `pip install -e .`)
- **`slack-agents build-docker`** works (the bundled Dockerfile runs `pip install .`)

### Custom providers

Add custom providers to `src/` and reference them in config:

```yaml
tools:
  internal-api:
    type: my_agents.tools.internal_api
    allowed_functions: [".*"]
    base_url: "{INTERNAL_API_URL}"
```

### Docker

No custom Dockerfile needed — `python-slack-agents` bundles one:

```bash
slack-agents build-docker agents/my-agent
slack-agents build-docker agents/my-agent --push registry.example.com
```
