# Agent Runtime Decomposition Campaign

> Based on `docs/architecture.md`, the approved Path C decision, and a current-code audit, replace the monolithic Agent with explicit runtime boundaries and no legacy compatibility layer.
> Core insight: the main problem is ownership concentration, not file length; every extracted component must own a production operation and be wired into the live path immediately.
> Key architecture decisions: direct cutover with no shims; versioned Core API v1 is separate from the principal-bound AgentRuntimePort; ReActLoop owns Reasoning→Acting→Observation iteration; CoreServer is the only production execution authority.
> status: finalized
> type: campaign-blueprint
> mode: full
> source: docs/architecture.md
> path-decision: .gs-harness/plans/agent-runtime-decomposition.yaml
> campaign: agent-runtime-decomposition  →  openspec/campaigns/agent-runtime-decomposition/campaign.yaml (after decompose)
> coverage-matrix: docs/agent-runtime-decomposition-coverage-matrix.yaml
> orchestration-budget: changes<=4; critical-path<=3; shared-path-overlap<=1
> granularity-override: C4 files~33, lines~800; explicitly required by the approved atomic no-compat cutover and split into five owned implementation tasks
> Last updated: 2026-08-07

## 1. Problem and path

### 1.1 Current pain

The current `Agent` is both the application entrypoint and the owner of concrete
construction, session persistence, prompt assembly, streaming protocol handling,
verified tool execution, artifact mediation, runtime configuration, and status
reporting. Its constructor spans 168 lines (`src/pc_assistant/agent.py:142`), and
the main `_run_loop` spans 693 lines (`src/pc_assistant/agent.py:1101`).

The concentration is visible outside the file as well:

- legacy `AgentLike.cancel()` is async (`src/pc_assistant/service/agent_like.py:28`),
  while the concrete `Agent.cancel()` is synchronous
  (`src/pc_assistant/agent.py:403`). The common contract is therefore not exact.
- The service directly reaches into `registry`, `memory`, and `config`
  (`src/pc_assistant/service/server.py:469`, `src/pc_assistant/service/server.py:478`,
  `src/pc_assistant/service/server.py:489`).
- The TUI branches on the concrete class name and then uses different command
  paths (`src/pc_assistant/ui/chat.py:328`).
- `SchedulerTool` stores an untyped complete Agent reference
  (`src/pc_assistant/tools/scheduler.py:226`) although it only needs to invoke a
  scheduled turn (`src/pc_assistant/tools/scheduler.py:609`).
- Tests frequently replace private `_llm`, `_executor`, `_limiter`, and
  `_conversation` members (`tests/test_agent.py:160`, `tests/test_agent.py:380`,
  `tests/test_agent.py:526`, `tests/test_agent.py:597`).

This contradicts the existing architecture boundary: Agent is intended to be an
orchestrator rather than a provider parser, persistence store, or safety-policy
implementation (`docs/architecture.md:164`). The architecture review already
identifies orchestration concentration and concrete collaborator ownership as
open findings (`docs/architecture.md:531`, `docs/architecture.md:609`) and
recommends an `AgentFactory` plus an explicit `ReActLoop`
(`docs/architecture.md:834`).

### 1.2 Path comparison

| Approach | Result | Tradeoff | Decision |
|---|---|---|---|
| Split helpers by line count | More files, unchanged ownership and coupling | Low initial effort; no architectural improvement | Rejected |
| Move `_run_loop` unchanged into one `TurnRunner` | Transfers a 693-line state machine and its 25 Agent dependencies | Superficial separation; creates another god object | Rejected |
| Boundary-first Campaign | Defines contracts, composition, model step, tool step, and orchestration ownership, then cuts consumers over directly | More staged work; each stage has an observable production result | Selected Path C |

The selected path is a replacement, not coexistence. Intermediate Changes may
move ownership incrementally, but there is never a parallel legacy runtime,
translation shim, mirrored private API, or old import compatibility layer.

## 2. Architecture model

### 2.1 As-Is

```text
UI / Channel / Service / Benchmark / Scheduler
        |        direct concrete access
        v
      Agent
        ├── constructs providers, stores, verifier, tools, traces
        ├── owns session ingress/persistence/cancellation
        ├── assembles prompts and parses model streams
        ├── authorizes and executes tool calls
        ├── mediates images/artifacts/evidence
        └── exposes registry/memory/config/conversation internals
```

The side-effect commit invariant is already correctly centralized in
`VerifiedToolExecutor`: authorization creates an opaque capability and commit
consumes it once (`src/pc_assistant/harness/executor.py:44`,
`src/pc_assistant/harness/executor.py:60`, `src/pc_assistant/harness/executor.py:77`).
The refactor preserves that boundary rather than recreating it.

### 2.2 To-Be

```text
TUI / CLI / Feishu / remote benchmark client
                   |
             thin Client Adapter
        (serialize, upload, render, confirm)
                   |
          CoreClient / Core API
                   |
             Core Server process
                   |
             AgentRuntimePort
                   |
             AgentRuntime
             /          \
       ControlService   ReActLoop
                            /      \
                      ModelStep   ToolStep
                         |            |
                    LLMProvider   VerifiedToolExecutor
                    Context       ToolRegistry
                    Trace         Idempotency / ArtifactStore

SchedulerTool -> TurnInvoker -> ReActLoop, never a complete Client or Agent

AgentFactory -> constructs AgentRuntime inside Core Server
SessionManager / transcript repositories remain Core-owned state owners
```

