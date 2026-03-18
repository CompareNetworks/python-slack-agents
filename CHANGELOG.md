# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2025-03-13

### Added

- Plugin architecture for LLM providers, storage backends, and tools
- Anthropic and OpenAI LLM providers
- SQLite and PostgreSQL storage providers
- MCP over HTTP tool provider
- Built-in document export tools (PDF, DOCX, XLSX, CSV, PPTX)
- Streaming output with native Slack table rendering
- Socket Mode support (no public URL required)
- OpenTelemetry observability
- `{ENV_VAR}` interpolation in agent configs
- Per-agent Docker builds via `docker-build-and-push.sh`
