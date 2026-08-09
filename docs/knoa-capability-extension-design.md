# Knoa Capability Extension Design

> Status: implemented forward-only architecture
>
> Date: 2026-08-09
>
> Scope: Built-in Tools, Skills, MCP Tools and their lifecycle/policy boundary

## 1. Decision

Knoa uses one small capability model:

```text
Capability
├── Skill                  # data and instructions only
└── Tool
   ├── Built-in Tool       # static Core primitives
   └── MCP Tool            # external capability through standard MCP
```

There is no arbitrary Python tool-plugin import and no separate business API
abstraction in Core. External reusable execution capability arrives through
MCP. An MCP Server may be remote over Streamable HTTP or local through a
supervised stdio child process; “local” does not mean “imported into Core”.

Skills contain bounded instructions and text resources. They may select and
compose available Built-in or MCP tools, but they cannot execute code, register
tools, grant capabilities or bypass the commit boundary.

## 2. Runtime ownership

```text
ExtensionManager
  ├── SkillPackageProvider[]
  │     └── SkillCatalog
  └── MCPServerProvider[]
        ├── StreamableHTTPMCPClient
        └── StdioMCPClient

MCP discovery
  → match explicit local policy
  → create MCPTool adapter
  → register with ToolOrigin(kind=mcp)
  → execute only through ToolStep
```

`ExtensionManager` owns failure-isolated startup, transactional registration,
status and reverse-order shutdown. A broken package or unavailable MCP Server
becomes a failed extension status and cannot prevent Core or other extensions
from starting.

Core owns only generic MCP lifecycle, transport, policy and result conversion.
The MCP Server owns its business API, authentication flow, protocol adapters,
domain validation and provider-specific audit. Channels never own extension
logic and Core never imports Channel code.

### 2.1 Process and trust boundary

Built-in Tools are trusted Core code and are registered directly by the Core
composition root. Imported executable capabilities are never loaded into the
Core interpreter:

- a local MCP Server runs as one independent stdio child process owned by its
  extension provider;
- a remote MCP Server runs outside Knoa and is reached through Streamable HTTP;
- launch details come from declarative configuration, never a server-specific
  branch in Core;
- startup, discovery and calls are bounded by timeouts, and returned data is
  bounded before it crosses the Tool boundary;
- startup failure or a broken process affects only calls to that extension; it
  must not terminate Core or prevent other extensions from running;
- Core passes a minimal environment plus explicitly allowed names and never
  imports package modules into the Core interpreter.

Process separation protects Core from third-party crashes and dependency
conflicts. It is not a complete security sandbox: CPU, memory, filesystem and
network containment require an OS-level launcher such as a service unit,
container or sandbox runtime. That containment belongs behind the generic MCP
process-launch boundary and remains policy/configuration, never package-specific
Core logic.

## 3. Authority model

MCP discovery is not authority. Server annotations, descriptions and schemas
are untrusted metadata. Every callable remote tool requires local policy:

```yaml
tools:
  monitor.list_observations:
    effect: read_only
    capabilities: []
    risk: low
  gitlab.retry_job:
    effect: external_side_effect
    capabilities: [network]
    risk: high
```

Rules:

1. Discovered tools without local policy are hidden.
2. Configured tools absent from discovery are not registered.
3. Local policy alone determines effect, capability and risk.
4. Every MCP tool additionally requires the `mcp` capability.
5. MCP does not imply `network`; a tool requests `network` only when its actual
   operation needs it.
6. Side effects use the same confirmation and idempotency boundaries as
   Built-in tools.
7. Input uses the discovered JSON Schema and fails closed before invocation.
8. Results are bounded and converted to the standard tool-result shape.
9. Cancellation propagates through the MCP client session.

Public names are deterministic and collision checked:

```text
monitor.list_observations
→ mcp__monitor__monitor_list_observations
```

## 4. Configured remote servers

Remote servers are configured in private Knoa configuration:

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
```

URLs cannot contain credentials. Credentials belong to the remote MCP
deployment or its own authentication mechanism, not model context or tool
metadata.

## 5. Manually imported local MCP packages

Local packages are manually copied/imported below the runtime root:

```text
~/.pc-assistant/mcp/monitor/
├── mcp.yaml
└── server package files
```

Example manifest:

```yaml
enabled: true
transport: stdio
command: python
args: ["-m", "monitor", "mcp"]
working_directory: "."
inherit_env:
  - MONITOR_DB_PATH
  - MONITOR_ACTIONS_ENABLED
  - GITLAB_URL
  - GITLAB_TOKEN
  - GITLAB_PROJECTS
  - JIRA_URL
  - JIRA_TOKEN
  - JIRA_PROJECTS
