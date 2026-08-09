# Knoa Durable Task Design

> Status: active forward design; B1-B5 complete
>
> Date: 2026-08-09
>
> Scope: persistent tasks, event journal, connection-independent execution,
> durable approval, cancellation and restart recovery

## 1. Decision

Knoa replaces the connection-owned public `Run` with a Core-owned persistent
`Task`.

```text
Client / Channel
  → create_task
  ← task_accepted(task_id)
  → subscribe_task(task_id, after_seq)
  ← ordered TaskEvent stream

                     disconnect only removes subscription
                                      │
                                      ▼
TaskService → TaskRepository → TaskExecutor → AgentRuntime → ToolStep
                    │               │                 │
                    └─ EventJournal └─ ApprovalService└─ ArtifactStore
```

The Task is the product object visible to Feishu, CLI and the future App. A Run
becomes an internal execution attempt and is never the owner of task identity,
approval or event history.

The implementation is forward-only. It does not preserve a second public Run
protocol, connection-owned confirmation futures or disconnect-cancels-run
semantics.

## 2. Invariants

1. Task state belongs to Core and is persisted before execution begins.
2. A client connection may subscribe to a Task but never owns it.
3. Disconnecting a client does not cancel, fail or pause a Task.
4. Every public Task event is persisted before live delivery.
5. Event sequence numbers are strictly increasing within one Task.
6. Every Task reaches at most one terminal state.
7. Every command verifies the authenticated principal owns the Task.
8. Approval is a persistent aggregate resolved atomically at most once.
9. Tool authorization and commit remain behind the single ToolStep boundary.
10. Unknown execution outcome after process loss is never silently retried.
11. Artifact delivery uses references; Task events never expose host paths.
12. Channel rendering policy does not enter Task, Agent or Tool code.

## 3. Aggregate model

### 3.1 Task

```text
Task
├── task_id
├── principal_id
├── session_handle
├── client_request_id
├── parent_task_id?
├── goal
├── attachments[]
├── tools_enabled
├── priority
├── state
├── phase
├── attempt_count
├── cancel_requested
├── final_summary?
├── failure_code?
├── created_at / updated_at / started_at? / finished_at?
└── next_event_seq
```

`client_request_id` is idempotent within a principal. Repeating the same create
request returns the original Task rather than creating duplicate work.

Initial priority is a small integer with a bounded range. Parent/child identity
is stored now because it is cheap and stable, while dependency DAG execution is
deferred until a real workflow requires it.

### 3.2 State machine

```text
queued ───────→ running ───────→ completed
  │               │  │
  │               │  ├────────→ waiting_approval ──approved──→ running
  │               │  │                    │
  │               │  │                    └─denied/expired───→ running
  │               │  ├────────→ paused ─────────────→ queued
  │               │  └────────→ failed
  │               └───────────→ cancelled
  └───────────────────────────→ cancelled
```

Terminal states are `completed`, `failed` and `cancelled`.

`waiting_approval` means no tool commit is running. `paused` means Core has
intentionally stopped automatic progress, normally because recovery cannot
prove whether a previous side effect completed.

State transitions are validated by the domain model and committed with an
optimistic `revision`. SQL callers cannot invent transitions ad hoc.

### 3.3 Execution attempt

Each time the executor takes a queued Task it creates one attempt:

```text
TaskAttempt
├── attempt_id
├── task_id
├── ordinal
├── state: running | completed | failed | cancelled | interrupted
├── started_at / finished_at?
└── failure_code?
```

Attempts are diagnostic and recovery records. Clients normally operate on the
Task, not on attempt IDs.

### 3.4 Tool execution record

Every proposed tool call receives a Core-generated `tool_step_id`:

```text
TaskToolStep
├── tool_step_id
├── task_id / principal_id
├── tool_call_id
├── tool_name / normalized_arguments
├── effect / risk
├── state: committing | completed | failed | outcome_unknown
├── typed result
└── created_at / updated_at
```

