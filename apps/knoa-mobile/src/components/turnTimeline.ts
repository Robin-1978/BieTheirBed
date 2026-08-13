import type { ChatTimelineEntry } from "@/api/models";

export type TimelineDisplayEntry =
  | { kind: "reasoning" | "content" | "notice"; key: string; content: string }
  | { kind: "completion"; key: string }
  | { kind: "tool"; key: string; toolName: string; state: "running" | "completed" | "failed" };

export function timelineDisplayEntries(entries: ChatTimelineEntry[], finalOutput = ""): TimelineDisplayEntry[] {
  const rows: TimelineDisplayEntry[] = [];
  const toolPositions = new Map<string, number>();

  for (const [index, entry] of entries.entries()) {
    if (entry.kind === "tool_call" || entry.kind === "tool_result") {
      const callId = entry.tool_call_id.trim();
      const existing = callId ? toolPositions.get(callId) : undefined;
      const state = entry.kind === "tool_call"
        ? "running"
        : entry.blocked ? "failed" : "completed";
      if (existing === undefined) {
        const key = callId ? `tool:${callId}` : `tool:${entry.iteration}:${index}`;
        if (callId) toolPositions.set(callId, rows.length);
        rows.push({
          kind: "tool",
          key,
          toolName: entry.tool_name || "Tool",
          state,
        });
      } else {
        const current = rows[existing];
        if (current?.kind === "tool") {
          rows[existing] = {
            ...current,
            toolName: entry.tool_name || current.toolName,
            state,
          };
        }
      }
      continue;
    }

    const content = withoutFinalDraft(entry.kind, entry.content, finalOutput);
    if (!content) continue;
    rows.push({
      kind: entry.kind === "reasoning" || entry.kind === "content" ? entry.kind : "notice",
      key: `${entry.kind}:${entry.iteration}:${index}`,
      content,
    });
  }
  if (finalOutput.trim()) {
    rows.push({ kind: "completion", key: "answer-completed" });
  }
  return rows;
}

function withoutFinalDraft(kind: string, content: string, finalOutput: string): string {
  const normalized = content.trim();
  const final = finalOutput.trim();
  if (kind !== "content" || !final || !normalized) return normalized;
  if (normalized === final) return "";
  if (normalized.endsWith(final)) return normalized.slice(0, -final.length).trim();
  return normalized;
}
