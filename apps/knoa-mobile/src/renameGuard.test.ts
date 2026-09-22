import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** Bulk hook renames once shipped `nodeId === nodeId` (always true),
 *  silently disabling node switching. tsc cannot catch self-comparisons,
 *  so this test scans source for bare `x === x` / `x !== x` instead. */
function sourceFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (entry === "node_modules") continue;
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(tsx?|mts|cts)$/.test(entry) && !/\.test\./.test(entry)) out.push(full);
    }
  };
  walk(root);
  return out;
}

describe("no bare self-comparisons", () => {
  it("flags x === x introduced by renames", () => {
    const root = join(__dirname, "..");
    const hits: string[] = [];
    for (const file of [...sourceFiles(join(root, "app")), ...sourceFiles(join(root, "src"))]) {
      const lines = readFileSync(file, "utf8").split("\n");
      lines.forEach((line, index) => {
        // Skip property access (`a.b === c.d`) and comments.
        const code = line.split("//")[0] ?? "";
        const match = code.match(/(^|[^._$A-Za-z0-9])([A-Za-z_$][\w$]*) (===|!==) \2([^_$A-Za-z0-9]|$)/);
        if (match) hits.push(`${file}:${index + 1}: ${line.trim()}`);
      });
    }
    expect(hits).toEqual([]);
  });
});
