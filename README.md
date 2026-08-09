# 小诺 · Knoa

A personal computer agent with ReAct reasoning, multi-LLM support, tool calling, and a Rich terminal UI.

## Features

- **ReAct Agent Loop** — Reasoning + Acting pattern with configurable max iterations
- **Stochastic-Deterministic Boundary (SDB)** — Typed verifier gate between LLM proposals and tool execution
- **Plan-Execute** — Optional planning phase for complex multi-step tasks
- **Multi-LLM** — llama.cpp, OpenAI, Anthropic, any OpenAI-compatible API
- **Split Vision Runtime** — text-only main models can inspect images through a dedicated local Qwen-VL/llama.cpp perception tool
- **Model Adapter Layer** — Canonical IR + per-provider payload/response parsers + provider profiles (endpoint, headers, capabilities)
- **Prompt Caching** — Cache-friendly static prefix (system + tool schemas + history); Anthropic `cache_control` blocks for prompt caching
- **Token Calibration** — Per-call token estimation calibrated against real usage
- **Built-in Tools** — Local automation, memory, scheduling, vision observation, screenshots, and managed file preparation
- **Canonical Tool Definitions** — Built-in and MCP tools share one MCP-compatible `inputSchema`/`outputSchema` contract
- **Core Artifact Delivery** — Tools produce opaque artifacts; Feishu/TUI/service clients adapt standard artifact events without channel logic in the Agent
- **Durable Tasks** — Core-owned Task state, persistent event replay, connection-independent execution, durable approval, safe-boundary pause/resume, Attempt/ToolStep checkpoints, and fail-closed restart recovery
- **Durable Automation** — Persistent one-time/interval/Cron schedules and authenticated business triggers create idempotent Tasks through bounded delivery dispatchers
- **MCP Extension Runtime** — Failure-isolated official SDK clients for Streamable HTTP and supervised local stdio packages
- **Selective Skill Packages** — Safe data-only packages activated by request, available tools, and granted capabilities
- **Typed Capability Inventory** — Principal-filtered tool origin, policy, risk, and confirmation metadata for management clients
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

### Multiple providers and models (`~/.pc-assistant/config/local.yaml`)

The private per-user config lives outside the source tree. A provider entry
represents one API account, while a model entry references that account. This
permits multiple keys for the same vendor as well as multiple models behind
one key.

```yaml
providers:
  ark_coding_primary:
    driver: "openai_compatible"
    api_base: "https://ark.cn-beijing.volces.com/api/coding/v3"
    api_key_env: "ARK_CODING_API_KEY"

  ark_coding_backup:
    driver: "openai_compatible"
    api_base: "https://ark.cn-beijing.volces.com/api/coding/v3"
    api_key_env: "ARK_CODING_BACKUP_API_KEY"

  local_qwen:
    driver: "llamacpp"
    server_url: "http://127.0.0.1:8192"

  keyless_openai_compatible_server:
    driver: "openai_compatible"
    api_base: "http://127.0.0.1:8080/v1"
    requires_api_key: false

models:
  coding_primary:
    provider: "ark_coding_primary"
    model: "replace-with-ark-model-id"
    supports_vision: false
    thinking:
      type: "enabled"

  coding_backup:
    provider: "ark_coding_backup"
    model: "replace-with-ark-model-id"
    supports_vision: false

  local_vision:
    provider: "local_qwen"
    model: "qwen-vl"
    supports_vision: true

default_model: "coding_primary"
vision_model: "local_vision"
fallback_enabled: true
# Optional; otherwise the first llamacpp model is selected automatically.
fallback_model: "local_vision"
vision_enabled: true
```

When the primary model request fails before producing any output, the agent
automatically retries the request with the configured local `llamacpp` fallback.
A partially streamed response is never replayed to avoid duplicated text or
tool calls. Set `fallback_enabled: false` to disable this behavior.

Use `api_key_env` when the service receives its environment from a process
manager. For a standalone local installation, `api_key` may instead be stored
directly in `~/.pc-assistant/config/local.yaml`; protect that file with
`chmod 600`.

If no `providers`/`models` catalog is configured, the original single-model
fields in `config/default.yaml` remain the fallback.

The Core API uses one token-authenticated WebSocket listener on
`127.0.0.1:9527` by default. Local CLI/TUI clients use an automatically managed
credential stored with mode `0600` at
`~/.pc-assistant/config/service.token`. Setting `service_token` adds a separate
credential for scoped clients; it is not required for local operation.

### MCP extensions

Remote MCP servers are explicitly configured with a local policy for every
tool Knoa may expose:

```yaml
mcp_servers:
  knowledge:
    enabled: true
    transport: streamable_http
    url: "https://127.0.0.1:9000/mcp"
    tools:
      search_documents:
        effect: read_only
        capabilities: [network]
        risk: low
```

