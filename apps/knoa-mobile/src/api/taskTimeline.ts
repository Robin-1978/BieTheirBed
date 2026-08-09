import type { ApprovalRequest, PrincipalTaskEvent, TaskEvent } from "./models";

export type TaskTimeline = {
  events: TaskEvent[];
  approval: ApprovalRequest | null;
};

export function reduceTimeline(
  timeline: TaskTimeline,
  feed: PrincipalTaskEvent,
): TaskTimeline {
  if (timeline.events.some((event) => event.task_id === feed.event.task_id && event.event_seq === feed.event.event_seq)) {
    return timeline;
  }
  const payload = feed.event.payload;
  let approval = timeline.approval;
  if (feed.event.event_type === "approval_requested") {
    approval = {
      approvalId: String(payload.approval_id ?? ""),
      taskId: feed.event.task_id,
      toolName: String(payload.tool_name ?? ""),
      reason: String(payload.reason ?? ""),
    };
  } else if (feed.event.event_type === "approval_resolved") {
    approval = null;
  }
  return { events: [...timeline.events, feed.event], approval };
}
