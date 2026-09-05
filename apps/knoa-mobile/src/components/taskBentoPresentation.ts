import type { Task, TaskState } from "@/api/models";

export type BentoCategory = "needs_action" | "running" | "completed" | "idle";

export interface DesktopGlanceRecord {
  taskId: string;
  attemptId?: string;
  timestamp: number;
  thumbnailBase64?: string;
  windowTitle?: string;
  activeApp?: string;
}

/**
 * Classifies a task into one of the 3+1 core Bento Hub dimensions.
 */
export function taskBentoCategory(task: Task): BentoCategory {
  if (task.pending_approval_count > 0 || task.latest_execution_state === "waiting_approval") {
    return "needs_action";
  }
  if (task.latest_execution_state === "running" || task.latest_execution_state === "queued") {
    return "running";
  }
  if (task.latest_execution_state === "completed") {
    return "completed";
  }
  return "idle";
}

/**
 * Calculates human time saved estimation in minutes based on completed executions and task scope.
 */
export function estimateSavedMinutes(task: Task): number {
  if (task.latest_execution_state !== "completed" && task.execution_count === 0) {
    return 0;
  }
  // Co-worker baseline: 10 minutes base per run + length-based complexity factor
  const baseMinutesPerRun = 12;
  const runs = Math.max(1, task.execution_count);
  const complexityFactor = task.tools_enabled ? 1.25 : 1.0;
  return Math.round(runs * baseMinutesPerRun * complexityFactor);
}

/**
 * Formats a clean step tracker summary or phase text.
 */
export function bentoProgressStep(task: Task, defaultLabel: string): string {
  if (task.latest_execution_phase?.trim()) {
    return task.latest_execution_phase.trim();
  }
  if (task.latest_execution_state === "running") {
    return defaultLabel;
  }
  return "";
}
