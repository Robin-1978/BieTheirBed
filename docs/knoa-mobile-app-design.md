# Knoa Mobile App Forward Design

## 1. Product boundary

Knoa Mobile is the personal workbench for long-running Agent work. It is not a
copy of Feishu chat and it does not own Agent decisions, Tool execution,
approval policy or task persistence. The App consumes only the versioned Secure
Gateway protocol; Core has no dependency on Expo, mobile navigation, Push or
deep links.

```text
Knoa Mobile
  -> Secure Gateway HTTP/SSE
    -> authenticated principal-scoped CoreClient
      -> durable Task / Approval / Artifact / Extension services
```

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

## 4. Implemented first slice

- QR/manual pairing and secure device/session storage;
- session bootstrap and expired-session re-authentication;
- Task create, list, filter, detail, pause, resume, cancel and retry;
- per-Task durable timeline replay plus resumable principal SSE updates;
- approval confirm/deny actions from replayed standard events;
- document upload and Artifact download/share;
- Markdown, code and table capable final-result rendering;
- Runtime token/tool counts, Skill/MCP state, Tool inventory and current-device
  audit view;
- `knoa://tasks/<task-id>` route shape through file-based mobile navigation;
- generated OpenAPI TypeScript contract, strict typecheck and unit tests.

## 5. Next slices

1. Push registration and Gateway-owned provider routing with opaque notification
   payloads.
2. Notification deep-link handling for Task and approval resources.
3. Native audio recording and direct transcription/task creation.
4. OS share-sheet ingress, camera capture and richer Artifact previews.
5. Offline Task snapshot cache and explicit reconnect diagnostics.

Push tokens, provider credentials and delivery audit remain Gateway-owned. Core
events remain provider-neutral and contain no mobile links.
