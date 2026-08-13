# GitLab MCP reference server

This is an independent, deployable standard MCP package for GitLab CI. Knoa
Core has no GitLab types or branches. App, CLI and Textual TUI consume the same
generic Task, Execution, Tool and Approval APIs.

It exposes four bounded read-only diagnostic Tools:

- `gitlab.get_pipeline`
- `gitlab.list_pipeline_jobs`
- `gitlab.get_job`
- `gitlab.get_job_trace`

and two high-risk side-effect Tools:

- `gitlab.retry_job`
- `gitlab.retry_pipeline`

Retry requires both `GITLAB_ACTIONS_ENABLED=true` and Knoa host approval. Every
call requires a stable idempotency key; an ambiguous network outcome is stored
as `outcome_unknown` and is never blindly repeated.

The package polls configured projects for failed pipelines. Its first successful
poll records a baseline without creating historical Tasks. Later failed states
are published as immutable Resources below:

```text
gitlab://failed-pipelines/events/{event_id}
```

Bind the collection Resource to a principal-owned Session through `mcp_deploy`
or `mcp_configure_resource_task` to create one durable diagnostic Task per
event. The Task tells the Agent to inspect bounded CI evidence, inspect a bound
workspace when available, and return `retry`, `stop`, or `needs_human`. Retry
remains an ordinary approval-gated MCP Tool call.

Store private configuration in `~/.knoa/secrets/mcp/gitlab.env` with mode 0600:

```dotenv
GITLAB_URL=https://gitlab.example.com
GITLAB_TOKEN=replace-with-token
GITLAB_PROJECTS=group/project,group/another-project
GITLAB_POLL_INTERVAL_SECONDS=60
GITLAB_MAX_PIPELINES=50
GITLAB_EVENT_RETENTION_DAYS=7
GITLAB_MCP_STATE_PATH=/home/user/.knoa/data/gitlab-mcp.db
GITLAB_ACTIONS_ENABLED=false
```

Deploy from an owned Knoa Session:

```text
mcp_deploy(
  path=/absolute/path/to/examples/gitlab_mcp_server,
  server_id=gitlab,
  resource_uri=gitlab://failed-pipelines,
  route_id=failed,
  priority=4
)
```
