import { File, Paths } from "expo-file-system";

import type { Task, TaskExecution } from "@/api/models";

const VERSION = 1 as const;

export type ExecutionCache = { execution: TaskExecution; task: Task; updatedAt: number };

export async function loadExecutionCache(executionId: string): Promise<ExecutionCache | null> {
  if (!executionId) return null;
  const file = cacheFile(executionId);
  if (!file.exists) return null;
  try {
    const value = JSON.parse(await file.text()) as Partial<ExecutionCache> & { version?: number; executionId?: string };
    if (value.version !== VERSION || value.executionId !== executionId || !value.execution || !value.task) return null;
    return { execution: value.execution, task: value.task, updatedAt: value.updatedAt ?? 0 };
  } catch {
    return null;
  }
}

export async function storeExecutionCache(executionId: string, value: Omit<ExecutionCache, "updatedAt">): Promise<void> {
  if (!executionId) return;
  const file = cacheFile(executionId);
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({ version: VERSION, executionId, updatedAt: Date.now(), ...value }));
}

function cacheFile(executionId: string): File {
  return new File(Paths.document, `tasks-v${VERSION}-execution-${hash(executionId)}.json`);
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, "0");
}
