import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AndroidRelease } from "@/api/models";

const native = vi.hoisted(() => ({
  cache: new Map<string, string>(),
  fileExists: false,
  fileSize: 0,
  task: {
    fileUri: `file:///cache/knoa-update-2-${"a".repeat(64)}.apk`,
    savable: vi.fn(() => ({ resumeData: undefined as string | undefined })),
    downloadAsync: vi.fn(),
    resumeAsync: vi.fn(),
    pauseAsync: vi.fn(),
  },
  createDownloadResumable: vi.fn(),
  deleteAsync: vi.fn(),
}));

vi.mock("expo-application", () => ({
  nativeBuildVersion: "1",
  applicationId: "dev.knoa.mobile",
}));
vi.mock("expo-crypto", () => ({
  CryptoDigestAlgorithm: { SHA256: "SHA-256" },
  digest: vi.fn(),
}));
vi.mock("expo-file-system", () => ({
  File: class {
    exists = false;
    uri: string;

    constructor(uri: string) {
      this.uri = uri;
    }
  },
}));
vi.mock("expo-file-system/legacy", () => ({
  cacheDirectory: "file:///cache/",
  createDownloadResumable: native.createDownloadResumable,
  deleteAsync: native.deleteAsync,
  getInfoAsync: vi.fn(async () => native.fileExists
    ? {
        exists: true,
        isDirectory: false,
        uri: native.task.fileUri,
        size: native.fileSize,
        modificationTime: 1,
      }
    : { exists: false, isDirectory: false }),
  getContentUriAsync: vi.fn(),
}));
vi.mock("expo-intent-launcher", () => ({
  ActivityAction: { MANAGE_UNKNOWN_APP_SOURCES: "settings" },
  startActivityAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => native.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    native.cache.set(key, value);
  }),
  deleteItemAsync: vi.fn(async (key: string) => {
    native.cache.delete(key);
  }),
}));
vi.mock("react-native", () => ({ Platform: { OS: "android" } }));

import {
  AndroidUpdateDownload,
  loadAndroidUpdateCheckpoint,
  resumableByteCount,
} from "./androidUpdater";

const release: AndroidRelease = {
  platform: "android",
  channel: "personal",
  version_name: "0.2.0",
  version_code: 2,
  min_supported_version_code: 1,
  size_bytes: 1024,
  sha256: "a".repeat(64),
  published_at: 1,
  release_notes: "",
  download_path: `/releases/android/2/${"a".repeat(64)}/knoa.apk`,
};

beforeEach(() => {
  native.cache.clear();
  native.fileExists = false;
  native.fileSize = 0;
  native.task.savable.mockReturnValue({ resumeData: undefined });
  native.task.downloadAsync.mockReset();
  native.task.resumeAsync.mockReset();
  native.task.pauseAsync.mockReset();
  native.createDownloadResumable.mockReset();
  native.createDownloadResumable.mockReturnValue(native.task);
  native.deleteAsync.mockReset();
});

describe("Android update checkpoints", () => {
  it("recovers a checkpoint from the partial file after a network failure", async () => {
    native.task.downloadAsync.mockRejectedValue(new Error("connection reset"));
    native.task.pauseAsync.mockRejectedValue(new Error("native task already failed"));
    const controller = await AndroidUpdateDownload.create({
      gatewayUrl: "https://knoa.example.com",
      release,
      onProgress: vi.fn(),
    });

    native.fileExists = true;
    native.fileSize = 384;
    await expect(controller.start()).rejects.toThrow("connection reset");
    await expect(controller.preserveCheckpoint()).resolves.toMatchObject({
      downloaded: 384,
      total: 1024,
    });

    const stored = JSON.parse(native.cache.get("knoa.android-update.resume.v1") ?? "{}");
    expect(stored).toMatchObject({
      versionCode: 2,
      sha256: "a".repeat(64),
      fileUri: native.task.fileUri,
      resumeData: "384",
    });
  });

  it("resumes from the actual partial-file length instead of stale saved data", async () => {
    native.cache.set("knoa.android-update.resume.v1", JSON.stringify({
      versionCode: 2,
      sha256: release.sha256,
      fileUri: native.task.fileUri,
      resumeData: "128",
    }));
    native.fileExists = true;
    native.fileSize = 512;
    const onProgress = vi.fn();

    await AndroidUpdateDownload.create({
      gatewayUrl: "https://knoa.example.com",
      release,
      onProgress,
    });

    expect(native.createDownloadResumable.mock.calls[0]?.[4]).toBe("512");
    expect(onProgress).toHaveBeenCalledWith({ downloaded: 512, total: 1024 });
    await expect(loadAndroidUpdateCheckpoint(release)).resolves.toMatchObject({
      downloaded: 512,
      total: 1024,
    });
  });

  it("reconstructs resume data from an orphaned partial file after an app restart", async () => {
    native.fileExists = true;
    native.fileSize = 640;

    await expect(loadAndroidUpdateCheckpoint(release)).resolves.toMatchObject({
      downloaded: 640,
      total: 1024,
    });

    const stored = JSON.parse(native.cache.get("knoa.android-update.resume.v1") ?? "{}");
    expect(stored).toMatchObject({
      versionCode: 2,
      sha256: release.sha256,
      fileUri: native.task.fileUri,
      resumeData: "640",
    });
  });

  it("discards empty, complete, oversized, and invalid partial lengths", () => {
    expect(resumableByteCount(0, 1024)).toBe(0);
    expect(resumableByteCount(1024, 1024)).toBe(0);
    expect(resumableByteCount(2048, 1024)).toBe(0);
    expect(resumableByteCount(12.5, 1024)).toBe(0);
    expect(resumableByteCount(384, 1024)).toBe(384);
  });
});
