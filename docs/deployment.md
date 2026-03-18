# Deployment

## Overview

Each agent runs as a single long-running process connected to Slack via Socket Mode (WebSocket). One process = one agent = one Slack app.

All configuration is in `config.yaml`. Secrets use `{ENV_VAR}` placeholders resolved from environment variables at startup.

## Docker

Build a Docker image for any agent with the CLI:

```bash
slack-agents build-docker agents/my-agent
```

This produces an image tagged `slack-agents-my-agent:<version>` (version comes from `config.yaml`). The image runs `slack-agents run agent` on startup.

To use a custom image name:

```bash
slack-agents build-docker agents/my-agent --image-name my-bot
```

To push to a registry:

```bash
slack-agents build-docker agents/my-agent --push registry.example.com
```

### docker-compose

A minimal setup for running an agent locally or on a single server:

```yaml
services:
  my-agent:
    image: slack-agents-my-agent:1.0.0
    restart: unless-stopped
    env_file: .env
```

With PostgreSQL for persistent conversations:

```yaml
services:
  my-agent:
    image: slack-agents-my-agent:1.0.0
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql://agent:secret@db:5432/agents
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: agents
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: pg_isready -U agent
      interval: 5s
      retries: 5

volumes:
  pgdata:
```

## Kubernetes

Socket Mode requires exactly one WebSocket connection per Slack app. Run each agent as a Deployment with **1 replica** (`replicas: 1`, or `minReplicas: 1` / `maxReplicas: 1` if using an autoscaler).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: my-agent
  template:
    metadata:
      labels:
        app: my-agent
    spec:
      containers:
        - name: agent
          image: registry.example.com/slack-agents-my-agent:1.0.0
          envFrom:
            - secretRef:
                name: my-agent-secrets
          livenessProbe:
            exec:
              command: ["slack-agents", "healthcheck", "agent"]
            initialDelaySeconds: 30
            periodSeconds: 30
          resources:
            requests:
              memory: 256Mi
              cpu: 100m
            limits:
              memory: 512Mi
```

### Secrets

Store tokens and API keys in a Kubernetes Secret and reference it via `envFrom`. The agent resolves `{ENV_VAR}` patterns in `config.yaml` from environment variables.

```bash
kubectl create secret generic my-agent-secrets \
  --from-literal=SLACK_BOT_TOKEN=xoxb-... \
  --from-literal=SLACK_APP_TOKEN=xapp-... \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-...
```

### Health checks

The `slack-agents healthcheck` command checks the agent's WebSocket heartbeat (written every 10s to storage). It requires persistent storage (file-based SQLite or PostgreSQL). Use it as a liveness probe — Kubernetes will restart the pod if the connection drops.

## Multiple agents

Each agent is independent — its own Slack app, its own Docker image, its own deployment. To run several agents, repeat the pattern for each one. They share nothing at runtime.
