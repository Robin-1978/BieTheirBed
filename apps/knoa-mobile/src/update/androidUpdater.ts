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
const READY_KEY = "knoa.android-update.ready.v1";
const APK_MIME = "application/vnd.android.package-archive";
const FLAG_GRANT_READ_URI_PERMISSION = 1;

type StoredResume = {
  versionCode: number;
  sha256?: string;
  fileUri: string;
  resumeData: string;
};

type StoredReady = {
  versionCode: number;
  sha256: string;
  fileUri: string;
};

export type AndroidUpdateProgress = {
  downloaded: number;
  total: number;
};

export type AndroidUpdateCheckpoint = AndroidUpdateProgress & {
  fileUri: string;
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
  private checkpointPromise: Promise<AndroidUpdateCheckpoint | null> | null = null;

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
    const fileUri = updateFileUri(input.release);
    const checkpoint = await loadCheckpoint(input.release, fileUri);
    let resumeData: string | undefined;
    if (checkpoint) {
      resumeData = String(checkpoint.downloaded);
      input.onProgress({ downloaded: checkpoint.downloaded, total: checkpoint.total });
    }
    const downloadUrl = resolveAndroidDownloadUrl(input.release.download_path, input.gatewayUrl);
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
    const resuming = Boolean(this.task.savable().resumeData);
    const result = resuming
      ? await this.task.resumeAsync()
      : await this.task.downloadAsync();
    if (!result || (result.status !== 200 && result.status !== 206)) {
      throw new Error("更新包下载失败");
    }
    try {
      if (resuming && result.status !== 206) {
        throw new Error("更新服务器未接受断点续传，请重新下载");
      }
      await verifyPackage(result.uri, this.release);
      await SecureStore.deleteItemAsync(RESUME_KEY);
      await SecureStore.setItemAsync(READY_KEY, JSON.stringify({
        versionCode: this.release.version_code,
        sha256: this.release.sha256,
        fileUri: result.uri,
      } satisfies StoredReady));
      return result.uri;
    } catch (error) {
      await SecureStore.deleteItemAsync(RESUME_KEY);
      await FileSystem.deleteAsync(result.uri, { idempotent: true });
      throw error;
    }
  }

  async pause(): Promise<AndroidUpdateCheckpoint> {
    const checkpoint = await this.preserveCheckpoint();
    if (!checkpoint) throw new Error("当前下载无法保存断点");
    return checkpoint;
  }

  async preserveCheckpoint(): Promise<AndroidUpdateCheckpoint | null> {
    if (!this.checkpointPromise) {
      this.checkpointPromise = this.captureCheckpoint().finally(() => {
        this.checkpointPromise = null;
      });
    }
    return this.checkpointPromise;
  }

  private async captureCheckpoint(): Promise<AndroidUpdateCheckpoint | null> {
    try {
      await this.task.pauseAsync();
    } catch {
      // A failed native request may no longer be pausable. Android resume data
      // is the number of bytes already written, so the partial file remains a
      // valid checkpoint even when pauseAsync itself fails.
    }
    const partial = await FileSystem.getInfoAsync(this.task.fileUri);
    const downloaded = partial.exists ? resumableByteCount(partial.size, this.release.size_bytes) : 0;
    if (!downloaded) {
      await SecureStore.deleteItemAsync(RESUME_KEY);
      return null;
    }
    const stored: StoredResume = {
      versionCode: this.release.version_code,
      sha256: this.release.sha256,
      fileUri: this.task.fileUri,
      resumeData: String(downloaded),
    };
    await SecureStore.setItemAsync(RESUME_KEY, JSON.stringify(stored));
    return {
      fileUri: this.task.fileUri,
      downloaded,
      total: this.release.size_bytes,
    };
  }
}

export function resolveAndroidDownloadUrl(downloadPath: string, gatewayUrl: string): string {
  if (/^https?:\/\//i.test(downloadPath)) return new URL(downloadPath).toString();
  const normalizedGateway = gatewayUrl.trim().replace(/\/$/, "");
  if (!/^https?:\/\//i.test(normalizedGateway)) {
    throw new Error("当前更新地址需要连接 Node");
  }
  return new URL(downloadPath, `${normalizedGateway}/`).toString();
}

export async function loadAndroidUpdateCheckpoint(
  release: AndroidRelease,
): Promise<AndroidUpdateCheckpoint | null> {
  requireAndroid();
  if (!FileSystem.cacheDirectory) return null;
  return loadCheckpoint(release, updateFileUri(release));
}

export async function loadReadyAndroidPackage(release: AndroidRelease): Promise<string> {
  const raw = await SecureStore.getItemAsync(READY_KEY);
  if (!raw) return "";
  try {
    const stored = JSON.parse(raw) as StoredReady;
    if (stored.versionCode !== release.version_code || stored.sha256 !== release.sha256) {
      await SecureStore.deleteItemAsync(READY_KEY);
      return "";
    }
    const file = new File(stored.fileUri);
    if (!file.exists) {
      await SecureStore.deleteItemAsync(READY_KEY);
      return "";
    }
    return file.uri;
  } catch {
    await SecureStore.deleteItemAsync(READY_KEY);
    return "";
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
      (value.sha256 === undefined || (
        typeof value.sha256 === "string" && /^[0-9a-f]{64}$/.test(value.sha256)
      )) &&
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

async function loadCheckpoint(
  release: AndroidRelease,
  fileUri: string,
): Promise<AndroidUpdateCheckpoint | null> {
  let stored = await loadResume();
  if (
    stored && (
      stored.versionCode !== release.version_code ||
      (stored.sha256 !== undefined && stored.sha256 !== release.sha256) ||
      stored.fileUri !== fileUri
    )
  ) {
    await clearStoredResume(stored);
    stored = null;
  }
  const partial = await FileSystem.getInfoAsync(fileUri);
  const downloaded = partial.exists ? resumableByteCount(partial.size, release.size_bytes) : 0;
  if (!downloaded) {
    await clearStoredResume(stored);
    if (stored?.fileUri !== fileUri) {
      await FileSystem.deleteAsync(fileUri, { idempotent: true });
    }
    return null;
  }
  if (!stored || stored.resumeData !== String(downloaded) || stored.sha256 !== release.sha256) {
    await SecureStore.setItemAsync(RESUME_KEY, JSON.stringify({
      versionCode: release.version_code,
      sha256: release.sha256,
      fileUri,
      resumeData: String(downloaded),
    } satisfies StoredResume));
  }
  return { fileUri, downloaded, total: release.size_bytes };
}

function updateFileUri(release: AndroidRelease): string {
  return `${FileSystem.cacheDirectory}knoa-update-${release.version_code}-${release.sha256}.apk`;
}

export function resumableByteCount(size: number, expectedSize: number): number {
  if (
    !Number.isSafeInteger(size) ||
    !Number.isSafeInteger(expectedSize) ||
    size <= 0 ||
    expectedSize <= 0 ||
    size >= expectedSize
  ) {
    return 0;
  }
  return size;
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