Manually imported local MCP packages live under
`~/.pc-assistant/mcp/<server-id>/`. Each package contains a bounded `mcp.yaml`
manifest and its local server files. Local packages use the official MCP stdio
transport in an independent child process. The manifest declaratively defines
the command, arguments, package-confined working directory and timeout; Core
contains no server-specific launch branches. Only a minimal base environment plus explicitly named
`inherit_env` values reaches the child process. Server metadata never grants
authority: unconfigured tools remain hidden and all enabled tools still pass
schema validation, capability checks, confirmation and cancellation through
the standard `ToolStep` boundary.

This process boundary isolates crashes and dependency conflicts. Strong
resource or host-access containment is a separate OS-level sandbox policy and
is not implied by stdio process isolation.

When explicitly requested, the Agent can call the confirmation-gated
`mcp_import` Built-in Tool with an existing local package directory. Knoa
validates and snapshots the package, omits hidden metadata and symlinks,
rejects size-limit violations, installs it atomically,
and activates the MCP provider without restarting Core. Newly registered tools
are visible to the next model iteration in the same Task attempt. Network download and
marketplace trust remain separate future concerns; this tool imports local
packages only.

Enable the independently mounted Feishu channel in
`~/.pc-assistant/config/local.yaml`:

```yaml
feishu_enabled: true
feishu_app_id: "cli_xxx"
feishu_app_secret: "..."
```

Feishu connects to Core only through `CoreClient`; Core does not import the
channel package. Each sender is mapped to a separate signed principal and
opaque Core session.
The Channel adds and removes the Feishu typing reaction, projects standard Core
events into one live-updated card, and sends long final answers as lossless
continuation cards instead of truncating model output. It also follows the
durable principal Task feed with a persisted cursor, so Schedule and Trigger
results are delivered proactively without coupling Feishu to Core automation.

An optional HTTP webhook adapter can feed configured durable Triggers without
adding HTTP parsing to Core. Keep it on loopback and expose it only through a
TLS reverse proxy:

```yaml
webhook_enabled: true
webhook_host: "127.0.0.1"
webhook_port: 9528
webhook_routes:
  gitlab:
    trigger_id: "trg_..."
    principal_id: "personal:feishu:..."
    secret_env: "KNOA_GITLAB_WEBHOOK_SECRET"
```

Send JSON to `POST /hooks/gitlab` with `X-Knoa-Event-Id` and
`X-Knoa-Signature: sha256=<hex>`. The signature is HMAC-SHA256 over
`event_id + "\\n" + raw_request_body`; route secrets must contain at least 32
bytes. Reusing an event ID is safe because Trigger ingress is durably
idempotent.

### Environment variables

All config fields can be overridden with `PC_` prefix:

| Variable | Field |
|----------|-------|
| `PC_LLM_PROVIDER` | llm_provider |
| `PC_DEFAULT_MODEL` | default_model |
| `PC_VISION_MODEL` | vision_model |
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
| `PC_WEBHOOK_ENABLED` | webhook_enabled |
| `PC_WEBHOOK_HOST` | webhook_host |
| `PC_WEBHOOK_PORT` | webhook_port |

### Runtime config

Use `/config set key=value` in the chat to change settings at runtime.

### Runtime data

By default, mutable application state is kept outside the source tree under
`~/.pc-assistant/`:

```text
~/.pc-assistant/
├── logs/          # application, service, audit, and trace logs
├── attachments/   # temporary inbound and generated artifacts
├── artifacts/     # persistent user-requested generated files
├── cache/         # idempotency and other rebuildable state
├── data/          # assistant.db and procedural memory
├── skills/        # manually imported data-only Skill packages
└── mcp/           # manually imported local stdio MCP packages
```

Set `PC_ASSISTANT_HOME` (or `PC_RUNTIME_ROOT`) to override this root. Service
socket and PID files use the operating system runtime directory.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/exit` | Exit |
| `/new` | Start a new conversation while preserving the old one |
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
| `system` | info, disk_usage | System information |
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
| `screenshot` | — | Capture a full-desktop PNG for delivery to the current conversation |
| `artifact_prepare` | path | Borrow an existing file for client delivery without copying or deleting it; protected paths are blocked and out-of-workspace paths require confirmation |
| `mcp_import` | path, server_id | Validate, atomically install, and activate a local MCP package after explicit confirmation |
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
├── artifacts/
│   └── store.py         # Session-scoped user-deliverable artifact registry
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
│   ├── base.py          # Tool contract, capability/effect/risk and origin
│   ├── registry.py      # Validated name→tool map with origin ownership
│   ├── describe_tool.py # Meta-tool: full schema for any registered tool
│   ├── image_inspect.py # Structured image observation by attachment ID
│   └── ...              # 16 built-in tool implementations
├── extensions/
│   ├── manager.py       # Failure-isolated extension lifecycle
│   ├── models.py        # Strict local MCP policy configuration
│   ├── mcp.py           # Official HTTP/stdio MCP clients and ToolBase adapter
│   ├── mcp_package.py   # Confined local package discovery and manifest loading
│   └── skill.py         # Safe Skill loading, indexing and selective activation
├── skill_packages/
│   └── research_report/ # Built-in research workflow instructions
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
│   ├── feishu.py        # CoreClient-based Feishu/Lark adapter
│   └── __init__.py      # Channel exports
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
