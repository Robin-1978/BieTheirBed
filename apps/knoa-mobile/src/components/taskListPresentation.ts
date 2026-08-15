import type { Task, TaskState } from "@/api/models";

export type CurrentTaskSectionKey = "needs_action" | "in_progress" | "recent" | "not_started";

export type CurrentTaskSection = {
  key: CurrentTaskSectionKey;
  data: Task[];
};

const TERMINAL_STATES = new Set<TaskState>(["completed", "failed", "cancelled"]);

export function currentTaskSections(tasks: Task[]): CurrentTaskSection[] {
  const groups: Record<CurrentTaskSectionKey, Task[]> = {
    needs_action: [],
    in_progress: [],
    recent: [],
    not_started: [],
  };
  for (const task of tasks.filter((item) => item.state !== "archived")) {
    const state = task.latest_execution_state;
    if (task.pending_approval_count > 0 || state === "waiting_approval") {
      groups.needs_action.push(task);
    } else if (state === "queued" || state === "running" || state === "paused") {
      groups.in_progress.push(task);
    } else if (state && TERMINAL_STATES.has(state)) {
      groups.recent.push(task);
    } else {
      groups.not_started.push(task);
    }
  }
  return (["needs_action", "in_progress", "recent", "not_started"] as const)
    .map((key) => ({ key, data: groups[key].sort(compareLatestFirst) }))
    .filter((section) => section.data.length > 0);
}

function compareLatestFirst(left: Task, right: Task): number {
  const leftTime = left.latest_execution_updated_at ?? left.updated_at;
  const rightTime = right.latest_execution_updated_at ?? right.updated_at;
  return rightTime - leftTime || right.task_id.localeCompare(left.task_id);
}
