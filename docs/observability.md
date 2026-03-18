# Observability

Agents can export traces via [OpenTelemetry](https://opentelemetry.io/) (OTLP/HTTP). This works with any OTLP-compatible backend: Langfuse, Jaeger, Datadog, Grafana Tempo, etc.

Observability is configured per-agent in `config.yaml`. If the `observability` section is omitted, tracing is disabled.

## Configuration

Add an `observability` section to your agent's `config.yaml`:

```yaml
observability:
  endpoints:
    - type: otlp
      endpoint: "https://otel-collector.internal:4318/v1/traces"
      headers:
        - key: Authorization
          value: "Bearer {OTEL_TOKEN}"
      attributes:
        trace_name: "my.trace.name"
        user_id: "enduser.id"
        model: "gen_ai.response.model"
        input_tokens: "gen_ai.usage.input_tokens"
        output_tokens: "gen_ai.usage.output_tokens"
```

### Endpoint fields

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Endpoint type (currently `otlp`) |
| `endpoint` | yes | OTLP/HTTP endpoint URL |
| `headers` | no | List of `{key, value}` headers sent with each export |
| `basic_auth` | no | `{user, password}` — auto-constructs a `Basic` auth header |
| `attributes` | no | Semantic key to OTEL attribute name mapping (see below) |

### Attribute mapping

The `attributes` dict maps semantic keys used in the code to OTEL span attribute names expected by your backend. Only keys present in the mapping are set on spans — unmapped keys are silently ignored.

Available semantic keys:

| Semantic key | Set by | Description |
|-------------|--------|-------------|
| `trace_name` | bot.py | Agent name |
| `user_id` | bot.py | Slack user's display name |
| `session_id` | bot.py | `{channel_name}.{thread_id}` |
| `version` | bot.py | Agent version from config |
| `input` | bot.py | User message text |
| `output` | bot.py | Assistant response text |
| `observation_type` | @observe decorator | Span type (e.g. `"generation"`) |
| `model` | LLM providers | Model ID (e.g. `claude-sonnet-4-6`) |
| `input_tokens` | LLM providers | Total input token count (including cached) |
| `output_tokens` | LLM providers | Output token count |
| `usage` | LLM providers | Token breakdown as JSON: `{input, output, cache_read_input, cache_creation_input}` |

### Multiple endpoints

Each endpoint has its own attribute mapping. When sending to multiple backends, each backend's attributes are all set on the same span — backends ignore attributes they don't recognize.

```yaml
observability:
  endpoints:
    - type: otlp
      endpoint: "https://langfuse.example.com/api/public/otel/v1/traces"
      basic_auth:
        user: "{LANGFUSE_PUBLIC_KEY}"
        password: "{LANGFUSE_SECRET_KEY}"
      attributes:
        trace_name: "langfuse.trace.name"
        user_id: "langfuse.user.id"
        model: "langfuse.observation.model.name"
    - type: otlp
      endpoint: "https://jaeger.internal:4318/v1/traces"
      attributes:
        user_id: "enduser.id"
        model: "gen_ai.response.model"
```

## Langfuse

[Langfuse](https://langfuse.com) supports native OTLP ingestion. Use `basic_auth` with your Langfuse public/secret keys and point the endpoint at `/api/public/otel/v1/traces`.

```yaml
observability:
  endpoints:
    - type: otlp
      endpoint: "{LANGFUSE_HOST}/api/public/otel/v1/traces"
      basic_auth:
        user: "{LANGFUSE_PUBLIC_KEY}"
        password: "{LANGFUSE_SECRET_KEY}"
      attributes:
        trace_name: "langfuse.trace.name"
        user_id: "langfuse.user.id"
        session_id: "langfuse.session.id"
        version: "langfuse.version"
        observation_type: "langfuse.observation.type"
        input: "langfuse.observation.input"
        output: "langfuse.observation.output"
        model: "langfuse.observation.model.name"
        input_tokens: "gen_ai.usage.input_tokens"
        output_tokens: "gen_ai.usage.output_tokens"
        usage: "langfuse.observation.usage_details"
```

Add the credentials to `.env`:

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

The `langfuse.*` attribute names are documented in [Langfuse's OpenTelemetry integration docs](https://langfuse.com/docs/integrations/opentelemetry).

## Architecture

The implementation is a thin wrapper around the OpenTelemetry SDK:

- **`observability.py`** creates a `TracerProvider` with one `OTLPSpanExporter` per endpoint
- **`@observe(name=...)`** decorator creates OTEL spans around functions (supports sync, async, and async generators)
- **`set_span_attrs()`** sets attributes on the current span using the configured mapping
- **`flush_trace()`** calls `TracerProvider.force_flush()`

The code has zero backend-specific knowledge — all attribute naming is driven by `config.yaml`.
