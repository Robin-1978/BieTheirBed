import {
  EncodingType,
  readAsStringAsync,
  StorageAccessFramework,
  writeAsStringAsync,
} from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { Platform } from "react-native";

import type { ResolvedArtifactFile } from "@/api/chatArtifacts";

export async function saveArtifactFile(file: ResolvedArtifactFile): Promise<string> {
  if (Platform.OS !== "android") {
    await Sharing.shareAsync(file.uri, {
      dialogTitle: `保存 ${file.name}`,
      mimeType: file.mediaType,
    });
    return "请在系统菜单中选择“存储到文件”";
  }

  const permission = await StorageAccessFramework.requestDirectoryPermissionsAsync(
    StorageAccessFramework.getUriForDirectoryInRoot("Download"),
  );
  if (!permission.granted) return "已取消保存";

  const targetUri = await StorageAccessFramework.createFileAsync(
    permission.directoryUri,
    fileNameWithoutExtension(file.name),
    file.mediaType || "application/octet-stream",
  );
  const base64 = await readAsStringAsync(file.uri, { encoding: EncodingType.Base64 });
  await writeAsStringAsync(targetUri, base64, { encoding: EncodingType.Base64 });
  return `已保存 ${file.name}`;
}

function fileNameWithoutExtension(name: string): string {
  const safe = name.trim().replace(/[\\/:*?"<>|]/g, "_") || "小诺文件";
  const extensionIndex = safe.lastIndexOf(".");
  return extensionIndex > 0 ? safe.slice(0, extensionIndex) : safe;
}
