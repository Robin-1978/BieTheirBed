# Agent Framework Forward Design

> Status: approved forward-design baseline
> Date: 2026-08-08
> Scope: production agent runtime, service protocol, scoped state, tool safety,
> reliability, observability, and engineering gates
> Relationship: this document is the authoritative target-state design.
> `agent-runtime-decomposition-phases.md` may provide implementation sequencing,
> but must conform to this design and must not preserve incompatible legacy
> behavior.

## 1. Executive decision

PC Assistant will implement a principal-scoped production runtime directly from
the target domain model. The old concrete `Agent`, free-form service protocol,
caller-owned session identifiers, global active-session behavior, and concrete
member reach-through are not compatibility constraints.

The design has five goals:

1. Bind every operation to a transport-established principal and an owned,
   opaque session handle.
2. Keep one deterministic authorization-to-commit boundary for every
   registered tool.
3. Make cancellation, disconnects, retries, transcript persistence, and stream
   termination explicit and testable.
4. Replace concentrated Agent ownership with cohesive production operations.
5. Enforce functional, coverage, lint, type, and dependency quality gates in a
   reproducible CI path.

This is a direct target-state replacement. Existing implementations may be
reused only when they satisfy the new contracts without adapters, legacy modes,
semantic branching, or weakened invariants. Reuse is an implementation choice,
not an architectural requirement.

### 1.1 Implementation status

Implemented on 2026-08-08 as target-state foundations, not legacy production
adapters:

- strict discriminated Core API v1 request DTOs that reject the old free-form
  `method + params` shape and caller-supplied principals;
- Core-generated opaque session handles with durable principal ownership,
  scoped transcript persistence, active-session isolation, and
  indistinguishable foreign/unknown lookup failures;
- cancellation contracts targeting `run_id` rather than a bare or implicit
  session;
- Core-generated run IDs with principal-scoped cancellation, request-local
  cancellation events, opaque run handles, and immutable terminal outcomes;
- ordered public run-event sequencing with exactly one terminal event;
- a scoped ControlService with separate typed operations for status, history,
  memory, tools, session creation, and local-admin configuration;
- a transport-neutral CoreApplication that binds trusted principals to owned
  sessions, RuntimeScope, runtime streams, cancellation, redacted failure
  events, and public RunEvent sequencing;
- a strict CoreServer/CoreClient pair with authenticated request demultiplexing,
  concurrent run streaming and cancellation, complete typed control operations,
  connection-loss cleanup, and no legacy protocol translation;
- scoped artifact ingress and egress that verify principal/session ownership,
  exchange bounded data URLs, never expose server paths, mark successful
  delivery, and save downloads only on the client side; run attachments contain
  only an opaque `artifact_id` and optional caption, and ArtifactStore exposes no
  public path-resolution API; delivery is acknowledged only after the WebSocket
  send succeeds, while local save failures remain client-side warnings;
- an independent CoreServiceHost for one token-authenticated loopback TCP
  WebSocket endpoint, including a generated owner-only local credential,
  optional configured remote credential, and explicit lifecycle cleanup;
- explicit effect, capability, and risk metadata for every built-in tool, with
  capability-filtered schema exposure, workspace-root enforcement, strict tool
  names, registration-time validation, and Draft 2020-12 JSON Schema checks;
- a single-call ToolStep authorization boundary covering normalization, schema,
  capability, workspace, confirmation, cancellation, commit, and typed terminal
  results;
- a provider-neutral ModelStep with deterministic prompt/tool budgeting,
  ephemeral artifact hydration, normalized streaming, cancellation, redacted
  failures, and per-call usage/latency/TTFT results;
- a request-local ReActLoop with serial multi-tool execution, complete
  tool-call/result pairing, explicit iteration/tool limits, and typed outcomes;
- a principal-scoped AgentRuntime with exact run cancellation context,
  per-session serialization, memory-scope binding, transcript transactions,
  success-only persistence, cancellation/failure rollback, reference-counted
  lease cleanup, and turn completion tracing only after durable commit;
- direct OpenAI-compatible and Anthropic HTTP providers, with normalized tool
  calls, active response closure on cancellation, redacted failures, and
  fallback only before observable primary output;
- a single forward-only composition root that owns runtime paths, stores,
  provider selection, capability profiles, tool registry, Core services, and
  the loopback WebSocket endpoint without calling the old AgentFactory;
