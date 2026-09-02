# GitLab MCP reference server

This is an independent, deployable standard MCP package for GitLab CI. Knoa
Core has no GitLab types or branches. App, CLI and Textual TUI consume the same
generic Task, Execution, Tool and Approval APIs.

It exposes four bounded read-only diagnostic Tools:

- `gitlab.get_pipeline`
- `gitlab.list_pipeline_jobs`
- `gitlab.get_job`
- `gitlab.get_job_trace`

and one high-risk side-effect Tool:

- `gitlab.retry_job`

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
OOM signals. The Resource contains domain facts, not a user workflow. Put the
trusted action policy in the user-owned Task Definition. A practical Task goal
is:

```text
Analyze the prepared failed-pipeline snapshot. Report attribution, compile/build
totals and each failed Job's fingerprint. For every failed Job that is confirmed
as OOM while peer compile Jobs succeeded, has `retry_attempts < retry_limit`,
and is still safely retryable, call the source GitLab MCP's precise Job retry
Tool. Do not suppress a retry merely because earlier pipelines also had OOM.
Otherwise return stop or needs_human with the reason. Only report a retry after
the Tool returns.
```

Immediately before each Job retry, the Provider re-reads the Job and its Pipeline
Job list. It permits only `failed` or `canceled` and rejects the retry when a
newer Job with the same name is already `created`, `pending`, `preparing`,
`running` or otherwise active. Each Job is checked independently, so one Task
may safely retry multiple eligible failed Jobs. `gitlab.retry_oom_jobs` wraps
that same check in one approval-gated call and performs at most three attempts
per logical Job, stopping as soon as that Job succeeds.

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
