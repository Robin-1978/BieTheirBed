# Design: Agent Runtime Contracts and Factory

## Architecture Model

C1 introduces one concrete composition root without changing the current turn
algorithm. `AgentFactory` constructs typed dependency bundles; the existing
`Agent` consumes those bundles and remains the temporary production runtime
until C4 deletes it. The `agent_runtime` package name avoids the `agent.py` /
package collision and exposes only canonical target vocabulary.

The target contracts are definitions, not a second runtime implementation:

- `RuntimeScope` carries transport-established principal and opaque session
  identity into Core-owned operations.
- `AgentRuntimePort` describes the exact uniformly asynchronous server-side
  application boundary.
- `RuntimeEvent` is internal execution state; `RunEvent` is the versioned public
  Core API v1 envelope.
- `TurnInvoker` is the narrow scheduled-turn callable.
- command/control request-result types define the future `ControlService`
  surface without implementing it in C1.

`AgentDependencies` contains process-lifetime dependencies plus one active
`ExecutionDependencies` value. `ExecutionDependencies` is the complete
publication unit for one execution generation: a committed configuration
snapshot, resolved main/fallback/vision model identities, provider, planner,
estimator, cache plan, reflection and vision collaborators, the registry and its
immutable tool-schema snapshot, and the verifier/executor bound to that same
registry. Immutability applies to the published composition and binding set;
operational collaborators may still maintain their documented internal state.

`Agent` publishes one reference to `ExecutionDependencies`; rebuild never
exposes a sequence of separately updated config, model, registry, or collaborator
attributes. `FactoryOverrides` records explicitly injected collaborators so a
candidate can carry them forward or reject a conflicting replacement without
silently discarding them. At turn ingress, `Agent` captures the current
`ExecutionDependencies` reference as a turn-local execution view. Every model
identity, turn-relevant configuration, provider, planner, estimator, cache,
reflection, vision, tool schema, authorization, and tool execution read for that
turn comes from that captured generation. A later publication affects only turns
that start afterward.

## Data Flow

```text
AppConfig + explicit overrides
  -> AgentFactory.build()
  -> construct candidate providers, stores, verifier/executor, tools, sessions
  -> validate complete AgentDependencies bundle
  -> Agent.__init__ consumes bundle
  -> existing production run loop

runtime model/config change
  -> copy committed config and current tool bindings into candidate state
  -> apply the requested change only to candidate state
  -> AgentFactory.rebuild_execution_dependencies(current generation, candidate, retained overrides)
  -> resolve identities and construct provider/planner/cache/vision candidates
  -> construct candidate registry/schema and matching verifier/executor bindings
  -> validate complete candidate ExecutionDependencies generation
  -> success: Agent publishes one generation reference for future turns
  -> failure: active generation, committed config, and registry remain unchanged

turn ingress
  -> capture current ExecutionDependencies generation once
  -> use its config/model/cache/vision/schema/registry/verifier/executor for the full turn
  -> concurrent publication is visible only to the next turn
```

No Client calls this factory. `CoreClient -> Core API v1 -> CoreServer` remains
the production deployment direction; C4 completes that cutover and removes the
current in-process fallback.

## Boundaries and Interfaces

### `agent_runtime/contracts.py`

- Defines immutable or validated request, scope, result, and event values.
- Defines `AgentRuntimePort` with direct asynchronous-stream `run` plus awaited
  scalar `cancel`, `health_check`, `get_status`, and `command` operations.
- Defines `TurnInvoker` narrowly enough for scheduler use.
- Does not import `agent.py`, service clients, UI modules, providers, registries,
  or repositories.
- Does not claim that `CoreClient` implements `AgentRuntimePort`.

Normative operation signatures are:

| Operation | Exact contract | Scope rule |
|---|---|---|
| run | `def run(scope: RuntimeScope, request: RunRequest) -> AsyncIterator[RuntimeEvent]` | returns the asynchronous iterator directly; caller uses `async for` without awaiting stream acquisition; validated scope required |
| cancel | `async cancel(scope: RuntimeScope, request: CancelRequest) -> CancelResult` | validated principal and opaque session handle required; no bare session-id overload |
| health_check | `async health_check() -> HealthStatus` | explicitly unscoped and exposes no session data |
| get_status | `async get_status(scope: RuntimeScope, request: StatusRequest) -> RuntimeStatus` | validated principal and opaque session handle required |
| command | `async command(scope: RuntimeScope, request: CommandRequest) -> CommandResult` | validated principal and opaque session handle required |
| TurnInvoker | `def __call__(scope: RuntimeScope, request: RunRequest) -> AsyncIterator[RuntimeEvent]` | returns the asynchronous iterator directly; Core scheduler receives a Core-minted scope; raw session strings are invalid |

`RuntimeScope` owns session identity; operation request types do not duplicate a
bare session identifier. C1 defines the trusted input shape. C4 owns principal
resolution and cross-principal enforcement in the live CoreServer/runtime path.
Only `run` and `TurnInvoker` are direct asynchronous-stream callables. Scalar
operations are `async def` and must be awaited exactly once.

### `agent_runtime/factory.py`

- Is a concrete factory, not a service locator or extensibility framework.
- Owns default main/fallback/vision provider creation, repositories, memory,
  safety, registry, verifier/executor, limiter, idempotency, session manager,
  recorders, evidence policy, artifact store, built-in tools, and cache plan.
- Accepts explicit overrides for existing tests and embedding call sites.
- Returns typed `AgentDependencies` and `ExecutionDependencies` bundles; it does
  not start a turn or retain mutable session execution state.
- Derives cache input and model-call tool schemas from the exact immutable schema
  snapshot stored in the candidate generation, not from a separately mutable
  live registry.
- When vision capability changes tool availability, constructs a candidate
  registry and rebinds the candidate verifier/executor to it. It never registers
  an image tool into a published registry during rebuild.
- Retains `FactoryOverrides` across rebuild. A configuration or tool-binding
  change that would replace an injected model, vision, registry, verifier, or
  executor collaborator returns a non-applied result and leaves the active
  generation unchanged; unrelated supported changes carry the injected
  collaborator into the complete candidate.

### `agent.py`

- Calls the factory and attaches its returned dependencies.
- Keeps current run-loop behavior during C1-C3.
- Stops owning concrete construction or provider-rebuild algorithms.
- Treats mutable admin configuration as candidate input, never as live turn
  authority. Successful validation publishes the candidate configuration only
  inside the new execution generation; rejection restores/discards staging.
- Captures one execution generation at turn ingress; status/config/registry
  reads resolve from the committed generation rather than independent live
  mirrors. Factory-controlled tool registration and clearing also publish a
  complete candidate generation rather than mutating the active registry.
- Receives no alias properties for new target components.

### Production closure

The exact Blueprint-owned edge is preserved:

`factory-runtime-dependencies`: `AgentFactory -> AgentRuntime` through an
explicit dependency bundle, owned by `agent-contracts-factory`, with no Change
dependency and a validated-configuration predecessor checkpoint.

During C1-C3 the current `Agent` is the temporary consumer of that same bundle;
C4 replaces the consumer with `AgentRuntime` without changing factory ownership.

## Invariants

1. There is exactly one default construction owner for each dependency.
2. Explicit injected collaborators are preserved and never silently rebuilt.
3. Config/model/tool candidates are fully constructed and validated before one
   active `ExecutionDependencies` reference is swapped.
4. One turn reads exactly one captured `ExecutionDependencies` generation,
   including model identity, config, cache, vision, schemas, registry, verifier,
   and executor, even if a newer generation is published while that turn is
   active.
5. The cache plan and model-call schema snapshot are derived from the same
   resolved model and registry contained in their generation.
6. A rejected candidate changes neither committed configuration nor active
   registry/schema/collaborator bindings.
7. Existing `VerifiedToolExecutor` remains the sole side-effect commit boundary.
8. `RuntimeEvent` never carries public `run_id` or `event_seq`; `RunEvent` does.
9. `RuntimeScope.principal_id` is transport-established runtime metadata and is
   never copied into model-visible messages.
10. `CoreClient` is not an `AgentRuntimePort` implementation.
11. C1 introduces no compatibility alias, private mirror, or alternate execution
   path.

## Failure and Operations

