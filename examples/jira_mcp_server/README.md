# Jira MCP reference server

This is a runnable standard MCP stdio server backed by a real Jira REST API. It demonstrates:

- standard Tools, Resources, Prompts and Resource Notifications;
- durable immutable Resources for “assigned to me” transitions;
- local materialization of issue, comments and all Jira attachments before an
  assignment Resource is published;
- a lightweight MCP Resource pointing the Agent to the materialized directory;
- guarded Jira comment writes with explicit `outcome_unknown` handling;
- automatic Knoa Durable Task creation through a generic Resource Task Source.

It is an example integration, not a dependency on Monitor or the local Jira skill.

The directory is also a deployable Knoa MCP Package because it contains
`mcp.yaml`. From a principal-owned Knoa Session, deploy or update the same
stable server ID with:

```text
mcp_deploy(
  path=/absolute/path/to/examples/jira_mcp_server,
  server_id=jira,
  resource_uri=jira://assigned-to-me,
  route_id=assigned,
  priority=4
)
```

The first call installs the package; later calls atomically update it and
restart only the Jira MCP Provider. A failed update restores the previous
package. The Resource Task binding belongs to the Knoa deployment and is
preserved across updates; it is not embedded in the distributable package.

Put private variables in `~/.knoa/config/service.env` with mode `0600`:

```dotenv
JIRA_BASE_URL=https://jira.example.com
JIRA_API_TOKEN=replace-with-token
JIRA_AUTH_MODE=bearer
PYTHONDONTWRITEBYTECODE=1
```

Knoa loads this private service environment on startup. The MCP package receives
only the names explicitly declared in `inherit_env`; secrets are never copied
into the package or `local.yaml`.

## 1. Configure Jira credentials

Keep credentials in the process environment, never in the YAML file:

```bash
export JIRA_BASE_URL="https://jira.example.com"
export JIRA_API_TOKEN="your-token"
export JIRA_AUTH_MODE="bearer"
export JIRA_API_VERSION="2"
```

`bearer` mode requires only `JIRA_API_TOKEN`. For Jira deployments using HTTP
Basic authentication, set `JIRA_AUTH_MODE=basic` and also provide
`JIRA_USERNAME`.

Optional settings:

```bash
export JIRA_JQL='assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC'
export JIRA_POLL_INTERVAL_SECONDS=60
export JIRA_EVENT_RETENTION_DAYS=7
export JIRA_MAX_ISSUES=100
export JIRA_MCP_STATE_PATH="$HOME/.knoa/data/jira-mcp-example.db"
export JIRA_ATTACHMENT_ROOT="/disk/knoa/mcp-data/jira/evidence"
export JIRA_CODE_ROOT="/home/robin/ws/aaa"
export JIRA_LOG_ROOT="/home/robin/ws/aaa"
export JIRA_ANALYSIS_PROMPT_PATH="$HOME/.knoa/config/mcp/jira/analyze.md"
export JIRA_MAX_ATTACHMENT_BYTES=104857600
export JIRA_WRITE_ENABLED=false
```

For a Knoa-managed deployment, do not keep these values in a shell profile or
in the MCP package. Store them in the server-scoped private environment:

```text
~/.knoa/secrets/mcp/jira.env
```

The file must be a regular file with mode `0600`. Knoa reads it only while
starting the `jira` stdio MCP child and only injects names explicitly listed by
`inherit_env` in `mcp.yaml`. Package install/update preserves this file because
credentials and package lifecycle are separate. Other MCP Servers do not
receive these values.

`JIRA_ATTACHMENT_ROOT` is selected only by the MCP Server operator. Jira issue
keys, attachment names, descriptions and comments cannot change it. For each
issue the server creates:

```text
/disk/knoa/mcp-data/jira/evidence/PROJECT-123/
├── issue.json
├── comments.json
├── manifest.json
└── attachments/
    ├── attachment-id-agent.log
    ├── attachment-id-error.png
    └── attachment-id-diagnostics.zip
```

The server does not extract archives. The selected Agent can use its authorized
filesystem, shell, archive, image and code-analysis capabilities directly on
this directory.

`JIRA_CODE_ROOT` is the root containing the product source repositories.
`JIRA_LOG_ROOT` is an optional additional local log search root; downloaded Jira
attachments always remain under the per-issue evidence directory. The analysis
Resource includes all three locations as absolute paths.

`JIRA_ANALYSIS_PROMPT_PATH` points to an operator-owned UTF-8 Markdown file
(maximum 64 KiB). It is read every time an analysis Resource or MCP Prompt is
requested, so editing it does not require reinstalling the MCP package or
restarting Knoa. Jira content cannot choose or modify this path.

Use `JIRA_API_VERSION=3` for Jira Cloud APIs that require Atlassian Document Format comments. Keep writes disabled until read-only behavior is verified.

## 2. Connect it from Knoa

