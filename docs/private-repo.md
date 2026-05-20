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

## Protecting secrets in your overlay

Overlay configs reference secrets via `{ENV_VAR}` placeholders — Slack tokens, LLM API keys, and OAuth client secrets. The scaffolded `.gitignore` keeps `.env` out of git, but that's a single layer. A few minutes of setup adds defense in depth.

### 1. Enable GitHub push protection

GitHub refuses pushes that contain known provider tokens (Slack `xoxb-`/`xapp-`, Anthropic `sk-ant-`, OpenAI `sk-`, AWS, etc.) before they ever reach the remote. It cannot be bypassed by `git commit --no-verify` — the check runs server-side. Free on public repos, and included in GitHub Advanced Security on private/organisation repos.

Toggle it in **Settings → Code security → Secret scanning** (enable both *Secret scanning* and *Push protection*), or in one shot via the CLI:

```bash
gh api -X PATCH repos/<org>/<repo> --input - <<'EOF'
{
  "security_and_analysis": {
    "secret_scanning": {"status": "enabled"},
    "secret_scanning_push_protection": {"status": "enabled"},
    "secret_scanning_non_provider_patterns": {"status": "enabled"}
  }
}
EOF
```

### 2. Add a gitleaks pre-commit hook

Catches secrets on the developer's machine before they ever reach a remote — useful as a first line of defense and as the only layer for contributors who fork the repo. Add to your overlay's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1   # pin to a tag; bump via `pre-commit autoupdate`
    hooks:
      - id: gitleaks
```

Then run `pre-commit install` once per clone. Pre-commit requires a pinned `rev` for reproducibility and supply-chain safety. Keep it fresh either by running `pre-commit autoupdate` periodically or by adding a `package-ecosystem: "pre-commit"` entry to `.github/dependabot.yml` so Dependabot opens hook-bump PRs.

### 3. Sweep history once

Before turning the layers above on, check whether anything already leaked. Trufflehog walks every commit in your history and reports candidate secrets:

```bash
docker run --rm -v "$PWD:/repo" trufflesecurity/trufflehog:latest \
  git file:///repo --no-update
```

If trufflehog finds a real secret, **rotate it immediately** at the issuer (Slack, Anthropic, OpenAI, etc.). Rewriting git history with `git-filter-repo` is optional — once a token has been pushed publicly, assume it's compromised and prioritise rotation over removal.
