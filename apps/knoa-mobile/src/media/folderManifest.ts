import { Directory, File } from "expo-file-system";

import type { GatewayClient } from "@/api/gatewayClient";
import type { ArtifactInput } from "@/api/models";

export const MAX_FOLDER_FILES = 200;
export const MAX_FOLDER_BYTES = 512 * 1024 * 1024;
export const MAX_FOLDER_FILE_BYTES = 64 * 1024 * 1024;

export type FolderSelectionFile = {
  relativePath: string;
  uri: string;
  name: string;
  mediaType: string;
  size: number;
};

export type FolderSelection = {
  rootName: string;
  files: FolderSelectionFile[];
  totalBytes: number;
};

export type FolderManifest = {
  schema: "knoa-folder-manifest-v1";
  root_name: string;
  file_count: number;
  total_bytes: number;
  files: Array<{
    relative_path: string;
    size: number;
    media_type: string;
    artifact_id: string;
  }>;
};

export async function pickFolderSnapshot(): Promise<FolderSelection> {
  const root = await Directory.pickDirectoryAsync();
  const files: FolderSelectionFile[] = [];
  enumerate(root, "", files);
  files.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  validateFolderBounds(files, totalBytes);
  return { rootName: root.name || "folder", files, totalBytes };
}

function enumerate(directory: Directory, prefix: string, files: FolderSelectionFile[]): void {
  for (const entry of directory.list()) {
    const relativePath = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry instanceof Directory) {
      enumerate(entry, relativePath, files);
      continue;
    }
    if (!(entry instanceof File)) continue;
    files.push({
      relativePath,
      uri: entry.uri,
      name: entry.name,
      mediaType: entry.type || "application/octet-stream",
      size: entry.size,
    });
    if (files.length > MAX_FOLDER_FILES) throw new RangeError("folder_file_count_exceeded");
  }
}

export function validateFolderBounds(files: ReadonlyArray<FolderSelectionFile>, totalBytes: number): void {
  if (!files.length) throw new RangeError("folder_empty");
  if (files.length > MAX_FOLDER_FILES) throw new RangeError("folder_file_count_exceeded");
  if (totalBytes > MAX_FOLDER_BYTES) throw new RangeError("folder_total_size_exceeded");
  if (files.some((file) => file.size > MAX_FOLDER_FILE_BYTES)) throw new RangeError("folder_file_size_exceeded");
  if (files.some((file) => file.relativePath.startsWith("/") || file.relativePath.split("/").includes(".."))) {
    throw new RangeError("folder_path_invalid");
  }
}

export function buildFolderManifest(
  selection: FolderSelection,
  artifacts: ReadonlyArray<ArtifactInput>,
): FolderManifest {
  if (artifacts.length !== selection.files.length) throw new Error("folder_upload_incomplete");
  return {
    schema: "knoa-folder-manifest-v1",
    root_name: selection.rootName,
    file_count: selection.files.length,
    total_bytes: selection.totalBytes,
    files: selection.files.map((file, index) => ({
      relative_path: file.relativePath,
      size: file.size,
      media_type: file.mediaType,
      artifact_id: artifacts[index]!.artifact_id,
    })),
  };
}

export async function uploadFolderSnapshot(
  client: GatewayClient,
  sessionHandle: string,
  selection: FolderSelection,
  onProgress: (completed: number, total: number) => void = () => undefined,
): Promise<ArtifactInput> {
  const artifacts: ArtifactInput[] = [];
  for (let index = 0; index < selection.files.length; index += 1) {
    const file = selection.files[index]!;
    const bytes = await new File(file.uri).arrayBuffer();
    artifacts.push(await client.uploadArtifact({
      sessionHandle,
      bytes,
      mediaType: file.mediaType,
      name: file.name,
      caption: file.relativePath,
    }));
    onProgress(index + 1, selection.files.length);
  }
  const manifest = buildFolderManifest(selection, artifacts);
  const bytes = new TextEncoder().encode(JSON.stringify(manifest)).buffer;
  return client.uploadArtifact({
    sessionHandle,
    bytes,
    mediaType: "application/vnd.knoa.folder-manifest+json",
    name: `${selection.rootName}.knoa-folder.json`,
    caption: `${selection.rootName} (${selection.files.length} files)`,
  });
}
