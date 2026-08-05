# PC Assistant Architecture

> Status: current-code architecture review and defect-hardening anchor
> Last updated: 2026-08-05
> Sources: `README.md`, `pyproject.toml`, confirmed `.trae` specifications and reviews, `specs/MULTIMODAL-GUI-001.md`, and current source-code call chains

## 1. Purpose and authority

This document defines the system-level module boundaries and architectural
invariants for PC Assistant. It deliberately separates:

- **Current architecture (As-Is):** behavior verified in the current workspace.
- **Defect-hardening direction (To-Be):** minimal boundary fixes to the current
  implementation. Existing correct capabilities are retained; only designs
  that cannot be repaired locally are refactored.
- **Historical findings:** older review statements that may already be fixed and
  therefore are not treated as current facts without source verification.

OpenSpec baseline specifications define public behavior. Campaign blueprints
define multi-Change migration intent. Source code remains the authority for
current implementation facts.

### 1.1 Industry-practice assessment

The architecture has sound foundations and follows industry practice at its
critical execution, multimodal, runtime-storage, identity, and GUI-targeting
boundaries. It is not yet an industry-reference architecture as a whole.
Application orchestration and the Feishu adapter remain concentrated, context
budgeting is advisory rather than hard, runtime configuration has unclear apply
semantics, and current channel/logging behavior can expose information that
should remain internal or redacted.

Mature foundations already present include a canonical provider-neutral message
layer, provider adapters, typed configuration, an `AgentLike` service port,
session isolation, deterministic safety/confirmation checks, tool schemas,
idempotency, context assembly, and observability. These should not be rebuilt.

The previously verified critical boundary defects have been repaired: binary
payloads are reference-only outside request hydration, postconditions run after
execution, tool commit uses a single-use verified capability, same-session
turns serialize, runtime paths have one resolver, and GUI targets bind to fresh
snapshots. Continued work should be boundary-driven rather than a file-count
rewrite or a remote grounding service. The desired shape is a thin turn
orchestrator over explicit context, model-routing, verified-execution, storage,
and channel ports.

### 1.2 Tool-schema injection and discovery

The registry injects every registered tool on each model request using only a
small, stable `core_schema` (name, short description, and commonly needed
parameters). With the current tool count this keeps the action surface complete,
avoids a selector/embedding failure mode, and makes the static prefix
deterministic for prompt-cache reuse. It is not a security boundary: calls
still pass schema validation, safety, confirmation, idempotency, and the
verified executor.

`describe_tool(tool_name)` is a meta-tool for progressive detail. It is used
only when a task needs parameters omitted from the compact schema; its response
is request-scoped and is not persisted into the stable cache prefix. MCP tools
follow the same compact-core/full-schema contract.

### 1.3 Context capacity, compaction, and live configuration

API models may expose much larger windows than the local fallback. A model
catalog entry may set `context_window`; that capacity is selected per active
model, while `context_window_budget` remains the fallback for models without an
explicit declaration. The request planner still reserves completion tokens and
static tool schemas.

Compaction follows the current industry baseline for personal agents: keep the
active turn lossless, retain the assistant's interpretation in completed turns,
and replace completed tool-result bodies with protocol-safe omission markers.
Tool-call messages remain for traceability and provider pairing; raw files,
screenshots, and large results stay outside the prompt behind artifact/tool
references. LLM rewriting is an optional lossy optimization, disabled by
default; it is never the only copy of history and never runs for safety-critical
authorization data.

`/config set` applies execution limits, context policy, temperature, and
compaction settings immediately. Provider identity/credentials are rebuilt
between turns when the Agent owns the provider; injected custom providers report
restart-required instead of being replaced implicitly.

## 2. System context

PC Assistant is a Python 3.10+ desktop agent that accepts requests from local
interactive clients and external channels, runs a ReAct-style orchestration
loop, calls one of several LLM providers, verifies proposed tool actions, and
executes local or MCP-provided tools.

```text
CLI / TUI / Feishu
        |
        v
AgentLike boundary ----> Service client ----> daemon transport
        |                                      |
        +---------------- Agent <--------------+
                           |
             +-------------+--------------+
             |             |              |
          Session       Context        LLM Provider
             |             |              |
             +-------------+--------------+
                           |
                   Verifier / Safety
                           |
                   Tool Registry
                           |
              Built-in tools / MCP / GUI
                           |
       inbound <---- unified ArtifactStore ----> outbound
                           |
                  core artifact event
                           |
                    client delivery
```

The principal production invariant is:

```text
Untrusted input -> stochastic proposal -> deterministic authorization
                -> side-effect execution -> explicit postcondition evidence
```

No observation, model output, channel payload, or tool result obtains execution
authority merely by entering the context window.

## 3. Confirmed module boundaries

### 3.1 Entry and interaction

**Paths:** `src/pc_assistant/__init__.py`, `ui/`, `channels/`

**Responsibilities:**

