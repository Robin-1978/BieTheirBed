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

原生 Windows 同机运行 Hosted Hub 与 Node（Python 3.14 + venv，无需 WSL）见
[`deploy/windows/README.md`](deploy/windows/README.md)。

```bash
# Install
pip install -e .

# Run with local llama.cpp server (default)
knoa

# Development checkout: run current source without installing a wheel
PYTHONPATH=src python -m knoa_platform

# Or install a PATH symlink to the source-backed development launcher
ln -s "$PWD/scripts/knoa" ~/.local/bin/knoa
knoa --restart

# Start/restart the background service from the current checkout
scripts/service-start.sh
scripts/service-restart.sh

# Run with OpenAI-compatible API
KNOA_LLM_PROVIDER=openai_compatible KNOA_LLM_API_BASE=http://localhost:11434/v1 KNOA_LLM_MODEL_NAME=qwen3 knoa

# Run with Anthropic
KNOA_LLM_PROVIDER=anthropic KNOA_LLM_API_KEY=sk-ant-... knoa

# Run with OpenAI
KNOA_LLM_PROVIDER=openai KNOA_LLM_API_KEY=sk-... KNOA_LLM_MODEL_NAME=gpt-4o knoa
```

## Configuration

### Multiple providers and models (`~/.knoa/config/local.yaml`)

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

### Approval reviewer Agent

Knoa can route approvals that already passed deterministic Tool Policy checks
to a restricted system Agent. The reviewer uses the same Agent Runtime and
model-provider stack as other Agents, but it is not selectable for a Session,
receives no Tools, and cannot resolve an Approval directly.

```yaml
agents:
  reviewer_agent:
    enabled: true
    max_concurrency: 1
    model: "local_qwen_reviewer"

approval_review:
  mode: "suggest" # off | suggest | auto
  agent: "reviewer_agent"
  model: "local_qwen_reviewer"
  timeout_seconds: 15
  max_output_tokens: 256
  auto_max_risk: "medium"
```

`suggest` records the reviewer's `approve`, `deny`, or `escalate` recommendation
on the existing Approval and still waits for a person. `auto` may resolve only
low/medium-risk Approvals within the configured ceiling; high-risk operations
always remain human-gated. Invalid output, timeout, or missing context always
falls back to human review. Tool Policy, scope checks, stale-Approval checks,
and the single ToolStep commit boundary remain authoritative.

When the primary model request fails before producing any output, the agent
automatically retries the request with the configured local `llamacpp` fallback.
A partially streamed response is never replayed to avoid duplicated text or
tool calls. Set `fallback_enabled: false` to disable this behavior.

Use `api_key_env` when the service receives its environment from a process
manager. For a standalone local installation, `api_key` may instead be stored
directly in `~/.knoa/config/local.yaml`; protect that file with
`chmod 600`.

If no `providers`/`models` catalog is configured, the original single-model
fields in `config/default.yaml` remain the fallback.

The Core API uses one token-authenticated WebSocket listener on
`127.0.0.1:9527` by default. Local CLI/TUI clients use an automatically managed
credential stored with mode `0600` at
`~/.knoa/config/service.token`. Setting `service_token` adds a separate
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
`~/.knoa/mcp/<server-id>/`. Each package contains a bounded `mcp.yaml`
manifest and its local server files. Local packages use the official MCP stdio
transport in an independent child process. The manifest declaratively defines
the command, arguments, package-confined working directory and timeout; Core
contains no server-specific launch branches. Only a minimal base environment plus explicitly named
`inherit_env` values reaches the child process. Server metadata never grants
authority: unconfigured tools remain hidden and all enabled tools still pass
schema validation, capability checks, confirmation and cancellation through
the standard `ToolStep` boundary.

MCP Servers that expose standard Resources can also be configured as explicit
Resource Task Sources. Core performs bounded `resources/list`/`read`, negotiates
optional subscriptions and matches Resource snapshots to existing Event Task
Definitions. Notifications are wake-up hints only; no MCP Resource creates work
unless an active Task Definition selects its Server and URI scope. See the runnable [Jira MCP reference server](examples/jira_mcp_server/README.md)
and [GitLab MCP reference server](examples/gitlab_mcp_server/README.md) for
automatic Resource-driven Task examples. They are independent Provider
packages; Knoa Core and clients contain no Jira/GitLab-specific branches.

This process boundary isolates crashes and dependency conflicts. Strong
resource or host-access containment is a separate OS-level sandbox policy and
is not implied by stdio process isolation.

An owner or local operator such as Codex should use the explicit management
command when the deployment itself was already requested by the user:

```bash
knoa mcp-package-deploy ./examples/jira_mcp_server jira
```

