export type PairingPayload = {
  version: "v1";
  gateway_url: string;
  grant_id: string;
  grant_secret: string;
  expires_at: number;
};

export type GatewaySession = {
  token: string;
  expires_at: number;
  device_id: string;
};

export type TaskState =
  | "queued"
  | "running"
  | "waiting_approval"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type TaskOrigin = "user" | "agent" | "scheduled" | "event";

export type ArtifactInput = {
  artifact_id: string;
  caption?: string;
};

export type ChatTurnState = "running" | "waiting_approval" | "completed" | "failed" | "cancelled";

export type ChatTimelineEntry = {
  kind: string;
  content: string;
  tool_call_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  tool_result: unknown;
  blocked: boolean;
  iteration: number;
};

export type ChatApproval = {
  approval_id: string;
  step_id: string;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  state: string;
  created_at: number;
  resolved_at: number | null;
  resolved_by: string;
};

export type ChatArtifact = {
  artifact_id: string;
  name: string;
  media_type: string;
  [key: string]: unknown;
};

export type ChatTurnSnapshot = {
  turn_id: string;
  session_handle: string;
  client_request_id: string;
  user_input: string;
  attachments: ArtifactInput[];
  tools_enabled: boolean;
  state: ChatTurnState;
  reasoning: string;
  content: string;
  final_output: string;
  artifacts: ChatArtifact[];
  failure_code: string;
  cancel_requested: boolean;
  tool_steps: Array<Record<string, unknown>>;
  approvals: ChatApproval[];
  timeline: ChatTimelineEntry[];
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  revision: number;
};

export type TaskSnapshot = {
  task_id: string;
  session_handle: string;
  client_request_id: string;
  origin: TaskOrigin;
  parent_task_id: string;
  goal: string;
  attachments: ArtifactInput[];
  tools_enabled: boolean;
  priority: number;
  state: TaskState;
  phase: string;
  attempt_count: number;
  cancel_requested: boolean;
  final_summary: string;
  failure_code: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  next_event_seq: number;
  trace: TaskExecutionTrace | null;
};

export type TaskTraceEntry = {
  entry_type: "reasoning" | "content" | "plan" | "tool_call" | "tool_result" | "artifact" | "context_compacted" | "warning" | "final_output";
  iteration: number;
  content: string;
  tool_call_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  tool_result: unknown;
  artifact: ChatArtifact | null;
  occurred_at: number;
};

export type TaskExecutionTrace = {
  task_id: string;
  entries: TaskTraceEntry[];
  final_output: string;
  created_at: number;
  updated_at: number;
  retained_until: number;
  compacted_at: number | null;
  revision: number;
};

export type TaskEvent = {
  task_id: string;
  event_seq: number;
  event_type: string;
  occurred_at: number;
  payload: Record<string, unknown>;
};

export type PrincipalTaskEvent = {
  feed_event_id: number;
  event: TaskEvent;
};

export type ApprovalRequest = {
  approvalId: string;
  taskId: string;
  toolName: string;
  reason: string;
};

export type AndroidRelease = {
  platform: "android";
  channel: "personal";
  version_name: string;
  version_code: number;
  min_supported_version_code: number;
  size_bytes: number;
  sha256: string;
  published_at: number;
  release_notes: string;
  download_path: string;
};
