# knoa-system Specification

## ADDED Requirements

### Requirement: Core runtime operations SHALL use an exact principal-bound async port
@id: knoa.runtime.exact-port

- [REQ-001] The system SHALL define `AgentRuntimePort.run(RuntimeScope, RunRequest) -> AsyncIterator[RuntimeEvent]` and `TurnInvoker(RuntimeScope, RunRequest) -> AsyncIterator[RuntimeEvent]` as direct asynchronous-stream callables consumed with `async for` and no stream-acquisition `await`; `cancel`, `health_check`, `get_status`, and `command` SHALL be scalar asynchronous operations awaited exactly once with the request-result types defined by this Change.

#### Scenario: [REQ-001-S01][normal] Valid runtime scope crosses the port
- **WHEN** CoreServer supplies a non-empty transport-established principal and opaque session handle
- **THEN** the port SHALL accept the scope without copying principal identity into model-visible content

#### Scenario: [REQ-001-S02][error] Invalid runtime scope is rejected
- **WHEN** a session-scoped request omits its principal or session handle
- **THEN** contract validation SHALL reject the request before runtime dispatch

#### Scenario: [REQ-001-S03][error] Bare cancellation handle is rejected
- **WHEN** a caller attempts to cancel with only a session identifier
- **THEN** the cancellation contract SHALL reject the call instead of bypassing `RuntimeScope`

#### Scenario: [REQ-001-S04][error] Bare status handle is rejected
- **WHEN** a caller requests session status with only a session identifier
- **THEN** the status contract SHALL reject the call instead of bypassing `RuntimeScope`

#### Scenario: [REQ-001-S05][error] Bare command handle is rejected
- **WHEN** a caller submits a control command with only a session identifier
- **THEN** the command contract SHALL reject the call instead of bypassing `RuntimeScope`

#### Scenario: [REQ-001-S06][boundary] Scheduler invokes a principal-bound turn
- **WHEN** Core scheduler code invokes `TurnInvoker`
- **THEN** it SHALL supply a Core-minted `RuntimeScope` and SHALL NOT invoke a raw-session overload

#### Scenario: [REQ-001-S07][boundary] Health remains unscoped
- **WHEN** a caller checks Core health
- **THEN** the health contract SHALL require no session scope and SHALL expose no session-owned data

#### Scenario: [REQ-001-S08][normal] Run stream is iterated directly
- **WHEN** CoreServer consumes `AgentRuntimePort.run` or scheduler consumes `TurnInvoker`
- **THEN** the caller SHALL use `async for` on the returned iterator without awaiting a separate stream-acquisition coroutine

### Requirement: Internal and public execution events SHALL remain separate contracts
@id: knoa.runtime.event-separation

- [REQ-002] The system SHALL represent Core-only operation events as `RuntimeEvent` and Core API v1 delivery events as `RunEvent`, with only `RunEvent` carrying `run_id`, `event_seq`, `event_type`, and a versioned typed payload.

#### Scenario: [REQ-002-S01][normal] Public event envelope validates its required shape
- **WHEN** a Core API v1 public event contract is constructed
- **THEN** it SHALL require run identity, positive event sequence, event type, and a typed payload without serving as the sequence allocator or delivery path

#### Scenario: [REQ-002-S02][boundary] Internal event remains transport-neutral
- **WHEN** an internal event is created by a runtime operation
- **THEN** it SHALL NOT require a public run identifier or wire sequence number

### Requirement: AgentFactory SHALL be the single concrete dependency-construction owner
@id: knoa.runtime.factory-owner

- [REQ-003] The system SHALL construct provider, fallback, vision, repository, memory, safety, registry, verifier/executor, limiter, idempotency, session, recorder, evidence, artifact, built-in tool, and cache-plan dependencies through one concrete `AgentFactory`, and the current production Agent SHALL consume the returned typed bundle.

#### Scenario: [REQ-003-S01][normal] Default runtime dependencies are constructed
- **WHEN** Agent is created from an application configuration without injected collaborators
- **THEN** AgentFactory SHALL return one complete dependency bundle preserving the configured model, fallback, vision, tools, paths, and policies

