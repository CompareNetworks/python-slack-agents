# Tools

There are two kinds of tool providers, both configured under `tools:` in `config.yaml`:

- **Tool providers** (`BaseToolProvider`) — tools the LLM can call during a conversation (e.g. search, export a PDF, run a calculation)
- **File importer providers** (`BaseFileImporterProvider`) — handlers that process files attached to Slack messages before they reach the LLM (e.g. extract text from a PDF, parse an Excel spreadsheet)

Both use the same `allowed_functions` regex filtering and are loaded as Python modules with a `Provider` class.

## Tool Providers

Tool providers give the LLM callable tools. Extend `BaseToolProvider`:

```python
# my_tools/calculator.py
from slack_agents.tools.base import (
    ERROR_INPUT_ERROR,
    RECOVERY_ABORT,
    BaseToolProvider,
    ToolResult,
    make_tool_error,
)

class Provider(BaseToolProvider):
    def __init__(self, allowed_functions: list[str]):
        super().__init__(allowed_functions)

    def _get_all_tools(self) -> list[dict]:
        return [
            {
                "name": "add",
                "description": "Add two numbers",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            }
        ]

    async def call_tool(self, name, arguments, user_context, storage) -> ToolResult:
        if name == "add":
            result = arguments["a"] + arguments["b"]
            return {"content": str(result), "is_error": False, "files": []}
        return make_tool_error(
            error=ERROR_INPUT_ERROR,
            code="unknown_tool",
            tool=name,
            recovery=RECOVERY_ABORT,
            message=f"Tool {name!r} is not provided by this calculator.",
        )
```

### Key points

