import type { AndroidRelease } from "@/api/models";

export function isNewerAndroidRelease(
  release: AndroidRelease,
  currentVersionCode: number,
): boolean {
  return Number.isSafeInteger(currentVersionCode) && release.version_code > currentVersionCode;
}

export function requiresAndroidUpdate(
  release: AndroidRelease,
  currentVersionCode: number,
): boolean {
  return currentVersionCode < release.min_supported_version_code;
}