#### Scenario: [REQ-003-S02][boundary] Explicit collaborators are injected
- **WHEN** a test or embedding call supplies supported collaborators explicitly
- **THEN** AgentFactory SHALL preserve those collaborators and construct only the missing defaults

#### Scenario: [REQ-003-S03][error] Construction fails partway
- **WHEN** any required candidate dependency cannot be constructed
- **THEN** AgentFactory SHALL fail without returning or installing a partial dependency bundle

#### Scenario: [REQ-003-S04][migration] Existing durable state survives construction-owner cutover
- **WHEN** transcript, memory, artifact, idempotency, audit, or trace data exists at the pre-C1 RuntimePaths-derived locations
- **THEN** factory-constructed dependencies SHALL use the same locations and formats and SHALL read the existing data without copying it to a new root

### Requirement: Execution dependency generation SHALL commit atomically
@id: knoa.runtime.atomic-model-rebuild

- [REQ-004] The system SHALL build one complete replacement `ExecutionDependencies` generation containing the committed configuration and resolved model identities, provider/planner/estimator/cache/reflection/vision collaborators, registry and immutable tool-schema snapshot, and matching verifier/executor bindings before atomically publishing its single active reference; each turn SHALL capture exactly one generation at ingress for its full lifetime, and candidate reconstruction SHALL retain explicit collaborators or reject changes that would silently replace them.

#### Scenario: [REQ-004-S01][normal] Model configuration changes successfully
- **WHEN** a supported provider or model configuration field changes and all replacement dependencies are valid
- **THEN** the runtime SHALL publish the complete candidate execution generation once for turns that begin after publication

#### Scenario: [REQ-004-S02][error] Replacement provider construction fails
- **WHEN** any replacement dependency raises an error
- **THEN** the active execution generation, committed configuration, resolved model identity, registry, and tool-schema snapshot SHALL remain unchanged

#### Scenario: [REQ-004-S03][boundary] Rebuild retains explicit collaborators
- **WHEN** supported unrelated model configuration changes are rebuilt while an explicit collaborator remains applicable
- **THEN** the complete candidate execution generation SHALL retain that collaborator rather than replacing it with a default

#### Scenario: [REQ-004-S04][error] Rebuild conflicts with an injected collaborator
- **WHEN** a configuration or tool-binding change would replace an explicitly injected model, vision, registry, verifier, or executor collaborator
- **THEN** the rebuild SHALL report that it was not applied and SHALL leave the active execution generation and committed configuration unchanged

#### Scenario: [REQ-004-S05][concurrency] Reader observes one complete bundle
- **WHEN** an active turn spans a successful publication that changes model identity or vision-dependent tool bindings
- **THEN** that turn SHALL use only its ingress-captured provider, model identity, config, cache, vision, schema, registry, verifier, and executor generation, and the next turn SHALL use the complete newly published generation

#### Scenario: [REQ-004-S06][error] Vision tool candidate fails validation
- **WHEN** a model capability change requires different image-tool bindings and candidate registry, schema, verifier, executor, or cache validation fails
- **THEN** none of the candidate configuration or bindings SHALL become visible and the prior generation SHALL remain active

### Requirement: The target package SHALL expose canonical names without compatibility aliases
@id: knoa.runtime.canonical-exports

- [REQ-005] The `knoa_platform.agent_runtime` package SHALL expose the approved runtime contracts and factory using canonical names and SHALL NOT export `AgentLike`, `ServiceClient`, `AgentEvent`, `RuntimeControl`, `TurnOrchestrator`, or an `Agent = AgentRuntime` alias.

#### Scenario: [REQ-005-S01][normal] Consumer imports target contracts
- **WHEN** Core code imports the runtime package
- **THEN** canonical contract and factory symbols SHALL be available from their owning modules

#### Scenario: [REQ-005-S02][error] Consumer requests a rejected alias
- **WHEN** a caller attempts to import a rejected compatibility name from the target package
- **THEN** the import SHALL fail rather than route through a shim or re-export