This command is owner-only, invokes no Agent, creates no approval Task, and
therefore does not ask for confirmation again in Feishu. When an Agent decides
to deploy during its own execution, it must instead call the confirmation-gated
`mcp_deploy` Built-in Tool with an existing local package directory. Knoa
validates and snapshots the package, omits hidden metadata and symlinks,
rejects size-limit violations, installs it atomically,
and activates the MCP provider without restarting Core. Newly registered tools
are visible to the next model iteration in the same Task attempt. Network download and
marketplace trust remain separate future concerns; this tool imports local
packages only.

For an already running remote or stdio MCP Server, the Agent first uses
read-only `mcp_inspect`, then proposes a confirmation-gated `mcp_connect` call
containing the exact Tool names to enable. Only user-confirmed Tools that also
declare `readOnlyHint=true` are persisted and activated. Write, ambiguous or
unselected Tools remain withheld. Resource-to-Task automation is configured on
the Task Definition with `event_source=mcp:<server_id>` and a Resource URI
selector. `mcp_disable` provides
a confirmation-gated rollback that stops the Provider and persists it disabled.

Enable the independently mounted DingTalk Stream channel in
`~/.knoa/config/local.yaml`:

```yaml
dingtalk_enabled: true
dingtalk_client_id: "client_xxx"
dingtalk_client_secret: "..."
dingtalk_robot_code: "robot_xxx"
```

DingTalk uses the official Stream long connection, so normal operation does
not require a public callback URL. Text, images, files, background Task
notifications, approvals and result delivery share the same Core session and
idempotency semantics as Feishu. The `dingtalk_stream` SDK is included in the
runtime dependency lock and is imported lazily so a disabled channel cannot
affect Node startup.

Enable the independently mounted Feishu channel in
`~/.knoa/config/local.yaml`:

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
Feishu also exposes `/tasks`, `/task <id>`, and `/stop <id>` for owned durable
Tasks. New messages can enqueue new Tasks while earlier work continues.
Background approval and terminal notifications are emitted only for Product
Task Executions and follow that Task's `waiting_approval`, `completed`, and
`failed` notification policy. Ad-hoc user/CLI Tasks are not mirrored into
Feishu, while Agent-created background Tasks retain approval and terminal
notifications.
Images, ordinary files, and Feishu voice messages enter the same owned Core
Artifact boundary. Attached text files preserve their safe name and can be
inspected through the bounded `read_artifact` Built-in Tool without exposing a
server filesystem path. Voice bytes remain an opaque owned Artifact; a
transcription provider is not embedded in the Feishu Channel. To make voice
messages immediately create Tasks, explicitly map a read-only, non-high-risk
MCP Tool in private config:

```yaml
audio_transcription:
  enabled: true
  tool: "mcp__speech__transcribe"
  max_bytes: 20971520
```

The mapped Tool receives only `audio_data_url`, `media_type`, and `file_name`.
It should return `structuredContent.transcript` (or a text content block).
Long final outputs are retained in the Task journal and also emitted as a
persistent Markdown Artifact. Feishu shows a compact preview and delivers the
complete file instead of flooding the conversation with continuation cards.

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

The Secure Gateway is a separate, default-off mobile API. Keep the primary
Gateway on loopback for local development. Node deployments can additionally
enable an authenticated LAN listener; it is advertised as
`_knoa-node._tcp.local.` via mDNS and is used only after the App verifies the
Node identity. The LAN listener does not expose unauthenticated APIs:

```yaml
gateway_enabled: true
gateway_host: "0.0.0.0"
gateway_port: 9529
gateway_lan_enabled: true
gateway_lan_host: "0.0.0.0"
gateway_lan_port: 9541
gateway_remote_enabled: true
gateway_public_url: "https://knoa.example.com"
gateway_tls_cert_file: "/absolute/path/gateway-cert.pem"
gateway_tls_key_file: "/absolute/path/gateway-key.pem"
```

The Gateway exposes only its allow-listed device, Task, approval, event and
Artifact protocol; it never exposes arbitrary Core methods. DNS, certificate
issuance and router/cloud networking remain deployment responsibilities.

Create the short-lived, single-use pairing grant locally; never expose an
administrative grant-creation endpoint:

```bash
knoa gateway pair --ttl 300
knoa gateway devices
knoa gateway revoke <device-id>
```

When `gateway_public_url` is configured, `knoa gateway pair` also prints one
canonical `pairing_json` payload and a terminal QR code for the mobile App. The
payload contains the short-lived grant secret and must be treated as sensitive
until it expires or is consumed. Paired devices can read only their own
secret-free audit history through `GET /v1/device/audit`.

When enabled, `GET /openapi.json` serves the OpenAPI 3.1 source for generated
mobile clients. Request models are shared with the running adapter, and tests
fail if the documented allow-listed paths drift from the actual routes.

The Android App can use a private, store-free update channel. Build every APK
with the same owner-controlled signing key and increase its Android version
code, then publish it locally:

```bash
scripts/build-mobile-apk.sh
KNOA_MOBILE_RELEASE_NOTES="Personal release" scripts/publish-mobile-apk.sh
knoa gateway release latest
```

