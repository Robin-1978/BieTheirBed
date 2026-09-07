import type { ChatTimelineEntry } from "@/api/models";

export type TimelineDisplayEntry =
  | { kind: "reasoning" | "content" | "notice"; key: string; content: string }
  | { kind: "completion"; key: string }
  | { kind: "tool"; key: string; toolName: string; detail?: string; state: "running" | "completed" | "failed" };

export function formatToolDetail(toolName: string, args?: Record<string, unknown>): string | undefined {
  if (!args || typeof args !== "object") return undefined;
  if (toolName === "web_search" && typeof args.query === "string" && args.query) {
    return `"${args.query}"`;
  }
  if (toolName === "web_fetch" && typeof args.url === "string" && args.url) {
    try {
      const u = new URL(args.url);
      const host = u.hostname.replace(/^www\./, "");
      const path = u.pathname.length > 24 ? `${u.pathname.slice(0, 24)}…` : u.pathname;
      return `${host}${path !== "/" ? path : ""}`;
    } catch {
      return args.url.length > 35 ? `${args.url.slice(0, 35)}…` : args.url;
    }
  }
  if ((toolName === "read_file" || toolName === "write_file") && typeof args.path === "string") {
    const parts = args.path.split("/");
    return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : args.path;
  }
  if (toolName === "read_artifact" && typeof args.artifact_id === "string") {
    const id = args.artifact_id.slice(0, 8);
    const range = args.offset ? ` (L${args.offset})` : "";
    return `${id}…${range}`;
  }
  if (toolName === "sleep") {
    const sec = args.seconds !== undefined ? `${args.seconds}s` : "";
    const reason = typeof args.reason === "string" && args.reason ? ` (${args.reason})` : "";
    return sec ? `等待 ${sec}${reason}` : undefined;
  }
  if (toolName === "spawn_subagent" && typeof args.goal === "string" && args.goal) {
    const goal = args.goal.trim();
    return goal.length > 30 ? `子任务: ${goal.slice(0, 30)}…` : `子任务: ${goal}`;
  }
  if (toolName === "exec_command" && typeof args.command === "string") {
    return args.command.length > 35 ? `${args.command.slice(0, 35)}…` : args.command;
  }
  // Generic fallback: first non-empty string value if available
  for (const [k, val] of Object.entries(args)) {
    if (typeof val === "string" && val.trim()) {
      const display = val.trim();
      return `${k}: ${display.length > 25 ? `${display.slice(0, 25)}…` : display}`;
    }
  }
  return undefined;
}

export function timelineDisplayEntries(
  entries: ChatTimelineEntry[],
  finalOutput = "",
  reasoningFallback = "",
): TimelineDisplayEntry[] {
  const rows: TimelineDisplayEntry[] = [];
  const toolPositions = new Map<string, number>();

  for (const [index, entry] of entries.entries()) {
    if (entry.kind === "tool_call" || entry.kind === "tool_result") {
      const callId = entry.tool_call_id.trim();
      const existing = callId ? toolPositions.get(callId) : undefined;
      const state = entry.kind === "tool_call"
        ? "running"
        : entry.blocked ? "failed" : "completed";
      const detail = formatToolDetail(entry.tool_name, entry.tool_args);
      if (existing === undefined) {
        const key = callId ? `tool:${callId}` : `tool:${entry.iteration}:${index}`;
        if (callId) toolPositions.set(callId, rows.length);
        rows.push({
          kind: "tool",
          key,
          toolName: entry.tool_name || "Tool",
          detail,
          state,
        });
      } else {
        const current = rows[existing];
        if (current?.kind === "tool") {
          rows[existing] = {
            ...current,
            toolName: entry.tool_name || current.toolName,
            detail: detail || current.detail,
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

  // Fallback: If no reasoning entry in timeline but turn.reasoning is present, prepend it
  if (reasoningFallback.trim() && !rows.some((r) => r.kind === "reasoning")) {
    rows.unshift({
      kind: "reasoning",
      key: "reasoning:fallback",
      content: reasoningFallback.trim(),
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
