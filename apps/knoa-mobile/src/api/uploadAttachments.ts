import type { GatewayClient } from "./gatewayClient";
import type { ArtifactInput } from "./models";

export type AttachmentUploadResult = {
  uploaded: ArtifactInput[];
  failed: number;
};

/**
 * Upload picked files into a conversation session. Per-item failures are
 * counted instead of thrown so callers can decide whether a task without
 * the missing files is still meaningful.
 */
export async function uploadSessionAttachments(
  client: GatewayClient,
  sessionHandle: string,
  items: ReadonlyArray<{ uri: string; name: string; mediaType: string }>,
): Promise<AttachmentUploadResult> {
  const uploaded: ArtifactInput[] = [];
  let failed = 0;
  for (const item of items) {
    try {
      const response = await fetch(item.uri);
      const bytes = await response.arrayBuffer();
      uploaded.push(await client.uploadArtifact({
        sessionHandle,
        bytes,
        mediaType: item.mediaType,
        name: item.name,
        caption: item.name,
      }));
    } catch {
      failed += 1;
    }
  }
  return { uploaded, failed };
}
