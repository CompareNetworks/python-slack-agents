# Contributing

Contributions are welcome — bug fixes, features, docs, examples.

## Before You Start

- **Bug fixes and small improvements** — go ahead and open a PR.
- **Large features or architectural changes** — open an issue first to discuss the approach.

## Setup

Requires Python >= 3.12.

```bash
git clone https://github.com/CompareNetworks/python-slack-agents.git
cd python-slack-agents
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Development

```bash
# Run tests
pytest

# Lint and format (also runs automatically on commit via pre-commit hooks)
ruff check --fix src/ tests/
ruff format src/ tests/
```

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/) — lowercase, imperative, short:

```
feat: add canvas tool provider
fix: streaming drops last chunk on disconnect
docs: clarify MCP server configuration
chore: bump dependencies
test: cover PostgreSQL export edge cases
refactor: simplify streaming formatter table detection
```

Prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`. First line under 72 characters. Add a body after a blank line if context is needed.

## Pull Requests

1. Fork the repo and create a branch
2. Make your changes
3. Ensure tests pass and linting is clean
4. Open a PR with a clear description of what and why

Keep PRs focused — one concern per PR. Small PRs get reviewed faster.

## Style

- Python 3.12+, line length 100
- Ruff rules: E, F, I
- Keep it simple — minimal abstractions, no over-engineering
- Type hints on function signatures
- Tests for new functionality

## Adding a Plugin

LLM providers, storage backends, and tools all follow the same pattern: a module with a `Provider` class. See the [docs/](docs/) directory for guides on creating each type.

## AI Documentation

This project ships `llms.txt` and `llms-full.txt` for AI agent discoverability. `llms-full.txt` is bundled in the PyPI wheel and generated from the docs by concatenation:

```bash
python3 src/slack_agents/scripts/generate_llms_full.py
```

When making changes that affect docs:
- Update the relevant file in `docs/` as you normally would
- Re-run the script above to regenerate `llms-full.txt` and commit the result
- `llms.txt` only needs updating if you add/remove/rename a doc file

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
