# PC Assistant Architecture

> Status: current-code architecture review and defect-hardening anchor
> Last updated: 2026-08-04
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

The architecture now follows industry practice at its critical execution,
multimodal, runtime-storage, and GUI-targeting boundaries. It is still not a
finished reference architecture as a whole because Agent construction remains
concentrated and Linux accessibility support depends on optional system
packages.

Mature foundations already present include a canonical provider-neutral message
layer, provider adapters, typed configuration, an `AgentLike` service port,
session isolation, deterministic safety/confirmation checks, tool schemas,
idempotency, context assembly, and observability. These should not be rebuilt.

The previously verified critical boundary defects have been repaired: binary
payloads are reference-only outside request hydration, postconditions run after
execution, tool commit uses a single-use verified capability, same-session
turns serialize, runtime paths have one resolver, and GUI targets bind to fresh
snapshots. Continued work should remain incremental rather than becoming a
general Agent rewrite or remote grounding service.

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
- Maintain user, episodic, and procedural memory.

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
├── attachments/   session-scoped temporary binary observations
└── cache/         reconstructable non-authoritative data
```

`attachments/` is a sibling of `logs/`, never a child of it. Socket and PID
files may remain under an OS-appropriate ephemeral runtime location, while
durable logs and bounded attachments use the configured application runtime
root.

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
  -> AttachmentStore.put(session, bytes, metadata)
  -> image_ref / observation_ref in conversation
  -> RequestAssembler selects references needed for this call
  -> AttachmentStore.resolve(ref)
  -> provider-specific temporary encoding
  -> request completes
  -> encoded payload released; reference remains bounded
```

History serialization of a non-hydrated reference is a short placeholder with
an identifier, source, dimensions, trust level, expiry, and bounded summary.
The MVP does not expose model-initiated recall: RequestAssembler automatically
hydrates only the bounded references selected for the current request. An
expired reference requires a new upload or capture.

### 4.3 GUI action target

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

### 4.4 Current context composition and scope

The provider request is not simply the current session transcript. Current
assembly contains:

| Context segment | Current scope | Current selection |
|---|---|---|
| System prompt | application | always present |
| Conversation transcript | session | at most the latest eight dialogue turns, then token truncation |
| UserMemory profile | global process/storage | up to ten highest-confidence items, not session/principal filtered |
| EpisodicMemory | global process/storage | latest three episodes, currently not session filtered |
| Procedural rules | application/workspace | bounded shared rules |
| Working-directory/session metadata | current request | always regenerated |
| Evidence/safety instruction | current turn | only when required |
| Image observations | session + request | durable references; request-only binary hydration |

Other sessions' raw conversation messages are not copied into the current
`SessionState`. However, global UserMemory and unfiltered EpisodicMemory mean
facts or summaries originating in another session can currently enter the
request. For Feishu this also means different users do not yet have isolated
user-memory namespaces. This is a context-scope defect, not conversation-store
interleaving.

### 4.5 Target complete-context design

Context should be assembled from typed, independently budgeted segments rather
than one implicit prompt string. Each segment needs at least:

```text
scope: application | principal | workspace | session | turn
owner_id: principal/workspace/session identifier where applicable
source: user | system | tool | memory | channel
trust: trusted-policy | user-stated | observed | model-derived
sensitivity: public | private | secret
ttl / freshness
token_budget
provenance reference
```

Recommended request order and default policy:

1. Immutable system and safety policy.
2. Workspace/project rules explicitly selected for the active workspace.
3. Principal-scoped user preferences, never global across unrelated users.
4. Current-session transcript and current-session summary only.
5. Query-relevant episodic retrieval restricted to the same principal; other
   sessions require an explicit cross-session recall policy.
6. Current-turn input, attachments, tool observations, and evidence directive.

The assembler should enforce separate budgets for policy, transcript, memory,
retrieval, and observations. It should emit a metadata-only `ContextManifest`
for `/context` diagnostics showing which segment IDs were included, omitted,
expired, or truncated; it must never include image bytes, secrets, or complete
provider payloads.