- Invalid or incomplete scope/event data fails validation before dispatch.
- A dependency-construction exception aborts construction; callers receive no
  partial bundle.
- A rebuild failure leaves the active execution generation and its committed
  configuration, resolved model identity, provider/planner/cache/vision,
  registry/schema, verifier, and executor bindings unchanged. Candidate config
  and registry state is discarded; no partially applied model alias remains.
- A rebuild cannot expose a mixed old/new execution set: readers dereference one
  `ExecutionDependencies` value, and each active turn retains the one generation
  captured at ingress. A conflicting update to an injected model, vision,
  registry, verifier, or executor collaborator is rejected without replacing it.
- A vision capability transition stages the corresponding image-tool binding,
  schema snapshot, cache plan, registry, verifier, and executor together. A
  validation or construction error publishes none of them.
- A disabled tool configuration returns a valid bundle with no built-ins rather
  than maintaining a second headless runtime.
- Resource paths and formats remain exactly:

  | Dependency | Existing authoritative location |
  |---|---|
  | memory and transcript repositories | `RuntimePaths.data / "assistant.db"` |
  | ArtifactStore database | `RuntimePaths.data / "assistant.db"` |
  | procedural memory | `RuntimePaths.data / "procedures"` |
  | idempotency log | `RuntimePaths.cache / "idempotency.json"` |
  | attachment staging | `RuntimePaths.attachments` |
  | persistent artifacts | `RuntimePaths.artifacts` |
  | audit logs | `RuntimePaths.logs / "audit"` |
  | LLM and turn traces | existing config paths resolved through `RuntimePaths.resolve()` |

  C1 changes construction ownership only; it performs no data copy, format
  migration, alternate-root fallback, or empty-store initialization policy.
- C1 adds no daemon/client retry or fallback behavior. Core availability policy
  remains the approved fail-closed C4 target.

## Production Closure

The Change is production-connected when `Agent.__init__` and model rebuild both
consume `AgentFactory` results. Contract types are exercised directly, and the
factory result is used by the live runtime rather than parked behind an unused
feature flag. The old constructor implementation is deleted as ownership moves;
it is not retained beside the factory.

Campaign baseline-path names remain exactly as finalized:
`interactive-agent-turn`, `verified-tool-turn`, `runtime-control-command`,
`scheduled-agent-turn`, and `artifact-mediated-turn`. C1 does not redefine
their Campaign semantics.

## Verification

- `tests/test_agent_contracts.py` verifies every exact signature independently,
  rejects a bare session handle for run/cancel/status/command/TurnInvoker,
  proves `run` and `TurnInvoker` are consumed by direct `async for` without an
  acquisition await, validates the awaited scalar operations and unscoped
  health contract, checks internal/public event shapes, canonical exports, and
  the absence of rejected aliases.
- `tests/test_agent_factory.py` verifies default construction, injected
  dependencies, fallback/vision decisions, built-in tools, current-Agent use of
  the factory, successful and failed immutable execution-generation rebuilds,
  injected override carry-forward/rejection, unchanged config and registry after
  rejection, absence of mixed reader-visible generations, exact
  RuntimePaths locations, and restart-style readability of pre-C1 durable data.
  A deterministic paused-turn interleaving proves that a spanning turn keeps one
  provider, resolved model identity, config snapshot, cache plan, vision binding,
  tool-schema snapshot, registry, verifier, and executor generation, and that the
  next turn observes the complete new generation.
- Existing behavior is protected by running both focused suites before the
  Change can enter Implement completion.

## Alternatives Considered

### Generic dependency container

Rejected. A container adds lookup indirection and lifetime rules without a
current requirement. Typed concrete bundles make ownership visible and testable.

### Factory protocol with multiple implementations

Rejected. There is one planned construction algorithm. Tests use explicit
overrides, not a second factory implementation.

### Keep `AgentEvent` and wrap it later

Rejected. It would preserve the current collision between internal execution
events and public transport events. C1 defines the separation now; C4 performs
the direct wire cutover.

### Make `CoreClient` implement the runtime port

Rejected. It collapses deployment and application boundaries and enables local
execution semantics in clients. Only CoreServer calls `AgentRuntimePort`.
