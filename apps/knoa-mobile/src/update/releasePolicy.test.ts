import { describe, expect, it } from "vitest";

import type { AndroidRelease } from "@/api/models";
import { isNewerAndroidRelease, requiresAndroidUpdate } from "./releasePolicy";

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

describe("Android release policy", () => {
  it("offers only strictly newer version codes", () => {
    expect(isNewerAndroidRelease(release, 1)).toBe(true);
    expect(isNewerAndroidRelease(release, 2)).toBe(false);
    expect(isNewerAndroidRelease(release, 3)).toBe(false);
  });

  it("uses the published minimum version independently", () => {
    const mandatory = { ...release, min_supported_version_code: 2 };
    expect(requiresAndroidUpdate(mandatory, 1)).toBe(true);
    expect(requiresAndroidUpdate(mandatory, 2)).toBe(false);
  });
});