timeout_seconds: 30
tools:
  monitor.list_observations:
    effect: read_only
    capabilities: []
    risk: low
  gitlab.retry_job:
    effect: external_side_effect
    capabilities: [network]
    risk: high
```

`command`, `args`, `working_directory`, inherited environment names and timeout
are package configuration. Core contains no knowledge of the executable,
language, framework or business domain represented by those values.

Package constraints:

- fixed UTF-8 `mcp.yaml`, bounded to 64 KiB;
- safe directory/server ID;
- stdio transport only;
- package-confined existing working directory;
- no manifest or working-directory symlink/path escape;
- fixed command and argument list chosen at import time, never by the model;
- child environment contains only a minimal runtime allowlist plus explicitly
  named required variables;
- missing required environment values fail only that extension;
- a configured `mcp_servers` entry takes precedence over the same local ID.

The imported object is the MCP package/process contract, not a Python class.
Core never imports downloaded package modules into its own interpreter.

## 6. Skill packages

Skill packages remain data-only:

```text
~/.pc-assistant/skills/research/
├── skill.yaml
├── instructions.md
└── references/*.md
```

Their manifests define identity, version, description, triggers, bounded text
resources, required tools and required capabilities. Activation is
deterministic and request-specific. Only matching Skills whose dependencies
are already available are injected into the session context. A Skill cannot
make an unavailable MCP Server available.

## 7. Agent-assisted local import

`mcp_import` lets the Agent prepare an installation while preserving user
authority. It accepts only an existing local directory and a safe target server
ID. Because installing an MCP package introduces executable code, the tool is
`local_write/high` and always crosses the standard confirmation boundary.

After approval Knoa:

1. validates the source manifest without importing its Python modules;
2. bounds file count, individual file size and total package size;
3. omits symbolic links and rejects special files;
4. omits hidden metadata without recognizing any external tool or project layout;
5. copies into an isolated staging directory;
6. validates the staged package using its final server ID;
7. atomically moves it into `~/.pc-assistant/mcp/<server-id>`;
8. adds and starts one provider through `ExtensionManager`;
9. refreshes model-visible definitions before the next ReAct iteration.

The model may propose the source and server ID, but cannot bypass confirmation,
change granted capabilities or silently replace an installed package. Import
does not download URLs, resolve marketplaces, install dependencies, upgrade or
remove packages. Those operations require separate trust and lifecycle design.

## 8. Monitor reference integration

Monitor is the first real local stdio MCP package. It keeps ownership of:

- GitLab/Jira polling and normalization;
- SQLite observations;
- guarded GitLab retry operations;
- project allowlists, idempotency and action audit;
- provider plugin discovery internal to Monitor.

Monitor exposes those operations with the official `mcp.server.fastmcp.FastMCP`
SDK over stdio. Knoa sees only standard MCP discovery and calls. The earlier
handwritten line-oriented JSON-RPC implementation has been removed.

## 9. Standard audio transcription capability

Audio transcription is a Core command over an owned Artifact, not Feishu
business logic. Private configuration maps the command to one discovered MCP
Tool by its deterministic public name. Core accepts the mapping only when that
Tool is locally classified as read-only and non-high-risk.

The MCP input contract is intentionally small:

```json
{
  "audio_data_url": "data:audio/ogg;base64,...",
  "media_type": "audio/ogg",
  "file_name": "voice-message.ogg"
}
```

No backing path, principal ID, session capability or arbitrary environment is
passed to the MCP process. The preferred result is
`structuredContent.transcript`; text content blocks are accepted as a bounded
fallback. Missing configuration, an unavailable extension, invalid audio, a
high-risk policy, and malformed output all fail closed while the original
Artifact remains owned by Core.

## 10. Non-goals

- arbitrary imported `ToolBase` subclasses;
- direct execution of downloaded Python tool code inside Core;
- a second external-tool category beside MCP;
- automatic marketplace trust or unconfirmed network download;
- trusting MCP annotations as policy;
- exposing all process environment variables to local servers;
- putting extension selection, credentials or business logic into Feishu;
- compatibility branches for removed extension models.
