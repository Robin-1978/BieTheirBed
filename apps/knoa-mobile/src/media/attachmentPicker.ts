import * as DocumentPicker from "expo-document-picker";

import { prepareImageAttachment } from "./prepareImageAttachment";

export type PickedAttachment = {
  uri: string;
  name: string;
  mediaType: string;
};

export const MAX_ATTACHMENTS = 8;

/** Pick files for task or chat input; images are downscaled before upload. */
export async function pickAttachments(currentCount: number): Promise<PickedAttachment[]> {
  const picked = await DocumentPicker.getDocumentAsync({
    multiple: true,
    copyToCacheDirectory: true,
  });
  if (picked.canceled) return [];
  const available = Math.max(0, MAX_ATTACHMENTS - currentCount);
  return Promise.all(picked.assets.slice(0, available).map(async (asset) => {
    const mediaType = asset.mimeType ?? "application/octet-stream";
    if (!mediaType.startsWith("image/")) {
      return { uri: asset.uri, name: asset.name, mediaType };
    }
    return prepareImageAttachment(asset.uri, asset.name);
  }));
}