The easiest path is to tell the Knoa Agent that a Jira MCP server is available. The Agent first calls read-only `mcp_inspect`, then proposes a confirmation-gated `mcp_connect` call whose payload lists the exact read-only Tools to enable. After confirmation Knoa persists and activates the connection. A Server annotation alone never grants authority.

Jira write Tools are declared as explicit external-side-effect/high-risk local
policies. They remain unavailable unless the operator enables
`JIRA_WRITE_ENABLED`, and every call still crosses Knoa's durable approval
boundary. The Jira MCP Server cannot grant itself write authority.

The complete operational set is:

- `jira.add_comment`: publish an approved analysis/comment draft.
- `jira.find_assignable_users` then `jira.assign_issue`: resolve a human name
  to an exact Jira identity, show that identity in the approval, then transfer
  the issue.
- `jira.list_transitions` then `jira.transition_issue`: discover the issue's
  current workflow transitions and required fields before proposing an exact
  transition.

These are ordinary MCP Tools. Knoa App renders the same generic approval card
for Jira, GitLab, mail, chat or any future MCP Server; neither the Platform nor
the App contains Jira workflow code.

Automatic issue analysis still needs one explicit Resource Task Source because standard MCP does not define which Resource should create an Agent Task. Tell the Agent to use `jira://assigned-to-me` for automatic analysis; it will call `mcp_configure_resource_task` against the current Knoa Session after confirmation, so YAML editing is not required.

## 3. Create or select a Knoa Session

Create a persistent Session through the Knoa App/CLI and copy its real `session_handle`. Resource Task Sources reject missing or foreign Sessions.

## 4. Add automatic analysis routing

After onboarding, add only the Resource Task routing (or let a future UI write the same local policy), replacing `REAL_SESSION_HANDLE`:

```yaml
mcp_servers:
  jira:
    enabled: true
    transport: stdio
    command: python
    args: ["-m", "examples.jira_mcp_server.server"]
    working_directory: "/absolute/path/to/BieTheirBed"
    inherit_env:
      - JIRA_BASE_URL
      - JIRA_USERNAME
      - JIRA_API_TOKEN
      - JIRA_AUTH_MODE
      - JIRA_API_VERSION
      - JIRA_JQL
      - JIRA_POLL_INTERVAL_SECONDS
      - JIRA_EVENT_RETENTION_DAYS
      - JIRA_MAX_ISSUES
      - JIRA_MCP_STATE_PATH
      - JIRA_ATTACHMENT_ROOT
      - JIRA_CODE_ROOT
      - JIRA_LOG_ROOT
      - JIRA_ANALYSIS_PROMPT_PATH
      - JIRA_MAX_ATTACHMENT_BYTES
      - JIRA_WRITE_ENABLED
    timeout_seconds: 30
    resource_tasks:
      assigned_issues:
        uri: "jira://assigned-to-me"
        principal_id: "personal:owner"
        session_handle: "REAL_SESSION_HANDLE"
        tools_enabled: true
        priority: 4
    # Read-only policies are generated by mcp_connect from readOnlyHint=true.
    # Every write Tool requires explicit high-risk policy and host approval:
    tools:
      jira.add_comment:
        effect: external_side_effect
        capabilities: [network]
        risk: high
      jira.assign_issue:
        effect: external_side_effect
        capabilities: [network]
        risk: high
      jira.transition_issue:
        effect: external_side_effect
        capabilities: [network]
        risk: high
```

The stdio process receives only the explicitly inherited environment names.

## 5. Runtime behavior

The server polls Jira, materializes the complete issue evidence directory, and
only then records the immutable assignment transition in its local SQLite
database. A failed download does not publish an incomplete event; the next poll
retries materialization. Successful events are exposed as:

```text
jira://assigned-to-me/events/{assignment_event_id}
```

Knoa discovers those Resources through standard `resources/list`, reads the
small fixed task instruction through `resources/read`, and creates one
idempotent Durable Task per Resource URI. The instruction contains the absolute
evidence directory and `manifest.json` path. Large logs, images and archives do
not pass through `resources/read`; Codex or another authorized Agent analyzes
the local files directly.

`jira.download_attachment` and `jira.materialize_issue` are available for
manual recovery or explicit refresh. Automatic assignment analysis uses the
same materialization implementation before publishing its Resource.

After analysis, a human can add instructions in the same Product Task, for
example "publish this conclusion to Jira", "transfer it to Zhang San", or
"move it to Submit Verification with these fields". The follow-up Execution
uses the Task's fixed Agent and context. If the Agent proposes a Jira write,
the Execution enters `waiting_approval`; the App shows the Tool name, exact
arguments, effect and risk. Approval commits the standard MCP `tools/call`,
denial records a rejected ToolStep, and the outcome stays in the same Task
timeline.

All Jira write Tools remain subject to Knoa's high-risk approval boundary and
the server-side `JIRA_WRITE_ENABLED` switch.
