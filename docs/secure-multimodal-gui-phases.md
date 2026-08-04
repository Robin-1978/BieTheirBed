# Secure Multimodal GUI Defect-Hardening Plan

> This replaces the rejected greenfield interpretation. It starts from current code, preserves implemented behavior, and repairs verified defects.
> Based on `docs/architecture.md`, `specs/MULTIMODAL-GUI-001.md`, current-code inspection, and independent Critic findings.
> Core insight: images are short-lived observations, never durable conversation content; GUI actions require stable targets and deterministic authorization ordering.
> Key architecture decisions: reference-only history; one runtime path authority; request-only hydration; snapshot-bound targeting; verified execution followed by postcondition evidence.
> status: implemented; functional regression passing; repository coverage debt remains
> type: corrective-plan
> mode: lite
> source: specs/MULTIMODAL-GUI-001.md
> rejected-blueprint: .gs-harness/plans/secure-multimodal-gui-v2.yaml (do not finalize/decompose)
> coverage-matrix: docs/secure-multimodal-gui-coverage-matrix.yaml
> orchestration-budget: changes<=6; critical-path<=5; shared-path-overlap<=2
> Last updated: 2026-08-04

## 1. Scope and non-goals

In scope: audit and correction of the existing implementation: unified runtime
logs, bounded AttachmentStore, attachment-reference ingress, reference-only history and events, request-scoped provider
hydration, provider role/capability contracts, stable GUI targets, coordinate
transforms, deterministic action authorization, and true post-action evidence.

Already implemented behavior is not a Change by itself. Each work item below
must close a source-verified defect, state its root cause, and prove the repair
with a regression test. Broad refactors require evidence that a local boundary
repair is insufficient.

Non-goals: legacy `data_url` compatibility, compatibility shims, model-initiated
image recall, any standalone grounding service, and a general Agent rewrite.

## 2. Target flows

```text
CLI / Feishu / screenshot / service upload
  -> AttachmentStore.put(session, validated bytes)
  -> attachment_id
  -> image_ref or observation_ref in Conversation
  -> RequestAssembler bounded selection
  -> request-only hydration
  -> role-aware ProviderSerializer
  -> provider request
  -> release temporary bytes/encoding
```

```text
ObservationRef -> ElementRef -> CoordinateTransform
               -> ActionPolicy -> VerifiedToolExecutor
               -> exactly one commit -> PostconditionVerifier
```

The current service attachment field is replaced, not shimmed: `data_url`
fields are rejected. New clients upload/store bytes first and send only
attachment references in run requests.

## 3. Phase roadmap

| Phase | Changes | Dependency |
|---|---|---|
| Runtime foundation | C1 | none |
| Binary lifecycle | C2 | C1 |
| Reference-only context | C3 | C2 |
| Provider boundary | C4 | C3 |
| GUI safety | C5 -> C6 | C3 |

```text
C1 -> C2 -> C3 -> C4
              \
               -> C5 -> C6
```

## 5. Change breakdown

### 5.0 Naming map

| ID | OpenSpec slug |
|---|---|
| C1 | `runtime-layout-and-logs` |
| C2 | `attachment-store-and-ingress` |
| C3 | `image-reference-message-pipeline` |
| C4 | `provider-role-aware-vision` |
| C5 | `gui-targeting-coordinate-transform` |
| C6 | `verified-execution-postcondition` |

### 5.1 Overview and ownership

| Change | Owner node | Summary |
|---|---|---|
| C1 | runtime/observability | Implemented: one runtime resolver; application, service, audit, LLM, and turn logs under `logs/` |
| C2 | runtime/service/channels | Implemented: TTL AttachmentStore, reference ingress, unified screenshot artifacts, and Feishu image delivery |
| C3 | context/agent | Implemented: reference-only history/events/cache and request-only hydration |
| C4 | model adapter | Implemented: provider/role-aware vision serialization and capability enforcement |
| C5 | tools/vision | Implemented: snapshot-bound ElementRef and service-side coordinate transforms |
| C6 | harness/agent | Implemented: single verified execution capability and post-execution verification |

### 5.2 Dependencies

Critical path length is 5: `C1 -> C2 -> C3 -> C5 -> C6`.
After C3, C4 and C5 may proceed in parallel. C6 depends on C5 because action
preconditions consume stable target and transform contracts.

### 5.3 Controllability and verification

