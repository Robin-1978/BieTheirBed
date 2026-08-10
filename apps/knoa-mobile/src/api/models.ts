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

export type TaskOrigin = "chat" | "user" | "agent" | "scheduled" | "event";

export type ArtifactInput = {
  artifact_id: string;
  caption?: string;
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
