import { Directory, File, Paths } from "expo-file-system";
import { getInfoAsync } from "expo-file-system/legacy";

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
  /** Newest modification time across cache files, in milliseconds. */
  updatedAt: number | null;
  byKind: Record<Exclude<CacheKind, "all">, { files: number; bytes: number }>;
};

export function emptyAppCacheSummary(): AppCacheSummary {
  return {
    files: 0,
    bytes: 0,
    updatedAt: null,
    byKind: {
      conversation: { files: 0, bytes: 0 },
      workspace: { files: 0, bytes: 0 },
      task: { files: 0, bytes: 0 },
      artifact: { files: 0, bytes: 0 },
    },
  };
}

export async function appCacheSummary(): Promise<AppCacheSummary> {
  const summary = emptyAppCacheSummary();
  try {
    for (const item of new Directory(Paths.document).list()) {
      if (!(item instanceof File)) continue;
      const entry = ENTRIES.find((candidate) => item.name.startsWith(candidate.prefix));
      if (!entry) continue;
      const size = item.size || 0;
      summary.files += 1;
      summary.bytes += size;
      summary.byKind[entry.kind].files += 1;
      summary.byKind[entry.kind].bytes += size;
      // The legacy info call is the only API exposing modification times;
      // it reports seconds since epoch.
      const info = await getInfoAsync(item.uri);
      if (info.exists && info.modificationTime) {
        summary.updatedAt = Math.max(summary.updatedAt ?? 0, info.modificationTime * 1000);
      }
    }
  } catch {
    // Cache diagnostics must never block the rest of Settings.
  }
  return summary;
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