The Core Server is the only production execution authority. Shell, filesystem,
window, screen, mouse/keyboard, scheduler, memory, and artifact operations run
on the host that owns the Core Server. TUI, CLI, and Feishu adapters do not call
tools or mutate Core state; they send requests and render/confirm streamed
events. A same-host adapter may use a local transport, but it still crosses the
same Core API boundary; only CoreServer code calls `AgentRuntimePort`.

### 2.3 Deployment boundary

`service/server.py` is the current Core Server host and becomes `CoreServer`.
`service/client.py` is the current client transport and becomes the thin
`CoreClient` adapter. The current lifecycle fallback that constructs an in-process Agent
(`src/pc_assistant/service/lifecycle.py:24`, `src/pc_assistant/service/lifecycle.py:106`)
is removed from the production path: if the daemon cannot be started or reached,
the client reports a Core availability error rather than taking ownership of
execution locally. Tests may instantiate `AgentRuntime` directly as a Core test
fixture; that is not a client deployment mode.

Feishu's ingress/delivery code remains a channel adapter. It may download or
upload transport payloads, but artifact ownership, session authorization,
ReAct, tool execution, and postcondition evidence remain Core operations
(`src/pc_assistant/channels/feishu.py:657`,
`src/pc_assistant/channels/feishu.py:1451`).

Target source layout:

```text
src/pc_assistant/agent_runtime/
├── contracts.py       # RuntimeEvent, requests/results, AgentRuntimePort, TurnInvoker
├── factory.py         # concrete dependency construction and model reconfiguration
├── runtime.py         # session ingress, cancellation, status, artifacts, control delegation
├── control.py         # new/tools/history/memory/config command semantics
├── react_loop.py      # ReAct Reasoning→Acting→Observation state machine
├── model_step.py      # prompt budget, context assembly, stream normalization
└── tool_step.py       # authorization, execution, idempotency, evidence/artifact results
```

The non-colliding `agent_runtime` package is used throughout C1-C4 while the
legacy module is reduced in place. The old `src/pc_assistant/agent.py` is deleted
during the final cutover. Callers import target modules explicitly;
`Agent = AgentRuntime`, re-export aliases, and private attribute mirrors are not
permitted. This avoids the Python import collision that would occur if
`agent.py` and an `agent/` package coexisted.

#### Canonical names

The forward-design vocabulary is fixed as follows:

| Boundary | Canonical name | Meaning |
|---|---|---|
| Deployment process | `CoreServer` | The only production execution authority |
| Client adapter | `CoreClient` | Thin transport, upload, render, and confirmation adapter |
| Public protocol | `Core API v1` | Versioned requests, run/task state, and ordered events |
| Core application port | `AgentRuntimePort` | Server-side async port called by `CoreServer` |
| Runtime | `AgentRuntime` | Session, ownership, transaction, artifact, and control lifecycle |
| Control plane | `ControlService` | Session, memory, history, tools, and configuration commands |
| ReAct orchestration | `ReActLoop` | Reasoning → Acting → Observation state machine |
| Model operation | `ModelStep` | Prompt assembly and model streaming/normalization |
| Tool operation | `ToolStep` | Authorization, verified commit, result, and evidence handling |
| Internal execution event | `RuntimeEvent` | Ordered Core-only operation and lifecycle event |
| Public execution event | `RunEvent` | Ordered event envelope carrying `run_id` and `event_seq` |

`AgentLike`, `ServiceClient`, `AgentEvent`, `RuntimeControl`, and
`TurnOrchestrator` are legacy or rejected target names. They must not appear in
the final source tree, public protocol, or documentation except in historical
evidence references identifying code that is being deleted.

### 2.4 Locked invariants

1. Every side effect still passes through `VerifiedToolExecutor`; no new direct
   registry execution path is introduced. Owner: `ToolStep` plus existing
   verifier/executor (`docs/architecture.md:509`,
   `src/pc_assistant/harness/executor.py:44`).
2. A complete turn remains serialized by one stable session lease/lock, while
   distinct sessions may run concurrently. An active state cannot be evicted,
   dropped, replaced, or cleaned up until its lease is released. Owner:
   `AgentRuntime` and `SessionManager`
   (`src/pc_assistant/session.py:27`, `src/pc_assistant/agent.py:1009`).
3. Conversation and context-summary rollback on cancellation/error/generator
   close and transcript persistence on allowed completion remain one
   session-runtime transaction responsibility
   (`src/pc_assistant/agent.py:1072`, `src/pc_assistant/agent.py:1080`).
4. Provider-native image bytes remain request-local and never enter durable
   history/events/idempotency. Owner: `ModelStep` and `ToolStep`
   (`docs/architecture.md:512`, `src/pc_assistant/agent.py:616`,
   `src/pc_assistant/agent.py:1795`).
5. The Core API v1 wire contract is a versioned task/run contract. `RunEvent`
   is an ordered public event with `run_id`, `event_seq`, `event_type`, and a
   typed payload; it is not the internal runtime event model.
6. A transport-established principal remains attached to every runtime,
   command, cancellation, transcript, and artifact operation while staying out
   of model-visible messages (`docs/architecture.md:141`).

## 3. Phase roadmap