- Parse CLI commands and configuration selection.
- Render streaming `AgentEvent` output.
- Translate interactive confirmation into the Agent confirmation boundary.
- Adapt Feishu messages to session-scoped Agent requests.

**Boundary:** presentation code consumes an Agent-like streaming interface. It
must not execute side-effecting tools directly or reinterpret verifier verdicts.
Clients also own channel-specific delivery of standard `artifact` events:
Feishu uploads the referenced image/file, terminal clients render a bounded
artifact reference, and future clients may present it using their own transport.
The model and Agent never receive channel identifiers such as a Feishu `open_id`.

### 3.2 Service and transport

**Paths:** `src/pc_assistant/service/`

**Responsibilities:**

- Expose JSON-over-WebSocket requests and streamed events.
- Prefer a daemon when available and fall back to an in-process Agent.
- Scope runs, cancellation, confirmation, and status by session/client.

**Public anchor:** `service/agent_like.py::AgentLike` is the intended common
interface between local Agent and remote `ServiceClient`.

**Boundary:** transport serializes request and event contracts. It must not
persist provider-native image payloads or weaken session authorization.

### 3.3 Agent and session orchestration

**Paths:** `agent.py`, `session.py`, `planner.py`, `reflection.py`

**Responsibilities:**

- Own the ReAct iteration lifecycle and `AgentEvent` stream.
- Bind a request to a `SessionState`.
- Assemble LLM messages, receive proposals, request verification, execute tools,
  and record outcomes.
- Roll back conversation additions after cancellation or errors.

`SessionManager` provides an LRU-bounded map of isolated conversation and usage
state. The empty session identifier is pinned for the local interactive client.

**Boundary:** Agent is an orchestrator, not a provider parser, persistence store,
or safety-policy implementation. New capabilities should enter through owned
interfaces rather than adding more provider, storage, or platform branches to
the main loop.

### 3.4 Context and memory

**Paths:** `src/pc_assistant/context/`

**Responsibilities:**

- Store role-ordered conversation history.
- Assemble cache-friendly request messages.
- Preserve tool-call/result grouping during truncation.
- Estimate tokens, compact old turns, and inject session/memory context.
- Maintain principal-scoped core/relevant memory, session-scoped episodes, and
  procedural rules.

**Current fact:** `ConversationManager.Message.content` accepts strings and
content-block lists, but durable multimodal content is restricted to bounded
`image_ref` blocks. Provider image/data-URL blocks are rejected at this boundary.

**Required invariant:** durable or reusable history contains bounded semantic
content only. Large binary encodings, secrets, and provider-native request
objects do not belong in conversation history, compaction input, events, logs,
or idempotency records.

### 3.5 Model provider and adapter

**Paths:** `llm_provider.py`, `model_adapter/`

**Responsibilities:**

- Resolve provider endpoint, headers, retry, streaming, cancellation, and usage.
- Convert canonical messages and tool schemas to OpenAI-style or Anthropic
  payloads.
- Convert provider responses to canonical `LLMResponse` and `StreamChunk` data.

**Boundary:** canonical content is provider-neutral. Provider serialization is
role-aware: user observations, assistant messages, and tool results may require
different wire representations.

**Required invariant:** unsupported vision combinations fail with a stable,
diagnosable error; image content is never silently dropped.

### 3.6 Tools, desktop automation, and vision

**Paths:** `src/pc_assistant/tools/`, `src/pc_assistant/vision/`

**Responsibilities:**

- Define schemas and execute built-in or MCP-discovered tools.
- Provide filesystem, shell, web, application, system, memory, scheduling, and
  desktop-control operations.
- Capture screen observations and access platform accessibility trees.

**Boundary:** `ToolRegistry` owns discovery and dispatch, but authorization is a
separate deterministic concern. Direct registry execution is an internal commit
primitive and must not become a public bypass around the verifier.

Tools may produce managed artifacts, but delivery is not itself a model tool.
The `screenshot` tool creates a user-visible PNG artifact; `artifact_prepare`
borrows an existing file without copying or taking deletion ownership. The Agent converts
their safe public references into a standard `AgentEvent(type="artifact")`, and
the active client adapts that event to its channel. Internal `screen` captures
remain GUI observations and are never automatically delivered.

Local opening and conversation delivery are intentionally separate operations.
`application`/shell launch (including `xdg-open`) opens a file on the Agent host;
an artifact event delivers a file to the current conversation. Neither implies
the other.

**GUI invariant:** an action target is bound to a current snapshot and a complete
coordinate transform. Partial-name matching or model-computed desktop
coordinates cannot be described as guaranteed-precision execution.

### 3.7 Harness safety boundary

**Paths:** `src/pc_assistant/harness/`

**Responsibilities:**

- Validate proposed actions against deterministic safety policy.
- Request user confirmation when policy requires it.
- Produce typed accept/reject verdicts.
- Record audit and idempotency evidence.

**Required ordering:**

