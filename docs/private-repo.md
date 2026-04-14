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
pip install -r requirements.txt
```

This scaffolds:

```
my-agents/
├── requirements.txt              # pins python-slack-agents
├── .env.example
├── .gitignore
├── agents/
│   └── hello-world/
│       ├── config.yaml
│       └── system_prompt.txt
└── src/
    └── my_agents/
        └── __init__.py           # add custom providers here
```

Your overlay is a **plain git repo** — not a Python package. You edit configs, commit, and run. There is no `pip install .` / `pip install -e .` step.

### Two conventions to know

- **`src/` holds custom Python.** On `slack-agents run`, the framework walks up from the agent directory looking for a `src/` sibling and prepends it to `sys.path`. Anything you put under `src/my_agents/...` becomes importable as `my_agents.…` — no install step.
- **`requirements.txt` pins your framework and any extra Python deps.** `pip install -r requirements.txt` is the only install command you ever run.

### Custom providers

Drop a module under `src/` and reference it in config:

```yaml
tools:
  internal-api:
    type: my_agents.tools.internal_api
    allowed_functions: [".*"]
    base_url: "{INTERNAL_API_URL}"
```

Create `src/my_agents/tools/internal_api.py` with a `Provider` class; the framework will find it on the next `slack-agents run`. No reinstall needed.

### Prefer pyproject.toml?

You can use a `pyproject.toml` instead of `requirements.txt` — but **do not add a `[project]` table**, or your overlay becomes an installable package again (the thing this design deliberately avoids). Use PEP 735 `[dependency-groups]`:

```toml
[dependency-groups]
default = ["python-slack-agents==X.Y.Z"]
```

Install with `pip install --group default` (pip ≥ 24.1) or `uv sync`.

### Docker

No custom Dockerfile needed — `python-slack-agents` bundles one that auto-detects your dependency file:

```bash
slack-agents build-docker agents/my-agent
slack-agents build-docker agents/my-agent --push registry.example.com
```