| Phase | Changes | Value | Dependency |
|---|---|---|---|
| Foundation | C1 contracts and factory | Exact target contracts and one composition owner | none |
| Operation extraction | C2 model step, C3 tool step | Model and verified-tool operations become independently testable on parallel branches | C1 |
| Atomic cutover | C4 ReAct loop and runtime cutover | ReActLoop becomes the only production iteration path; old module and all legacy consumers are removed together | C2, C3 |

```text
C1 contracts/factory
       ├────────> C2 model step ────┐
       └────────> C3 tool step  ────┤
                            v
                           C4 ReAct loop and runtime cutover
```

C2 and C3 may proceed independently after C1 because they own different current
operations. The canonical critical path is C1 → C2 → C4; the equal-length
co-critical branch is C1 → C3 → C4.

## 4. Technical design

### 4.1 Runtime contracts

`AgentRuntimePort` is the Core-owned application port, not the public wire type.
CoreServer maps versioned Core API requests to its uniformly async operations:
`run`, `cancel`, `health_check`, `get_status`, and `command`. Runtime and control requests carry a
`RuntimeScope` containing a transport-established `principal_id` and an opaque
session handle. Ownership is validated before run, history/export, cancellation,
memory, and artifact operations; identity is never copied into model messages.
Attachments are represented by the existing `ImageAttachment` type
(`src/pc_assistant/model_adapter/types.py:1`).

`RuntimeEvent` is the Core-only ordered execution event returned by
`AgentRuntimePort`. `RunEvent` is the public Core API event envelope. CoreServer
maps the former to the latter and adds `run_id`, `event_seq`, `event_type`, and
the versioned typed payload; the two are never the same DTO.

`TurnInvoker` is the narrow scheduled-execution dependency: a callable accepting
input and session identity and returning the ordered `RuntimeEvent` stream. It replaces
`SchedulerTool.set_agent(Any)` (`src/pc_assistant/tools/scheduler.py:231`).

`ControlService` owns command semantics currently duplicated or branched across
the service and UI: session creation, tools, history/export, memory, and config
mutation (`src/pc_assistant/service/server.py:449`,
`src/pc_assistant/ui/chat.py:321`). This is a current multi-consumer boundary,
not a speculative extension point.

### 4.2 ModelStep

Input is a turn/session view plus explicit dependencies. `ModelStep.stream()` is
an async typed operation stream: it yields internal `RuntimeEvent` values as provider
chunks arrive and ends with exactly one `ModelStepCompleted` signal containing
normalized content, tool calls, finish reason, usage, proposed compaction update,
and error state. Consumer backpressure is preserved because the orchestrator
awaits each yielded signal. It owns:

- context/memory assembly and automatic compaction currently beginning at
  `src/pc_assistant/agent.py:1123`;
- schema/completion/input budget allocation currently at
  `src/pc_assistant/agent.py:1216`;
- request-local image hydrate/manifest behavior currently at
  `src/pc_assistant/agent.py:1795`;
- stream accumulation and provider-neutral tool-call normalization currently at
  `src/pc_assistant/agent.py:1290`;
- LLM-call trace/usage recording currently at `src/pc_assistant/agent.py:1377`.

It does not authorize tools, mutate idempotency state, commit side effects,
persist compaction, or decide whether another iteration is required.

### 4.3 ToolStep

Input is one normalized tool call plus explicit per-turn limits and runtime
scope. `ToolStep.stream()` yields ordered internal proposal/confirmation/result/
artifact events and ends with exactly one `ToolStepCompleted` signal containing
conversation blocks, evidence status, and continuation state. Authorization and
commit do not occur before the corresponding earlier signals have been consumed.
It owns:

- semantic call normalization and loop detection
  (`src/pc_assistant/agent.py:1468`, `src/pc_assistant/agent.py:1470`);
- deterministic authorization and prepared capability handling
  (`src/pc_assistant/agent.py:1495`);
- side-effect idempotency (`src/pc_assistant/agent.py:1533`);
- commit/cancellation handling (`src/pc_assistant/agent.py:1610`);
- safe result projection, artifact events, evidence accounting, and tool-result
  conversation insertion (`src/pc_assistant/agent.py:1630`).

It calls the existing `VerifiedToolExecutor`; it does not reproduce verifier or
registry internals.

### 4.4 ReActLoop and AgentRuntime

`ReActLoop` is the explicit ReAct application layer. It owns the state machine
only; it is not a provider adapter, tool registry, policy engine, or UI layer:

```text
prepare turn
  -> ModelStep (Reasoning proposal)
     -> final result: record and stop
     -> tool calls: ToolStep (Act), append results/observations, iterate
     -> retry/reflection/vision requirement: append instruction, iterate
     -> error/cancel/limit: terminate with explicit outcome
```

Counters and mutable flags become a per-run `TurnContext` rather than local
variables spread through `_run_loop` (`src/pc_assistant/agent.py:1183`). Internal
`RuntimeEvent` ordering is explicit and CoreServer preserves it when assigning
Core API `RunEvent.event_seq`:

```text
stream_start -> stream_delta/stream_think_delta* -> stream_end
  -> tool_call -> confirmation/authorization -> commit -> tool_result -> artifact*
  -> next stream_start OR evidence_warning -> final_answer
```

`AgentRuntime` binds principal/session scope, active-state leases, memory scope,
turn transactions, rollback, persistence, status, artifacts, and control.
It invokes `ReActLoop`; model/tool steps do not receive the complete runtime
object.