| Change | Estimated owned paths | Size | Observable outcome | Simultaneous quality floor | Exact verification |
|---|---|---|---|---|---|
| C1 | `runtime.py`, `config.py`, `logger.py`, `service/server.py`, `observability/trace.py`, runtime-path tests | 5 logical files, ~170 lines | All logs resolve below one configured `logs/` | Socket/PID OS semantics preserved; no attachment files under logs | `pytest tests/test_runtime.py tests/test_service.py` |
| C2 | attachment store module, `service/client.py`, `service/protocol.py`, Feishu/image ingress, screenshot ingress, tests | 6 logical files, ~200 lines | Every accepted history image becomes a session-owned attachment ID; screenshots use explicit temporary artifacts | Run-level `data_url` rejected; bounded store; periodic/startup/session cleanup; Feishu sends declared artifacts | `pytest tests/test_attachment_store.py tests/test_multimodal.py tests/test_service.py tests/test_tool_artifacts.py tests/test_feishu_channel.py` |
| C3 | `model_adapter/content.py`, `context/conversation.py`, `context/assembly.py`, `agent.py`, cache/idempotency integration, tests | 6 logical files, ~200 lines | Stored history and emitted events contain only references/placeholders | No binary content in history/compaction/event/trace/audit/cache/idempotency; tool groups remain atomic | `pytest tests/test_context.py tests/test_context_cache.py tests/test_agent.py tests/test_multimodal.py` |
| C4 | provider profiles, OpenAI/Anthropic parsers, provider facade, tests | 5 files, ~170 lines | Each supported provider/role receives legal image content | No silent drop; unsupported role/MIME/count/size returns typed error; fallback never writes synthetic content into Conversation | `pytest tests/test_model_adapter.py tests/test_llm_provider.py tests/test_multimodal.py` |
| C5 | `vision/a11y.py`, `vision/grid.py`, `tools/ui.py`, `tools/screen.py`, window integration, tests | 5 files, ~190 lines | Target and image coordinates map to a fresh desktop target | Duplicate/stale/hidden targets fail closed; negative origin, DPI, rotation, and crop transforms tested | `pytest tests/test_layer2_gui.py tests/test_window.py` |
| C6 | `harness/verifier.py`, verified executor boundary, `agent.py`, audit/idempotency integration, tests | 5 files, ~180 lines | Authorization precedes exactly one execution and postcondition follows it | Registry cannot be used as public bypass; visual prompt injection cannot expand authority; failure stops/reobserves | `pytest tests/test_harness.py tests/test_layer2_gui.py tests/test_agent.py` |

Campaign gate: `pytest --cov=pc_assistant --cov-report=term-missing --cov-fail-under=80`.

Comparison:

| Plan | Changes | Critical path | Lifecycle amplification | Shared-path overlap | Outcome-less | Preserves closure? |
|---|---:|---:|---:|---:|---:|---|
| Candidate | 6 | 5 | 1.0 | 2 (`agent.py`, multimodal tests) | 0 | yes |
| Five-change alternative | 5 | 4 | 0.83 | 4+ by merging runtime/logs with ingress or targeting with execution | 0 | no: exceeds controllability and couples rollback/security ownership |

The candidate stays within the approved budget and each Change has a distinct
runtime owner, rollback surface, observable outcome, and test suite.

### 5.4 Locked decisions

#### C1 — Runtime layout and logs

- `RuntimePaths` is the single resolver.
- `logs/`, `attachments/`, and `cache/` are siblings.
- All application/service/audit/trace recorders consume `RuntimePaths.logs`.
- Socket and PID files retain OS runtime placement.

#### C2 — AttachmentStore and ingress

- IDs are non-guessable and scoped to session/owner.
- Writes are atomic; metadata records hash, MIME, dimensions, source, trust, and expiry.
- Cleanup uses startup sweep, periodic sweep with a controllable clock, and
  session-drop cleanup. Tests prove deletion without a later `get()`.
- New service wire sends attachment references only. Legacy `data_url` is a typed
  breaking-protocol error; no shim exists.
- Image preprocessing validates bytes/pixels before full decode, handles EXIF,
  transparency, metadata stripping, decompression bombs, and PNG/WebP/JPEG choice.

#### C3 — Reference-only messages

- History blocks are `image_ref`/`observation_ref`, never data URLs or bytes.
- RequestAssembler automatically hydrates only the current bounded selection.
- No model recall API exists. Expired references require new upload/capture.
- Hydration is request-only and never mutates Conversation.
- Truncation removes/summarizes complete dialogue and tool groups.

#### C4 — Provider vision

- Capabilities cover accepted canonical roles, transport, MIME, count, and
  byte/pixel limits; model selection remains part of provider profile resolution.
- Every provider/role branch is deterministic: serialize or return a typed error.
- No synthetic fallback observation is persisted to Conversation.
- Temporary base64 may exist only inside the provider request serializer.

#### C5 — Targeting and coordinates

- `ElementRef` binds backend/window/role/path/state/bbox to `snapshot_id`.
- Actions re-resolve and validate uniqueness, visibility, interactability, and freshness.
- `CoordinateTransform` includes virtual origin, monitor bounds, region origin,
  capture/encoded size, DPI, rotation, and scale; conversion is service-side.

#### C6 — Verified execution lifecycle

