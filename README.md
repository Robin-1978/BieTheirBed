# PC Assistant

A Python desktop AI agent with ReAct reasoning, multi-LLM support, tool calling, and a Rich terminal UI.

## Features

- **ReAct Agent Loop** — Reasoning + Acting pattern with configurable max iterations
- **Multi-LLM** — llama.cpp, OpenAI, Anthropic, any OpenAI-compatible API
- **7 Built-in Tools** — Shell, Filesystem, Application, Web, System, Clipboard, Memory
- **Safety Guardrails** — Dangerous command blocking, protected paths, user confirmation
- **User Memory** — Auto-extracts preferences, persists across sessions
- **Rich TUI** — Streaming output, thinking visualization, status bar, slash commands
- **Audit Logging** — JSONL audit trail for all tool actions
- **Rate Limiting** — Sliding window per-key rate limits

## Quick Start

```bash
# Install
pip install -e .

# Run with local llama.cpp server (default)
pc-assistant

# Run with OpenAI-compatible API
PC_LLM_PROVIDER=openai_compatible PC_LLM_API_BASE=http://localhost:11434/v1 PC_LLM_MODEL_NAME=qwen3 pc-assistant

# Run with Anthropic
PC_LLM_PROVIDER=anthropic PC_LLM_API_KEY=sk-ant-... pc-assistant

# Run with OpenAI
PC_LLM_PROVIDER=openai PC_LLM_API_KEY=sk-... PC_LLM_MODEL_NAME=gpt-4o pc-assistant
```

## Configuration

### Config file (`config/default.yaml`)

```yaml
llm_provider: "llamacpp"
llm_server_url: "http://127.0.0.1:8080"
llm_model_name: ""
llm_api_key: ""
llm_temperature: 0.7
llm_timeout: 120
max_iterations: 8
context_window_budget: 4096
```

### Environment variables

All config fields can be overridden with `PC_` prefix:

| Variable | Field |
|----------|-------|
| `PC_LLM_PROVIDER` | llm_provider |
| `PC_LLM_SERVER_URL` | llm_server_url |
| `PC_LLM_MODEL_NAME` | llm_model_name |
| `PC_LLM_API_KEY` | llm_api_key |
| `PC_LLM_API_BASE` | llm_api_base |
| `PC_LLM_TEMPERATURE` | llm_temperature |
| `PC_LLM_TIMEOUT` | llm_timeout |
| `PC_MAX_ITERATIONS` | max_iterations |
| `PC_SHELL_TIMEOUT` | shell_timeout |
| `PC_CONTEXT_WINDOW_BUDGET` | context_window_budget |
| `PC_TOKEN_FAMILY` | token_family |
| `PC_LLM_COMPACT_ENABLED` | llm_compact_enabled |
| `PC_MAX_SESSIONS` | max_sessions |
| `PC_TRACE_ENABLED` | trace_enabled |
| `PC_LLM_TRACE_LOG` | llm_trace_log |
| `PC_TURN_TRACE_LOG` | turn_trace_log |
| `PC_EVIDENCE_POLICY_ENABLED` | evidence_policy_enabled |

### Runtime config

Use `/config set key=value` in the chat to change settings at runtime.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/exit` | Exit |
| `/clear` | Clear conversation history |
| `/memory` | Show stored user preferences |
| `/memory clear` | Wipe all memories |
| `/history` | Show conversation history |
| `/tools` | List available tools |
| `/status` | Show agent status |
| `/config` | Show current configuration |
| `/config set key=value` | Set a config field |

## Tools

| Tool | Actions | Description |
|------|---------|-------------|
| `shell` | command | Execute shell commands with timeout |
| `filesystem` | read, write, list, mkdir, delete, copy, move, exists | File operations |
| `application` | launch, list_running, kill | Desktop app management |
| `web` | fetch, search | Web page fetching and search |
| `system` | info, screenshot, disk_usage | System info and screenshots |
| `clipboard` | read, write | Clipboard access |
| `memory` | store, retrieve, search, delete | Persistent user memory |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=pc_assistant --cov-report=term-missing
```

## Architecture

```
src/pc_assistant/
├── agent.py             # ReAct agent loop
├── llm_provider.py      # Multi-provider LLM abstraction
├── config.py            # Pydantic config model
├── exceptions.py        # Custom exception hierarchy
├── platform_.py         # Cross-platform utilities
├── logger.py            # Structured JSON logging
├── context/
│   ├── conversation.py  # Conversation history
│   ├── memory.py        # User memory persistence
│   ├── system_prompt.py # System prompt builder
│   └── truncator.py     # Context window truncation
├── tools/               # Tool implementations
├── harness/
│   ├── safety.py        # Command/path safety checks
│   ├── limiter.py       # Rate limiting
│   └── audit.py         # Audit logging
└── ui/
    └── chat.py          # Rich terminal UI
```

## License

MIT
