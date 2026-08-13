# knoa-system Specification

## Purpose

Defines the knoa-system capability and its required behavior.

## Requirements

### Requirement: Interaction surfaces SHALL use CoreServer as the only execution authority
@id: knoa.entry.streaming-execution

The system SHALL expose user requests, streamed execution events, cancellation,
confirmation, and status through a versioned client-to-CoreServer contract
across CLI, TUI, Feishu, service, and future remote App entrypoints. CoreServer
SHALL be the only production execution authority; clients SHALL NOT construct
or execute an in-process AgentRuntime fallback.

#### Scenario: CoreServer is unavailable
- **WHEN** the configured CoreServer cannot be reached or started
- **THEN** the client SHALL report a Core availability error without executing the request locally



### Requirement: Sessions SHALL isolate conversation and cancellation state
@id: knoa.session.isolation

The system SHALL bind conversation history, usage accounting, cancellation,
rollback, and tool-loop state to a session identifier.

#### Scenario: One session is cancelled
- **WHEN** session A is cancelled while session B is active
- **THEN** session B SHALL retain its conversation and continue independently





### Requirement: Context assembly SHALL preserve message and artifact integrity
@id: knoa.context.integrity

The context subsystem SHALL assemble bounded provider requests while preserving
role order, atomic assistant-tool-call/tool-result relationships, and
reference-only durable representation of binary artifacts.

#### Scenario: Context budget is exceeded
- **WHEN** assembled history exceeds the configured context budget
- **THEN** truncation or compaction SHALL retain a structurally valid current turn and valid tool-call/result grouping





### Requirement: Model adapters SHALL provide provider-neutral responses
@id: knoa.provider.neutral-ir

The model adapter layer SHALL convert supported provider requests and responses
to canonical message, response, stream-chunk, usage, and tool-call
representations.

#### Scenario: Provider-specific tool calling
- **WHEN** a supported provider returns native tool-calling data
- **THEN** the execution loop SHALL receive the same canonical tool-call structure





### Requirement: Side-effecting tools SHALL require deterministic authorization
@id: knoa.safety.authorize-before-execute

The system SHALL evaluate deterministic safety policy and required user
confirmation before committing a side-effecting tool execution.

#### Scenario: Confirmation channel is unavailable
- **WHEN** policy requires confirmation and no confirmation mechanism is available
- **THEN** the operation SHALL fail closed without executing the tool





### Requirement: Tool execution SHALL expose schemas and bounded results
@id: knoa.tools.schema-and-results

The tool subsystem SHALL expose JSON-compatible schemas and return bounded
results or typed failures suitable for the execution observation flow.

#### Scenario: Unknown tool
- **WHEN** the model proposes a tool name that is not registered
- **THEN** the system SHALL reject the proposal with a typed tool-not-found verdict





### Requirement: Runtime observability SHALL use configurable bounded sinks
@id: knoa.runtime.observability

The system SHALL write application, audit, LLM-call, and turn records to
configurable sinks without treating logs as authoritative conversation storage.

#### Scenario: A log directory does not exist
- **WHEN** logging is enabled and the configured parent directory is absent
- **THEN** the system SHALL create the directory or disable the affected optional recorder without terminating the request lifecycle





### Requirement: Verification SHALL cover core architecture boundaries
@id: knoa.quality.verification

The project SHALL provide executable pytest coverage for execution flow,
sessions, providers, context assembly, services, tools, safety, observability,
and critical desktop behavior, subject to the configured coverage threshold.

#### Scenario: Verification is run
- **WHEN** the project test command executes in a supported development environment
- **THEN** failures in an architecture boundary SHALL prevent a passing completion claim
