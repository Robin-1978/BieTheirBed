import { describe, expect, it, vi } from "vitest";

vi.mock("expo-file-system", () => ({
  Directory: class {},
  File: class {},
  Paths: { document: "" },
}));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async () => null),
  setItemAsync: vi.fn(async () => undefined),
  deleteItemAsync: vi.fn(async () => undefined),
}));

vi.mock("@/security/deviceIdentity", () => ({
  loadOrCreateInstallationId: vi.fn(async () => "installation-1"),
  loadOrCreatePrivateKey: vi.fn(async () => "private-key"),
  publicKey: vi.fn(() => "public-key"),
}));

import { maxWorkUpdatedAt, mergeWorkItems } from "./workspaceCache";
import type { WorkspaceWorkProjection } from "@/hub/hubClient";

function item(entity_id: string, source_updated_at: number): WorkspaceWorkProjection {
  return {
    workspace_id: "w",
    entity_kind: "task",
    entity_id,
    node_id: "n",
    principal_id: "p",
    title: entity_id,
    state: "working",
    progress: null,
    summary: "",
    approval_summary: "",
    artifact_refs: [],
    source_generation: 1,
    source_digest: "d",
    projection_seq: 1,
    source_created_at: 1,
    source_updated_at,
    projected_at: 1,
    payload: {},
  };
}

describe("mergeWorkItems", () => {
  it("merges fresh over cached and sorts by update time", () => {
    const merged = mergeWorkItems(
      [item("a", 10), item("b", 20)],
      [item("b", 30), item("c", 5)],
    );
    expect(merged.map((entry) => entry.entity_id)).toEqual(["b", "a", "c"]);
    expect(merged[0]?.source_updated_at).toBe(30);
  });

  it("keeps cached rows absent from the incremental page", () => {
    const merged = mergeWorkItems([item("a", 10)], []);
    expect(merged.map((entry) => entry.entity_id)).toEqual(["a"]);
  });
});

describe("maxWorkUpdatedAt", () => {
  it("returns 0 for empty lists", () => {
    expect(maxWorkUpdatedAt([])).toBe(0);
  });

  it("returns the newest timestamp", () => {
    expect(maxWorkUpdatedAt([item("a", 3), item("b", 9)])).toBe(9);
  });
});
