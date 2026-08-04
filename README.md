# PC Assistant

A Python desktop AI agent with ReAct reasoning, multi-LLM support, tool calling, and a Rich terminal UI.

## Features

- **ReAct Agent Loop** — Reasoning + Acting pattern with configurable max iterations
- **Stochastic-Deterministic Boundary (SDB)** — Typed verifier gate between LLM proposals and tool execution
- **Plan-Execute** — Optional planning phase for complex multi-step tasks
- **Multi-LLM** — llama.cpp, OpenAI, Anthropic, any OpenAI-compatible API
- **Split Vision Runtime** — text-only main models can inspect images through a dedicated local Qwen-VL/llama.cpp perception tool
- **Model Adapter Layer** — Canonical IR + per-provider payload/response parsers + provider profiles (endpoint, headers, capabilities)
- **Prompt Caching** — Cache-friendly static prefix (system + tool schemas + history); Anthropic `cache_control` blocks for prompt caching
- **Token Calibration** — Per-call token estimation calibrated against real usage
- **16+ Built-in Tools** — Shell, Filesystem, Application, Web, System, Clipboard, Memory, Weather, Exchange, Timer, Window, Notification, Keyboard, Mouse, Scheduler, DescribeTool
- **MCP Adapter** — Register tools from any MCP-compatible server
- **Safety Guardrails** — Dangerous command blocking, protected paths, user confirmation, typed refusal codes
- **Idempotency** — Side-effecting tools are protected against duplicate execution on retry
- **Scoped Memory** — Principal-scoped core/relevant preferences plus session-scoped episodes in SQLite
- **Reflection** — Optional self-critique before yielding final answers
- **Rich TUI** — Streaming output, thinking visualization, status bar, slash commands
- **Feishu Bot** — Session-safe Feishu/Lark WebSocket integration
- **EventBus** — Pub/sub event bus for decoupled subscribers (audit, metrics, plugins)
- **Audit Logging** — JSONL audit trail for all tool actions
- **Observability** — LLM call traces, turn metrics, token calibration
- **Benchmark** — Weighted multi-dimension scoring with rule-based and LLM-judge evaluation

## Quick Start

```bash
# Install
pip install -e .

# Run with local llama.cpp server (default)
pca

# Run with OpenAI-compatible API
PC_LLM_PROVIDER=openai_compatible PC_LLM_API_BASE=http://localhost:11434/v1 PC_LLM_MODEL_NAME=qwen3 pca

# Run with Anthropic
PC_LLM_PROVIDER=anthropic PC_LLM_API_KEY=sk-ant-... pca

# Run with OpenAI
PC_LLM_PROVIDER=openai PC_LLM_API_KEY=sk-... PC_LLM_MODEL_NAME=gpt-4o pca
```

## Configuration

### Config file (`config/default.yaml`)

