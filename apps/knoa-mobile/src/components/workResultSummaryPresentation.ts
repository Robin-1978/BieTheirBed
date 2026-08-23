import type { TaskExecution } from "@/api/models";

export type StructuredWorkChange = { label: string; reference: string };

/** Read only explicit Tool/Artifact facts; never infer changes from model prose. */
export function structuredWorkChanges(execution: TaskExecution): StructuredWorkChange[] {
  const changes: StructuredWorkChange[] = [];
  for (const entry of execution.trace?.entries ?? []) {
    if (entry.entry_type === "artifact" && entry.artifact) {
      changes.push({ label: entry.artifact.name || entry.artifact.artifact_id, reference: entry.artifact.artifact_id });
      continue;
    }
    if (entry.entry_type !== "tool_result" || !entry.tool_result || typeof entry.tool_result !== "object") continue;
    const output = (entry.tool_result as { output?: unknown }).output;
    if (!output || typeof output !== "object") continue;
    const explicit = (output as { changes?: unknown }).changes;
    if (!Array.isArray(explicit)) continue;
    for (const item of explicit) {
      if (!item || typeof item !== "object") continue;
      const value = item as { label?: unknown; reference?: unknown };
      if (typeof value.label === "string" && typeof value.reference === "string") {
        changes.push({ label: value.label.slice(0, 200), reference: value.reference.slice(0, 512) });
      }
    }
  }
  return changes;
}
