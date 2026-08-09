# Knoa Capability Extension Design

> Status: Phase A implementation contract
>
> Date: 2026-08-09
>
> Scope: Skill, MCP, Connector, Secret and dynamic tool lifecycle

## 1. Decision

Knoa will use one capability extension boundary for built-in tools, Skills, MCP
servers and business Connectors. Dynamic discovery is not authority. Every
callable operation must become a validated `ToolBase` and pass the existing
`ToolStep` authorization, confirmation, cancellation and commit boundary.

The first implementation slice is MCP Streamable HTTP because it provides a
real end-to-end extension without launching an unconstrained local child
process. Stdio support will enter through the same provider contract only after
its process isolation and environment/Secret boundary are specified.

### 1.1 Implementation status

Started on 2026-08-09:

- A1 lifecycle foundation completed with tool origin ownership,
  transactional per-provider registration, failure isolation and daemon-owned
  startup/shutdown;
- A2 first vertical slice completed with strict configuration, the official
  SDK Streamable HTTP client, bounded discovery, explicit local tool policy,
  MCP/NETWORK capability isolation, ToolStep execution and bounded results;
- fake-session tests cover discovery allowlisting, capability denial,
  confirmation, error conversion, result bounds and cleanup;
- an official FastMCP live local server test covers real Streamable HTTP
  initialization, discovery, invocation and shutdown;
- typed extension status is exposed through `RuntimeStatus`; richer tool-level
  descriptors remain for the later management API;
- A3 Skill Package foundation completed with strict manifests, bounded and
  path-confined text resources, deterministic dependency-aware activation,
  prompt-size limits and a built-in research-report Skill proving the runtime
  path;
- Skill packages are data-only extensions: they register lifecycle/status with
  `ExtensionManager`, inject instructions only for matching authorized runs and
  cannot directly execute code or grant capabilities;
- A4 Connector/Secret foundation implemented with stable Secret references,
  owner-only local files or environment resolution, a real Yuque read/write
  Connector, startup authorization health, reauthorization-safe failures and
  metadata-only audit records;
- live validation against a user-authorized Yuque account remains a deployment
  step because repository tests intentionally contain no credential;
- the additive `/tools` management response now includes typed descriptors for
  origin, extension ownership, effect, risk, capabilities and confirmation
  behavior while retaining the simple name list for existing clients.

## 2. Domain model

```text
ExtensionManager
  └── ExtensionProvider[]
        ├── MCPServerProvider
        ├── SkillProvider
        └── ConnectorProvider

ExtensionProvider.start()
  → discover definitions
  → apply local policy configuration
  → create ToolBase instances with ToolOrigin
  → register atomically before Core accepts traffic

ExtensionProvider.stop()
  → reject new calls by stopping Core traffic first
  → close sessions/transports
  → unregister provider-owned tools
```

An extension has:

- stable `extension_id`;
- kind: `mcp`, `skill`, or `connector`;
- lifecycle state and bounded diagnostic text;
- zero or more namespaced tools;
- explicit local configuration owned by Knoa;
- no direct access to sessions, memory, Channel instances or Core internals.

Each registered tool has a `ToolOrigin` containing kind and extension ID.
Built-in tools use the `builtin` origin.

## 3. Authority model

MCP annotations and server metadata are untrusted hints. They never determine
Knoa capabilities, effect or risk.

Every enabled MCP tool requires local policy configuration:

```yaml
mcp_servers:
  knowledge:
    enabled: true
    transport: streamable_http
    url: "https://127.0.0.1:9000/mcp"
    timeout_seconds: 30
    tools:
      search_documents:
        effect: read_only
        capabilities: [network]
        risk: low
      publish_document:
        effect: external_side_effect
        capabilities: [network]
        risk: high
```

Rules:

1. A discovered tool without local policy is ignored.
2. A configured tool not advertised by the server is reported unavailable.
3. Unknown effect, capability or risk fails configuration validation.
4. External side effects and high-risk calls use existing confirmation rules.
5. Tool names are namespaced as `mcp__<server>__<tool>`.
6. Duplicate public names fail that extension without replacing another tool.
7. Remote principal profiles see only tools whose required capabilities they
   already possess.
8. MCP output is bounded before it enters model context or durable history.

## 4. Lifecycle and failure isolation

Core startup order:

```text
build stores/runtime → register built-ins → start extensions → start Core host
```

Core shutdown order:

```text
stop Core host → stop extensions → close extension transports
```

An extension connection, initialization, discovery or schema failure is stored
as a failed extension status and logged. It does not prevent built-in Core from
starting. A partially discovered extension registers no tools.

Registration is transactional per provider: validate and construct the complete
tool set first, then register it. If any registration fails, previously added
tools from that provider are removed.

## 5. MCP transport contract

The first slice supports official MCP Streamable HTTP through the official
Python SDK.

Requirements:

