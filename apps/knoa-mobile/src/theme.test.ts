import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

function semanticColorResources(): string[] {
  const source = readFileSync(fileURLToPath(new URL("./theme.ts", import.meta.url).href), "utf8");
  return [...source.matchAll(/semanticColor\("([^"]+)"/g)].map((match) => match[1]!);
}

describe("Android theme resources", () => {
  it("declares every PlatformColor resource in both light and dark palettes", () => {
    const resources = semanticColorResources();
    const palettes = [
      fileURLToPath(new URL("../android/app/src/main/res/values/colors.xml", import.meta.url).href),
      fileURLToPath(new URL("../android/app/src/main/res/values-night/colors.xml", import.meta.url).href),
    ];

    for (const palette of palettes) {
      const xml = readFileSync(palette, "utf8");
      for (const resource of resources) {
        expect(xml, `${palette} is missing knoa_${resource}`).toContain(
          `<color name="knoa_${resource}">`,
        );
      }
    }
  });
});