```text
validate schema
  -> evaluate safety and trust
  -> obtain required confirmation
  -> commit exactly one tool execution
  -> evaluate postcondition
  -> record audit outcome
```

Precondition checks and postcondition verification are different operations and
must not share misleading lifecycle names.

### 3.8 Runtime infrastructure

**Paths:** `config.py`, `logger.py`, `observability/`, service runtime paths

**Responsibilities:**

- Load typed configuration from YAML and environment variables.
- Resolve process runtime paths.
- Write application logs, audit logs, LLM traces, and turn metrics.
- Own future attachment and cache lifecycle configuration.

**Approved target layout:**

```text
<runtime-root>/
├── logs/          application, service, audit, LLM and turn logs
├── attachments/   temporary inbound and generated artifacts
├── artifacts/     persistent generated artifacts
├── data/          SQLite state and procedural memory
└── cache/         reconstructable non-authoritative data
```

`attachments/` and `artifacts/` are siblings of `logs/`, never children of it. Socket and PID
files may remain under an OS-appropriate ephemeral runtime location, while
durable logs, bounded temporary artifacts, and persistent generated artifacts
use the configured application runtime root.

### 3.9 Benchmark and tests

**Paths:** `src/pc_assistant/benchmark/`, `tests/`

**Responsibilities:**

- Execute repeatable datasets and score outcomes.
- Verify unit, integration, service, provider, session, safety, and GUI behavior.
- Enforce the configured coverage floor.

Architecture-sensitive tests must validate boundaries and failure modes, not
only happy-path helper output.

## 4. Primary data flows

### 4.1 Text request

```text
Channel/UI
  -> AgentLike.run(input, session_id)
  -> SessionManager
  -> Conversation + runtime context assembly
  -> provider adapter
  -> LLM proposal
  -> Verifier
  -> ToolRegistry.execute
  -> tool result
  -> next LLM iteration or final AgentEvent
```

### 4.2 Approved multimodal target

The implemented multimodal path is:

```text
upload or screen capture
  -> ArtifactStore.put(session, direction=inbound, ownership=managed)
  -> image_ref / observation_ref in conversation
  -> RequestAssembler selects references needed for this call
  -> ArtifactStore hydrates artifact_id for this request only
  -> provider-specific temporary encoding
  -> request completes
  -> encoded payload released; reference remains bounded
```

History serialization of a non-hydrated reference is a short placeholder with
an identifier, source, dimensions, trust level, expiry, and bounded summary.
The MVP does not expose model-initiated recall: RequestAssembler automatically
hydrates only the bounded references selected for the current request. An
expired reference requires a new upload or capture.

### 4.3 User-visible artifact delivery

```text
user asks to send/capture a file
  -> verified artifact-producing tool
  -> ArtifactStore(session, direction=outbound, ownership + retention)
  -> public artifact reference (ID + bounded metadata, no path/bytes)
  -> AgentEvent(type="artifact")
  -> active client/channel adapter
  -> image/file delivery to that conversation
```

`ArtifactStore.resolve()` is an in-process delivery capability, not model
context. Only the trusted client adapter resolves an owned, unexpired
`artifact_id` to its internal path. Conversation history, public events, and
logs contain neither paths nor bytes. SQLite stores only the internal metadata
needed to recover persistent Artifact IDs after restart; it never stores file
bytes or base64. Delivering a protected path is rejected, and delivering a file
outside the configured working directory requires confirmation.

Artifact direction and lifecycle are explicit:

| Ownership / retention | Typical use | Cleanup rule |
|---|---|---|
| `borrowed / temporary` | Existing user file sent to a channel | Registry expires; source file is never copied or deleted |
| `managed / session` | Uploaded inbound image | Core-owned bytes are deleted when the session ends or TTL expires |
| `generated / temporary` | Screenshot sent to the user | Client acknowledges delivery; Core deletes after a grace period |
| `generated / persistent` | User-requested saved output | Stored under `artifacts/`, registered in SQLite, never session-cleaned |

Clients report successful delivery through `mark_artifact_delivered`; clients
never delete files directly. Session deletion only removes `retention=session`
entries.

### 4.4 GUI action target

```text
screen/a11y observation
  -> snapshot_id + observation_ref
  -> ElementRef or grounded target
  -> service-side CoordinateTransform
  -> risk/precondition policy
  -> confirmation when required
  -> action
  -> structured postcondition verification
```

### 4.5 Current context composition and scope

The provider request is not simply the current session transcript. Current
assembly contains:

| Context segment | Current scope | Current selection |
|---|---|---|
| System prompt | application | always present |
| Conversation transcript | session | at most the latest eight dialogue turns, then token truncation |
| Core user memory | principal | at most 12 stable, confirmed items; always injected |
| Relevant user memory | principal + current query | up to five matching SQLite records |
| Episodic memory | principal + session | not automatically injected; explicit recall only |
| Procedural rules | application | bounded shared rules |
| Working-directory/session metadata | current request | always regenerated |
| Evidence/safety instruction | current turn | only when required |
| Image observations | session + request | temporary references; request-only binary hydration |