### 4.5 Failure, concurrency, and commit points

| Operation | Commit point | Failure state | Retry/idempotency owner |
|---|---|---|---|
| Session turn | successful final/limit completion before persisted transcript/context | cancellation, generator close, or error rolls transcript and context summary back to one transaction snapshot | AgentRuntime + SessionState |
| Model stream | terminal `ModelStepCompleted` signal after ordered live events | provider error emits error outcome; partial response is not replayed | existing provider/failover policy |
| Tool action | `VerifiedToolExecutor.commit(prepared)` | structured tool error is appended to conversation | ToolStep + IdempotencyLog |
| Artifact ingress | successful ArtifactStore registration | invalid/unowned input is rejected | AgentRuntime + ArtifactStore |
| Config change | new provider dependency set built before swap between turns | old runtime dependencies remain active when construction fails | AgentFactory |

Runtime acquisition creates an active-state lease before exposing
`SessionState`. LRU eviction and explicit drop skip leased states and defer
artifact cleanup until release. This closes the current possibility that
`SessionManager._evict_locked()` removes a long-running state while its
`run_lock` is held (`src/pc_assistant/session.py:83`).

Compaction is computed by ModelStep for the current prompt but returned as a
proposed transaction update. AgentRuntime applies it only on an allowed terminal
outcome. `asyncio.CancelledError`, client disconnect, generator close, and run
replacement all map to an explicit cancelled outcome before finalization.

## 5. Change breakdown

### 5.0 Change naming map

| ID | OpenSpec slug |
|---|---|
| C1 | `agent-contracts-factory` |
| C2 | `agent-model-turn-step` |
| C3 | `agent-tool-turn-step` |
| C4 | `agent-react-loop-cutover` |

### 5.1 Overview

| Change | Node | Summary |
|---|---|---|
| C1 | Foundation | Introduce exact runtime/control contracts and extract concrete construction into AgentFactory |
| C2 | Model runtime | Move prompt, budget, compaction, streaming, usage, and model-message preparation into ModelStep |
| C3 | Tool runtime | Move normalized verified tool execution, idempotency, artifact/evidence results, and limits into ToolStep |
| C4 | Runtime cutover | Compose ReActLoop and AgentRuntime, migrate every consumer, split tests, delete old agent module |

### 5.2 Dependencies

C1 establishes the types and dependency construction consumed by C2/C3. C2 and
C3 are parallel because the model proposal boundary and deterministic tool
commit boundary are distinct. C4 owns their only production composition in
`ReActLoop` and the
direct consumer cutover.

Canonical critical path: `agent-contracts-factory` →
`agent-model-turn-step` → `agent-react-loop-cutover`.
Equal-length co-critical branch: `agent-contracts-factory` →
`agent-tool-turn-step` → `agent-react-loop-cutover`.

No Change may introduce an unused alternate runtime route. Each extraction is
connected to the current `Agent` production path in the same Change until C4
deletes that module.

### 5.3 LLM controllability

| Change | Files | Lines | Controllability | Observable outcome | Quality floor | Verification |
|---|---:|---:|---|---|---|---|
| C1 | 6 | ~350 | Medium; contracts and composition root | Agent construction uses AgentFactory and target runtime/Core API contracts are executable in contract tests | no legacy alias and no duplicated provider/tool construction owner | `pytest tests/test_agent_contracts.py tests/test_agent_factory.py` |
| C2 | 3 | ~400 | Medium; one cohesive model operation | model streaming and tool-call normalization run through ModelStep | token budget, compaction, image hydration, trace, and emitted deltas preserve current scenarios | `pytest tests/test_model_step.py tests/test_context.py tests/test_context_cache.py tests/test_multimodal.py` |
| C3 | 3 | ~430 | Medium; security-sensitive operation isolated | every tool call runs through ToolStep and existing VerifiedToolExecutor | authorization-before-commit, single-use capability, limits, idempotency, evidence, and artifact redaction remain tested | `pytest tests/test_tool_step.py tests/test_harness.py tests/test_artifacts.py tests/test_image_inspect.py` |
| C4 | 33 | ~800 | Explicit granularity override; five tasks: runtime transaction/lease, Core API/server, local clients, channel/benchmark/scheduler, deletion+test convergence | CoreServer's AgentRuntime/ReActLoop are the only production path and `agent.py` is absent | principal ownership, leased session locking, full rollback, ordered streaming, Core API/UI/Feishu/benchmark/scheduler behavior and full suite pass | targeted suites plus `pytest` |

Estimated file slots are explicit:

- C1 (6): `agent_runtime/__init__.py`, `agent_runtime/contracts.py`,
  `agent_runtime/factory.py`, `agent.py`, `tests/test_agent_contracts.py`,
  `tests/test_agent_factory.py`.
- C2 (3): `agent_runtime/model_step.py`, `agent.py`,
  `tests/test_model_step.py`.
- C3 (3): `agent_runtime/tool_step.py`, `agent.py`,
  `tests/test_tool_step.py`.
