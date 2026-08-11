# Knoa Mobile App Forward Design

> The canonical cross-feature product contract and implementation order are now
> [knoa-product-forward-blueprint.md](./knoa-product-forward-blueprint.md) and
> [knoa-product-forward-implementation-plan.md](./knoa-product-forward-implementation-plan.md).
> This document remains a detailed Mobile boundary reference where it does not
> conflict with those documents.

配套 UI 线框与交互说明见
[knoa-mobile-ui-design.md](./knoa-mobile-ui-design.md)。
Task 的创建、启动方式、执行记录、控制和交付闭环见
[knoa-task-product-design.md](./knoa-task-product-design.md)。
Conversation、ChatTurn 和持久化 Session 压缩见
[knoa-conversation-design.md](./knoa-conversation-design.md)。

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
- a conversation turn is owned by Conversation and invokes the shared
  AgentRuntime directly; it is never persisted or transported as a Task;
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
8. Android release metadata remains device-authenticated. APK bytes use a
   public, content-addressed URL containing version code and SHA-256, remain
   outside Core, and are served as immutable byte-range resources suitable for
   Cloudflare CDN caching. Published APKs must contain no credentials.
9. The App persists only native download resume data in SecureStore, verifies
   the final size and SHA-256 digest, and delegates package-signature validation
   and installation confirmation to Android.

## 4. Implemented first slice

- QR/manual pairing and secure device/session storage;
- session bootstrap and expired-session re-authentication;
- conversation-first text, photo and file input with streamed replies; the
  composer uses one clear add button for photo/file actions and one primary
  button whose action follows the explicitly selected text/voice input mode;
  voice mode remains active across multiple transcription segments until the
  owner switches back to text mode to edit or send;
- unified Task list plus execution detail, pause, resume, cancel and retry;
- per-Task coalesced ExecutionTrace snapshot plus resumable coarse lifecycle SSE;
- approval confirm/deny actions from replayed standard events;
- document upload and Artifact download/share;
- Markdown, code and table capable final-result rendering;
- Runtime token/tool counts, Skill/MCP state, Tool inventory and current-device
  audit view;
- `knoa://tasks/<task-id>` route shape through file-based mobile navigation;
- local in-App reminders for standard approval/terminal events, replay after
  reconnect, unread Task badge and reminder-to-execution navigation;
- native audio recording routed through Artifact upload and the configured Core
  transcription capability; camera capture returns immediately to the chat
  composer as a pending attachment and uploads only when the message is sent;
- version/update page, proactive update discovery on the chat home, pause/resume
  across background transitions, CDN-cacheable Range download, SHA-256
  verification and system-installer handoff;
- generated OpenAPI TypeScript contract, strict typecheck and unit tests.

## 5. Next slices

1. OS share-sheet ingress and richer Artifact previews.
2. Offline Task snapshot cache and explicit reconnect diagnostics.
3. Owner-signed Android APK build automation and real-device update validation.

Task reminder state remains device-local. The App consumes the existing durable
SSE feed and requires no Push token, provider credential, delivery worker or
Gateway notification table. Core events remain provider-neutral.