Clear operations also require explicit semantics:

- `/clear`: current session transcript, pending observations, and attachments.
- `/memory clear`: current principal's durable profile and episodes.
- Workspace-rule deletion: separate administrative operation.
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
`ConversationManager`. `AttachmentStore` now writes session-scoped temporary
files; Conversation accepts `image_ref` blocks and rejects provider image
payloads; request assembly hydrates only a copied provider request. Events are
redacted and binary results are not persisted in idempotency. Service clients
upload images first and run requests accept `attachment_id` references only.

### AR-005: Runtime path ownership was fragmented — high (fixed 2026-08-04)

`RuntimePaths` now resolves sibling `logs/`, `attachments/`, and `cache/`
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

### AR-008: Platform accessibility dependencies are incomplete — medium

The Python environment contains Pillow, mss, pyautogui, and pywinctl, but Linux
AT-SPI imports (`pyatspi`, `gi`) are unavailable. Dependencies must be expressed
as platform extras or documented system packages, with explicit fallback to the
screen layer.

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

### AR-013: Durable memory scope is broader than session/principal — high

Session transcripts are isolated, but `UserMemory` is one global store and
`EpisodicMemory.build_context_string()` selects recent episodes without
filtering `session_id`. Consequently, another session's summary can enter the
current request, and different Feishu users can share extracted profile facts.
The repair is a principal/workspace/session scope model plus default-deny
cross-session retrieval. This should be implemented behind a context assembler
and memory repository boundary, not by copying more history into Conversation.

## 7. Defect-hardening matrix

| Priority | Verified defect | Root cause | Minimal repair | State |
|---|---|---|---|---|
| P0 | Post-verify captured pre-action state | Verifier finalized before registry execution | Separate authorization from `post_verify`; invoke only after successful execution | fixed, targeted tests passing |
| P0 | Same-session state can interleave | No run-scoped lock on mutable session transcript | Serialize full runs per session | fixed, targeted test added |
| P0 | Base64/data URLs enter history and persistence paths | Provider payload block doubles as canonical history block | AttachmentStore + reference-only history + request-scoped hydration | fixed; service ingress is reference-only |
| P1 | Tool execution can bypass verifier | Public registry dispatch is also the commit capability | Bind authorization and single commit behind a verified executor/internal capability | fixed; opaque single-use prepared call |
| P1 | GUI target selection is ambiguous/stale | Partial-name match returns first element without snapshot identity | Snapshot-bound `ElementRef`, uniqueness/freshness checks | fixed; ambiguity/staleness/identity tests added |
| P1 | Logs/runtime data use unrelated roots | Paths are resolved independently by config, service, audit and idempotency | One `RuntimePaths` resolver with sibling `logs/`, `attachments/`, `cache/` | fixed; custom runtime root and cleanup lifecycle tested |
| P2 | Vision compatibility is a boolean | Provider/model/role/MIME limits are collapsed into `supports_vision` | Typed provider capability contract | fixed; provider limits and role serialization tested |
| P2 | TUI selection copy was shadowed by cancellation and popup behavior | App-level key override and GUI-style root popup | Restore Textual copy semantics, move cancellation to Esc, use OSC52 plus local clipboard | fixed |
| P2 | Screenshot files and Feishu delivery are implicit | Generic CWD filenames and ignored tool-result image paths | Unified temporary artifact allocator plus explicit channel-safe image metadata | fixed; filesystem and Feishu regressions added |
| P1 | Durable memory crosses session/principal boundaries | Global UserMemory and unfiltered recent EpisodicMemory are injected into every request | Typed principal/workspace/session scopes with default-deny cross-session retrieval | open; architecture target recorded |

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

Current verification record (2026-08-04): default non-live regression is
`701 passed, 4 skipped`; the skipped tests require explicit `RUN_LIVE_E2E=1`.
Repository-wide statement coverage is 61.31%, below the pre-existing 80% gate.
The threshold has not been lowered; closing that cross-module test debt remains
separate from the completed functional hardening repairs above.