Feishu image messages are downloaded into the runtime attachment inbox,
converted to an `ImageAttachment`, and registered to the session without a
second byte-for-byte copy before provider hydration. Attachment metadata and bytes
remain in the bounded temporary attachment store and are not persisted in SQLite.

Image messages are attachment ingress, not implicit prompts. Feishu stores the
image and asks the user for a question; the next top-level message consumes the
pending image, an explicit reply targets that exact image message, and a reply
to the assistant continues the active image thread. Historical image
references remain in canonical history but are not rehydrated on unrelated
turns. This prevents every old screenshot from repeatedly consuming the vision
context budget.

Main-model vision and image perception are separate capabilities. If the main
model supports images, the current-turn reference may be hydrated directly for
that one provider request; the fallback vision provider is not constructed and
`image_inspect` is not registered or included in the tool schema. This preserves
the original multimodal prompt and function-calling surface. If the main model
is text-only, every reference is converted to a metadata manifest containing
`image_id`, MIME type, dimensions, and source. The deterministic image-evidence
gate prevents delivery of a visual claim until the main model calls
`image_inspect`.

`image_inspect` delegates to an independently configured vision provider. Its
contract is deliberately perceptual: `describe`, `ocr`, `locate`, and `compare`.
The optional `focus` names a visible detail to observe; obvious diagnosis or
solution questions are rejected. The vision model returns structured visible
evidence and uncertainty, while the main model owns diagnosis, recommendations,
and task reasoning. Base64 exists only in the broker's provider request and is
never returned by the tool or written into conversation history.

Other sessions' raw conversation messages are never copied into the current
`SessionState`. Durable domain state is stored in `data/assistant.db`: profile
facts are partitioned by `principal_id`, episodes by both `principal_id` and
`session_id`, and schedules use a separate table. Feishu principals derive
from the sender open ID; local TUI/CLI sessions share the local principal.
Unknown channel namespaces fail closed to a session-specific principal.

### 4.5 Target complete-context design

Context should be assembled from typed, independently budgeted segments rather
than one implicit prompt string. Each segment needs at least:

```text
scope: application | principal | session | turn
owner_id: principal/session identifier where applicable
source: user | system | tool | memory | channel
trust: trusted-policy | user-stated | observed | model-derived
sensitivity: public | private | secret
ttl / freshness
token_budget
provenance reference
```

Recommended request order and default policy:

1. Immutable system and safety policy.
2. A small confirmed core profile for the current principal.
3. Query-relevant memory for the current principal.
4. Current-session transcript and optional current-session summary only.
5. Episodic records only after explicit recall, restricted to the same
   principal and session.
6. Current-turn input, attachments, tool observations, and evidence directive.

There is deliberately no required `workspace_id`: this is a general-purpose
agent, and durable identity should not fragment when the working directory
changes. Project-specific facts may carry optional tags or a context key, but
those are retrieval metadata rather than an ownership boundary.

Profile keys must name their subject and meaning. Examples include
`user_name`, `assistant_name`, `preferred_language`,
`preferred_answer_style`, and `delete_requires_confirmation`. Ambiguous keys
such as `name`, `language`, `style`, `browser`, or `framework` are rejected.
Core writes require confirmation; ordinary conversation and model inference do
not silently become durable memory.

The assembler should enforce separate budgets for policy, transcript, memory,
retrieval, and observations. It should emit a metadata-only `ContextManifest`
for `/context` diagnostics showing which segment IDs were included, omitted,
expired, or truncated; it must never include image bytes, secrets, or complete
provider payloads.

Clear operations also require explicit semantics:

- `/clear`: current session transcript, pending observations, and attachments.
- `/memory clear`: current principal's durable profile and episodes.
- No command should silently clear or expose another principal/session.

## 5. Architecture invariants

1. All side effects pass through one deterministic authorization boundary.
2. User/channel/session identity remains attached across transport and execution.
3. Provider-native payloads do not become the durable internal message model.
4. Base64 images and data URLs never enter conversation history, compaction,
   events, logs, caches, or idempotency persistence.
5. Attachment access is session-scoped, size-bounded, expiring, and deleted on
   session removal where applicable.
6. Screen and accessibility observations are untrusted data, not instructions.
7. GUI coordinates are converted on the service side using a complete transform.
8. High-risk actions require pre-execution policy; post-action evidence cannot
   retroactively authorize an action.
9. Logs are metadata-safe and must redact binary content, credentials, and
   provider request bodies.
10. Optional platform dependencies degrade explicitly without disabling unrelated
    application capabilities.
11. A vision model observes pixels only; diagnosis, recommendations, and action
    selection remain responsibilities of the main agent model.

## 6. Architecture review findings

### AR-001: Agent orchestration concentration — high

