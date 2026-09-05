import { describe, expect, it } from "vitest";

import type { Task, TaskState } from "@/api/models";
import {
  bentoProgressStep,
  estimateSavedMinutes,
  taskBentoCategory,
} from "./taskBentoPresentation";

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    task_id: "task-1",
    session_handle: "session-1",
    agent_id: "agent-knoa",
    title: "测试任务",
    goal: "完成自动化运维与报告输出",
    attachments: [],
    tools_enabled: true,
    priority: 1,
    launch_policy: {
      kind: "immediate",
      schedule_type: null,
      run_at: null,
      interval_seconds: null,
      cron: "",
      timezone: "Asia/Shanghai",
      event_source: "",
      source_config: {},
    },
    notification_policy: {},
    state: "active",
    revision: 1,
    latest_execution_id: "exec-1",
    execution_count: 1,
    latest_execution_state: "running",
    latest_execution_phase: "正在扫描代码目录",
    latest_execution_summary: "已完成 3 个关键步骤",
    latest_execution_failure_code: "",
    latest_execution_updated_at: Date.now(),
    pending_approval_count: 0,
    created_at: Date.now() - 60000,
    updated_at: Date.now(),
    ...overrides,
  };
}

describe("taskBentoPresentation", () => {
  it("classifies task as needs_action when pending_approval_count > 0 or state is waiting_approval", () => {
    const taskApproval = makeTask({ pending_approval_count: 2, latest_execution_state: "running" });
    expect(taskBentoCategory(taskApproval)).toBe("needs_action");

    const taskStateWaiting = makeTask({ pending_approval_count: 0, latest_execution_state: "waiting_approval" });
    expect(taskBentoCategory(taskStateWaiting)).toBe("needs_action");
  });

  it("classifies task as running when in execution or queued", () => {
    const taskRunning = makeTask({ pending_approval_count: 0, latest_execution_state: "running" });
    expect(taskBentoCategory(taskRunning)).toBe("running");

    const taskQueued = makeTask({ pending_approval_count: 0, latest_execution_state: "queued" });
    expect(taskBentoCategory(taskQueued)).toBe("running");
  });

  it("classifies task as completed when latest execution state is completed", () => {
    const taskCompleted = makeTask({ pending_approval_count: 0, latest_execution_state: "completed" });
    expect(taskBentoCategory(taskCompleted)).toBe("completed");
  });

  it("classifies idle task without execution or in paused state without running", () => {
    const taskIdle = makeTask({ pending_approval_count: 0, latest_execution_state: null });
    expect(taskBentoCategory(taskIdle)).toBe("idle");
  });

  it("estimates saved minutes accurately for completed and multi-run tasks", () => {
    const taskCompleted = makeTask({
      latest_execution_state: "completed",
      execution_count: 2,
      tools_enabled: true,
    });
    const minutes = estimateSavedMinutes(taskCompleted);
    expect(minutes).toBeGreaterThan(20);

    const taskUnrun = makeTask({
      latest_execution_state: null,
      execution_count: 0,
    });
    expect(estimateSavedMinutes(taskUnrun)).toBe(0);
  });

  it("formats bentoProgressStep properly", () => {
    const taskWithPhase = makeTask({ latest_execution_phase: "生成分析图表中 2/5" });
    expect(bentoProgressStep(taskWithPhase, "运行中")).toBe("生成分析图表中 2/5");

    const taskDefault = makeTask({ latest_execution_phase: "", latest_execution_state: "running" });
    expect(bentoProgressStep(taskDefault, "运行中")).toBe("运行中");
  });
});
