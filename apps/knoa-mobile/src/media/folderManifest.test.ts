import { describe, expect, it, vi } from "vitest";

vi.mock("expo-file-system", () => ({ Directory: class {}, File: class {} }));

import { buildFolderManifest, validateFolderBounds, type FolderSelection } from "./folderManifest";

const selection: FolderSelection = {
  rootName: "project",
  totalBytes: 7,
  files: [
    { relativePath: "a.txt", uri: "content://a", name: "a.txt", mediaType: "text/plain", size: 3 },
    { relativePath: "src/b.txt", uri: "content://b", name: "b.txt", mediaType: "text/plain", size: 4 },
  ],
};

describe("folder manifest", () => {
  it("keeps relative paths and contains no provider content URI", () => {
    const manifest = buildFolderManifest(selection, [
      { artifact_id: "artifact-a" }, { artifact_id: "artifact-b" },
    ]);
    expect(manifest.files.map((item) => item.relative_path)).toEqual(["a.txt", "src/b.txt"]);
    expect(JSON.stringify(manifest)).not.toContain("content://");
  });

  it("rejects traversal and incomplete uploads", () => {
    expect(() => validateFolderBounds([{ ...selection.files[0]!, relativePath: "../secret" }], 3)).toThrow("folder_path_invalid");
    expect(() => buildFolderManifest(selection, [{ artifact_id: "artifact-a" }])).toThrow("folder_upload_incomplete");
  });
});