Approval owns the pre-commit states. `TaskToolStep` is created atomically at the
last boundary before registry commit, so a stored `committing` record means the
outcome cannot be inferred after process loss.

`commit_key` is unique. A known completed commit is returned from storage and
never executed twice. If Core stops while a non-read-only step is `committing`,
recovery changes it to `outcome_unknown` and pauses the Task. Automatic replay
is forbidden because the external side effect may already have happened.

Read-only calls may be retried in a later phase after the tool declares that
property through Core-owned policy. No generic retry is part of the initial
slice.

## 4. Event journal

### 4.1 Event contract

```text
TaskEvent
├── task_id
├── event_seq
├── event_type
├── occurred_at
└── payload
```

Initial event types:

- `task_created`
- `state_changed`
- `reasoning_delta`
- `content_delta`
- `plan`
- `tool_call`
- `tool_result`
- `approval_requested`
- `approval_resolved`
- `artifact`
- `context_compacted`
- `warning`
- `final_output`
- `completed`
- `failed`
- `cancelled`

Runtime event payloads remain typed and channel-neutral. Approval events carry
stable approval identity and display-safe tool information, never secrets or
host-only paths.

### 4.2 Append rule

Appending an event and advancing `next_event_seq` occur in the same SQLite
transaction. Live delivery happens only after commit:

```text
domain transition
  → database transaction: mutate aggregate + append event
  → commit
  → publish event to in-memory subscribers
```

The in-memory EventHub is only a latency optimization. On subscription, a
client first reads persisted events after `after_seq`, registers for live
events, and closes the race with one final persisted read.

### 4.3 Retention

Phase B stores the complete Task event stream. A later compactor may replace
old high-frequency deltas with immutable content/reasoning snapshots only after
the Task is terminal. Terminal state, approvals, tool steps, warnings,
artifacts and final output are never discarded by delta compaction.

## 5. Durable approval

Approval belongs to Core, not a WebSocket connection:

```text
TaskApproval
├── approval_id
├── task_id / tool_step_id
├── principal_id
├── state: pending | approved | denied | expired | cancelled
├── reason
├── display_payload
├── created_at / resolved_at? / expires_at?
└── resolved_by?
```

`ApprovalService.request()` atomically creates the approval, moves the Task to
`waiting_approval` and appends `approval_requested`. The executor waits on a
Core-owned signal keyed by `approval_id`; this signal is only a wake-up
optimization because the database remains authoritative.

`ApprovalService.resolve()`:

1. verifies Task and principal ownership;
2. atomically changes only a `pending` approval;
3. appends `approval_resolved`;
4. moves the Task back to `running` when appropriate;
5. wakes the executor.

Feishu buttons, CLI prompts and App biometric confirmation all call the same
command. Double click, stale card and cross-user resolution return a stable
`already_resolved` or `not_found` result without changing state.

## 6. Execution ownership

### 6.1 TaskService

Owns commands and queries:

- create, get and list Tasks;
- cancel, pause and resume;
- resolve approval;
- read events after a sequence;
- subscribe to future events.

It contains no model, tool or Channel behavior.

### 6.2 TaskExecutor

Owns execution orchestration:

1. claims a queued Task with an atomic lease;
2. creates an attempt;
3. invokes AgentRuntime with TaskContext;
4. persists each normalized event;
5. coordinates durable approval through ApprovalService;
6. commits terminal Task state;
7. releases the lease.

One Task has at most one live executor lease. The implementation uses one Core
process, a bounded four-slot dispatcher and SQLite-backed claims. Different
sessions may execute concurrently, while the claim query prevents two active
Tasks from sharing one session. The lease fields keep restart recovery explicit
without prematurely building distributed workers.

### 6.3 AgentRuntime

AgentRuntime continues to own session serialization, context assembly, ReAct
and transcript commit. Its internal execution context carries Task identity,
cancellation, durable approval and durable tool-commit ports. It does not store
Task aggregate state or publish to clients.

### 6.4 CoreServer

CoreServer authenticates, validates wire contracts and invokes TaskService. It
does not create execution tasks tied to a socket and does not cancel work in a
disconnect `finally` block.