- connection-scoped confirmation round trips, including timeout/disconnect
  denial and routing back only to the client that initiated the run;
- screenshot artifact/image-reference pairing so the next model step can see
  the captured desktop without persisting paths, bytes, or provider payloads;
- daemon-side desktop-session preparation before desktop observation or control,
  with environment discovery failing closed before tool commit;
- call-specific policy for mixed read/write tools so clipboard, mouse position,
  memory lookup, and window inspection do not inherit mutation confirmation,
  while separate minimum schema capabilities prevent unavailable calls from
  being advertised;
- correlated model/turn JSONL traces with stable hashes for principals,
  sessions, runs, and client request IDs, input lengths instead of user text,
  actual provider model, and
  explicit failover attribution; transcript persistence failure is recorded as
  a failed turn rather than a completed turn;
- bounded CoreClient requests that close the connection on timeout so a request
  that may already be running cannot become an orphan, plus server disconnect
  cleanup that cancels run tasks even before a run ID has been published;
- connection-level resource bounds: CoreServer admits at most eight active runs
  per connection, while each CoreClient run stream buffers at most 256 events;
  admission overflow returns `resource_exhausted`, and client stream overflow
  closes the connection so one slow consumer cannot block demultiplexing or grow
  memory without bound;
- process-level run admission is owned by CoreRunRegistry and capped at 32
  active handles across all connections; it reuses the same
  `resource_exhausted` outcome rather than adding another limiter service;
- CoreClient accepts `run_accepted` only for a currently pending run request and
  caps pending confirmation handlers at eight, so unsolicited protocol messages
  cannot allocate unbounded queues or background tasks;
- restart-bound configuration writes to the same explicit configuration file
  used at startup, or to `runtime_root/config/local.yaml` when no explicit file
  was supplied;
- fail-closed transport configuration: generated owner-only local credentials,
  non-empty configured remote credentials and principals, and loopback-only
  plain TCP until TLS exists;
- owner-only runtime state: transcript/memory/artifact databases, persisted
  configuration, traces, and managed artifact/download files use 0600; new
  runtime-owned directories use 0700 without changing permissions on an existing
  user-selected config/log parent; borrowed files retain original permissions;
- fail-fast SQLite ownership: runtime session, memory, episode, and artifact
  schemas are checked exactly at startup, required indexes are checked, and
  session foreign-key violations require explicit offline repair;
- bounded external input and output: built-in HTTP tools stream through hard
  byte limits, model SSE streams cap both total bytes and line size, large file
  reads never load the entire file before truncation, and artifact hydration
  rechecks size immediately before reading;
- bounded durable/input state: each persisted session transcript is capped at
  16 MiB before JSON parsing, and YAML configuration files are capped at 1 MiB
  before parsing;
- only the currently used `react` and benchmark `reflection` model purposes;
  speculative planner, compaction, and vision purpose variants have been removed;
- executable CI for clean package build, ruff, the 426-test functional suite,
  and an 80% authority-boundary coverage floor (currently 88.17%); source and
  wheel manifests exclude workspace/cache/private state, and the wheel carries
  its required default configuration resource;
- Core-native daemon, one-shot CLI, Textual TUI, and benchmark consumers. The
  old ServiceServer, ServiceClient, free-form protocol, lifecycle fallback, and
  compatibility UI have been removed from production and from the repository.

The monolithic Agent/Factory, old service/UI/channel/session/harness stacks,
vision sidecar, scheduler, MCP bridge, compatibility utilities, and their
obsolete tests have been removed. Package initialization no longer imports a
parallel runtime or broad provider surface.

## 2. Design principles

### 2.1 High cohesion

Each component owns one operation that changes for one reason:

- `CoreServer`: authentication, transport decoding, public protocol, connection
  lifecycle, and mapping public events.
- `AgentRuntime`: principal/session ownership, run leases, transaction boundary,
  cancellation, persistence, and control operations.
- `ReActLoop`: Reasoning -> Acting -> Observation iteration only.
- `ModelStep`: one model request, including prompt assembly, streaming parsing,
  usage accounting, and provider-neutral output.
- `ToolStep`: one proposed tool call from validation through terminal result.
- `ControlService`: scoped status, history, memory, configuration, and new-session
  commands.

No component is created solely to reduce line count.

### 2.2 Low coupling

Callers depend on small behavioral contracts rather than concrete runtime
internals:

```text
Client -> Core API -> CoreServer -> AgentRuntimePort
                                    |
                                    +-> ReActLoop
                                         +-> ModelStep
                                         +-> ToolStep
```

The server must not reach into `agent.registry`, `agent.memory`,
`agent.config`, or `agent.conversation`. Scheduler code receives a `TurnInvoker`,
not a complete Agent. Tools never receive a transport client.

### 2.3 YAGNI and KISS

The implementation must not introduce:

- microservices or a remote execution coordinator;
- an event bus for in-process events;
- distributed locks or distributed transactions;
- a generic policy language;
- enterprise RBAC before more than one privilege class exists;
- parallel legacy and new runtimes;
- compatibility shims for private Agent members;
- interfaces with only hypothetical consumers.

Use SQLite transactions, `asyncio` locks, typed Pydantic contracts, enums, and
plain functions where they are sufficient.

### 2.4 Fail closed at authority boundaries

Unknown principal ownership, unknown tool effect, invalid protocol fields,
missing confirmation channels, and incomplete authorization all reject the
operation. Fail-open behavior is acceptable only for presentation-only
enrichment that cannot authorize or commit an action.

### 2.5 Direct cutover

The new runtime, server, and clients replace the old production path atomically.
All first-party consumers are updated in the same cutover and the old concrete
path is deleted. The repository must never have two production authorities for
tool execution or session persistence.

### 2.6 No legacy semantic compatibility

The new design does not preserve old behavior merely because callers or tests
currently depend on it. In particular, it does not preserve:

- empty cancellation meaning global cancellation;
- caller-selected or semantically encoded session IDs;
- unscoped memory and control operations;
- free-form request parameter dictionaries;
- implicit in-process fallback;
- direct access to runtime registry, memory, configuration, or conversation;
- unknown or unclassified tools defaulting to read-only or low risk;
- incomplete tool-call/result transcripts;
- multiple terminal responses for one run.

Tests that assert those behaviors are deleted or rewritten against the new
contract. No compatibility flag, dual-read path, legacy DTO, translation shim,
or deprecation period is introduced.

## 3. Required invariants and reusable implementations

The target design requires the following invariants:

- ToolStep is the only production path from a model proposal to tool commit;
- same-session turns serialize while different sessions may run concurrently;
- cancelled and failed turns can roll back to a transcript watermark;
- durable multimodal history stores references rather than provider payloads;
- ArtifactStore validates artifact/session association;
- durable memory queries accept principal and session scopes;
- model adapters normalize supported provider protocols;
- context assembly budgets prompt, schemas, history, and requested output;

Request-scoped memory has no process-wide default identity. Access outside a
bound RuntimeScope fails immediately; values such as `local:default` and empty
artifact sessions are not fallback scopes. Artifact session ownership uses the
full SHA-256 derivation rather than a shortened compatibility key.

Current code that already proves one of these invariants may be moved or reused
after contract-level verification. Names, call shapes, storage schemas, and
private APIs are not preserved for compatibility.

## 4. Deployment and threat model

### 4.1 Supported trust profiles

The framework supports two explicit profiles:

| Profile | Intended use | Identity | Default capabilities |
|---|---|---|---|
| `personal_local` | One OS user, local CLI/TUI | managed local WebSocket credential | workspace read/write, configured desktop access, confirmation-gated mutation |
| `remote_scoped` | Token-authenticated loopback TCP client | authenticated principal | least privilege; no workspace escape or unrestricted shell by default |

Local does not mean untrusted model output becomes trusted. Prompt injection,
malicious files, web content, and model proposals remain
untrusted in both profiles.

### 4.2 Principal derivation

Principals are established by the transport, never accepted from request JSON:

- the owner-only managed local credential -> `local`;
- configured TCP credentials -> authenticated identity, not a client-provided name;

Plain WebSocket TCP endpoints bind only to loopback. Non-loopback transport is
rejected until certificate-backed TLS is a real configured feature; bearer
tokens are never sent over an unauthenticated network channel.
Connections that do not complete the authentication-first handshake within the
bounded authentication timeout are closed without allocating a session or run.

A single shared TCP token represents one principal. Supporting multiple remote
principals requires distinct credentials; it does not require a general RBAC
system.

### 4.3 Protected resources

Every access to the following resources is scope checked:

- sessions and transcripts;
- context summaries and durable memory;
- artifacts;
- active runs and confirmations;
- configuration and control commands.

## 5. Identity and session ownership