- `_get_all_tools()` returns tool definitions in Anthropic API format
- `allowed_functions` filtering is handled by the base class
- `call_tool(name, arguments, user_context, storage)` returns a `ToolResult` (`{"content": str, "is_error": bool, "files": list[OutputFile]}`)
- Files in the response are uploaded to Slack automatically
- `initialize()` and `close()` are optional lifecycle hooks
- For error returns, use `make_tool_error(...)` so the LLM gets a structured payload it can interpret consistently. See [Tool error schema](#tool-error-schema) below.

## File Importer Providers

File importer providers process files that users attach to Slack messages. They are invisible to the LLM — the framework calls them automatically to convert files into content the LLM can understand.

Extend `BaseFileImporterProvider`:

```python
# my_tools/csv_importer.py
from slack_agents import InputFile
from slack_agents.tools.base import BaseFileImporterProvider, ContentBlock, FileImportToolException

class Provider(BaseFileImporterProvider):
    def _get_all_tools(self) -> list[dict]:
        return [
            {
                "name": "import_csv",
                "mimes": {"text/csv"},
                "max_size": 5_000_000,
            }
        ]

    async def call_tool(self, name, arguments, user_context, storage) -> ContentBlock:
        if name == "import_csv":
            text = arguments["file_bytes"].decode("utf-8", errors="replace")
            return {"type": "text", "text": f"[File: {arguments['filename']}]\n\n{text}"}
        raise FileImportToolException(f"Unknown handler: {name}")
```

### Tool manifest fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Handler name, matched against `allowed_functions` (e.g. `import_csv`) |
| `mimes` | `set[str]` | MIME types this handler processes |
| `max_size` | `int` | Maximum file size in bytes |

### call_tool arguments

`call_tool()` receives an `InputFile` dict (with keys `file_bytes`, `mimetype`, `filename`) as the `arguments` parameter, plus `user_context` and `storage`. Return a `ContentBlock` dict that will be included in the user message sent to the LLM:

- Text: `{"type": "text", "text": "..."}`
- Image: `{"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}`
- Raise `FileImportToolException` if extraction fails (the framework catches this and logs the error)

### Built-in handlers

The built-in provider (`slack_agents.tools.file_importer`) handles PDF, DOCX, XLSX, PPTX, plain text, and images.

## MCP over HTTP

`slack_agents.tools.mcp_http` connects to any MCP server over HTTP. Tools are auto-discovered at startup.

```yaml
tools:
  my-mcp-server:
    type: slack_agents.tools.mcp_http
    url: "https://my-server.example.com/mcp"
    headers:
      Authorization: "Bearer {MCP_API_TOKEN}"
    allowed_functions:
      - "search_.*"
      - "get_document"
    init_retries: [5, 10, 30]
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `str` | required | MCP server endpoint |
| `headers` | `dict` | `{}` | HTTP headers sent with every request |
| `allowed_functions` | `list[str]` | required | Regex patterns to filter discovered tools |
| `init_retries` | `list[int]` | `[5, 10, 30]` | Seconds to wait between connection retries at startup. The server is tried once immediately, then once after each delay. Set to `[]` to disable retries. |

All MCP tool providers are initialized in parallel at startup. If any provider fails to connect after exhausting its retries, the agent exits with an error.

## MCP over HTTP with OAuth

`slack_agents.tools.mcp_http_oauth` is the OAuth-authenticated counterpart to
`mcp_http` — for MCP servers that require per-user OAuth 2.1 authentication
rather than a static API key. See [`docs/oauth.md`](oauth.md) for the full
guide. Minimal example:

```yaml
tools:
  my-mcp:
    type: slack_agents.tools.mcp_http_oauth
    url: "https://my-server.example.com/mcp"
    allowed_functions: [".*"]
```

This pulls in extra runtime requirements: `OAUTH_PUBLIC_URL`,
`OAUTH_SECRET_KEY`, and an in-process HTTP listener for OAuth callbacks. Read
the OAuth doc before configuring this in production.

## Tool error schema

When a tool returns `is_error: True`, every built-in tool emits a JSON-encoded
payload in `content` matching this shape, so the LLM consuming the result can
reason about the failure uniformly. Custom tools should produce the same shape
via the `make_tool_error` helper.

```json
{
  "error":    "<type>",        // required: e.g. "permission_denied"
  "code":     "<subtype>",     // optional: stable per-error sub-classification
  "tool":     "<tool name>",   // optional: which tool was being called
  "server":   "<server id>",   // optional: provider/server context
  "message":  "<human>",       // required: human-readable summary
  "recovery": "<action>",      // required: see below
  "details":  { ... }          // optional: free-form per-error-type
}
```

**Top-level error types** (`error`):

| Constant | When to use |
|---|---|
| `ERROR_PERMISSION_DENIED` | Auth/scope/role refusal — user-level |
| `ERROR_SYSTEM_ERROR` | Operational/library/transient failure |
| `ERROR_AUTH_SETUP_FAILED` | Auth flow itself broke (timeout, prompt failure) |
| `ERROR_INPUT_ERROR` | Bad call / unknown tool / bad arguments |

**Recovery actions** (`recovery`):

| Constant | Meaning |
|---|---|
| `RECOVERY_RETRY` | Transient or user-recoverable; just try again |
| `RECOVERY_CONTACT_ADMIN` | Requires realm/IdP/account admin |
| `RECOVERY_CONTACT_SUPPORT` | Framework operator/dev needs to investigate logs |
| `RECOVERY_ABORT` | Nothing to do for this call (LLM may try a different tool) |

**Helper signature:**

```python
from slack_agents.tools.base import make_tool_error  # plus ERROR_*, RECOVERY_*

return make_tool_error(
    error=ERROR_SYSTEM_ERROR,             # required
    message="Server returned 502.",       # required
    recovery=RECOVERY_RETRY,              # required
    code="upstream_502",                  # optional
    tool="search_docs",                   # optional
    server="my-mcp",                      # optional
    details={"status": 502},              # optional, schema-less
)
```

`details` is intentionally schema-less — each error type can carry whatever
structured fields the LLM benefits from seeing (missing scopes, exception
types, timestamps for support correlation, etc.).

## Configuration

Both types are configured the same way in `config.yaml`:

```yaml
tools:
  calculator:
    type: my_tools.calculator
    allowed_functions: [".*"]
  import-documents:
    type: slack_agents.tools.file_importer
    allowed_functions: [".*"]
```

The module must be importable from your Python path.