The build uses the Android/JDK environment in `/disk/dev/env.sh`, keeps Gradle
caches and APK output under `/disk/dev`, and reads the fixed owner signing key
from `~/.knoa/secrets/android`. Publication reads and verifies
`versionName` and `versionCode` directly from the compiled APK manifest.

### Version management

Knoa Platform and Knoa Mobile have independent semantic versions. Agent Runtime
protocol `1.0` and Core/Gateway API `v1` are protocol versions and are changed
only with their respective protocol contracts.

```bash
# Validate all product version sources
scripts/bump-version.sh check

# Platform only: 0.2.0 -> 0.2.1
scripts/bump-version.sh bump platform patch

# Mobile only: versionName patch + Android versionCode increment
scripts/bump-version.sh bump mobile patch
```

Platform release tags use `knoa-vX.Y.Z`; Mobile release tags use
`knoa-mobile-vX.Y.Z`. Creating tags remains an explicit release action and is
not performed by the bump command.

The authenticated Gateway serves an immutable release manifest and APK byte
ranges. The App can pause and resume the download, verifies its size and SHA-256
digest, then opens Android's system installer. No release administration
endpoint is exposed remotely, and release storage never enters Core.

### Environment variables

Supported environment overrides use the `KNOA_` prefix:

| Variable | Field |
|----------|-------|
| `KNOA_LLM_PROVIDER` | llm_provider |
| `KNOA_DEFAULT_MODEL` | default_model |
| `KNOA_FALLBACK_ENABLED` | fallback_enabled |
| `KNOA_FALLBACK_MODEL` | fallback_model |
| `KNOA_LLM_SERVER_URL` | llm_server_url |
| `KNOA_LLM_MODEL_NAME` | llm_model_name |
| `KNOA_LLM_API_KEY` | llm_api_key |
| `KNOA_LLM_API_BASE` | llm_api_base |
| `KNOA_LLM_TEMPERATURE` | llm_temperature |
| `KNOA_LLM_TIMEOUT` | llm_timeout |
| `KNOA_MAX_ITERATIONS` | max_iterations |
| `KNOA_MAX_TOTAL_TOOL_CALLS` | max_total_tool_calls |
| `KNOA_SHELL_TIMEOUT` | shell_timeout |
| `KNOA_CONTEXT_WINDOW_BUDGET` | context_window_budget |
| `KNOA_TRACE_ENABLED` | trace_enabled |
| `KNOA_LLM_TRACE_LOG` | llm_trace_log |
| `KNOA_TURN_TRACE_LOG` | turn_trace_log |
| `KNOA_LOG_FILE` | log_file |
| `KNOA_HOME` | runtime_root (friendly alias) |
| `KNOA_RUNTIME_ROOT` | runtime_root |
| `KNOA_WORKING_DIRECTORY` | working_directory |
| `KNOA_OWNER_PRINCIPAL_ID` | owner_principal_id |
| `KNOA_WEBHOOK_ENABLED` | webhook_enabled |
| `KNOA_WEBHOOK_HOST` | webhook_host |
| `KNOA_WEBHOOK_PORT` | webhook_port |
| `KNOA_GATEWAY_ENABLED` | gateway_enabled |
| `KNOA_GATEWAY_HOST` | gateway_host |
| `KNOA_GATEWAY_PORT` | gateway_port |
| `KNOA_GATEWAY_LAN_ENABLED` | gateway_lan_enabled |
| `KNOA_GATEWAY_LAN_HOST` | gateway_lan_host |
| `KNOA_GATEWAY_LAN_PORT` | gateway_lan_port |
| `KNOA_GATEWAY_REMOTE_ENABLED` | gateway_remote_enabled |
| `KNOA_GATEWAY_PUBLIC_URL` | gateway_public_url |
| `KNOA_GATEWAY_TLS_CERT_FILE` | gateway_tls_cert_file |
| `KNOA_GATEWAY_TLS_KEY_FILE` | gateway_tls_key_file |

### Runtime config

Use `/config set key=value` in the chat to change settings at runtime.

### Runtime data

By default, mutable application state is kept outside the source tree under
`~/.knoa/`:

```text
~/.knoa/
├── logs/          # application, service, audit, and trace logs
├── attachments/   # temporary inbound and generated artifacts
├── artifacts/     # persistent user-requested generated files
├── cache/         # idempotency and other rebuildable state
├── data/          # assistant.db and procedural memory
├── skills/        # manually imported data-only Skill packages
└── mcp/           # manually imported local stdio MCP packages
```

Set `KNOA_HOME` (or `KNOA_RUNTIME_ROOT`) to override this root. Service
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
| `mcp_deploy` | path, server_id | Validate, atomically install or update, and activate a local MCP package after explicit confirmation |
| `describe_tool` | tool_name | Meta-tool: query the full JSON schema of any registered tool |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=knoa_platform --cov-report=term-missing
```

## Architecture

```
src/knoa_platform/
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
