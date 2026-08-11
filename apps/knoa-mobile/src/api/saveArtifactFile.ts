import {
  EncodingType,
  readAsStringAsync,
  StorageAccessFramework,
  writeAsStringAsync,
} from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { Platform } from "react-native";

import type { ResolvedArtifactFile } from "@/api/chatArtifacts";

export type SaveArtifactMessages = {
  saveDialog: string;
  saveToFile: string;
  cancelled: string;
  saved: string;
};

const DEFAULT_MESSAGES: SaveArtifactMessages = {
  saveDialog: "保存",
  saveToFile: "请在系统菜单中选择“存储到文件”",
  cancelled: "已取消保存",
  saved: "已保存 {name}",
};

export async function saveArtifactFile(file: ResolvedArtifactFile, messages: Partial<SaveArtifactMessages> = {}): Promise<string> {
  const text = { ...DEFAULT_MESSAGES, ...messages };
  if (Platform.OS !== "android") {
    await Sharing.shareAsync(file.uri, {
      dialogTitle: `${text.saveDialog} ${file.name}`,
      mimeType: file.mediaType,
    });
    return text.saveToFile;
  }

  const permission = await StorageAccessFramework.requestDirectoryPermissionsAsync(
    StorageAccessFramework.getUriForDirectoryInRoot("Download"),
  );
  if (!permission.granted) return text.cancelled;

  const targetUri = await StorageAccessFramework.createFileAsync(
    permission.directoryUri,
    fileNameWithoutExtension(file.name),
    file.mediaType || "application/octet-stream",
  );
  const base64 = await readAsStringAsync(file.uri, { encoding: EncodingType.Base64 });
  await writeAsStringAsync(targetUri, base64, { encoding: EncodingType.Base64 });
  return text.saved.replace("{name}", file.name);
}

function fileNameWithoutExtension(name: string): string {
  const safe = name.trim().replace(/[\\/:*?"<>|]/g, "_") || "小诺文件";
  const extensionIndex = safe.lastIndexOf(".");
  return extensionIndex > 0 ? safe.slice(0, extensionIndex) : safe;
}