- `http://` and `https://` URLs only;
- explicit connect/call timeout;
- bounded number of discovered tools and pagination pages;
- validated Draft 2020-12 input schemas;
- no sampling, elicitation or server-requested roots in the first slice;
- cancellation propagates from the ToolStep task to `call_tool`;
- structured content and typed text/resource metadata are preserved as bounded
  JSON-compatible results;
- MCP `isError` becomes a normal tool failure result;
- credentials are not accepted as literal headers in repository configuration.

Header Secret references and OAuth belong to the Connector/Secret slice rather
than being improvised inside the MCP adapter.

## 6. Skill contract direction

A Skill is a versioned package of domain instructions, resources, triggers and
capability dependencies. It is not an unchecked Python plugin.

The Skill manifest declares:

- identity, version and description;
- instruction and resource files contained below the package root;
- activation triggers;
- required built-in/MCP/Connector tools;
- required capabilities.

Skill resources are bounded, path-confined and loaded as data. Executable work
still occurs only through registered tools. Prompt activation must be selective;
all Skill instructions must not be injected into every model call.

The A3 foundation intentionally excludes installation, signing, Secret
references and executable health checks. Those require the later management and
Secret trust boundaries; adding speculative manifest fields now would create an
API without an implementation.

## 7. Connector and Secret direction

Connectors own authenticated APIs such as email, calendar, Jira, Yuque and
GitHub. They expose semantic tools through the same ToolBase contract.

Secrets are referenced by stable IDs and resolved only at the Connector or
transport boundary. They must never appear in:

- tool schemas;
- model messages;
- RunEvents;
- logs or traces;
- confirmation arguments;
- Skill manifests committed to the repository.

The first local implementation resolves `PC_SECRET_<ID>` or an owner-owned,
non-symlink file at `~/.pc-assistant/secrets/<id>.secret` with exact mode
`0600`. Connector configuration contains only the stable ID. Secret values are
revealed explicitly only while constructing the authenticated transport.

The first business Connector is Yuque. It exposes one read-only document tool
and one confirmation-gated document update tool. Both require `NETWORK` and
`CONNECTOR` capabilities, use bounded JSON responses, reject unsafe repository
or document paths and report expired authorization without transport details.
Audit records contain connector ID, operation, outcome, HTTP status and latency;
they never contain arguments, document bodies or credentials.

## 8. Public status

The `/tools` response keeps its list of available names and also exposes typed,
principal-filtered descriptors containing:

- origin and extension ID;
- effect, risk and required capabilities;
- whether the standard confirmation boundary applies.

Extension health and bounded diagnostic text remain in `RuntimeStatus.extensions`.
Transport-specific version data can be added later without making clients
inspect registry internals.

## 9. Phase A implementation slices

### A1 — Extension lifecycle foundation

- `ExtensionProvider` protocol;
- `ExtensionManager` with failure isolation and status;
- `ToolOrigin` stored by ToolRegistry;
- daemon startup/shutdown ownership;
- transactional registration tests.

### A2 — MCP Streamable HTTP vertical slice

- strict MCP configuration models;
- official SDK session lifecycle;
- paginated discovery bounds;
- explicit per-tool local policy;
- namespaced MCPTool adapter;
- bounded result conversion;
- fake-session and local test-server coverage.

### A3 — Skill package foundation

- [x] strict manifest model;
- [x] safe, bounded package/resource loader with symlink escape protection;
- [x] deterministic trigger and tool/capability dependency index;
- [x] selective, bounded instruction activation contract;
- [x] one real repository Skill proving the path.

### A4 — Connector and Secret foundation

- [x] Secret reference port and private local implementation;
- [x] Connector lifecycle through ExtensionManager;
- [x] one read/write business integration (Yuque documents);
- [x] startup health, reauthorization and metadata-only audit behavior.

### A5 — Management descriptors

- [x] additive typed tool descriptors on the existing Core API;
- [x] principal-filtered origin, policy and confirmation metadata;
- [x] existing name-only Channel/TUI consumers remain unchanged.

## 10. Acceptance criteria

Phase A is complete when:

1. A new extension never changes ReActLoop, CoreApplication or any Channel.
2. A failed extension does not prevent built-in tools from serving requests.
3. An unconfigured discovered MCP tool is invisible and cannot be called.
4. A configured MCP tool follows the same capability and confirmation behavior
   as a built-in tool.
5. Extension stop closes transports and leaves no callable registered tools.
6. Skill content cannot escape its package root or execute directly.
7. Secrets cannot enter schemas, events, traces or confirmation payloads.
8. Fresh tests cover lifecycle, schema rejection, authority denial, confirmation,
   cancellation, output bounds and failure isolation.

## 11. Non-goals

- preserving the deleted legacy MCP adapter;
- trusting MCP tool annotations as policy;
- arbitrary Python plugin imports;
- stdio child processes without an isolation design;
- a marketplace or generic workflow editor;
- putting Skill/MCP management logic into Feishu or the future mobile App.
