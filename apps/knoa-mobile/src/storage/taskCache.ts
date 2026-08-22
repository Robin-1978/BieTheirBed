import { File, Paths } from "expo-file-system";

import type { Task } from "@/api/models";

const VERSION = 1 as const;

export async function loadTaskCache(scope: string): Promise<Task[] | null> {
  if (!scope) return null;
  const file = cacheFile(scope);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as { version?: number; scope?: string; tasks?: Task[] };
    if (value.version !== VERSION || value.scope !== scope || !Array.isArray(value.tasks)) return null;
    return value.tasks.filter(isTask);
  } catch {
    return null;
  }
}

export async function storeTaskCache(scope: string, tasks: Task[]): Promise<void> {
  if (!scope) return;
  const file = cacheFile(scope);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({ version: VERSION, scope, updatedAt: Date.now(), tasks }));
}

function cacheFile(scope: string): File {
  return new File(Paths.document, `tasks-v${VERSION}-${hash(scope)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}

function isTask(value: unknown): value is Task {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<Task>;
  return typeof task.task_id === "string"
    && typeof task.title === "string"
    && typeof task.goal === "string"
    && typeof task.state === "string"
    && typeof task.updated_at === "number";
}