### 5.1 Runtime scope

The application boundary is defined by this authoritative contract:

```python
class RuntimeScope:
    principal_id: str
    session_handle: str
```

`principal_id` is transport-established. `session_handle` is an opaque random
identifier with at least 128 bits of entropy. Semantic identifiers such as
`feishu:<open_id>` must not be public session capabilities.

### 5.2 Ownership persistence

Session persistence stores, at minimum:

```text
session_handle PRIMARY KEY
principal_id   NOT NULL
created_at     NOT NULL
updated_at     NOT NULL
```

Transcript and context rows are accessed only after resolving the session under
the current principal. A session handle owned by another principal returns the
same external error as an unknown handle.

The implementation uses the Core-owned SQLite database. Existing repository
code may be replaced or adapted internally, but the runtime does not support an
old ownership-free schema alongside the new schema. If retained user data needs
migration, use a one-time offline migration before startup; runtime dual-read,
dual-write, and fallback interpretation are forbidden.

Every Core-owned repository follows the same rule: startup validates exact
table columns, types, nullability, defaults, and primary keys. Required lookup
indexes are checked, and session transcript/active-pointer foreign keys are
checked for both schema and stored-data integrity. The artifact registry is
subject to the same validation. Runtime code never performs an opportunistic
`ALTER TABLE` compatibility migration.

The transcript table stores only the actual message JSON and update timestamp;
unused summary/counter columns are not speculative compatibility fields. A
transcript is limited to 16 MiB per session. Save checks UTF-8 bytes before SQL,
and load checks SQLite blob length before fetching or parsing JSON.

### 5.3 Operation rules

- `run`: creates or resolves a session for the authenticated principal.
- `history/export`: reads only the resolved owned session.
- `artifact upload/download/deliver`: uses the same resolved scope and never
  returns a server filesystem path. Run attachments are strict opaque artifact
  references; raw paths, inline data URLs, and client-supplied media metadata are
  not attachment contract variants.
- `cancel`: targets an owned run, never a caller-supplied bare session ID.
- `memory`: binds the runtime memory ContextVar before read, clear, or write.
- `new session`: creates a new owned opaque handle; it never embeds the prior
  session ID.
- `config`: allowed only for the local principal until another real privilege
  class is required.

## 6. Public Core API v1

### 6.1 Request envelope

The wire protocol is versioned and rejects unknown fields:

```json
{
  "api_version": "v1",
  "request_id": "client-stable-id",
  "method": "run",
  "session_handle": "opaque-handle",
  "input": "capture the current desktop",
  "attachments": [],
  "tools_enabled": true
}
```

Pydantic models use `extra="forbid"`, strict types, bounded strings and arrays,
and method-specific fields. A free-form `params: dict` is not a public
contract.

### 6.2 Run event envelope

Every run event carries:

```json
{
  "api_version": "v1",
  "run_id": "server-run-id",
  "event_seq": 1,
  "event_type": "tool_result",
  "payload": {}
}
```

`event_seq` starts at one and is strictly increasing per run. The server emits
exactly one terminal event: `completed`, `cancelled`, or `failed`.

An exception must not produce both an error and a successful `done` result.

### 6.3 Cancellation

Cancellation targets `run_id` and is validated against the current principal.
For interactive convenience, CoreClient offers `cancel_active()`, which resolves
its locally tracked active run and sends that exact ID. The wire request itself
always requires a non-empty run ID; the server never interprets an empty cancel
request as “cancel every client”.

Cancellation is idempotent:

- active run -> cancellation accepted;
- a repeated request while cancellation is in progress -> remains cancelling;
- terminal runs are removed from the active registry after their final event;
- unknown or foreign run -> not found.

### 6.4 Disconnect behavior

On connection loss, CoreClient must:

1. mark the transport disconnected;
2. reject and remove every pending request future;
3. place a terminal connection error into every run queue;
4. clear confirmation handlers associated with the connection;
5. make subsequent sends fail immediately.

Request timeout and send failure also remove their pending entries in `finally`.
Either condition closes the connection and releases all other waiters because a
failed send has an unknown transport outcome.
Streaming consumers must never wait forever on `queue.get()` after reader exit.
A request timeout closes the entire connection, causing server-side disconnect
cleanup to cancel every associated run task. The server cancels the task itself
in addition to any already-published run ID, closing the acceptance/publication
race without requiring a second run registry.