`agent.py` coordinates sessions, context assembly, provider streaming, tool
limits, verification, idempotency, tool execution, multimodal extraction,
observability, memory updates, and event production. The Campaign should reduce
new responsibility growth by moving attachment lifecycle and provider hydration
behind explicit collaborators. A broad Agent rewrite is not required for the
multimodal MVP.

### AR-002: Tool execution bypass surface — high (fixed 2026-08-04)

The former public `ToolRegistry.execute()` dispatch allowed a caller holding the
registry to bypass policy. It has been replaced by internal `_commit()`, while
model-proposed calls pass through `VerifiedToolExecutor`. Authorization produces
an opaque single-use `PreparedToolCall`; only that executor can commit it, and a
second commit is rejected.

### AR-003: Post-verification lifecycle was inverted — critical (fixed 2026-08-04)

Previously, `Verifier._finalize()` invoked post-verification while producing an
accepted verdict, before actual execution. Authorization and postcondition
collection are now separate. The verified executor invokes `post_verify` only
after the tool completes successfully; regression tests assert the
`execute -> post_verify` order.

### AR-004: Inline image payload contaminated durable context — high (fixed 2026-08-04)

The former attachment and screenshot paths appended data URLs to
`ConversationManager`. The unified `ArtifactStore` now writes session-scoped temporary
files; Conversation accepts `image_ref` blocks and rejects provider image
payloads; request assembly hydrates only a copied provider request. Events are
redacted and binary results are not persisted in idempotency. Service clients
upload images first and run requests accept `artifact_id` references only.

### AR-005: Runtime path ownership was fragmented — high (fixed 2026-08-04)

`RuntimePaths` now resolves sibling `logs/`, `attachments/`, `artifacts/`, `cache/`, and `data/`
directories from `runtime_root`. Application, service, audit, trace,
idempotency, attachment, and screenshot paths consume this layout; socket/PID
files retain OS runtime placement. Service startup failure cancels its periodic
attachment cleanup task.

### AR-006: Provider capability model was under-specified — medium (fixed 2026-08-04)

`ProviderProfile` now owns typed `VisionCapabilities` covering canonical roles,
transport, MIME, image count, bytes, and pixels. Provider requests validate the
contract and fail explicitly; OpenAI and Anthropic tool-image roles serialize
through their legal provider-specific structures.

### AR-007: GUI semantic identity was unstable — high (fixed 2026-08-04)

The UI tool returns snapshot-bound `ElementRef` values. Actions reject stale,
ambiguous, changed, or non-unique targets rather than selecting the first
partial match. `CoordinateTransform` covers crop, scale, virtual origins, and
supported rotation values.

### AR-008: Linux accessibility runtime and traversal differed — medium (fixed 2026-08-05)

The service intentionally runs with the system Python 3.10 runtime, where
`python3-pyatspi`, `python3-gi`, and `gir1.2-atspi-2.0` are installed. A Conda
Python 3.12 development shell does not see those system extensions and is not a
valid proxy for daemon capability. The AT-SPI adapter now uses native role and
component APIs and walks all application roots breadth-first, so a large GNOME
Shell tree cannot consume the element budget before other applications are
visited. Electron applications may still expose sparse semantics; UI actions
retain the screenshot/coordinate fallback and must fail closed on ambiguous
targets.

### AR-009: Same-session runs can interleave — critical (fixed 2026-08-04)

The service can start runs concurrently for different clients, and callers may
reuse the same `session_id`. Previously, `SessionState` had no per-session run
lock, so snapshot watermarks, conversation messages, cancellation state, tool
history, counters, and rollback could interleave. `SessionState.run_lock` now
serializes a complete turn per session while preserving concurrency across
different sessions. A regression test exercises two concurrent runs sharing a
session.

### AR-010: Agent owns too many concrete collaborators — medium

`Agent.__init__` constructs provider, three memory stores, safety, registry,
verifier, idempotency, planning, reflection, session management, traces,
evidence policy, tools, and cache planning. Constructor injection exists for
several seams, so this is repairable. New attachment lifecycle, runtime path,
and verified-execution behavior should be introduced behind narrow
collaborators; the current ReAct loop should not be split merely to reduce file
length.

### AR-011: TUI selection and cancellation bindings conflicted — medium (fixed 2026-08-04)

The App previously rebound `Ctrl+C` to turn cancellation, shadowing Textual's
screen-selection copy path. A root-mounted custom context menu also interpreted
screen coordinates as unconstrained widget coordinates and copied message-level
content rather than the exact selection. `Ctrl+C` is now left to Textual's
selection action, `Esc` cancels the active turn, and right-click directly copies
an existing selection without mounting a popup. Copy dispatch uses OSC52 for the
terminal plus the local platform clipboard helper when available.

### AR-012: Tool artifacts and channel delivery were implicit — medium (fixed 2026-08-04)