- Screen/web/image content is untrusted observation data, never authority.
- One executor owns verifier verdict plus exactly one registry commit.
- High-risk actions check target/window/user intent and confirmation before commit.
- Postcondition evidence is captured after commit with expected result, timeout,
  failure stop, and recovery/reobserve behavior.

### 5.5 End-to-end closure

#### `runtime-storage`

| Edge ID | Producer -> Consumer | Owner |
|---|---|---|
| `runtime-config-resolve` | AppConfig -> RuntimePaths | C1 |
| `runtime-log-resolve` | RuntimePaths -> all log/audit/trace sinks | C1 |
| `runtime-attachment-resolve` | RuntimePaths -> AttachmentStore | C2 |
| `attachment-ingress-store` | CLI/Feishu/screenshot/service upload -> AttachmentStore | C2 |
| `attachment-ref-wire` | AttachmentStore -> new service run request | C2 |
| `attachment-session-delete` | Session drop -> AttachmentStore | C2 |
| `attachment-periodic-expire` | periodic/startup sweeper -> AttachmentStore deletion | C2 |

#### `multimodal-context`

| Edge ID | Producer -> Consumer | Owner |
|---|---|---|
| `reference-history-store` | AttachmentStore metadata -> Conversation refs | C3 |
| `reference-request-select` | Conversation -> RequestAssembler | C3 |
| `reference-request-hydrate` | RequestAssembler -> AttachmentStore -> request bytes | C3 |
| `provider-role-serialize` | hydrated request -> ProviderSerializer | C4 |
| `provider-request-release` | provider request completion -> temporary data release | C4 |

#### `gui-automation-safety`

| Edge ID | Producer -> Consumer | Owner |
|---|---|---|
| `observation-target-bind` | ObservationRef -> ElementRef | C5 |
| `target-coordinate-resolve` | ElementRef/visual target -> CoordinateTransform | C5 |
| `observation-authority-limit` | untrusted observation -> ActionPolicy | C6 |
| `gui-action-authorize` | ActionPolicy -> VerifiedToolExecutor | C6 |
| `gui-action-commit` | executor -> internal ToolRegistry commit | C6 |
| `gui-action-postcondition` | commit result -> PostconditionVerifier | C6 |
| `gui-action-fail-closed` | stale/denied/failed state -> stop/reobserve | C6 |

No closure edge is ownerless or duplicated. Grounding and recall have no edge
because both are explicit non-goals.

## 9. Risks

| Risk | Closure |
|---|---|
| Cross-session attachment access | owner/session check before resolve |
| Expired files remain untouched | periodic + startup sweep and max sweep interval test |
| Binary content leaks through diagnostics | serialization-level negative tests for every durable/event sink |
| Provider tool-role incompatibility | explicit capability error; no persistent synthetic fallback |
| Registry bypass survives | C6 executor boundary and direct-bypass tests |

## 10. Campaign verification criteria

1. Legacy service `data_url` attachment fields are rejected; new image ingress
   produces attachment references before Agent history.
2. Conversation, compaction, events, outbound service messages, trace, audit,
   cache, exception, and idempotency representations contain no image bytes or
   data URLs.
3. Provider encoders may generate temporary base64 only inside request assembly;
   tests inspect persisted/emitted structures instead of a naive repository grep.
4. Logs resolve below one configured `logs/`; attachments only below sibling
   `attachments/`.
5. Attachments are atomically written, session-scoped, bounded, and deleted by
   session cleanup or periodic/startup expiry without requiring a later read.
6. Image preprocessing covers malformed/oversized images, decompression bombs,
   EXIF rotation, transparency, UI text, and MIME truthfulness.
7. Provider tests cover role, MIME, count, size, runtime rejection, and no-silent-drop.
8. GUI tests cover ambiguous/stale targets and negative-origin, DPI, rotation,
   and crop transforms.
9. Confirmation and preconditions occur before exactly one tool commit;
   postcondition evidence occurs after it.
10. Visual prompt injection cannot expand user-granted authority.
11. Missing Linux accessibility dependencies yield explicit visual fallback.
12. Full pytest coverage command passes at 80% or greater.

## 11. Critic resolution record

| Finding | Resolution |
|---|---|
| CBP-CRIT-001 | Greenfield wire approved; legacy data_url rejected; ingress edges assigned to C2 |
| CBP-CRIT-002 | Model recall and persistent provider fallback removed |
| CBP-CRIT-003 | Original C4 split into C5 targeting and C6 execution lifecycle |
| CBP-CRIT-004 | Periodic/startup/session cleanup locked in C2 |
| CBP-CRIT-005 | Coverage matrix now embeds source, summary, disposition, owner; owned paths listed |
| CBP-CRIT-006 | Exact pytest and coverage commands assigned |
| CBP-CRIT-007 | Revised Path Decision anchors `docs/architecture.md` |
