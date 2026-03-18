# Setup

## Prerequisites

- Python 3.12+
- A Slack workspace (all plans supported, including free)
- API key for your LLM provider (Anthropic and/or OpenAI)

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your tokens:

```
SLACK_BOT_TOKEN=xoxb-...  # see below
SLACK_APP_TOKEN=xapp-...  # see below
ANTHROPIC_API_KEY=sk-ant-...
```

## Creating a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create a new app from the manifest in `docs/slack-app-manifest.json`
- Update 4 placeholders starting with ** for names and descriptions
- Settings > Basic Information > App-Level Tokens > Generate Tokens and Scopes
  - token name: "slack-agents-app-token"
  - add scope: add "connections:write"
  - click "Generate"
  - Copy: App Token (eg, SLACK_APP_TOKEN=xapp-...)
- Settings > Install App
  - Copy: Bot User OAuth Token (eg, SLACK_BOT_TOKEN=xoxb-...)
3. If App does not appeat in your Slack client:
  - ... > Tools > Apps > (search by name and add the app)

## Download Fonts

PDF generation requires DejaVu Sans for Unicode support:

```bash
python -m slack_agents.scripts.download_fonts
```

This downloads `DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` into `fonts/` (~700KB total). Without these fonts, PDF generation falls back to Helvetica (latin-1 only).

## Running an Agent

```bash
slack-agents run agents/hello-world
```

## Optional: PostgreSQL

For conversation persistence via PostgreSQL, update your agent's `config.yaml`:

```yaml
storage:
  type: slack_agents.storage.postgres
  url: "{DATABASE_URL}"
```

Set `DATABASE_URL` in your `.env` file.
