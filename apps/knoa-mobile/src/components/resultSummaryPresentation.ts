import type { Task } from "@/api/models";

/**
 * Unified result delivery: every finished work item must answer what was
 * completed, what is missing, where the evidence lives, and what to do next.
 * This module derives those facts from persisted task state only; callers are
 * responsible for wording and rendering.
 */
export type ResultNextStep = "approve" | "review" | "retry" | "track";

export type ResultOutcome = {
  completion: "completed" | "failed" | "working" | "waiting_for_you" | "queued" | "paused" | "cancelled" | "unknown";
  /** False when the latest run reached a terminal state without a failure. */
  incomplete: boolean;
  failureCode: string;
  /** Execution id that holds the run log and artifacts for this result. */
  evidenceExecutionId: string | null;
  /** Artifacts were produced into the task session. */
  hasSessionArtifacts: boolean;
  nextStep: ResultNextStep;
};

export function resultOutcome(task: Task): ResultOutcome {
  const completion = outcomeCompletion(task);
  return {
    completion,
    incomplete: completion === "failed" || completion === "cancelled",
    failureCode: completion === "failed" ? task.latest_execution_failure_code : "",
    evidenceExecutionId: task.latest_execution_id || null,
    hasSessionArtifacts: Boolean(task.session_handle),
    nextStep: nextStepFor(task, completion),
  };
}

function outcomeCompletion(task: Task): ResultOutcome["completion"] {
  const status = task.work_status?.status;
  if (status === "waiting_for_you" || status === "completed" || status === "failed" || status === "queued" || status === "working" || status === "paused" || status === "cancelled") {
    return status;
  }
  switch (task.latest_execution_state) {
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "running":
      return "working";
    case "waiting_approval":
      return "waiting_for_you";
    case "queued":
      return "queued";
    case "paused":
      return "paused";
    case "cancelled":
      return "cancelled";
    default:
      return "unknown";
  }
}

function nextStepFor(task: Task, completion: ResultOutcome["completion"]): ResultNextStep {
  if (task.pending_approval_count > 0 || completion === "waiting_for_you") return "approve";
  if (completion === "failed" || completion === "cancelled") return "retry";
  if (completion === "completed") return "review";
  return "track";
}
