# CLI Reference

All commands are available as `slack-agents <command>`.

## run

Start a Slack agent.

```bash
slack-agents run agents/<name>
```

Connects to Slack via Socket Mode, initializes storage and tools, and begins handling messages.

## healthcheck

Check whether an agent's WebSocket connection is healthy.

```bash
slack-agents healthcheck agents/<name>
```

Reads the heartbeat timestamp from storage (written every 10s by the agent). Exits 0 if the heartbeat is fresh (<60s), exits 1 otherwise.

Requires persistent storage (file-based SQLite or PostgreSQL). Designed for use as a Kubernetes liveness probe or similar health check.

## export-conversations

Export stored conversations to HTML.

```bash
slack-agents export-conversations agents/<name> --format=html [options]
```

Options:

| Flag | Description |
|------|-------------|
| `--format` | Export format (required, currently: `html`) |
| `--handle` | Filter by Slack user handle |
| `--date-from` | Filter start datetime (ISO format with timezone, e.g. `2026-01-01T00:00:00+00:00`) |
| `--date-to` | Filter end datetime (ISO format with timezone) |
| `--output` | Output directory (default: `./export-<agent-name>`) |

Requires persistent storage (file-based SQLite or PostgreSQL).

## export-usage

Export per-conversation usage data as CSV. One row per conversation with aggregated token counts, cost, and metadata.

```bash
slack-agents export-usage agents/<name> --format=csv --output=usage.csv [options]
```

Options:

| Flag | Description |
|------|-------------|
| `--format` | Export format (required, currently: `csv`) |
| `--handle` | Filter by Slack user handle |
| `--date-from` | Filter start datetime (ISO format with timezone, e.g. `2026-01-01T00:00:00+00:00`) |
| `--date-to` | Filter end datetime (ISO format with timezone) |
| `--output` | Output CSV file path (required) |

Requires persistent storage (file-based SQLite or PostgreSQL).

## build-docker

Build a Docker image for an agent.

```bash
slack-agents build-docker agents/<name> [options]
```

Options:

| Flag | Description |
|------|-------------|
| `--push REGISTRY` | Push image to registry after building (e.g. `registry.example.com`) |
| `--image-name NAME` | Custom image name (default: `slack-agents-<agent-dir-name>`) |
| `--platform` | Target platform (default: `linux/amd64`) |

The image tag is `<image-name>:<version>`, where version comes from `config.yaml`. The default image name is `slack-agents-<agent-dir-name>`. When `--push` is provided, the registry is prepended.
