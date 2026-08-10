import * as Application from "expo-application";
import * as Crypto from "expo-crypto";
import { File } from "expo-file-system";
import * as FileSystem from "expo-file-system/legacy";
import * as IntentLauncher from "expo-intent-launcher";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

import type { AndroidRelease } from "@/api/models";
import { isNewerAndroidRelease } from "./releasePolicy";

const RESUME_KEY = "knoa.android-update.resume.v1";
const APK_MIME = "application/vnd.android.package-archive";
const FLAG_GRANT_READ_URI_PERMISSION = 1;

type StoredResume = {
  versionCode: number;
  fileUri: string;
  resumeData: string;
};

export type AndroidUpdateProgress = {
  downloaded: number;
  total: number;
};

export function installedAndroidVersionCode(): number {
  const value = Number.parseInt(Application.nativeBuildVersion ?? "0", 10);
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

export function isAndroidUpdateAvailable(
  release: AndroidRelease,
  currentVersionCode = installedAndroidVersionCode(),
): boolean {
  return Platform.OS === "android" && isNewerAndroidRelease(release, currentVersionCode);
}

export class AndroidUpdateDownload {
  private constructor(
    private readonly release: AndroidRelease,
    private readonly task: FileSystem.DownloadResumable,
  ) {}

  static async create(input: {
    gatewayUrl: string;
    release: AndroidRelease;
    onProgress(progress: AndroidUpdateProgress): void;
  }): Promise<AndroidUpdateDownload> {
    requireAndroid();
    if (!FileSystem.cacheDirectory) throw new Error("系统没有可用的更新缓存目录");
    const fileUri = `${FileSystem.cacheDirectory}knoa-update-${input.release.version_code}.apk`;
    const stored = await loadResume();
    let resumeData: string | undefined;
    if (stored?.versionCode === input.release.version_code && stored.fileUri === fileUri) {
      const partial = await FileSystem.getInfoAsync(fileUri);
      if (partial.exists) {
        resumeData = stored.resumeData;
      } else {
        await clearStoredResume(stored);
      }
    } else {
      await clearStoredResume(stored);
      await FileSystem.deleteAsync(fileUri, { idempotent: true });
    }
    const downloadUrl = new URL(
      input.release.download_path,
      `${input.gatewayUrl.replace(/\/$/, "")}/`,
    ).toString();
    const task = FileSystem.createDownloadResumable(
      downloadUrl,
      fileUri,
      {
        headers: {
          "If-Range": `"${input.release.sha256}"`,
        },
      },
      ({ totalBytesWritten, totalBytesExpectedToWrite }) => {
        input.onProgress({
          downloaded: totalBytesWritten,
          total: totalBytesExpectedToWrite || input.release.size_bytes,
        });
      },
      resumeData,
    );
    return new AndroidUpdateDownload(input.release, task);
  }

  async start(): Promise<string> {
    const result = this.task.savable().resumeData
      ? await this.task.resumeAsync()
      : await this.task.downloadAsync();
    if (!result || (result.status !== 200 && result.status !== 206)) {
      throw new Error("更新包下载失败");
    }
    try {
      await verifyPackage(result.uri, this.release);
      await SecureStore.deleteItemAsync(RESUME_KEY);
      return result.uri;
    } catch (error) {
      await SecureStore.deleteItemAsync(RESUME_KEY);
      await FileSystem.deleteAsync(result.uri, { idempotent: true });
      throw error;
    }
  }

  async pause(): Promise<void> {
    const paused = await this.task.pauseAsync();
    if (!paused.resumeData) throw new Error("当前下载无法保存断点");
    const stored: StoredResume = {
      versionCode: this.release.version_code,
      fileUri: paused.fileUri,
      resumeData: paused.resumeData,
    };
    await SecureStore.setItemAsync(RESUME_KEY, JSON.stringify(stored));
  }
}

export async function installAndroidPackage(fileUri: string): Promise<void> {
  requireAndroid();
  const contentUri = await FileSystem.getContentUriAsync(fileUri);
  await IntentLauncher.startActivityAsync("android.intent.action.VIEW", {
    data: contentUri,
    type: APK_MIME,
    flags: FLAG_GRANT_READ_URI_PERMISSION,
  });
}

export async function openUnknownSourcesSettings(): Promise<void> {
  requireAndroid();
  await IntentLauncher.startActivityAsync(
    IntentLauncher.ActivityAction.MANAGE_UNKNOWN_APP_SOURCES,
    { data: `package:${Application.applicationId ?? "dev.knoa.mobile"}` },
  );
}

async function verifyPackage(fileUri: string, release: AndroidRelease): Promise<void> {
  const info = await FileSystem.getInfoAsync(fileUri);
  if (!info.exists || info.size !== release.size_bytes) {
    throw new Error("更新包大小校验失败，请重新下载");
  }
  const bytes = await new File(fileUri).bytes();
  const digest = await Crypto.digest(Crypto.CryptoDigestAlgorithm.SHA256, bytes);
  const actual = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  if (actual !== release.sha256) {
    throw new Error("更新包完整性校验失败，请重新下载");
  }
}

async function loadResume(): Promise<StoredResume | null> {
  const raw = await SecureStore.getItemAsync(RESUME_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<StoredResume>;
    if (
      Number.isSafeInteger(value.versionCode) &&
      typeof value.fileUri === "string" &&
      value.fileUri.startsWith("file://") &&
      typeof value.resumeData === "string" &&
      value.resumeData.length > 0
    ) {
      return value as StoredResume;
    }
  } catch {
    // Invalid private state is discarded below.
  }
  await SecureStore.deleteItemAsync(RESUME_KEY);
  return null;
}

async function clearStoredResume(stored: StoredResume | null): Promise<void> {
  await SecureStore.deleteItemAsync(RESUME_KEY);
  if (stored?.fileUri) {
    await FileSystem.deleteAsync(stored.fileUri, { idempotent: true });
  }
}

function requireAndroid(): void {
  if (Platform.OS !== "android") throw new Error("私人自更新目前仅支持 Android");
}