## 7. Restart recovery

At Core startup, RecoveryService performs one bounded pass:

| Persisted state | Recovery action |
|---|---|
| `queued` | enqueue |
| `waiting_approval` | keep approval pending, interrupt the old attempt, clear its lease and start a new attempt after resolution |
| `running`, no `committing` ToolStep | interrupt attempt and pause for explicit resume |
| `running`, ToolStep `committing` | mark ToolStep `outcome_unknown`, interrupt attempt, pause and notify |
| terminal | no action |

Recovery intentionally prefers a visible pause over speculative replay. Normal
interruption accepts `resume_task`; `outcome_unknown` additionally requires
`acknowledge_outcome_unknown=true`. A matching ToolStep identity remains blocked
after acknowledgement, so generic resume cannot silently repeat the uncertain
commit. A later reconciliation command may explicitly resolve or replace it.

## 8. Cancellation

Cancellation is a persistent command:

1. set `cancel_requested` transactionally;
2. append a state/warning event;
3. signal the live executor if present;
4. before every model step and tool commit, re-check persistent cancellation;
5. finish as `cancelled` at the next safe boundary.

Cancelling while waiting approval atomically cancels the approval. Cancelling a
completed, failed or already cancelled Task is idempotent and returns its
terminal state.

Manual pause follows the same safe-boundary rule without overloading
cancellation. A queued or approval-blocked Task moves directly to `paused`; a
running Task first persists `phase=pause_requested`, signals its runtime, then
enters `paused/manual_pause` after the current non-interruptible commit boundary.
Restart also completes a persisted pause request conservatively. Resume creates
a new Attempt through the normal queue.

## 9. Core API target

Public operations become:

```text
create_task(session_handle, client_request_id, input, attachments, tools_enabled, priority)
get_task(task_id)
list_tasks(session_handle?, state?, limit, cursor?)
subscribe_task(task_id, after_seq)
cancel_task(task_id, reason)
resolve_approval(approval_id, approved)
pause_task(task_id, reason)
resume_task(task_id, acknowledge_outcome_unknown=false)
```

Messages are `task_accepted`, `task_snapshot`, `task_list`, `task_event`,
`task_cancel_result`, `task_pause_result`, `task_resumed` and
`approval_resolved`. There is no public compatibility alias from Task back to
Run.

Create, detail, cursor list, subscribe, cancel, safe-boundary pause, resume and
approval resolution are implemented.

## 10. SQLite ownership

Use dedicated tables in the existing runtime database:

- `runtime_tasks`
- `runtime_task_attempts`
- `runtime_task_events`
- `runtime_task_tool_steps`
- `runtime_task_approvals`

Repository methods own SQL and transactions. Service and executor code operate
on typed domain records. Every query includes principal ownership either
directly or through an owned Task join.

Schema changes use an explicit forward migration. Runtime startup must not
silently reinterpret an incompatible table and must not retain legacy run
tables as a fallback.

## 11. Capacity and backpressure

- bound global and per-principal queued/running Tasks;
- bound persisted payload and event size;
- bound subscriber queues and close slow subscriptions with the last delivered
  sequence so clients can reconnect;
- limit Task list and event replay page sizes;
- serialize Tasks sharing one session while allowing independent sessions to
  execute concurrently;
- never hold a database transaction across model, network or tool execution.

The production repository currently admits at most 128 non-terminal Tasks
globally and 32 per principal. Idempotent retries are resolved before capacity
checks, so retrying an accepted create request never fails merely because the
queue subsequently filled.

## 12. Observability

Metrics and logs use task/attempt identity:

- queue latency and execution duration;
- state transition counts;
- event persistence and delivery lag;
- approval age and resolution outcome;
- recovery and `outcome_unknown` counts;
- tool success, failure and commit latency;
- active/queued Tasks by principal-safe aggregate only.

Message bodies, reasoning, secrets, raw principal IDs and tool arguments remain
outside INFO logs.

## 13. Implementation sequence

