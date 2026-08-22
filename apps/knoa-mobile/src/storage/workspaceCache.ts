import { Directory, File, Paths } from "expo-file-system";

import type {
  HostedWorkspace,
  HostedWorkspaceMember,
  HubNode,
  WorkspaceResourceState,
  WorkspaceWorkProjection,
} from "@/hub/hubClient";

const CACHE_VERSION = 1 as const;
const MAX_WORK_ITEMS = 300;
export const WORKSPACE_CACHE_TTL_MS = 5 * 60 * 1000;

export type WorkspaceCacheFreshness = "fresh" | "stale" | "empty";

export type WorkspaceCacheSnapshot = {
  version: typeof CACHE_VERSION;
  workspaceId: string;
  updatedAt: number;
  workspace: HostedWorkspace | null;
  nodes: HubNode[];
  resources: WorkspaceResourceState | null;
  work: WorkspaceWorkProjection[];
  members: HostedWorkspaceMember[];
};

export function workspaceCacheAge(snapshot: WorkspaceCacheSnapshot, now = Date.now()): number {
  if (!snapshot.updatedAt) return Number.POSITIVE_INFINITY;
  return Math.max(0, now - snapshot.updatedAt);
}

export function workspaceCacheFreshness(
  snapshot: WorkspaceCacheSnapshot | null,
  now = Date.now(),
): WorkspaceCacheFreshness {
  if (!snapshot) return "empty";
  return workspaceCacheAge(snapshot, now) <= WORKSPACE_CACHE_TTL_MS ? "fresh" : "stale";
}

type WorkspaceCachePatch = Partial<
  Pick<WorkspaceCacheSnapshot, "workspace" | "nodes" | "resources" | "work" | "members">
>;

export async function loadWorkspaceCache(workspaceId: string): Promise<WorkspaceCacheSnapshot | null> {
  if (!workspaceId) return null;
  const file = cacheFile(workspaceId);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as Partial<WorkspaceCacheSnapshot>;
    if (value.version !== CACHE_VERSION || value.workspaceId !== workspaceId) return null;
    return normalize(workspaceId, value);
  } catch {
    return null;
  }
}

export async function mergeWorkspaceCache(workspaceId: string, patch: WorkspaceCachePatch): Promise<void> {
  if (!workspaceId) return;
  const current = await loadWorkspaceCache(workspaceId);
  const next = normalize(workspaceId, { ...current, ...patch, updatedAt: Date.now() });
  const file = cacheFile(workspaceId);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify(next));
}

export function clearWorkspaceCache(workspaceId: string): void {
  if (!workspaceId) return;
  const file = cacheFile(workspaceId);
  if (file.exists) file.delete();
}

export function clearAllWorkspaceCaches(): void {
  try {
    const document = new Directory(Paths.document);
    for (const item of document.list()) {
      if (item instanceof File && item.name.startsWith(`workspace-v${CACHE_VERSION}-`)) item.delete();
    }
  } catch {
    // Logout must still succeed if the cache directory is unavailable.
  }
}

function normalize(workspaceId: string, value: Partial<WorkspaceCacheSnapshot>): WorkspaceCacheSnapshot {
  return {
    version: CACHE_VERSION,
    workspaceId,
    updatedAt: typeof value.updatedAt === "number" ? value.updatedAt : 0,
    workspace: isWorkspace(value.workspace) ? value.workspace : null,
    nodes: Array.isArray(value.nodes) ? value.nodes.filter(isNode) : [],
    resources: isResourceState(value.resources) ? value.resources : null,
    work: Array.isArray(value.work) ? value.work.slice(0, MAX_WORK_ITEMS) : [],
    members: Array.isArray(value.members) ? value.members.filter(isMember) : [],
  };
}

function cacheFile(workspaceId: string): File {
  return new File(Paths.document, `workspace-v${CACHE_VERSION}-${hash(workspaceId)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

function isWorkspace(value: unknown): value is HostedWorkspace {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<HostedWorkspace>;
  return typeof item.workspaceId === "string" && typeof item.displayName === "string";
}

function isNode(value: unknown): value is HubNode {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<HubNode>;
  return typeof item.node_id === "string" && typeof item.display_name === "string";
}

function isMember(value: unknown): value is HostedWorkspaceMember {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<HostedWorkspaceMember>;
  return typeof item.accountId === "string" && typeof item.displayName === "string";
}

function isResourceState(value: unknown): value is WorkspaceResourceState {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<WorkspaceResourceState>;
  return Array.isArray(item.workspaceResources)
    && Array.isArray(item.workspaceDeployments)
    && Array.isArray(item.grants)
    && Array.isArray(item.observations);
}
