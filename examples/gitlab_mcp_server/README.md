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

The package polls configured projects for failed pipelines. It filters events to
pipelines attributable to the authenticated GitLab user: a direct Pipeline user,
an associated Merge Request authored by that user, or a matching commit email.
This deliberately keeps a later `ci-robot` packaging Pipeline when its SHA still
belongs to that user's Merge Request, while ignoring unrelated developers'
failures. Its first successful
poll records a baseline without creating historical Tasks. Later failed states
are published as immutable Resources below:

```text
gitlab://failed-pipelines/events/{event_id}
```

Create a user-owned Task Definition matching descendants of
`gitlab://failed-pipelines/events`. Each immutable event creates one Execution
under that Task. Before publishing the Resource, the GitLab MCP Server prepares a bounded
immutable snapshot containing compact Pipeline and Job data, failed Job trace
tails, deterministic fingerprints, compile/build totals, ownership evidence and
OOM signals. The Agent analyzes that snapshot directly and returns `retry`,
`stop`, or `needs_human`; read Tools are only for missing evidence or later
inspection.
The diagnostic Task explicitly forbids local workspace, filesystem and shell
access. Immediately before a Job retry, both the Agent and Provider re-read the
Job and its Pipeline Job list. The Provider permits only `failed` or `canceled`
and rejects the retry when a newer Job with the same name is already `created`,
`pending`, `preparing`, `running` or otherwise active. Retry remains an
ordinary approval-gated MCP Tool call.

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

Deploy the package:

```bash
scripts/knoa mcp-package-deploy /absolute/path/to/examples/gitlab_mcp_server gitlab
```
