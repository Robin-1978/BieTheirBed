import { describe, expect, it, vi } from "vitest";

import type { ChatArtifact } from "./models";
import {
  assistantArtifactItems,
  resolveAssistantArtifactFile,
} from "./chatArtifacts";

function artifact(overrides: Partial<ChatArtifact> = {}): ChatArtifact {
  return {
    artifact_id: "artifact-1",
    name: "desktop.png",
    media_type: "image/png",
    ...overrides,
  };
}

describe("assistantArtifactItems", () => {
  it("preserves every server artifact in order and classifies images", () => {
    const items = assistantArtifactItems([
      artifact(),
      artifact({ artifact_id: "artifact-2", name: "report.pdf", media_type: "application/pdf" }),
    ]);

    expect(items.map((item) => item.key)).toEqual(["artifact-1:0", "artifact-2:1"]);
    expect(items.map((item) => item.isImage)).toEqual([true, false]);
    expect(items.map((item) => item.displayName)).toEqual(["desktop.png", "report.pdf"]);
  });

  it("provides visible labels and path-safe deterministic cache filenames", () => {
    const [image, file] = assistantArtifactItems([
      artifact({ artifact_id: "../shot/1", name: "   " }),
      artifact({ artifact_id: "../file/2", name: "../notes?.txt", media_type: "text/plain" }),
    ]);

    expect(image?.displayName).toBe("图片");
    expect(file?.displayName).toBe("../notes?.txt");
    expect(image?.cacheFileName).toBe("_shot_1-图片");
    expect(file?.cacheFileName).toBe("_file_2-_notes_.txt");
    expect(image?.cacheFileName).not.toContain("/");
    expect(file?.cacheFileName).not.toContain("/");
  });
});

describe("resolveAssistantArtifactFile", () => {
  it("reuses an existing cache file without downloading", async () => {
    const [item] = assistantArtifactItems([artifact()]);
    const download = vi.fn();
    const write = vi.fn();

    const resolved = await resolveAssistantArtifactFile(item!, {
      cachedUri: () => "file:///cache/desktop.png",
      download,
      write,
    });

    expect(resolved).toEqual({
      uri: "file:///cache/desktop.png",
      name: "desktop.png",
      mediaType: "image/png",
    });
    expect(download).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("downloads and writes a cache miss before publishing its URI", async () => {
    const [item] = assistantArtifactItems([artifact()]);
    const bytes = new Uint8Array([1, 2, 3]);
    const download = vi.fn(async () => ({ bytes, name: "server.png", mediaType: "image/png" }));
    const write = vi.fn(async () => "file:///cache/artifact-1-desktop.png");

    const resolved = await resolveAssistantArtifactFile(item!, {
      cachedUri: () => null,
      download,
      write,
    });

    expect(download).toHaveBeenCalledWith("artifact-1");
    expect(write).toHaveBeenCalledWith("artifact-1-desktop.png", bytes);
    expect(resolved).toEqual({
      uri: "file:///cache/artifact-1-desktop.png",
      name: "server.png",
      mediaType: "image/png",
    });
  });

  it("propagates a download failure so one preview can enter retry state", async () => {
    const [item] = assistantArtifactItems([artifact()]);
    const failure = new Error("network unavailable");

    await expect(resolveAssistantArtifactFile(item!, {
      cachedUri: () => null,
      download: async () => { throw failure; },
      write: vi.fn(),
    })).rejects.toBe(failure);
  });
});
