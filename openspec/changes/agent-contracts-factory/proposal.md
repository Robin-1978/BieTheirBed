# Proposal: Agent Runtime Contracts and Factory

## Motivation

`Agent` currently owns both execution behavior and nearly all concrete dependency
construction. Its constructor creates providers, fallback providers, memory and
transcript repositories, safety verification, tool execution, idempotency,
recorders, artifacts, vision support, sessions, and built-in tools. That
concentration prevents the approved runtime boundaries from being expressed or
tested without importing the monolith.

This Change establishes the exact target contracts and moves concrete
construction to one `AgentFactory`. The existing production `Agent` immediately
consumes the factory result, so C1 delivers a live composition-root outcome and
does not create an alternate runtime path.

## Campaign Anchor

- Campaign: `agent-runtime-decomposition`
- Blueprint: `docs/agent-runtime-decomposition-phases.md`
- Blueprint digest: `sha256:665c9e99f4dbb85b250325f12d7de8dce730675e0f0fb817ef20615ba72bb0a8`
- Owned closure edge: `factory-runtime-dependencies`
- Campaign dependencies: none

The Blueprint remains authoritative for Campaign-wide paths and closure. This
Change owns only the explicit dependency-bundle edge from `AgentFactory` to the
runtime consumer.

## Investigation

- `src/knoa_platform/agent.py:142-309` constructs the main/fallback model,
  repositories, verifier/executor, session manager, recorders, artifact store,
  vision broker, tools, and cache plan inside `Agent.__init__`.
- `src/knoa_platform/agent.py:685-779` reconstructs model dependencies during
  configuration changes, creating a second construction site.
- `src/knoa_platform/agent.py:111` defines the current internal event beside the
  concrete runtime, while `src/knoa_platform/service/protocol.py` wraps that event
  in an unversioned service envelope.
- `src/knoa_platform/service/agent_like.py` defines an inexact shared protocol:
  its asynchronous `cancel` does not match concrete `Agent.cancel`.
- `src/knoa_platform/service/lifecycle.py` still falls back to an in-process
  `Agent`; its removal belongs to C4, not this Change.
- Existing tests inject concrete dependencies by replacing private `Agent`
  members. C1 introduces constructor-level dependency seams; C2-C4 migrate the
  operation tests to their final owners.

## Scope

### In scope

- Add the non-colliding `knoa_platform.agent_runtime` package.
- Define `RuntimeScope`, `RuntimeEvent`, Core API v1 `RunEvent`, exact async
  `AgentRuntimePort`, `TurnInvoker`, and operation-specific request-result types;
  every session-scoped signature takes `RuntimeScope`, while health is explicitly
  unscoped.
- Add a concrete `AgentFactory` and explicit dependency bundles.
- Move default provider/fallback/vision, repository, verifier/executor, tool,
  recorder, artifact, session, and cache-plan construction out of `Agent`.
- Route model/configuration and affected tool-binding reconstruction through the
  same factory boundary.
- Preserve explicit dependency injection used by tests and custom embeddings,
  including during successful and rejected model rebuilds.
- Characterize every existing RuntimePaths-derived durable location and prove
  pre-C1 transcript, memory, artifact, idempotency, audit, and trace data remains
  readable after the factory cutover.

### Out of scope

- Moving model streaming into `ModelStep` (C2).
- Moving tool authorization/commit into `ToolStep` (C3).
- Implementing `AgentRuntime`, `ControlService`, `ReActLoop`, CoreServer sequence
  allocation/delivery,
  client migration, lifecycle fallback removal, or old-module deletion (C4).
- Rewriting providers, verifier policy, repositories, tools, or persistence.

## Compatibility

This is a forward replacement with `breaking_ok` internal and wire policy. New
code imports only canonical target names. C1 adds no `AgentLike`, `ServiceClient`,
`AgentEvent`, `RuntimeControl`, or `TurnOrchestrator` alias, no private-attribute
mirror, no translation shim, and no second execution route. The final Core API
wire cutover remains atomic in C4.

## Observable Outcome

Constructing the current production `Agent` invokes `AgentFactory` as the single
owner of concrete dependencies, while contract tests can exercise the target
runtime scope, event separation, and exact asynchronous port independently of
the monolithic implementation.

## Quality Floor

- Default and injected construction retain existing provider, fallback, vision,
  tool, safety, persistence, artifact, and recorder behavior.
- Execution dependencies are installed through one immutable generation
  reference containing the committed configuration and resolved model identity,
  provider/planner/cache/vision collaborators, and the matching registry, tool
  schema, verifier, and executor bindings. Reconstruction stages the complete
  candidate without mutating live configuration or bindings, then publishes it
  once or leaves the prior generation unchanged. Each turn captures one
  generation at ingress and uses that snapshot for its complete lifetime.
- Every durable dependency retains its existing RuntimePaths-derived location
  and reads data created before the factory cutover.
- `RuntimeEvent` and public `RunEvent` remain different models.
- The new package exports no legacy alias and duplicates no construction owner.

## Estimate

6 owned files, approximately 1,400 production lines touched plus 600 focused
test lines. This exceeds the Blueprint's initial sizing estimate because the
Critic-required closure replaced the narrower model-only rebuild with one
complete execution generation covering configuration, main/fallback/vision
identity, provider/planner/cache/vision collaborators, registry/schema, and
matching verifier/executor bindings. The owned closure edge and Change scope are
unchanged; no additional runtime path or compatibility surface was added.

## Alternatives Considered

1. Keep construction in `Agent` and add helper methods. Rejected because `Agent`
   remains the composition owner and model rebuild stays duplicated.
2. Add a generic dependency-injection container or plugin registry. Rejected as
   unnecessary; one concrete factory and typed bundles satisfy current needs.
3. Move the entire run loop in C1. Rejected because it mixes contract/factory
   work with the separately approved ModelStep, ToolStep, and ReActLoop Changes.
