import { File, Paths } from "expo-file-system";

import type { ManagedConfig } from "@/api/models";

const VERSION = 1 as const;

export type CapabilityCache = { document: ManagedConfig; toolCount: number; updatedAt: number };

export async function loadCapabilityCache(scope: string): Promise<CapabilityCache | null> {
  if (!scope) return null;
  const file = cacheFile(scope);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as Partial<CapabilityCache> & { version?: number; scope?: string };
    if (value.version !== VERSION || value.scope !== scope || !value.document || typeof value.toolCount !== "number") return null;
    return { document: value.document, toolCount: value.toolCount, updatedAt: value.updatedAt ?? 0 };
  } catch {
    return null;
  }
}

export async function storeCapabilityCache(scope: string, value: Omit<CapabilityCache, "updatedAt">): Promise<void> {
  if (!scope) return;
  const file = cacheFile(scope);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({ version: VERSION, scope, updatedAt: Date.now(), ...value }));
}

function cacheFile(scope: string): File {
  return new File(Paths.document, `capabilities-v${VERSION}-${hash(scope)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}