- C4 (33): `agent_runtime/runtime.py`, `agent_runtime/control.py`,
  `agent_runtime/react_loop.py`, `agent.py` deletion, `session.py`,
  `service/agent_like.py` deletion, `service/client.py`, `service/lifecycle.py`,
  `service/protocol.py`, `service/server.py`, `pc_assistant/__init__.py`,
  `ui/app.py`, `ui/chat.py`, `channels/feishu.py`, `benchmark/runner.py`,
  `benchmark/scorer.py`, `tools/scheduler.py`, `tests/test_agent.py`,
  `tests/test_agent_sessions.py`, `tests/test_artifacts.py`,
  `tests/test_benchmark.py`, `tests/test_benchmark_ext.py`, `tests/test_e2e.py`,
  `tests/test_e2e_new_features.py`, `tests/test_feishu_channel.py`,
  `tests/test_image_inspect.py`, `tests/test_live_e2e.py`,
  `tests/test_multimodal.py`, `tests/test_scheduler_persistence.py`,
  `tests/test_service.py`, `tests/test_session_persistence.py`,
  `tests/test_ui_chat.py`, and `tests/test_agent_runtime.py`.

Large tasks inside a Change are split before implementation according to
`harness.yaml`. C4 intentionally exceeds the recommended file count because the
approved no-compat rule requires one atomic consumer cutover; splitting that
cutover into separate lifecycle Changes would require a shim, alias, or dual
runtime. Its five implementation tasks each have a focused owner and share one
rollback boundary.

| Plan | Changes | Critical path | Lifecycle amplification | Shared-path overlap | Outcome-less | Outcome/quality/closure preserved? |
|---|---:|---:|---:|---:|---:|---|
| Candidate | 4 | 3 | 1.33 | 1 (`src/pc_assistant/agent.py`) | 0 | yes, with explicit C4 granularity override |
| Smallest cohesive alternative | 3 | 3 | 1.0 | 1 (`src/pc_assistant/agent.py`) | 0 | no: combining ModelStep and ToolStep exceeds ~800 changed lines and merges stochastic parsing with the deterministic security boundary |

The four-Change candidate is retained because C2 and C3 have different current
owners, failure modes, verification suites, and rollback surfaces. Combining
them loses the security review boundary and exceeds the configured 600-line
granularity warning.

### 5.4 Key design decisions

#### C1 — agent-contracts-factory

**Responsibility:** target runtime/event/control contracts and all concrete
dependency construction.

**In scope:** internal `RuntimeEvent`, public Core API v1 `RunEvent`,
principal-bound exact async port, `RuntimeScope`,
`TurnInvoker`, command/control surface, provider/fallback/vision construction,
tool registration, dynamic model rebuild, dependency bundle.

**Out of scope:** turn iteration, model streaming implementation, tool commit
implementation.

**Verification:** contract tests exercise CoreServer and CoreClient parity plus
cross-principal rejection for run, command, cancel, transcript, and artifact;
factory tests exercise default/injected providers, vision capability switching,
and built-in tools.

**Decision:** direct call-site migration. No re-export of the old `Agent`, no
private attribute mirror, no class-name branching.

#### C2 — agent-model-turn-step

**Responsibility:** one provider-neutral model request and its normalized result.

**In scope:** planning/context input, compaction, budgets, message preparation,
stream deltas, thinking filtering, normalized tool calls, usage/trace.

**Out of scope:** tool authorization/execution, turn retry policy, final outcome
selection.

**Verification:** extracted characterization scenarios cover exact ordered live
events, direct answer,
thinking, stream exception/error, usage, length finish, empty content, multimodal
manifest/hydration, accumulated JSON tool arguments, cancellation, and proposed
compaction without pre-commit persistence.

**Decision:** a typed result replaces mutable cross-section locals; no generic
handler registry is introduced.

#### C3 — agent-tool-turn-step

**Responsibility:** one normalized tool proposal from authorization through
conversation/event result.

**In scope:** loop/limit checks, verifier authorization, confirmation,
idempotency, scheduler session binding, commit cancellation, safe result,
artifact/evidence accounting.

**Out of scope:** verifier policy internals, registry execution internals,
provider streaming, turn-level reflection/final-answer policy.

**Verification:** exact proposal/confirmation/commit/result event order plus
accepted, denied, hard-blocked, repeated, replayed, cancelled,
exceptional, image-producing, artifact-producing, and evidence-producing calls.

**Decision:** ToolStep depends on `VerifiedToolExecutor`, not `ToolRegistry._commit`.

#### C4 — agent-react-loop-cutover

**Responsibility:** target Core Server ReAct state machine, session lifecycle,
and direct client migration.

**In scope:** `TurnContext`, `ReActLoop`, transaction snapshot, active session lease,
principal/session ownership enforcement, runtime ingress/persistence, Core
Server transport delegation, removal of in-process production fallback,
UI/service/channel/benchmark/scheduler imports and calls, test-suite split,
documentation update, deletion of `agent.py` and obsolete `service/agent_like.py`.

**Out of scope:** new agent features, new tool behavior, verifier/provider/store
rewrites, or parallel tool execution. Core API v1 naming and event envelopes
are in scope for the direct cutover.

**Verification:** same-session serialization under LRU/drop pressure,
cross-session concurrency, cross-principal rejection, transcript+summary rollback
during stream/confirmation/tool/disconnect/replacement cancellation, persistence
restart, ordered live events, direct/remote command parity, scheduled turns,
Feishu artifacts, benchmark execution, and full pytest suite.

**Decision:** final tree contains only target modules. Temporary adapters are not
left for downstream callers.

### 5.5 End-to-end closure analysis

#### Baseline path `interactive-agent-turn`

