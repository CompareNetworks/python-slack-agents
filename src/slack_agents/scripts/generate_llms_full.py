#!/usr/bin/env python3
"""Generate llms-full.txt by concatenating docs with a preamble."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

PREAMBLE = (
    "# python-slack-agents: Complete Reference\n"
    "\n"
    "> A Python framework for deploying AI agents as Slack bots.\n"
    "> Each agent is a YAML config and a system prompt — pick your LLM,\n"
    "> connect some MCP tools, and `slack-agents run`.\n"
    "\n"
    "- **Package:** `pip install python-slack-agents`\n"
    "- **CLI entry point:** `slack-agents`\n"
    "- **Python:** >= 3.12\n"
    "- **License:** Apache 2.0\n"
    "- **Source:** https://github.com/CompareNetworks/python-slack-agents\n"
    "\n"
    "## How to read this document\n"
    "\n"
    "This file is a concatenation of all documentation files,\n"
    "designed to be consumed in a single read.\n"
    "The sections below correspond to individual doc files\n"
    "in the `docs/` directory.\n"
    "\n"
    "### Key concepts\n"
    "\n"
    "- **Config-driven:** each agent is a directory with\n"
    "  `config.yaml` + `system_prompt.txt`.\n"
    "  All behavior is configured in YAML.\n"
    "- **Plugin pattern:** every pluggable component (LLM, storage,\n"
    "  tools, access) uses a `type` field with a dotted Python import\n"
    "  path pointing to a module with a `Provider` class. All other\n"
    "  config keys are passed as kwargs to `Provider.__init__`.\n"
    "- **Two kinds of tool providers:** `BaseToolProvider` (tools the\n"
    "  LLM calls) and `BaseFileImporterProvider` (file handlers the\n"
    "  *framework* calls automatically — invisible to the LLM). Both\n"
    "  are configured under `tools:` in config.yaml.\n"
    "- **Environment variables:** `{ENV_VAR}` patterns in config values\n"
    "  are resolved from environment variables at startup.\n"
)

# Docs in reading order
DOCS = [
    "docs/setup.md",
    "docs/agents.md",
    "docs/llm.md",
    "docs/tools.md",
    "docs/storage.md",
    "docs/access-control.md",
    "docs/canvas.md",
    "docs/user-context.md",
    "docs/cli.md",
    "docs/observability.md",
    "docs/deployment.md",
    "docs/private-repo.md",
]


def main():
    parts = [PREAMBLE]
    for doc in DOCS:
        content = (REPO_ROOT / doc).read_text()
        parts.append(f"\n---\n\n{content}")

    output = REPO_ROOT / "llms-full.txt"
    output.write_text("".join(parts))
    line_count = output.read_text().count("\n") + 1
    print(f"Generated llms-full.txt ({line_count} lines)")


if __name__ == "__main__":
    main()
