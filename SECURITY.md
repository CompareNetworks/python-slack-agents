# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately using [GitHub's security advisory feature](https://github.com/CompareNetworks/python-slack-agents/security/advisories/new).

Do **not** open public issues for security concerns.

We will acknowledge reports within 72 hours and aim to release fixes promptly.

## For overlay maintainers

If you operate an overlay repository (your own `agents/`, `src/`, and configs built on top of this framework), see [Protecting secrets in your overlay](docs/private-repo.md#protecting-secrets-in-your-overlay) for the recommended setup: GitHub push protection, a gitleaks pre-commit hook, and a one-time trufflehog history sweep.
