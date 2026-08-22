import type { UserWorkStatus } from "@/api/models";
import type { WorkspaceWorkProjection } from "@/hub/hubClient";

type UserWorkStatusName = UserWorkStatus["status"];

const STATUS_FROM_DOMAIN_STATE: Record<string, UserWorkStatusName> = {
  queued: "queued",
  running: "working",
  waiting_approval: "waiting_for_you",
  paused: "paused",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
};

export function projectionWorkStatus(item: WorkspaceWorkProjection): UserWorkStatusName {
  const candidate = item.payload.work_status;
  if (isUserWorkStatus(candidate)) return candidate.status;
  return STATUS_FROM_DOMAIN_STATE[item.state] ?? "working";
}

function isUserWorkStatus(value: unknown): value is Pick<UserWorkStatus, "status"> {
  if (!value || typeof value !== "object") return false;
  const status = (value as { status?: unknown }).status;
  return typeof status === "string" && Object.values(STATUS_FROM_DOMAIN_STATE).includes(status as UserWorkStatusName);
}