Resource admission and stream buffering are deliberately simple and bounded.
CoreServer permits at most eight active run tasks per connection and returns
`resource_exhausted` before task creation when the limit is reached. CoreClient
uses a 256-event queue per accepted run and never awaits a slow run queue from
the shared reader. If that queue fills, the client closes the connection and
injects a terminal overflow error into all waiters; it does not add an event bus,
disk spool, or unbounded fallback queue.
CoreRunRegistry additionally admits at most 32 active run handles across every
connection and endpoint. This is the only process-level run quota because the
registry already owns the authoritative active-run set.
Run acceptance is correlated with a live pending request before a queue is
created. Pending confirmation resolution tasks are capped at eight. Either an
unsolicited acceptance or confirmation overflow is a protocol failure that
closes the connection and releases all waiters.

Artifact egress follows the same transport boundary: preparing bytes does not
mark delivery. The server acknowledges delivery only after the complete response
send returns successfully. A client-side decode or filesystem failure is a local
warning and does not abort consumption of the authoritative run stream.

## 7. Runtime transaction model

### 7.1 Session lease

`AgentRuntime.run()` resolves ownership and acquires one per-session
`asyncio.Lock`. The lease covers snapshot, ReAct execution, terminal outcome,
rollback or commit, and persistence.

Different session handles may execute concurrently. No global Agent active
session participates in request routing or cancellation.
Lease entries are reference counted and removed after the last holder or waiter
exits, so the lock table does not grow with historical session handles.

### 7.2 Transcript transaction

Before adding the user turn, runtime records a conversation watermark. Outcomes
are handled as follows:

| Outcome | Transcript action |
|---|---|
| completed | persist complete paired turn |
| cancelled | roll back to watermark |
| failed before a valid terminal response | roll back to watermark |
| tool/iteration limit | either complete every declared tool call with a typed rejection or roll back the turn |

A completed turn is reported to the turn observer only after transcript commit
succeeds. If persistence fails, the run fails and observability records
`transcript_persistence_failed`; it must never retain a completed trace for an
uncommitted turn. Observer failure remains presentation-only and cannot change
the run outcome.

Authorization audit records are append-only and are not rolled back with the
conversation.

### 7.3 Tool-call pairing invariant

For every assistant tool call persisted into provider history, exactly one tool
result must follow before another assistant message is sent.

For a multi-tool model response:

- each call is processed in declared order unless parallel execution is later
  explicitly supported;
- rejected calls receive a typed rejection result;
- calls skipped because of cancellation, limits, or loop detection receive a
  typed `not_executed` result, or the entire turn is rolled back;
- prompt assembly defensively excludes any incomplete historical tool group.

Parallel tool execution is out of scope until effects and ordering semantics can
be proven safe.

## 8. Runtime component boundaries

### 8.1 AgentRuntime

Owns:

- scope and ownership validation;
- session lease and transcript transaction;
- run registry and cancellation;
- memory scope binding;
- persistence and artifact lifecycle;
- invocation of ReActLoop;
- terminal RuntimeEvent emission.

Does not own provider parsing, tool schema validation details, UI delivery, or
channel identifiers.

### 8.2 ReActLoop

Owns:

- iteration and tool-call limits;
- Reasoning -> Acting -> Observation sequencing;
- deciding whether to request another ModelStep;
- accumulating turn-level evidence and outcome.

It receives a request-scoped context object. It does not look up global sessions,
write SQLite directly, authenticate clients, or construct providers/tools.

### 8.3 ModelStep

One invocation performs:

1. context and tool-schema assembly;
2. budget calculation and deterministic truncation;
3. one provider call or stream;
4. normalized content/tool-call parsing;
5. usage, latency, TTFT, cache, and error recording;
6. cancellation propagation.

The production runtime has one ReAct model-call path. Provider failover is
allowed only before observable primary output and reports the actual completing
model to the same trace pipeline. Benchmark judging is a separate first-party
consumer of the provider port, not a hidden runtime authority.
Fallback must resolve to a model distinct from the primary; retrying the same
endpoint/model under a second label is rejected as configuration error or
treated as no fallback.

### 8.4 ToolStep

One invocation performs:

```text
normalize
  -> full schema validation
  -> effect/capability policy
  -> workspace and execution-environment checks
  -> confirmation
  -> commit
  -> terminal tool result
```

ToolRegistry exposes no public unchecked execution API. ToolStep performs the
single internal commit only after every preceding check succeeds.

