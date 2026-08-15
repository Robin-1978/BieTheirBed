import { describe, expect, it } from "vitest";

import type { Task, TaskState } from "@/api/models";
import { currentTaskSections } from "./taskListPresentation";

function task(id: string, state: TaskState | null, pending = 0): Task {
  return {
    task_id: id,
    session_handle: "session-a",
    agent_id: "knoa",
    title: id,
    goal: id,
    attachments: [],
    tools_enabled: true,
    priority: 0,
    launch_policy: { kind: "immediate", schedule_type: null, run_at: null, interval_seconds: null, cron: "", timezone: "Asia/Shanghai", event_source: "", source_config: {} },
    notification_policy: {},
    state: "active",
    revision: 1,
    latest_execution_id: state ? `execution-${id}` : "",
    execution_count: state ? 1 : 0,
    latest_execution_state: state,
    latest_execution_phase: "",
    latest_execution_summary: "",
    latest_execution_failure_code: "",
    latest_execution_updated_at: state ? 10 : null,
    pending_approval_count: pending,
    created_at: 1,
    updated_at: 1,
  };
}

describe("current task sections", () => {
  it("puts action-required executions before running and recent work", () => {
    const sections = currentTaskSections([
      task("done", "completed"),
      task("running", "running"),
      task("approval", "waiting_approval", 1),
      task("new", null),
    ]);
    expect(sections.map((section) => section.key)).toEqual([
      "needs_action",
      "in_progress",
      "recent",
      "not_started",
    ]);
    expect(sections[0]?.data[0]?.task_id).toBe("approval");
  });
});
