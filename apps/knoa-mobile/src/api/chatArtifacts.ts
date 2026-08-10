import type { ChatArtifact } from "./models";

export type AssistantArtifactItem = {
  artifact: ChatArtifact;
  key: string;
  displayName: string;
  cacheFileName: string;
  isImage: boolean;
};

export type DownloadedArtifact = {
  bytes: Uint8Array;
  name: string;
  mediaType: string;
};

export type ResolvedArtifactFile = {
  uri: string;
  name: string;
  mediaType: string;
};

type ArtifactFileOperations = {
  cachedUri(cacheFileName: string): string | null;
  download(artifactId: string): Promise<DownloadedArtifact>;
  write(cacheFileName: string, bytes: Uint8Array): Promise<string> | string;
};

export function assistantArtifactItems(artifacts: ChatArtifact[]): AssistantArtifactItem[] {
  return artifacts.map((artifact, index) => {
    const isImage = artifact.media_type.trim().toLowerCase().startsWith("image/");
    const displayName = artifact.name.trim() || (isImage ? "图片" : "附件");
    const safeId = safeFileSegment(artifact.artifact_id, "artifact", 64);
    const safeName = safeFileSegment(displayName, isImage ? "image" : "file", 120);
    return {
      artifact,
      key: `${artifact.artifact_id}:${index}`,
      displayName,
      cacheFileName: `${safeId}-${safeName}`,
      isImage,
    };
  });
}

export async function resolveAssistantArtifactFile(
  item: AssistantArtifactItem,
  operations: ArtifactFileOperations,
): Promise<ResolvedArtifactFile> {
  const cachedUri = operations.cachedUri(item.cacheFileName);
  if (cachedUri) {
    return {
      uri: cachedUri,
      name: item.displayName,
      mediaType: item.artifact.media_type,
    };
  }

  const downloaded = await operations.download(item.artifact.artifact_id);
  const uri = await operations.write(item.cacheFileName, downloaded.bytes);
  return {
    uri,
    name: downloaded.name.trim() || item.displayName,
    mediaType: downloaded.mediaType.trim() || item.artifact.media_type,
  };
}

function safeFileSegment(value: string, fallback: string, maxLength: number): string {
  const sanitized = value
    .trim()
    .replace(/[^\p{L}\p{N}._ -]/gu, "_")
    .replace(/^[. ]+|[. ]+$/g, "")
    .slice(0, maxLength);
  return sanitized || fallback;
}
