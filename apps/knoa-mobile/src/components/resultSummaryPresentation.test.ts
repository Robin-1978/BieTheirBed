import { describe, expect, it } from "vitest";
import { resultOutcome } from "./resultSummaryPresentation";
import type { Task } from "@/api/models";

function task(overrides: Partial<Task>): Task {
  return {
    task_id: "t1",
    session_handle: "s1",
    agent_id: "knoa",
    title: "整理下载目录",
    goal: "整理下载目录",
    attachments: [],
    tools_enabled: true,
    priority: 1,
    launch_policy: {
      kind: "immediate",
      schedule_type: null,
      run_at: null,
      interval_seconds: null,
      cron: "",
      timezone: "",
      event_source: "",
      source_config: {},
    },
    notification_policy: {},
    state: "active",
    revision: 1,
    latest_execution_id: "e1",
    execution_count: 1,
    latest_execution_state: "completed",
    latest_execution_phase: "",
    latest_execution_summary: "已归档 12 个文件",
    latest_execution_failure_code: "",
    latest_execution_updated_at: 1000,
    pending_approval_count: 0,
    created_at: 900,
    updated_at: 1000,
    ...overrides,
  } as Task;
}

describe("result outcome presentation", () => {
  it("answers completed work with review as the next step", () => {
    const outcome = resultOutcome(task({}));
    expect(outcome.completion).toBe("completed");
    expect(outcome.incomplete).toBe(false);
    expect(outcome.evidenceExecutionId).toBe("e1");
    expect(outcome.nextStep).toBe("review");
  });

  it("answers failed work with the failure code and retry as the next step", () => {
    const outcome = resultOutcome(task({
      latest_execution_state: "failed",
      latest_execution_summary: "",
      latest_execution_failure_code: "vision_empty_observation",
    }));
    expect(outcome.completion).toBe("failed");
    expect(outcome.incomplete).toBe(true);
    expect(outcome.failureCode).toBe("vision_empty_observation");
    expect(outcome.nextStep).toBe("retry");
  });

  it("prefers user work status and surfaces pending approvals first", () => {
    const outcome = resultOutcome(task({
      work_status: { status: "waiting_for_you", terminal: false, requires_user: true, recoverable: true, recommended_action: "respond" },
      pending_approval_count: 1,
    }));
    expect(outcome.completion).toBe("waiting_for_you");
    expect(outcome.nextStep).toBe("approve");
  });

  it("keeps running work on the tracking step without failure facts", () => {
    const outcome = resultOutcome(task({
      latest_execution_state: "running",
      latest_execution_failure_code: "stale_code",
    }));
    expect(outcome.completion).toBe("working");
    expect(outcome.incomplete).toBe(false);
    expect(outcome.failureCode).toBe("");
    expect(outcome.nextStep).toBe("track");
  });

  it("treats cancelled work as incomplete but recoverable", () => {
    const outcome = resultOutcome(task({ latest_execution_state: "cancelled" }));
    expect(outcome.incomplete).toBe(true);
    expect(outcome.nextStep).toBe("retry");
  });
});