Screenshot tools previously wrote generic filenames into the process working
directory, and Feishu discarded all tool-result events. Screenshot-producing
tools now allocate collision-resistant files below
`attachments/screenshots/`, emit explicit image-artifact metadata without
binary content, and reject paths escaping that root. Feishu uploads and sends
declared image artifacts while ignoring arbitrary tool paths. Filesystem paths
expand `~` and resolve relative paths against configured `working_directory`.

### AR-013: Durable memory scope was broader than session/principal — high (fixed 2026-08-04)

The former global JSON stores could leak profile facts and recent episodes
between sessions and Feishu users. The default repository is now transactional
SQLite under the runtime data directory. Profile memory is owned by
`principal_id`; episodic memory is owned by `principal_id + session_id` and is
never automatically injected. Core and relevant retrieval are separate,
ambiguous keys are rejected, and ordinary chat text is not auto-persisted.

### AR-014: Durable tool state used ad-hoc files — medium (fixed 2026-08-04)

Scheduler tasks previously used `data/scheduled_tasks.json`, relative to the
process working directory. They now use a dedicated `scheduled_tasks` table in
`~/.pc-assistant/data/assistant.db`. SQLite is the shared transactional store
for durable domain state and Artifact registry metadata. Logs remain
append-only files, Artifact bytes remain files, configuration remains
YAML/environment input, and caches remain rebuildable files; binary content
does not belong in the database.

### AR-015: Visual artifacts leaked internal observations to channels — medium (fixed 2026-08-04)

The Feishu channel previously inferred delivery from keywords and image-shaped
tool results, which exposed internal grid observations and produced duplicate
screenshots. Delivery now consumes only typed Core `artifact` events after the
turn. The dedicated `screenshot` tool produces one PNG; internal `screen`
observations never become outbound artifacts. Managed paths are registered in
the unified `ArtifactStore` without duplicate copies.

### AR-016: Main-model multimodality was coupled to image ingress — high (fixed 2026-08-04)

A text-only main model previously rejected every image before the ReAct loop,
which prevented deployments such as DeepSeek for reasoning plus local Qwen-VL
for perception. The agent now exposes attachment manifests to text models and
provides a dedicated `image_inspect` tool backed by an independently configured
vision provider. Its prompt and schema restrict it to visible description,
OCR, location, and comparison; solution-seeking focus values are rejected.
Repeated identical observations are cached by image hash, model, operation,
focus, region, and comparison image. A deterministic gate suppresses and rejects
an unsupported visual answer until a successful structured observation exists.
When the main model supports vision, the fallback provider is not constructed
and `image_inspect` is absent from both the registry and provider tool schema;
the direct multimodal prompt and function-calling surface remain unchanged.

### AR-017: Context budgeting is advisory, not a hard request limit — high (fixed 2026-08-05)

The request path now reserves completion capacity and estimates the static core
tool schemas before passing the remaining message budget to deterministic
history trimming. This keeps the complete static tool surface cacheable while
preventing schema tokens from silently consuming the completion reserve.

### AR-018: External channels can expose model reasoning — high (fixed 2026-08-05)

Feishu drops `stream_think_delta` from outbound cards. Channel users receive
bounded progress/status events and the final answer; raw reasoning remains
available only to explicitly enabled local diagnostics.

### AR-019: Feishu adapter combines too many lifecycle concerns — medium

`channels/feishu.py` combines SDK patching, WebSocket lifecycle, polling,
deduplication, thread/async bridging, attachment correlation, confirmation UI,
reaction state, message rendering, and Agent execution in one module. Split it
behind `ChannelIngress`, `ChannelDelivery`, and a small persistent correlation
store. This reduces race surfaces without changing the channel contract.

### AR-020: Runtime configuration mutation has misleading semantics — medium (fixed 2026-08-05)

`/config set` now applies execution limits, temperature, context and compaction
settings immediately. Provider transport and cache state are rebuilt between
turns when safe; changes that alter the vision route or replace an injected
provider explicitly report restart-required.

### AR-021: `/compact` currently means clear — medium (fixed 2026-08-05)

All clients route `/compact` through the Agent/service command contract, which
mechanically compresses older turns. Session deletion remains exclusive to
`/clear`.

### AR-022: Credential redaction is incomplete for third-party logs — high

Current service logs can contain Feishu WebSocket connection URLs with temporary
access credentials and tickets emitted by the SDK. The application logging
invariant is therefore not fully satisfied. Install a root logging redaction
filter before every handler and lower or isolate third-party transport logs;
tests should scan emitted records for API keys, bearer tokens, tickets, and URL
query credentials.

### AR-023: In-memory transcript retention is unbounded per live session — medium (fixed 2026-08-05)

`ConversationManager(max_messages=...)` enforces complete-turn retention, while
explicit compaction creates a bounded summary/reference archive. Long-lived
sessions therefore remain bounded without leaving orphaned tool results. The
retention policy keeps a bounded recent transcript plus an optional
summary/reference archive.

### AR-024: Desktop mutations were under-gated — high (fixed 2026-08-05)

