import { describe, expect, it } from "vitest";

import type { Task } from "@/api/models";
import {
  calculateTotalSavedHours,
  classifyArtifactType,
  hostRelativePath,
} from "./trophyPresentation";

describe("trophyPresentation", () => {
  it("classifies images and diagrams correctly", () => {
    const png = classifyArtifactType("architecture.png", "image/png");
    expect(png.isVisual).toBe(true);
    expect(png.icon).toBe("image");
    expect(png.label).toBe("视觉图表");

    const svg = classifyArtifactType("diagram.svg", "image/svg+xml");
    expect(svg.isVisual).toBe(true);
  });

  it("classifies code files and patches correctly", () => {
    const code = classifyArtifactType("fix_gateway.py", "text/x-python");
    expect(code.isCode).toBe(true);
    expect(code.icon).toBe("code");
    expect(code.label).toBe("代码补丁");

    const patch = classifyArtifactType("migration.patch", "text/plain");
    expect(patch.isCode).toBe(true);
  });

  it("classifies markdown and documents correctly", () => {
    const report = classifyArtifactType("morning_briefing.md", "text/markdown");
    expect(report.isVisual).toBe(false);
    expect(report.isCode).toBe(false);
    expect(report.icon).toBe("file");
    expect(report.label).toBe("调研简报");
  });

  it("generates host relative path with standard structure", () => {
    const path = hostRelativePath("result.pdf", "art-123");
    expect(path).toBe("~/Downloads/knoa-artifacts/result.pdf");
  });

  it("calculates total saved hours from tasks accurately", () => {
    const mockTasks: Task[] = [
      {
        task_id: "t1",
        session_handle: "s1",
        agent_id: "a1",
        title: "Task 1",
        goal: "Goal 1",
        attachments: [],
        tools_enabled: true,
        priority: 1,
        launch_policy: { kind: "immediate", schedule_type: null, run_at: null, interval_seconds: null, cron: "", timezone: "Asia/Shanghai", event_source: "", source_config: {} },
        notification_policy: {},
        state: "active",
        revision: 1,
        latest_execution_id: "e1",
        execution_count: 4,
        latest_execution_state: "completed",
        latest_execution_phase: "",
        latest_execution_summary: "Done",
        latest_execution_failure_code: "",
        latest_execution_updated_at: Date.now(),
        pending_approval_count: 0,
        created_at: Date.now(),
        updated_at: Date.now(),
      },
    ];
    const hours = calculateTotalSavedHours(mockTasks);
    expect(hours).toBeGreaterThan(0.5);
  });
});