### 8.5 ControlService

Owns scoped commands such as:

- health and status;
- new session;
- history/export;
- memory list/clear;
- tool descriptions;
- configuration read/change;

It receives `RuntimeScope` for every operation and never uses the default memory
scope implicitly.

Restart-bound configuration changes are atomically written to the configuration
source that the next process start will load. With an explicit configuration
path, that file is updated; otherwise overrides live at
`runtime_root/config/local.yaml`. A write-only sidecar filename that startup
does not read is forbidden. Both `PC_ASSISTANT_HOME` and the higher-priority
`PC_RUNTIME_ROOT` select that canonical runtime root.

## 9. Tool effect and capability model

### 9.1 Required metadata

Every tool declares explicit effect metadata:

```python
effect: READ_ONLY | LOCAL_WRITE | EXTERNAL_SIDE_EFFECT | DESKTOP_CONTROL
capabilities: set[Capability]
risk: LOW | MEDIUM | HIGH
```

Minimum capabilities are:

```text
workspace_read
workspace_write
host_read
host_write
shell
network
desktop_observe
desktop_control
memory_read
memory_write
```

This is a small enum model, not a policy language.

Tool schema visibility and call authorization are related but distinct. Each
tool declares the minimum capabilities needed to expose a useful schema, while
call-specific policy derives the capabilities required by the concrete
arguments. ToolStep authorizes against that concrete requirement, not against
the maximum capability of another operation in the same tool. Read-only memory
lookup uses `memory_read`; memory mutation uses `memory_write`.

### 9.2 Default policy

- missing or unknown metadata -> disabled;
- read-only does not imply unrestricted host read;
- external side effects require confirmation and are never retried implicitly;
- desktop state changes require confirmation unless the user has enabled a
  narrowly scoped trusted automation mode.

### 9.3 Extension boundary

MCP and other dynamic tool discovery are intentionally out of scope. A future
integration is accepted only when there is a concrete use case and every
discovered tool enters through the same ToolBase metadata, registration-time
schema validation, capability filtering, confirmation, and ToolStep commit
boundary. Discovery metadata can never grant authority by itself.

### 9.4 Filesystem, shell, network, and desktop

- File tools resolve paths against configured roots and reject escape after
  symlink-aware canonicalization.
- `workspace_read/write` cannot access arbitrary home or system paths.
- `host_read/write` is a separate explicit capability.
- Shell executes inside an OS sandbox/profile where supported. Command regexes
  remain defense in depth, not the primary authority boundary.
- Network access is explicit. A read operation followed by network upload is
  treated as data egress, not two harmless operations.
- Desktop observation and desktop control are separate capabilities.

Shell cancellation, timeout, and output-limit termination kill the complete
process group so descendants cannot outlive the run; captured stdout and stderr
are bounded. User-supplied URL fetches fail closed on DNS errors, reject
credentials and non-global addresses, and re-run SSRF validation at every
redirect hop. Response bodies are streamed with a 2 MiB hard byte limit before
HTML conversion; the 8,000-character result truncation is a separate model
context bound, not a substitute for network memory safety. Search responses are
bounded to 2 MiB; weather and exchange JSON are bounded to 1 MiB. Model-provider
SSE is parsed from byte chunks with a 2 MiB per-line limit and 16 MiB total
limit, so a provider cannot force `aiter_lines()` to accumulate an unbounded
line. File and artifact reads check byte limits before or during reading rather
than loading first and truncating afterward.

The first implementation may provide only `personal_local` and
`remote_scoped` profiles. Per-tool custom policies are deferred until there is a
real use case.

## 10. Retry semantics

The Core never automatically retries a tool commit. A disconnect, cancellation,
or process loss terminates the run; a later user request is a new proposal and
must pass policy and confirmation again. Provider failover occurs only before
any observable primary content, reasoning, or tool call, so it cannot replay an
already-visible model decision.

The framework does not claim exactly-once external effects. If a future tool
requires automatic retry, that concrete tool must first provide a downstream
idempotency key or an explicit uncertain-outcome contract. A generic
idempotency subsystem is deferred under YAGNI.

## 11. Model-call observability

Every model call records:

- stable hashes of principal, session, run, and client request identifiers;
- provider/model identity;
- prompt, completion, cached, and reasoning token usage where available;
- requested output and context budgets;
- latency and TTFT;
- failover use and the model that actually completed the call;
- cancellation and terminal error;
- actual cached-token usage when the provider reports it and the estimated tool
  schema token cost used by ModelStep budgeting;

