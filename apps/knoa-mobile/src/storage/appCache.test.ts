import { beforeEach, describe, expect, it, vi } from "vitest";

type FakeFile = { name: string; bytes: number; mtime: number | null; deleted: boolean };

const state = vi.hoisted(() => ({ files: [] as FakeFile[] }));

const MockFile = vi.hoisted(() => class MockFile {
  constructor(public readonly fake: { name: string; bytes: number }) {}
  get name(): string { return this.fake.name; }
  get size(): number { return this.fake.bytes; }
  get uri(): string { return `file:///docs/${this.fake.name}`; }
  delete(): void { (this.fake as { deleted?: boolean }).deleted = true; }
});

vi.mock("expo-file-system", () => {
  const Paths = { document: "file:///docs" };
  const Directory = class {
    list() { return state.files.map((fake) => new MockFile(fake)); }
  };
  const File = MockFile;
  return { Directory, File, Paths };
});

vi.mock("expo-file-system/legacy", () => ({
  // The legacy API reports modification time in seconds since epoch.
  getInfoAsync: async (uri: string) => {
    const fake = state.files.find((item) => `file:///docs/${item.name}` === uri);
    return fake && fake.mtime !== null
      ? { exists: true, isDirectory: false, modificationTime: fake.mtime / 1000 }
      : { exists: false, isDirectory: false };
  },
}));

import { appCacheSummary, clearAppCache, formatCacheBytes } from "./appCache";

const push = (name: string, bytes: number, mtime: number | null) => state.files.push({ name, bytes, mtime, deleted: false });

beforeEach(() => {
  state.files = [];
});

describe("app cache diagnostics", () => {
  it("aggregates usage per cache kind", async () => {
    push("conversation-v:a", 100, 1_000_000);
    push("conversation-list-v:b", 50, 2_000_000);
    push("tasks-v:c", 30, null);
    push("received-d", 10, 4_000_000);
    push("unrelated", 999, 5_000_000);
    const summary = await appCacheSummary();
    expect(summary.files).toBe(4);
    expect(summary.bytes).toBe(190);
    expect(summary.byKind.conversation).toEqual({ files: 2, bytes: 150 });
    expect(summary.byKind.task).toEqual({ files: 1, bytes: 30 });
    expect(summary.byKind.artifact).toEqual({ files: 1, bytes: 10 });
    expect(summary.updatedAt).toBe(4_000_000);
  });

  it("reports no update time when no file exposes one", async () => {
    push("tasks-v:x", 10, null);
    expect((await appCacheSummary()).updatedAt).toBeNull();
  });

  it("clears only the requested kind", () => {
    push("conversation-v:a", 100, 1);
    push("workspace-v:b", 100, 2);
    const result = clearAppCache("conversation");
    expect(result.removed).toBe(1);
    expect(result.failed).toBe(0);
    expect(state.files.find((item) => item.name === "conversation-v:a")?.deleted).toBe(true);
    expect(state.files.find((item) => item.name === "workspace-v:b")?.deleted).toBe(false);
  });

  it("formats cache sizes for humans", () => {
    expect(formatCacheBytes(512)).toBe("512 B");
    expect(formatCacheBytes(2048)).toBe("2.0 KB");
    expect(formatCacheBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