```text
UI/channel/client adapter
  -> CoreClient wire request
  -> Core Server AgentRuntimePort.run(RuntimeScope)
  -> AgentRuntime ownership validation + session lease/transaction
  -> ReActLoop
  -> ModelStep
  -> final RuntimeEvent OR ToolStep loop
  -> transcript persistence
  -> CoreServer maps RuntimeEvent to ordered RunEvent
  -> CoreClient delivery
```

#### Baseline path `verified-tool-turn`

```text
ModelStep tool proposal
  -> ReActLoop
  -> ToolStep normalize/authorize
  -> VerifiedToolExecutor commit
  -> ToolStep safe result/evidence/artifact
  -> conversation tool result
  -> next ModelStep
```

#### Baseline path `runtime-control-command`

```text
UI or CoreClient request
  -> CoreClient wire request
  -> Core Server AgentRuntimePort.command(RuntimeScope)
 -> ControlService
  -> session/memory/config/tool owner
  -> structured command result
```

#### Baseline path `scheduled-agent-turn`

```text
Scheduler task
  -> TurnInvoker
  -> AgentRuntime.run(session_id)
  -> RuntimeEvent collection
  -> existing result callback/channel delivery
```

#### Baseline path `artifact-mediated-turn`

```text
attachment input
  -> AgentRuntime validation/registration
  -> ArtifactStore session reference
  -> ModelStep hydrate or manifest for active model
  -> ToolStep internal artifact RuntimeEvent
  -> CoreServer public artifact RunEvent
  -> CoreClient delivery/mark delivered
```

| closure_edge_id | Producer → consumer | Boundary/interface | Owner Change slug | Depends on Change slugs | Predecessor edge/checkpoint |
|---|---|---|---|---|---|
| `client-wire-request` | Client adapter → CoreClient/Core API | versioned serialized request/event contract | `agent-react-loop-cutover` | `agent-contracts-factory` | validated client input/upload checkpoint |
| `wire-runtime-dispatch` | Core API transport → CoreServer | authenticated request and derived `RuntimeScope` | `agent-react-loop-cutover` | `agent-contracts-factory` | `client-wire-request` |
| `request-runtime-contract` | CoreServer → AgentRuntime | `AgentRuntimePort.run(RuntimeScope)` | `agent-react-loop-cutover` | `agent-contracts-factory` | `wire-runtime-dispatch` |
| `control-runtime-contract` | CoreServer → ControlService | `AgentRuntimePort.command(RuntimeScope)` | `agent-react-loop-cutover` | `agent-contracts-factory` | authenticated control-request checkpoint |
| `factory-runtime-dependencies` | AgentFactory → AgentRuntime | explicit dependency bundle | `agent-contracts-factory` | none | validated configuration checkpoint |
| `runtime-react-operation` | AgentRuntime → ReActLoop | principal-bound `TurnContext` and lifecycle | `agent-react-loop-cutover` | `agent-contracts-factory`, `agent-model-turn-step`, `agent-tool-turn-step` | `request-runtime-contract` |
| `react-model-operation` | ReActLoop → ModelStep | ordered typed operation stream | `agent-model-turn-step` | `agent-contracts-factory` | prepared-turn checkpoint |
| `model-react-continuation` | ModelStep → ReActLoop | live events + terminal completion signal | `agent-model-turn-step` | `agent-contracts-factory` | `react-model-operation` |
| `react-tool-operation` | ReActLoop → ToolStep | normalized tool proposal operation | `agent-tool-turn-step` | `agent-contracts-factory` | model tool-proposal checkpoint |
| `tool-verified-commit` | ToolStep → VerifiedToolExecutor | prepared single-use capability | `agent-tool-turn-step` | `agent-contracts-factory` | `react-tool-operation` |
| `tool-conversation-feedback` | ToolStep → conversation/ReActLoop | safe tool result blocks/events | `agent-tool-turn-step` | `agent-contracts-factory` | `tool-verified-commit` |
| `runtime-session-boundary` | AgentRuntime → SessionState/repos | principal ownership + lease + transaction snapshot/rollback/persist | `agent-react-loop-cutover` | `agent-contracts-factory`, `agent-model-turn-step`, `agent-tool-turn-step` | acquired session lease checkpoint |
| `scheduled-turn-invocation` | SchedulerTool → AgentRuntime | `TurnInvoker` | `agent-react-loop-cutover` | `agent-contracts-factory`, `agent-model-turn-step`, `agent-tool-turn-step` | due scheduled-task checkpoint |
| `artifact-runtime-boundary` | AgentRuntime/steps → ArtifactStore | session-scoped references | `agent-react-loop-cutover` | `agent-model-turn-step`, `agent-tool-turn-step` | validated artifact ownership checkpoint |
| `runtime-event-dispatch` | AgentRuntime → CoreServer | ordered `RuntimeEvent` stream | `agent-react-loop-cutover` | `agent-contracts-factory`, `agent-model-turn-step`, `agent-tool-turn-step` | emitted runtime-event checkpoint |
| `event-client-delivery` | CoreServer → CoreClient/UI/channel | Core API v1 ordered `RunEvent` stream | `agent-react-loop-cutover` | `agent-contracts-factory` | `runtime-event-dispatch` |

Every closure edge has exactly one owning Change. Existing lower-level owners
(`SessionManager`, `VerifiedToolExecutor`, `ArtifactStore`) remain in place; the
listed owner is responsible only for the new integration edge.