Turn metrics aggregate every model iteration in the ReAct run.
Sensitive prompts, credentials, raw authority identifiers, and binary payloads
are never written to traces. Session handles and active run IDs are capabilities,
so observability stores only their hashes.
Turn outcome is observed at the runtime transaction boundary: `completed` only
after durable transcript commit, otherwise the actual `failed` or `cancelled`
terminal state. Cancellation before ReAct begins and failures during pre-ReAct
context assembly are also recorded rather than disappearing from turn metrics.

## 12. Error model

Internal exceptions map to stable public codes:

```text
invalid_request
resource_exhausted
unauthenticated
session_not_found
artifact_not_found
artifact_too_large
run_not_found
capability_denied
confirmation_denied
tool_invalid_arguments
tool_failed
provider_failed
cancelled
connection_lost
internal_error
```

Public errors contain a safe message and correlation ID. Detailed exceptions
remain in redacted server logs. Clients must not parse human-readable error text
to determine behavior.

## 13. Engineering quality gates

### 13.1 Required CI jobs

The CI pipeline runs:

1. package build/install from a clean environment;
2. `ruff check`;
3. the full pytest suite;
4. authority-boundary coverage at the configured 80% floor.

Tools used by CI are declared in `project.optional-dependencies.dev`. CI
commands do not depend on globally installed packages. Type checking,
dependency scanning, and lock-file enforcement are added only with an adopted
tool and repository-wide remediation plan, not as non-executable policy text.

The coverage source set is explicit: agent runtime, artifact ownership,
principal-scoped memory, desktop-session preparation, Core API/client/server/
host, and tool policy/registry. The full functional suite still covers the
entire repository. Platform adapters, UI rendering, network vendors, and
benchmark presentation are not padded with line-only tests merely to raise an
aggregate percentage; they use focused behavior and integration tests.

### 13.2 Coverage priorities

Before broad UI coverage, tests target authority and failure boundaries:

- cross-principal rejection for run/history/artifact/cancel/memory/config;
- cancel defaults to the client's active run only;
- disconnect terminates pending and streaming consumers;
- per-connection run admission and per-run client buffers are bounded;
- one terminal run event under every exception path;
- unknown tool metadata and schemas fail closed at registration;
- filesystem root escape and symlink escape rejection;
- multi-tool pairing under limit, rejection, loop, cancellation, and failure;
- artifact upload/download ownership and bounded wire delivery;
- oversized declared and chunked web responses fail before unbounded buffering;
- incompatible SQLite schemas and orphan session rows fail during startup;
- actual provider/failover attribution in model traces.

The coverage threshold is not lowered to make CI green.

## 14. Implementation sequence

This sequence is dependency-driven. It describes construction of the new path,
not incremental compatibility with the old path. Intermediate development may
land internal components that are not yet production entrypoints, but production
switching occurs once, after all first-party consumers are ready.

### Phase A: establish the new authority contracts

- Define strict Core API v1 DTOs, RuntimeScope, ordered events, terminal outcomes,
  and stable error codes without importing legacy DTOs.
- Implement transport principal derivation and owned opaque sessions.
- Implement scoped ControlService operations.
- Add contract tests for cross-principal rejection, cancel scope, disconnects,
  one terminal event, and streaming errors.

Exit criterion: the new contracts and Core-owned state model are executable and
independently tested; no new contract contains a legacy field or behavior.

### Phase B: harden tool policy

- Add required effect/capability metadata to ToolBase.
- Classify all built-in tools.
- Enforce filesystem roots and explicit network/desktop capabilities.
- Replace the shallow validator with standards-compliant JSON Schema validation
  for nested built-in schemas.

Exit criterion: every registered tool has a deterministic effect policy before
authorization.

### Phase C: build the complete runtime

- Implement AgentRuntime and scoped session transactions.
- Implement ModelStep and ToolStep as target-state production operations.
- Implement ReActLoop without depending on the concrete Agent.
- Build the new CoreServer and CoreClient against Core API v1.
- Update CLI, TUI, daemon, and benchmark consumers against public ports.

Exit criterion: the complete new execution path passes contract, integration,
and end-to-end tests before becoming the production entrypoint.

### Phase D: atomic production replacement and governance convergence

