import { describe, expect, it } from "vitest";

import { projectionWorkStatus } from "./workProjectionPresentation";

const base = {
  workspace_id: "ws",
  entity_kind: "task" as const,
  entity_id: "task",
  node_id: "node",
  principal_id: "principal",
  title: "Task",
  state: "running",
  progress: null,
  summary: "",
  approval_summary: "",
  artifact_refs: [],
  source_generation: 1,
  source_digest: "d".repeat(64),
  projection_seq: 1,
  source_created_at: 1,
  source_updated_at: 1,
  projected_at: 1,
  payload: {},
};

describe("work projection presentation", () => {
  it("uses the durable user status when present", () => {
    expect(projectionWorkStatus({ ...base, payload: { work_status: { status: "waiting_for_you" } } })).toBe("waiting_for_you");
  });

  it("maps domain states without leaking internal wording", () => {
    expect(projectionWorkStatus({ ...base, state: "waiting_approval" })).toBe("waiting_for_you");
    expect(projectionWorkStatus({ ...base, state: "running" })).toBe("working");
  });
});
