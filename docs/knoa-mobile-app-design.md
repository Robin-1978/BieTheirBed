# Knoa Mobile App Forward Design

配套 UI 线框与交互说明见
[knoa-mobile-ui-design.md](./knoa-mobile-ui-design.md)。
Task 的创建、启动方式、执行记录、控制和交付闭环见
[knoa-task-product-design.md](./knoa-task-product-design.md)。

## 1. Product boundary

Knoa Mobile is the owner's primary remote conversation with the personal Agent.
Text, voice, photos and files are conversation messages first. The App also
provides a Task center for durable independent work with immediate, scheduled
or event-based launch policies. It does not own Agent decisions, Tool execution,
approval policy or execution persistence. The App consumes only the versioned
Secure Gateway protocol; Core has no dependency on Expo, mobile navigation,
Push or deep links.

```text
Knoa Mobile
  -> Secure Gateway HTTP/SSE
    -> authenticated principal-scoped CoreClient
      -> conversation Run / durable Task / Approval / Artifact / Automation services
```

The product vocabulary is deliberately smaller than Core's execution model:

- a **conversation turn** is an owner's message and the Agent's streamed reply;
- a conversation turn uses a durable execution envelope but is never listed as
  a user-facing Task;
- a **Task** is an independently delegated goal with an immediate, scheduled or
  event launch policy;
- every launch produces a durable **TaskExecution** in an isolated Core Session;
- Schedule, Trigger, occurrence and retry-attempt records stay internal to Core.

## 2. Technology decision

The first verifiable client uses Expo + React Native + strict TypeScript:

- one codebase for Android and iOS;
- the current repository already has a working Node toolchain, while Flutter
  and Dart are not installed;
- Expo provides SecureStore, camera QR scanning, document ingress, file sharing,
  notifications and deep-link routing without introducing native business
  logic;
- `openapi-typescript` generates the protocol model snapshot from the running
  Gateway schema;
- Vitest, TypeScript and Expo Doctor provide executable local evidence now.

This is a product implementation choice, not a new Core dependency. A future
native or Flutter client can consume the same Gateway contract.

The current Expo 57 dependency graph reports no critical npm advisories. CI
fails on any future critical advisory; existing upstream moderate/high
transitive advisories remain tracked until the Expo/Metro ecosystem publishes
compatible fixes rather than forcing an unverified major downgrade or override.

## 3. Security model

1. The App generates an Ed25519 private key locally and stores it only in the
   platform secure store.
2. Pairing scans the versioned, short-lived local `pairing_json` QR payload.
3. Pairing and authentication sign the exact `KNOA-GATEWAY-PROOF-V1` canonical
   payload used by the server.
4. Opaque Gateway sessions expire and are re-authenticated with device proof.
5. The App never receives the Core local service token or a free-form Core
   method surface.
6. SSE cursors are persisted and only strictly newer events advance the cursor.
7. Artifact bytes use the bounded binary Gateway endpoints and temporary local
   files are created only for explicit user preview/share.
8. Private Android releases are owner-published locally, authenticated by the
   Gateway and immutable by monotonically increasing version code. The APK
   remains outside Core and is served with byte-range support.
9. The App persists only native download resume data in SecureStore, verifies
   the final size and SHA-256 digest, and delegates package-signature validation
   and installation confirmation to Android.

## 4. Implemented first slice

- QR/manual pairing and secure device/session storage;
- session bootstrap and expired-session re-authentication;
- conversation-first text, photo and file input with streamed replies;
- unified Task list plus execution detail, pause, resume, cancel and retry;
- per-Task durable timeline replay plus resumable principal SSE updates;
- approval confirm/deny actions from replayed standard events;
- document upload and Artifact download/share;
- Markdown, code and table capable final-result rendering;
- Runtime token/tool counts, Skill/MCP state, Tool inventory and current-device
  audit view;
- `knoa://tasks/<task-id>` route shape through file-based mobile navigation;
- Expo Push registration, standard approval/terminal notifications and
  notification-to-Task navigation using opaque IDs only;
- native audio recording routed through Artifact upload and the configured Core
  transcription capability; camera capture returns immediately to the chat
  composer as a pending attachment and uploads only when the message is sent;
- private Android update discovery, pause/resume across background transitions,
  authenticated Range download, SHA-256 verification and system-installer handoff;
- generated OpenAPI TypeScript contract, strict typecheck and unit tests.

## 5. Next slices

1. OS share-sheet ingress and richer Artifact previews.
2. Offline Task snapshot cache and explicit reconnect diagnostics.
3. Owner-signed Android APK build automation and real-device update validation.

Push tokens, provider credentials and delivery audit remain Gateway-owned. Core
events remain provider-neutral and contain no mobile links.