- Complete artifact egress and client-side delivery without exposing paths.
- Correlate model and turn traces with actual provider/failover attribution.
- Switch all production entrypoints to CoreServer/CoreClient in one change.
- Delete the old Agent orchestration path, AgentLike, free-form protocol,
  concrete reach-through, old session semantics, and in-process fallback in the
  same change.
- Enforce CI, coverage, typing, dependency locking, and security scanning.
- Delete tests and fixtures that assert obsolete behavior; do not retain them as
  compatibility suites.

Exit criterion: the clean CI pipeline passes all gates, only the new runtime is
reachable in production, and no legacy runtime contract remains in source or
tests.

## 15. Acceptance criteria

The design is complete only when all statements below are true:

1. A client cannot run, read, cancel, mutate, or resolve artifacts for another
   principal's session.
2. Interactive cancellation resolves only the initiating client's tracked
   active run and sends an explicit run ID.
3. A client disconnect cannot leave a pending request or stream waiting
   indefinitely.
4. Every run produces a strictly ordered event sequence and exactly one terminal
   event.
5. Every persisted assistant tool call has exactly one persisted terminal tool
   result.
6. Every registered tool has explicit effect, risk, and capability metadata.
7. Unknown tool effects and missing confirmations fail closed.
8. Workspace-scoped file tools cannot escape configured roots.
9. Tool commits are never retried implicitly; provider failover cannot replay
   observable output or a tool proposal.
10. ReAct and failover calls participate in tracing, budgets, cancellation, and
    actual provider attribution.
11. Server and consumers depend on runtime/control ports rather than concrete
    Agent internals.
12. No production in-process fallback or parallel legacy runtime remains.
13. The full functional suite and configured 80% authority-boundary coverage
    gate pass in CI.
14. Development and CI dependencies are declared by the project and the clean
    CI job executes the documented gates.
15. A timed-out Core request closes its connection and cannot leave an orphaned
    server run, including before run-ID publication.
16. Completed turn traces imply a successfully persisted transcript; persistence
    failure is traced as failure.
17. Runtime configuration writes are loaded by the next startup from the same
    explicit source or the canonical local override path.
18. Artifact delivery state is acknowledged only after successful transport
    send; client-side persistence failure cannot terminate the Core run stream.
19. Cancelling or timing out a shell tool terminates its process group, and
    unbounded command output cannot exhaust the Core process.
20. User-controlled URL fetches reject unresolved/non-global targets and unsafe
    redirect destinations.
21. A connection cannot create unbounded run tasks, and a slow client run
    consumer cannot create an unbounded in-memory event queue.
22. Web response bodies are hard-bounded before decoding or HTML conversion.
23. Core-owned SQLite repositories reject incompatible schemas and session
    foreign-key corruption at startup rather than performing online migration.
24. Unsolicited run acceptance and confirmation floods cannot allocate
    unbounded client queues or tasks.
25. Model SSE, search, weather, and exchange responses are byte-bounded before
    parsing, and large local files are bounded before decoding or hydration.
26. The clean sdist/wheel build excludes workspace/private/cache state and an
    installed wheel can load its packaged default configuration.
27. Active runs are bounded both per connection and across the process, without
    maintaining duplicate active-run authorities.
28. Session transcripts and YAML configuration are byte-bounded before parsing,
    and send failure closes the ambiguous connection.

## 16. Architectural fitness checks

During review, each change must answer these questions:

- Does it remove or add an authority path?
- Is the owner of mutable state unambiguous?
- Can a caller bypass scope validation through another method?
- Does the new type express an existing production concept, or is it speculative?
- Can the behavior be tested without replacing private members?
- Does failure produce one deterministic terminal outcome?
- Can the old path be deleted in the same change?
- Does the change reduce total conceptual complexity after migration?

If a proposal adds an interface, service, state machine, or compatibility layer
without satisfying a current acceptance criterion, it should be rejected under
YAGNI.

## 17. Final target

```text
Authenticated TCP / protected local client
             |
       strict Core API v1
             |
         CoreServer
   auth, protocol, connection
             |
       AgentRuntimePort
             |
         AgentRuntime
 scope, ownership, lease, transaction
       /               \
ControlService       ReActLoop
                    /         \
               ModelStep    ToolStep
                   |            |
             ProviderPort    ToolRegistry
                   |       effect/capability policy
            HTTP providers
```

The target is intentionally small. Its quality comes from explicit ownership
and enforced invariants, not from the number of layers.