#### Gap allocation

| Current gap | Evidence | Owner | Resolution |
|---|---|---|---|
| Contract cancel mismatch | `service/agent_like.py:28` vs `agent.py:403` | C1 | one exact async contract and direct caller migration |
| Bare session IDs lack trusted ownership context | `service/server.py:297`, `service/server.py:352` | C1/C4 | RuntimeScope contract plus transport-established principal validation |
| Concrete admin-state access | `service/server.py:469-492`, `ui/chat.py:342-424` | C1/C4 | C1 owns `ControlService`; C4 migrates consumers |
| Scheduler complete-Agent back-reference | `tools/scheduler.py:226-231` | C1/C4 | C1 owns TurnInvoker; C4 wires it |
| Active LRU state can be evicted while running | `session.py:83-96` | C4 | active-state lease blocks eviction/drop/cleanup until release |
| Summary mutation is outside transcript snapshot | `agent.py:1167-1177`, `agent.py:1072-1080` | C2/C4 | proposed compaction update plus one transcript/context transaction |
| Model and tool mutable state share one function | `agent.py:1101-1793` | C2/C3/C4 | typed results plus TurnContext composition |
| Tests depend on private internals | `tests/test_agent.py:160-597` | C2/C3/C4 | constructor dependencies and operation-level tests |

No ownerless production interface remains in the approved scope.

## 6. Compatibility and cutover policy

This is a forward replacement. Internal import paths, concrete class names, and
private test seams may break and are updated in the same owning Change. The
Campaign adds no compatibility modules, aliases, deprecation wrappers, mirrored
properties, or dual execution routes.

The current service event/message schema is replaced by the Core API v1 contract
in the same direct cutover. No old wire schema, import alias, or compatibility
adapter remains after C4.

## 7. Security and lifecycle preservation

- The authorization/commit capability remains opaque and single-use
  (`src/pc_assistant/harness/executor.py:39`,
  `src/pc_assistant/harness/executor.py:77`).
- Tool results continue to be converted to structured actionable errors by the
  verified execution boundary (`src/pc_assistant/harness/executor.py:12`).
- Same-session locking remains around the entire serialized run
  (`src/pc_assistant/agent.py:1009`).
- Image encodings continue to be removed from events and persistence payloads
  (`src/pc_assistant/agent.py:616`).
- Scheduler session context remains bound only around scheduled tool execution
  (`src/pc_assistant/agent.py:1606`).

## 8. Simplicity budget

The target adds named components only for current operations with current
consumers. It adds no plugin manager, step registry, event bus, mediator, generic
pipeline framework, or compatibility layer. `AgentFactory`, `ModelStep`,
`ToolStep`, `ReActLoop`, and `ControlService` each replace an existing
concrete responsibility documented above.

Net abstraction growth is offset by deleting the monolithic Agent class,
`service/agent_like.py`, class-name branching, direct internal property access,
and scheduler's `Any` Agent dependency.

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Stream event ordering changes during extraction | UI/Feishu rendering regression | characterize exact event sequences before C2; run UI/channel tests in C4 |
| Tool security boundary is accidentally bypassed | unauthorized side effects | C3 prohibits direct registry commit; harness tests assert authorize → commit ordering |
| Session rollback/persistence moves incompletely | lost or contaminated history | C4 keeps lease/transaction/rollback/persist in one runtime owner and runs cancellation + LRU pressure regressions |
| Model/tool counters diverge | limits or metrics become incorrect | explicit TurnContext and per-step typed deltas; turn metric tests |
| Consumer cutover misses direct internal access | runtime failure after deleting old module | repository-wide import/member search and no-old-import verification in C4 |
| Four staged Changes repeatedly touch `agent.py` | merge/review conflict | serialize C1→C2/C3→C4 on one Campaign branch; only one shared legacy path allowed |
| Step extraction buffers events | interactive latency and confirmation order regress | typed async operation streams with exact sequence tests and awaited backpressure |
| Runtime accepts another principal's session | transcript/artifact/control exposure | transport-established RuntimeScope plus ownership validation before every scoped operation |

### 9.1 Independent Critic disposition

| Finding | Severity | Disposition |
|---|---|---|
| IC-AGENT-001 package/import collision and understated C4 surface | high | Resolved in design: use non-colliding `agent_runtime`; repository-wide consumer search expands C4 to 33 files/~800 lines with five implementation tasks and an explicit no-compat atomic cutover override. |
| IC-AGENT-002 unbound principal/session identity | high | Resolved in design: `RuntimeScope` carries transport-established principal; runtime validates ownership before every scoped operation; cross-principal rejection is a C1/C4 acceptance criterion. |
| IC-AGENT-003 active LRU eviction can replace a running state | high | Resolved in design: active-state lease blocks eviction/drop/cleanup; C4 tests same-session serialization under LRU pressure. |
| IC-AGENT-004 cancellation and compaction summary can escape rollback | high | Resolved in design: one transcript+context transaction, explicit cancelled outcome for task/generator cancellation, and compaction applied only at allowed commit. |
| IC-AGENT-005 completed step result could buffer streaming events | high | Resolved in design: ModelStep/ToolStep are ordered async typed operation streams with terminal completion signals, awaited backpressure, and sequence tests. |

## 10. Campaign verification criteria

