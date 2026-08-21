import {
  documentDirectory,
  deleteAsync,
  readAsStringAsync,
  writeAsStringAsync,
} from "expo-file-system/legacy";

export type QueuedTask = {
  queueId: string;
  createdAt: number;
  title: string;
  goal: string;
  notificationPolicy: Record<string, boolean>;
  launchPolicy: Record<string, unknown>;
  agentId: string;
  clientRequestId: string;
};

const FILE = `${documentDirectory ?? ""}knoa-offline-tasks-v1.json`;

export async function loadOfflineTasks(): Promise<QueuedTask[]> {
  if (!documentDirectory) return [];
  try {
    const raw = await readAsStringAsync(FILE);
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isQueuedTask).slice(-20);
  } catch {
    return [];
  }
}

export async function enqueueOfflineTask(task: Omit<QueuedTask, "queueId" | "createdAt">): Promise<QueuedTask> {
  const queued: QueuedTask = { ...task, queueId: `${Date.now()}-${Math.random().toString(36).slice(2)}`, createdAt: Date.now() };
  const items = [...(await loadOfflineTasks()), queued].slice(-20);
  await writeAsStringAsync(FILE, JSON.stringify(items));
  return queued;
}

export async function removeOfflineTask(queueId: string): Promise<void> {
  const remaining = (await loadOfflineTasks()).filter((item) => item.queueId !== queueId);
  if (!remaining.length) {
    if (documentDirectory) await deleteAsync(FILE, { idempotent: true });
    return;
  }
  await writeAsStringAsync(FILE, JSON.stringify(remaining));
}

function isQueuedTask(value: unknown): value is QueuedTask {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<QueuedTask>;
  return typeof item.queueId === "string" && typeof item.createdAt === "number" && typeof item.goal === "string" && typeof item.clientRequestId === "string";
}