> Progress (2026-08-09): B1-B4 are implemented in the
> production composition root. Every claim creates a durable Attempt; every
> ToolStep records `committing` before registry execution and a terminal result
> afterward. A terminal-checkpoint failure atomically marks unfinished steps
> `outcome_unknown`, interrupts the Attempt and pauses the Task; restart applies
> the same fail-closed rule. Pending approvals survive restart, uncertain resume
> requires explicit acknowledgement, and matching ToolSteps remain blocked.
> Bounded cross-session concurrency, same-session serialization, Task detail,
> cursor pagination and safe-boundary manual pause are live.

### B1. Persistent Task aggregate and EventJournal

- add typed Task records and state transition rules;
- add SQLite repository and explicit schema validation;
- add atomic create/idempotency, claim, transition and event append;
- test ownership, sequencing, terminal immutability and restart reads.

### B2. TaskExecutor and connection-independent stream

- wrap AgentRuntime behind TaskExecutor;
- add persistent-first EventHub publication and `after_seq` replay;
- remove socket-disconnect cancellation;
- make client cancellation target Task identity.

### B3. Durable ApprovalService

- replace connection confirmation futures;
- persist approval request/resolution and Task waiting state;
- update Feishu/TUI confirmation adapters to standard Task commands.

### B4. Recovery and multi-task control

- startup recovery pass and execution leases;
- Task list/detail, pause/resume and per-session scheduling;
- explicit `outcome_unknown` handling.

### B5. Scheduler and Trigger

- one-time, interval and Cron schedules;
- authenticated webhook/business trigger port;
- bounded retry/backoff and proactive notification;
- create Tasks through TaskService rather than invoke AgentRuntime directly.

> In progress: the shared recurrence kernel now validates typed one-time,
> interval and five-field Cron specifications with explicit IANA timezones.
> Interval calculation remains anchored to the original start time to avoid
> drift. `runtime_schedules` and `runtime_schedule_occurrences` now persist the
> plan and every delivery claim. The production dispatcher uses the stable
> occurrence ID as the Task request ID, retries with bounded exponential backoff
> and survives expired worker leases without duplicating Task creation. Core API
> v1 exposes create/detail/list/pause/resume schedule commands. Resuming interval
> or Cron plans skips accumulated downtime instead of flooding catch-up Tasks;
> an expired one-time plan completes without silently running late.
>
> Authenticated Trigger ingress is also implemented as a transport-independent
> Core service and authenticated Core API adapter. Trigger definitions and
> external events are persisted separately; `trigger_id + external_event_id`
> provides event deduplication, and the stable trigger-event ID becomes the Task
> request ID. Paused triggers reject new events and hold already received events.
> Payloads are size-bounded and explicitly labelled as untrusted data before
> entering Task input. Core now also publishes a durable, ordered,
> principal-scoped Task event feed. Feishu consumes that standard feed with a
> persisted cursor, suppresses duplicate delivery for Tasks already presented
> in a live card, presents durable background approvals, and proactively
> delivers terminal results.
> The optional HTTP webhook adapter is mounted above Core on a separate
> loopback port. Declarative routes bind an external path to one principal and
> Trigger, verify HMAC-SHA256 over the external event ID and bounded raw JSON
> body, then call the authenticated Core Trigger API. It owns no Task execution
> logic and must be exposed externally only through a TLS reverse proxy.

## 14. Acceptance criteria

1. A disconnected client can reconnect with `after_seq` and recover all
   committed events.
2. Disconnect never cancels a Task.
3. Core restart preserves queued and waiting-approval Tasks.
4. Approval can be resolved from a different authenticated connection belonging
   to the same principal.
5. Cross-principal Task reads, cancellation, subscription and approval fail
   without revealing existence.
6. Event sequences remain gap-free and monotonic under concurrent subscribers.
7. A Task emits one terminal state and cannot transition afterward.
8. An uncertain external side effect is never automatically executed again.
9. Feishu, CLI and future App consume the same persisted Task events.
10. No Channel object, WebSocket future or legacy public Run contract owns Task
    lifecycle.