The confirmation policy now treats semantic UI click/type, mouse activation and
drag actions, keyboard text/shortcuts/execution keys, window close, and session
lock as explicit user decisions. Read-only inspection, pointer movement,
scrolling, window focus/geometry, and session status remain available without a
confirmation prompt. Unlock is deliberately absent from the model tool surface;
it remains an authenticated channel-broker operation. The first-party `session`
tool exposes only verified `status` and `lock` actions.

### AR-025: Tool invocation was mistaken for evidence — high (fixed 2026-08-05)

Evidence accounting now advances only after a completed, usable tool result.
Errors, rejected results, explicit `success: false` responses, and stopped calls
cannot suppress the unverified-answer warning. Invocation count remains useful
for loop limits and reflection heuristics, but is not proof of current state.

### AR-026: First-party Feishu logs exposed message and principal data — high (fixed 2026-08-05)

Normal ingress, worker, and delivery logs no longer contain message bodies or
raw Feishu `open_id` values. They record message type, character count, queue
size, file basename, and a stable truncated SHA-256 principal identifier. This
closes application-owned logging leakage; AR-022 remains open for credentials
that may be emitted inside third-party SDK transport records.

### AR-027: Live session history disappeared on daemon restart — high (fixed 2026-08-05)

Conversation state was previously held only in the in-memory `SessionManager`.
Restarting the service therefore preserved durable memories but discarded the
dialogue needed to resolve short follow-ups such as “好的” or “查一下吧”. A
small `session_transcripts` table now restores each stable session ID at first
use and saves the reference-only, post-turn transcript after every turn.
Completed tool payloads are compacted to status/artifact placeholders before
writing. Explicit `/clear` deletes both the in-memory state and its persisted
transcript; LRU eviction does not.

### AR-028: Context reservation could collapse to a 256-token floor — high (fixed 2026-08-05)

The request planner previously subtracted the configured completion maximum and
static tool schemas from the model window, then clamped the remaining history
budget to `256`. An optimistic output setting could therefore erase nearly all
conversation context. Input and completion budgets are now allocated together:
completion is capped at half the usable window, and the remainder is assigned
to history. Deterministic trimming may drop all older turns when necessary,
while the Agent rejects a still-overlarge current request before sending it to
the provider. The deployed configuration uses `50000` for the Volcengine main
models and `16384` for the local Qwen-VL perception model.

### AR-029: Feishu cards rendered CommonMark as plain text — medium (fixed 2026-08-05)

