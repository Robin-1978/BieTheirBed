const PRESENTATION_EVENT_TYPES = new Set([
  "task_created",
  "state_changed",
  "approval_requested",
  "approval_resolved",
  "interaction_requested",
  "interaction_resolved",
  "final_output",
  "completed",
  "failed",
  "cancelled",
]);

const EXECUTION_REFRESH_EVENT_TYPES = new Set([
  ...PRESENTATION_EVENT_TYPES,
  "plan",
  "tool_call",
  "tool_result",
  "artifact",
  "context_compacted",
  "warning",
]);

export function isPresentationTaskEvent(eventType: string): boolean {
  return PRESENTATION_EVENT_TYPES.has(eventType);
}

export function shouldRefreshExecution(eventType: string): boolean {
  return EXECUTION_REFRESH_EVENT_TYPES.has(eventType);
}