```yaml
llm_provider: "llamacpp"
llm_server_url: "http://127.0.0.1:8192"
llm_model_name: ""
llm_api_key: ""
llm_temperature: 0.7
llm_timeout: 120
vision_enabled: true
vision_provider: "llamacpp"
vision_server_url: "http://127.0.0.1:8192"
vision_model_name: ""
vision_timeout: 120
vision_max_tokens: 1024
max_iterations: 8
context_window_budget: 4096
reflection_enabled: false
reflection_threshold: 7
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
| `PC_VISION_ENABLED` | vision_enabled |
| `PC_VISION_PROVIDER` | vision_provider |
| `PC_VISION_SERVER_URL` | vision_server_url |
| `PC_VISION_MODEL_NAME` | vision_model_name |
| `PC_VISION_API_KEY` | vision_api_key |
| `PC_VISION_API_BASE` | vision_api_base |
| `PC_VISION_TIMEOUT` | vision_timeout |
| `PC_VISION_MAX_TOKENS` | vision_max_tokens |
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
| `PC_ASSISTANT_HOME` | runtime_root (friendly alias) |
| `PC_RUNTIME_ROOT` | runtime_root |

### Runtime config

Use `/config set key=value` in the chat to change settings at runtime.

### Runtime data

By default, mutable application state is kept outside the source tree under
`~/.pc-assistant/`:

```text
~/.pc-assistant/
├── logs/          # application, service, audit, and trace logs
├── attachments/   # temporary uploads and screenshots
├── cache/         # idempotency and other rebuildable state
└── data/          # assistant.db and procedural memory
```

Set `PC_ASSISTANT_HOME` (or `PC_RUNTIME_ROOT`) to override this root. Service
socket and PID files use the operating system runtime directory.

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
| `application` | launch, list_running, search, info, kill | Desktop app management |
| `web` | fetch, search | Web page fetching and search |
| `system` | info, screenshot, disk_usage | System info and screenshots |
| `clipboard` | read, write | Clipboard access |
| `memory` | store, retrieve, search, delete, store_episode, recall_episodes | Persistent user & episodic memory |
| `weather` | current, forecast | Weather data for any location |
| `exchange` | rate, convert, list | Currency exchange rates |
| `timer` | set, list, cancel, status, pause, resume, modify | Countdown timers and reminders |
| `window` | list, info, focus, move, resize, minimize, maximize, restore, close | Window management |
| `notification` | show, alert, reminder | System notifications |
| `keyboard` | press, type, hotkey, write, shortcut | Keyboard input control |
| `mouse` | position, move, click, double_click, right_click, scroll, drag | Mouse control |
| `scheduler` | create, list, run, delete, enable, disable, start, stop, status | Cron-like task scheduling |
| `image_inspect` | describe, ocr, locate, compare | Observe visible image content by `image_id`; diagnosis and solutions remain with the main model |
| `describe_tool` | tool_name | Meta-tool: query the full JSON schema of any registered tool |

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
├── agent.py             # ReAct agent loop with SDB, planning, reflection
├── eventbus.py          # Pub/sub event bus for decoupled subscribers
├── planner.py           # Plan-Execute layer for complex tasks
├── reflection.py        # Self-critique before final answer
├── llm_provider.py      # Facade: transport, per-session cancel, retry
├── model_adapter/
│   ├── types.py         # Canonical IR (LLMResponse, StreamChunk)
│   ├── profiles.py      # Per-provider endpoint/header/capability profiles
│   ├── retry.py         # Retry with backoff for transient HTTP failures
│   └── parsers/
│       ├── openai.py    # OpenAI-style payload & stream parsing
│       └── anthropic.py # Anthropic messages & SSE parsing
├── vision/
│   ├── broker.py        # Dedicated perception-only vision provider boundary
│   └── preprocess.py    # Image resize/encoding helpers
├── session.py           # Multi-session state with LRU eviction & rollback
├── config.py            # Pydantic config model + YAML + env overrides
├── exceptions.py        # Typed exception hierarchy
├── platform_.py         # Cross-platform utilities
├── logger.py            # Structured JSON logging
├── context/
│   ├── assembly.py      # Cache-friendly message assembly & truncation
│   ├── conversation.py  # Conversation history management
│   ├── memory.py        # Memory value types + procedural/legacy adapters
│   ├── memory_db.py     # Principal/session-scoped SQLite memory repository
│   ├── scope.py         # Request-local principal_id + session_id
│   ├── prompt.py        # System prompt & session context builders
│   ├── compact.py       # Heuristic history compression
│   ├── llm_compact.py   # LLM-assisted compaction (optional)
│   ├── filter.py        # Stale content trimming
│   ├── tags.py          # XML context wrappers
│   ├── cache.py         # Prompt cache planning
│   ├── token_estimate.py# Token estimation with runtime calibration
│   └── evidence.py      # Evidence policy for factual queries
├── tools/
│   ├── base.py          # ToolBase ABC (is_side_effecting flag)
│   ├── registry.py      # Name→tool map with MCP server registration
│   ├── mcp_adapter.py   # MCP protocol tool adapter
│   ├── describe_tool.py # Meta-tool: full schema for any registered tool
│   ├── image_inspect.py # Structured image observation by attachment ID
│   └── ...              # 16 built-in tool implementations
├── harness/
│   ├── verifier.py      # SDB: deterministic verifier (propose→verify→commit)
│   ├── refusal.py       # Typed refusal codes & Verdict
│   ├── safety.py        # Command/path safety policy rules
│   ├── idempotency.py   # Side-effect dedup with hash-based keys
│   ├── limiter.py       # Sliding-window rate limiting
│   └── audit.py         # JSONL audit trail
├── observability/
│   └── trace.py         # LLM call & turn tracing
├── channels/
│   ├── base.py          # Channel ABC
│   ├── feishu.py        # Feishu/Lark bot (session-safe)
│   └── __init__.py      # Config-driven channel creation
├── benchmark/
│   ├── runner.py        # Benchmark executor
│   ├── scorer.py        # Rule-based scoring
│   ├── evaluator.py     # LLM judge scoring
│   ├── reporter.py      # Markdown report generator
│   ├── dataset.py       # JSONL dataset loader
│   └── types.py         # Data models
└── ui/
    ├── chat.py          # Full-screen Rich TUI
    ├── state.py         # UI state management
    └── theme.py         # Tokyo Night theme
```

## License

MIT
