import { Directory, File, Paths } from "expo-file-system";

export type CacheKind = "conversation" | "workspace" | "task" | "artifact" | "all";

type CacheEntry = { kind: Exclude<CacheKind, "all">; prefix: string };

const ENTRIES: CacheEntry[] = [
  { kind: "conversation", prefix: "conversation-v" },
  { kind: "conversation", prefix: "conversation-list-v" },
  { kind: "workspace", prefix: "workspace-v" },
  { kind: "task", prefix: "tasks-v" },
  { kind: "task", prefix: "capabilities-v" },
  { kind: "artifact", prefix: "received-" },
];

export type AppCacheSummary = {
  files: number;
  bytes: number;
  byKind: Record<Exclude<CacheKind, "all">, { files: number; bytes: number }>;
};

export function appCacheSummary(): AppCacheSummary {
  const byKind = {
    conversation: { files: 0, bytes: 0 },
    workspace: { files: 0, bytes: 0 },
    task: { files: 0, bytes: 0 },
    artifact: { files: 0, bytes: 0 },
  };
  let files = 0;
  let bytes = 0;
  try {
    for (const item of new Directory(Paths.document).list()) {
      if (!(item instanceof File)) continue;
      const entry = ENTRIES.find((candidate) => item.name.startsWith(candidate.prefix));
      if (!entry) continue;
      const size = item.size || 0;
      files += 1;
      bytes += size;
      byKind[entry.kind].files += 1;
      byKind[entry.kind].bytes += size;
    }
  } catch {
    // Cache diagnostics must never block the rest of Settings.
  }
  return { files, bytes, byKind };
}

export function clearAppCache(kind: CacheKind): { removed: number; failed: number } {
  let removed = 0;
  let failed = 0;
  try {
    for (const item of new Directory(Paths.document).list()) {
      if (!(item instanceof File)) continue;
      const entry = ENTRIES.find((candidate) => item.name.startsWith(candidate.prefix));
      if (!entry || (kind !== "all" && entry.kind !== kind)) continue;
      try {
        item.delete();
        removed += 1;
      } catch {
        failed += 1;
      }
    }
  } catch {
    failed += 1;
  }
  return { removed, failed };
}

export function formatCacheBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