Feishu cards use a `div.text` element with `tag: "lark_md"`. The official
references are [Card JSON 2.0 structure](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/card-json-v2-structure)
and [Markdown content](https://open.feishu.cn/document/common-capabilities/message-card/message-card-components/content-components/markdown).
The channel preserves the documented pipe-table syntax verbatim and only
adapts constructs that need a stable fallback, such as headings and fenced
code blocks, at the delivery boundary. Supported emphasis, links, lists, and
tables remain intact.
The canonical conversation still stores the model's original Markdown; only
the external card representation is transformed.

### AR-030: Scheduled Agent runs were notification-only — high (fixed 2026-08-05)

Scheduler tasks now distinguish a reminder `message` from an Agent `command`.
When a task is created during a channel session, the opaque session reference
is persisted with the task. At execution time `command` starts a full Agent
run, and the result is delivered back through the originating channel (for
example, to the Feishu user who created it). Session identifiers are never
model-visible and are not accepted as tool parameters.

## 7. Defect-hardening matrix

| Priority | Verified defect | Root cause | Minimal repair | State |
|---|---|---|---|---|
| P0 | Post-verify captured pre-action state | Verifier finalized before registry execution | Separate authorization from `post_verify`; invoke only after successful execution | fixed, targeted tests passing |
| P0 | Same-session state can interleave | No run-scoped lock on mutable session transcript | Serialize full runs per session | fixed, targeted test added |
| P0 | Base64/data URLs enter history and persistence paths | Provider payload block doubles as canonical history block | ArtifactStore + reference-only history + request-scoped hydration | fixed; service ingress is reference-only |
| P1 | Tool execution can bypass verifier | Public registry dispatch is also the commit capability | Bind authorization and single commit behind a verified executor/internal capability | fixed; opaque single-use prepared call |
| P1 | GUI target selection is ambiguous/stale | Partial-name match returns first element without snapshot identity | Snapshot-bound `ElementRef`, uniqueness/freshness checks | fixed; ambiguity/staleness/identity tests added |
| P1 | Logs/runtime data use unrelated roots | Paths are resolved independently by config, service, audit and idempotency | One `RuntimePaths` resolver with sibling `logs/`, `attachments/`, `artifacts/`, `data/`, `cache/` | fixed; custom runtime root and cleanup lifecycle tested |
| P2 | Vision compatibility is a boolean | Provider/model/role/MIME limits are collapsed into `supports_vision` | Typed provider capability contract | fixed; provider limits and role serialization tested |
| P2 | TUI selection copy was shadowed by cancellation and popup behavior | App-level key override and GUI-style root popup | Restore Textual copy semantics, move cancellation to Esc, use OSC52 plus local clipboard | fixed |
| P2 | Screenshot files and Feishu delivery are implicit | Generic CWD filenames and ignored tool-result image paths | Unified temporary artifact allocator plus explicit channel-safe image metadata | fixed; filesystem and Feishu regressions added |
| P1 | Durable memory crosses session/principal boundaries | Global UserMemory and unfiltered recent EpisodicMemory are injected into every request | SQLite principal/session scopes, core/relevant policy, explicit episode recall | fixed; scoped regressions added |
| P1 | Text-only main model cannot process image tasks safely | Image ingress is coupled directly to main-provider multimodality | Attachment manifest + dedicated perception-only `image_inspect` provider + evidence gate | fixed; isolation, caching, ownership, and no-base64 regressions added |
| P1 | Context budget can be exceeded by mandatory/current segments and tool schemas | Truncation only drops older turns and does not budget the full provider request | Reserve completion and compact static tool-schema tokens before deterministic history trimming | fixed; request path accounts for schema and completion reserve |
| P1 | Feishu can publish raw model thinking | Channel renders `stream_think_delta` as response-card content | Channel-safe progress events; discard raw reasoning outside explicit local diagnostics | fixed; Feishu only renders bounded status/final output |
| P1 | Credentials can appear in third-party transport logs | No global redaction filter for SDK-emitted URLs and query parameters | Root-handler redaction plus sensitive-log regression tests | open |
| P1 | Desktop actions can mutate state without confirmation | Confirmation policy covered text input but not click, shortcut, close, or lock semantics | Gate state-changing desktop actions and keep unlock outside model tools | fixed; action matrix tests added |
| P1 | Failed tool calls satisfy evidence requirements | Evidence counted authorized invocations rather than successful results | Count only completed non-error results | fixed; failed-tool warning regression added |
| P1 | Feishu application logs expose messages and principals | INFO records include message body and raw `open_id` | Structured metadata plus stable principal hash | fixed; delivery log regression added |
| P1 | Session context disappears after service restart | SessionManager retained transcripts only in RAM | Persist reference-only transcripts in SQLite and restore by stable session ID | fixed; repository round-trip regression added |
| P2 | `/config set` appears live but architecture fields are not applied | Mutable config object is detached from constructed collaborators | Mark restart-required fields; atomic apply handlers for a small dynamic subset | open |
| P2 | `/compact` clears the session | Three client implementations duplicate command semantics | One Agent command contract invoking real conversation compression | fixed; `/clear` remains destructive and `/compact` is mechanical |
| P2 | Live-session transcript grows without a retention bound | `max_messages` is non-functional and only request assembly is bounded | Explicit transcript retention and summary/archive policy | fixed; complete-turn retention and compaction are bounded |

### 7.1 Recommended refactor order

1. Close information-boundary defects: remove channel reasoning exposure and
   redact third-party credentials from all logs.
2. Introduce a complete request budget planner, including tool schemas and
   completion reserve; expose its metadata through `/context` diagnostics.
3. Centralize command semantics (`clear`, `compact`, configuration) behind the
   Agent/service contract so TUI, CLI, and Feishu cannot diverge.
4. Split Feishu into ingress, correlation, delivery, and Agent bridge modules.
5. Extract an `AgentFactory` composition root and a smaller `TurnOrchestrator`;
   keep the verified executor, provider adapters, memory repositories, and
   attachment broker as existing boundaries rather than rewriting them.

## 8. Hardening boundaries

The replacement `secure-multimodal-gui` hardening plan owns:

- Runtime layout and temporary attachment lifecycle.
- Reference-only multimodal history and request-boundary hydration.
- Provider role-aware vision serialization.
- Stable GUI targeting, coordinate transforms, trust labels, and verification
  ordering.

It does not own a general rewrite of Agent, all historical architecture debt,
remote grounding deployment, semantic tool selection, or unrelated memory and
web-tool improvements.

## 9. Verification expectations

- Unit tests for runtime path resolution and attachment expiry/deletion.
- Contract tests proving no `data:image` or image base64 appears in conversation,
  event, trace, audit, cache, or idempotency representations.
- Provider payload tests by role and supported capability.
- Multi-monitor coordinate tests including negative origins and scaled regions.
- Safety tests proving precondition/confirmation occurs before execution and
  postcondition evidence occurs after execution.
- Full `pytest` with the configured coverage policy before completion claims.

Current verification record (2026-08-05): the exact test count is recorded with
the release commit; the skipped tests require explicit `RUN_LIVE_E2E=1`.
Repository-wide statement coverage is 61.31%, below the pre-existing 80% gate.
The threshold has not been lowered; closing that cross-module test debt remains
separate from the completed functional hardening repairs above.
