# Knoa Secure Gateway Forward Design

> Status: approved implementation direction
>
> Date: 2026-08-09
>
> Scope: mobile device identity, pairing, revocation, TLS and Core protocol adaptation

## 1. Decision

The mobile App never connects to the loopback Core WebSocket directly. A
separate Secure Gateway authenticates devices, applies transport limits and
adapts external requests to principal-scoped Core API calls.

```text
Knoa App
  -> TLS / device authentication
  -> Secure Gateway
  -> short-lived signed local principal credential
  -> loopback Core API
```

The Gateway is not part of AgentRuntime and does not execute Tools. Core does
not know about TLS, QR codes, mobile devices, Push providers or App deep links.

## 2. Security invariants

1. The public listener never starts without TLS, except an explicit loopback
   development mode.
2. Pairing uses a 256-bit single-use secret generated locally, expires within
   minutes and is consumed atomically.
3. A device generates its own Ed25519 key pair. The private key remains in the
   mobile secure keystore; pairing proves possession of that private key.
4. A device record maps to one stable Core principal but remains a distinct
   audit and revocation identity.
5. Revocation is checked from Gateway-owned state for every new connection and
   token renewal. A long-lived self-validating token cannot outlive revocation.
6. Gateway credentials and device public keys never enter prompts, Task goals,
   Task events or Core memory.
7. External requests use the existing strict Core schema or a smaller generated
   subset. The Gateway never accepts free-form Core method names or parameters.
8. Upload, response, connection, subscription and request rates are bounded
   before data reaches Core.
9. Device disconnect never cancels a durable Task. Reconnection resumes from a
   persisted Task event cursor.
10. App deep links contain only opaque resource IDs. Authentication and
    authorization occur after the App opens; links never contain credentials.

## 3. Ownership and process boundary

```text
ApplicationDaemon
  ├── CoreDaemon             # tasks, tools, memory, artifacts
  ├── ChannelRuntime         # Feishu and future channels
  ├── WebhookAdapter         # authenticated trigger ingress
  └── SecureGateway          # mobile identity and protocol adaptation
```

The Gateway is failure-isolated from Core. It opens `CoreClient` connections
using the existing short-lived signed principal credential issued from the
private local service token. It cannot import Core repositories or receive an
in-process Agent object.

Gateway identity state lives in a separate owner-only SQLite database below
`~/.pc-assistant/data/`. Core's task and memory database remains authoritative
for Agent state; the Gateway database is authoritative only for devices,
pairing grants, transport sessions and Push routing.

## 4. Identity model

### 4.1 Pairing grant

A local CLI command creates:

- opaque `grant_id`;
- random 256-bit secret, shown once as QR/manual text;
- target principal;
- issue and expiry timestamps;
- optional Gateway origin and certificate fingerprint.

Only a hash of the secret is stored. Wrong, expired or consumed grants return
the same rejection. Consumption uses one SQLite `BEGIN IMMEDIATE` transaction.

### 4.2 Device

A paired device stores:

- opaque `device_id`;
- bound principal ID;
- user-visible device name;
- Ed25519 public key;
- active/revoked state;
- created, last-seen and revoked timestamps.

The pairing request signs a Gateway nonce and includes the grant secret. The
Gateway verifies proof of private-key possession before the repository consumes
the grant and creates the device. Re-pairing creates a new device identity; it
does not silently revive a revoked record.

### 4.3 Connection authentication

For each connection the Gateway sends a fresh nonce. The device signs a bounded
challenge containing protocol version, device ID, nonce, audience and current
time window. After signature verification and an active-device lookup, the
Gateway creates a short-lived external session and a separate local
principal credential for Core.

Opaque session credentials are preferred for the first implementation because
their server-side lookup gives immediate revocation. Stateless access tokens may
be added only with a bounded lifetime and a mandatory device-state check.

## 5. External protocol

The first Gateway protocol exposes only the mobile workbench requirements:

- pair and authenticate device;
- create/list/detail/cancel/pause/resume Task;
- resolve approval;
- replay/tail principal Task events;
- upload/download owned Artifacts;
- list Tool/Skill/MCP status;
- health and connection status.

Schedule, Trigger and capability installation remain out of the first public
surface. They can be added from the same generated Core contracts after the App
has a real product flow and stronger confirmation UI.

HTTP is used for bounded commands and Artifact transfer; WebSocket is used for
resumable event delivery. Event payloads retain Core sequence IDs so reconnect
never depends on Gateway memory.

## 6. TLS and deployment

Production accepts either:

- configured certificate/key files owned by the service user; or
- TLS termination by an explicitly configured local reverse proxy where the
  Gateway binds only to loopback and validates forwarded-origin policy.

The first implementation does not automate public DNS, ACME, router port
forwarding or cloud relay. Tailscale/WireGuard may be used as a private product
validation transport, but does not replace device authentication or revocation.

## 7. Deep links and Push

The canonical resource shape is conceptually `task/<opaque-task-id>`, but no
concrete URI scheme or HTTPS domain is emitted until the App bundle identifier
and verified link domain exist. Feishu may then render a configured link from
Channel-owned presentation policy. Core events remain link-free.

Push payloads contain only a notification category and opaque Task/approval ID.
The App fetches authorized detail after opening. Provider tokens belong to the
Gateway device record and are never sent to Core.

## 8. Implementation phases

### D1 — identity persistence

- owner-only Gateway database;
- atomic single-use pairing grants;
- device records, listing and revocation;
- no network listener and no cryptographic claims beyond stored public-key
  validation.

### D2 — authenticated loopback Gateway

- Ed25519 proof verification;
- pairing/authentication endpoints;
- opaque short-lived Gateway sessions;
- strict request/rate/body limits;
- selected CoreClient command adaptation.

> Progress: identity, cryptographic proof and the authenticated loopback HTTP
> surface are implemented. Pairing and authentication use single-use persisted
> challenges, canonical signed payloads and Ed25519 proof; opaque session secrets
> are stored only as hashes, expire within a configured short window and are
> rejected immediately when the bound device is revoked. The listener is disabled
> by default, accepts strict bounded JSON, applies per-route limits and refuses
> non-loopback binding. An explicit principal-scoped Core bridge now exposes only
> session creation, Task create/list/detail/cancel and approval resolution; it
> does not accept arbitrary Core method names. Resumable event delivery remains.

### D3 — secure remote transport

- TLS configuration and fail-closed public binding;
- resumable principal Task event stream;
- bounded Artifact upload/download;
- device audit and immediate revocation checks.

> Progress: the principal Task feed is exposed as bounded standard SSE with
> `Last-Event-ID` resumption, stable Core event names, per-device connection
> limits and session revalidation before delivery and on heartbeats. Binary
> Artifact upload/download is principal/session scoped, bounded before Core calls,
> delivered with no-store headers and never encoded inside Gateway JSON. TLS and
> public binding remain disabled.

### D4 — mobile delivery

- Push routing;
- configured App/Universal Links;
- Flutter client generated from the Gateway/Core schemas;
- task list/detail, approval and Artifact workflows.

## 9. Non-goals

- exposing the current loopback Core port to the internet;
- putting device tables or Push tokens in Core;
- trusting a six-digit pairing code as the sole secret;
- bearer credentials without expiration and revocation checks;
- storing mobile private keys on the service;
- copying Agent decisions or Tool execution into the Gateway;
- emitting placeholder deep links before a real App target exists;
- building a generic multi-tenant identity platform for a personal Agent.
