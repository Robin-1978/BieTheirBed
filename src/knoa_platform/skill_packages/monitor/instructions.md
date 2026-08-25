When the user asks to monitor directories, services, disk usage, websites, or recurring health signals:

1. Clarify what to watch, acceptable check interval or cron schedule, thresholds, notification urgency, and how long monitoring should run.
2. Design one or more independent monitoring tasks. Use `create_task` with an explicit launch policy:
   - **Directory changes**: interval or cron task whose goal instructs a worker to detect new/changed/removed files and abnormal size growth under the target path
   - **Service health**: HTTP endpoint checks, process presence, or port listening status via `run_command` inside the task goal
   - **Disk space**: periodic usage checks against user-defined thresholds
   - **Website availability/content**: `web_fetch` for status, latency, and content fingerprints; optional visual checks with `screenshot` when layout/regression matters
3. Make each task goal self-contained: targets, thresholds, comparison rules, evidence to collect, and the exact notification text on failure/recovery.
4. When an anomaly is detected during a run or live check, collect evidence before alerting:
   - Relevant log excerpts via `run_command`
   - `web_fetch` response metadata or body diff summary
   - `screenshot` for UI/website regressions when applicable
5. Send user-visible alerts with `notify`, including target name, severity, observed value vs threshold, timestamp, and pointers to collected evidence.
6. Use `task` to list, inspect, pause, resume, or cancel monitoring tasks when the user changes requirements or asks for status.
7. On demand, provide a **monitoring dashboard summary**: active tasks, schedule, last run outcome, open incidents, and recent notifications grouped by target.
8. Confirm before creating high-frequency tasks or checks that may burden the host/network. Prefer conservative intervals unless the user requests aggressive polling.

Do not store secrets in task goals. When baseline behavior is unknown, establish a baseline snapshot first, then monitor against it.