1. `src/pc_assistant/agent.py` and `src/pc_assistant/service/agent_like.py` no
   longer exist.
2. No source or test imports an old Agent compatibility alias or accesses
   `_llm`, `_executor`, `_conversation`, `_registry`, `_memory`, or `_config`
   through the runtime object.
3. `CoreServer` maps the versioned Core API to the exact principal-bound async
   `AgentRuntimePort`; `CoreClient` satisfies the corresponding semantic
   contract, including attachments and cancellation. Cross-principal run,
   command, cancel, transcript, and artifact attempts are rejected.
4. Model direct answer, thinking, tool-call accumulation, usage, compaction,
   context budgeting, multimodal hydration/manifest, length, empty, and error
   scenarios pass operation-level tests with exact live event ordering.
5. Tool allowed/denied/blocked/replayed/looped/limited/cancelled/error/artifact/
   evidence scenarios pass through `VerifiedToolExecutor` exactly once.
6. Same-session turns remain serialized under LRU/drop pressure; different
   sessions remain isolated; cancellation/generator-close/error rollback covers
   transcript and context summary; restart persistence passes.
7. TUI, CLI, Feishu, and remote benchmark clients use the thin client contract;
   all production ReAct/model/tool/session/artifact execution occurs in Core
   Server, with no class-name or concrete-internal branching.
8. Production lifecycle never falls back to an in-process Agent when Core Server
   is unavailable; it reports a connection/startup error.
9. Repository search finds no import collision, compatibility shim, legacy alias, or dual runtime
  path introduced by the Campaign.
10. Targeted evidence commands for all four Changes pass, followed by the full
  `pytest` suite.
11. Architecture and README module-layout documentation describe only the
  target runtime.

## 11. Out of scope

- Parallel tool execution or new ReAct behavior.
- New provider features or provider-adapter rewrite.
- Verifier, policy, registry, memory repository, ArtifactStore, or session DB
  redesign.
- A second transport or compatibility version. Core API v1 may use WebSocket or
  streamable HTTP, but its run/task lifecycle and event envelope are defined here.
- Feishu module decomposition beyond replacing its Agent integration calls.
- General command feature additions.

## 12. Rework risk comparison

| Concern | Line-based split | Boundary-first replacement |
|---|---|---|
| Ownership | unchanged | one owner per operation and closure edge |
| Testing | continues through private Agent fields | operation-level injected dependencies |
| Security review | mixed with stream/session code | isolated ToolStep plus existing executor |
| Consumer coupling | concrete Agent internals remain | exact runtime/control contracts |
| Future changes | continue enlarging orchestration center | land in current owner without runtime-wide branches |

## 13. Performance budget

The Campaign introduces no additional provider call, tool call, persistence
round-trip, or image hydration. Event delivery remains streaming. Regression
tests compare call counts and event order; existing token/latency trace recording
remains in the model operation (`src/pc_assistant/agent.py:914`).

## 14. Cross-module adaptation

| Consumer | Current coupling | Target |
|---|---|---|
| CLI entry | constructs concrete `Agent` (`src/pc_assistant/__init__.py:97`) | AgentFactory |
| Service server | concrete Agent plus direct admin internals (`service/server.py:24`, `service/server.py:469`) | Core Server owns AgentRuntime + ControlService |
| Core client | imports event from legacy concrete Agent (`service/client.py:17`) | thin wire adapter using Core API v1 `RunEvent`; no runtime-port implementation |
| Textual/Rich UI | concrete type and class-name branches (`ui/app.py:14`, `ui/chat.py:328`) | thin client using `CoreClient.command/run` |
| Feishu | direct runtime admin/artifact properties (`channels/feishu.py:1081`, `channels/feishu.py:1504`) | channel client adapter; execution remains Core Server |
| Benchmark | constructs concrete Agent (`benchmark/runner.py:8`) | remote benchmark client or direct Core test fixture |
| Scheduler | `Any` complete Agent (`tools/scheduler.py:226`) | TurnInvoker |

## 15. Per-change rollback

| Change | Rollback boundary |
|---|---|
| C1 | revert contracts/factory and direct contract migrations together; no persisted data migration |
| C2 | revert ModelStep wiring and module together; conversation/storage formats unchanged |
| C3 | revert ToolStep wiring and module together; verifier/idempotency formats unchanged |
| C4 | revert complete runtime/consumer cutover, session lease, and transaction boundary as one unit; do not restore partial aliases |

Rollback is source-level because this Campaign changes no durable storage schema;
the intentionally breaking Core API v1 wire contract is cut over atomically with
the source implementation.

## 16. Test strategy

```text
Contract tests
  AgentRuntimePort / RuntimeEvent / Core API RunEvent / ControlService / TurnInvoker
        |
Operation tests
  ModelStep                    ToolStep
        \                        /
  ReActLoop + session lifecycle
                    |
Integration tests
  service / UI / Feishu / scheduler / benchmark / persistence
                    |
                full pytest
```

Existing high-value scenarios in `tests/test_agent.py:144`,
`tests/test_agent_sessions.py:26`, and `tests/test_multimodal.py:263` are moved to
the owner-level suites rather than retained as private Agent monkeypatch tests.

## 17. Human effort

Human review is concentrated at three points: this Blueprint finalization, each
Change-local `design.md` approval when required by harness, and final Campaign
review/archive. No additional compatibility or migration policy decision is
expected within the approved scope.
