import type { TaskState, TaskTraceEntry } from "@/api/models";

export type TaskTimelineToolState = "running" | "completed" | "failed" | "cancelled" | "incomplete";

export type TaskTimelineItem =
  | { kind: "tool"; key: string; toolName: string; state: TaskTimelineToolState }
  | { kind: "entry"; key: string; entry: TaskTraceEntry };

export function mergeTaskTimeline(entries: TaskTraceEntry[], executionState?: TaskState): TaskTimelineItem[] {
  const rows: TaskTimelineItem[] = [];
  const positions = new Map<string, number>();
  entries.forEach((entry, index) => {
    if (entry.entry_type === "tool_call" || entry.entry_type === "tool_result") {
      const id = entry.tool_call_id.trim() || `${entry.iteration}:${entry.tool_name}:${index}`;
      const existing = positions.get(id);
      if (existing === undefined) {
        positions.set(id, rows.length);
        rows.push({
          kind: "tool",
          key: `tool:${id}`,
          toolName: entry.tool_name,
          state: entry.entry_type === "tool_result" ? "completed" : "running",
        });
      } else {
        const current = rows[existing];
        if (current?.kind === "tool") {
          rows[existing] = {
            ...current,
            toolName: entry.tool_name || current.toolName,
            state: "completed",
          };
        }
      }
      return;
    }
    rows.push({
      kind: "entry",
      key: `${entry.entry_type}:${entry.occurred_at}:${index}`,
      entry,
    });
  });
  if (executionState !== "completed" && executionState !== "failed" && executionState !== "cancelled") {
    return rows;
  }

  return rows.map((row) => {
    if (row.kind !== "tool" || row.state !== "running") return row;
    return {
      ...row,
      state: executionState === "cancelled"
        ? "cancelled"
        : executionState === "failed"
          ? "failed"
          : "incomplete",
    };
  });
}
