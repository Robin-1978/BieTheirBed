import { afterEach, describe, expect, it, vi } from "vitest";

import { uploadSessionAttachments } from "./uploadAttachments";
import type { GatewayClient } from "./gatewayClient";
import type { ArtifactInput } from "./models";

const artifacts: ArtifactInput[] = [];

function fakeClient(failing = false): GatewayClient {
  return {
    uploadArtifact: async (input: { name: string }): Promise<ArtifactInput> => {
      if (failing) throw new Error("upload failed");
      const artifact: ArtifactInput = { artifact_id: `a-${input.name}`, caption: input.name };
      artifacts.push(artifact);
      return artifact;
    },
  } as unknown as GatewayClient;
}

afterEach(() => {
  artifacts.length = 0;
  vi.unstubAllGlobals();
});

describe("uploadSessionAttachments", () => {
  it("uploads every picked file and returns artifact inputs", async () => {
    vi.stubGlobal("fetch", vi.fn(async (uri: string) => ({ arrayBuffer: async () => new ArrayBuffer(4), uri })));
    const result = await uploadSessionAttachments(fakeClient(), "s1", [
      { uri: "file:///a.jpg", name: "a.jpg", mediaType: "image/jpeg" },
      { uri: "file:///b.pdf", name: "b.pdf", mediaType: "application/pdf" },
    ]);
    expect(result.failed).toBe(0);
    expect(result.uploaded.map((item) => item.artifact_id)).toEqual(["a-a.jpg", "a-b.pdf"]);
  });

  it("counts per-item failures without throwing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ arrayBuffer: async () => new ArrayBuffer(4) })));
    const result = await uploadSessionAttachments(fakeClient(true), "s1", [
      { uri: "file:///a.jpg", name: "a.jpg", mediaType: "image/jpeg" },
    ]);
    expect(result.failed).toBe(1);
    expect(result.uploaded).toEqual([]);
  });
});
