import { File, Paths } from "expo-file-system";

import type { Task, TaskExecution } from "@/api/models";

const VERSION = 1 as const;

export type TaskDetailCache = { task: Task; executions: TaskExecution[]; updatedAt: number };

export async function loadTaskDetailCache(taskId: string): Promise<TaskDetailCache | null> {
  if (!taskId) return null;
  const file = cacheFile(taskId);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as Partial<TaskDetailCache> & { version?: number; taskId?: string };
    if (value.version !== VERSION || value.taskId !== taskId || !value.task || !Array.isArray(value.executions)) return null;
    return { task: value.task, executions: value.executions, updatedAt: value.updatedAt ?? 0 };
  } catch {
    return null;
  }
}

export async function storeTaskDetailCache(taskId: string, value: Omit<TaskDetailCache, "updatedAt">): Promise<void> {
  if (!taskId) return;
  const file = cacheFile(taskId);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({ version: VERSION, taskId, updatedAt: Date.now(), ...value }));
}

function cacheFile(taskId: string): File {
  return new File(Paths.document, `tasks-v${VERSION}-detail-${hash(taskId)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}
