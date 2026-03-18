# LLM Providers

## Built-in Providers

Two providers ship with the framework: `slack_agents.llm.anthropic` (Claude) and `slack_agents.llm.openai` (OpenAI and compatible APIs).

### OpenAI-compatible providers

Many providers expose an OpenAI-compatible API (Mistral, Groq, Together, Ollama, vLLM, etc.). Use the built-in `slack_agents.llm.openai` provider with `base_url` to point at them:

```yaml
llm:
  type: slack_agents.llm.openai
  model: mistral-small-latest
  api_key: "{MISTRAL_API_KEY}"
  base_url: "https://api.mistral.ai/v1"
  max_tokens: 4096
  max_input_tokens: 32000
  input_cost_per_million: 0.1   # optional — USD per 1M input tokens
  output_cost_per_million: 0.3  # optional — USD per 1M output tokens
```

`input_cost_per_million` and `output_cost_per_million` are optional. When provided, they're used for cost estimation. When omitted, the built-in cost table is checked (covers native OpenAI models). If neither matches, cost estimation returns `None`.

## Adding a Custom Provider

LLM providers are Python modules that export a `Provider` class extending `BaseLLMProvider`.

### Example

```python
# my_llm/gemini.py
from slack_agents.llm.base import BaseLLMProvider, LLMResponse, Message, StreamEvent

class Provider(BaseLLMProvider):
    def __init__(self, model: str, api_key: str, max_tokens: int, max_input_tokens: int):
        self.model = model
        self.max_tokens = max_tokens
        self.max_input_tokens = max_input_tokens
        # Initialize your client here

    def estimate_cost(self, input_tokens, output_tokens,
                      cache_creation_input_tokens=0, cache_read_input_tokens=0):
        # Return estimated cost in USD, or None
        return None

    async def complete(self, messages, system_prompt="", tools=None):
        # Return LLMResponse
        ...

    async def stream(self, messages, system_prompt="", tools=None):
        # Yield StreamEvent objects
        ...
```

### Configuration

```yaml
llm:
  type: my_llm.gemini
  model: gemini-2.0-flash
  api_key: "{GEMINI_API_KEY}"
  max_tokens: 4096
  max_input_tokens: 200000
```

### Key Points

- Internal message format is Anthropic-style (content as list of typed blocks)
- Convert to your provider's format at the boundary (see `openai.py` for an example)
- `stream()` must yield `StreamEvent` objects with types: `text_delta`, `tool_use_start`, `tool_use_delta`, `tool_use_end`, `message_end`
- `estimate_cost()` returns USD cost or None if unknown
