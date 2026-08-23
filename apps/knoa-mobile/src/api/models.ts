export type PairingPayload = {
  version: "v3";
  transport: "direct" | "relay";
  gateway_url: string;
  node_id: string;
  node_signing_public_key: string;
  node_configuration_public_key: string;
  grant_id: string;
  grant_secret: string;
  expires_at: number;
};

export type NodeDescriptor = {
  node_id: string;
  signing_public_key: string;
  signing_key_version: number;
  configuration_public_key: string;
  configuration_key_version: number;
  created_at: number;
};

export type ExtensionPackage = {
  package_id: string;
  kind: "skill" | "mcp" | "capability";
  content_digest: string;
  source_type: string;
  source_locator: string;
  imported_by: string;
  imported_at: number;
  file_count: number;
  size_bytes: number;
};

export type CapabilityRequestedTool = {
  name: string;
  effect: "read_only" | "internal_write" | "local_write" | "external_side_effect" | "desktop_control";
  capabilities: string[];
  risk: "low" | "medium" | "high";
};

export type CapabilityInstallPlan = {
  operation_id: string;
  capability_id: string;
  version: string;
  display_name: string;
  package_id: string;
  package_digest: string;
  component_packages: Record<string, string>;
  requested_tools: CapabilityRequestedTool[];
  withheld_tools: string[];
  setup_inputs: Array<{ name: string; kind: "secret"; required: boolean; description: string }>;
  checks: Array<Record<string, unknown>>;
  draft_id: string;
  draft_version: number;
  previous_revision_id: string;
  plan_digest: string;
  state: "awaiting_confirmation" | "installing" | "installed" | "failed";
};

export type CapabilityInstallation = {
  capability_id: string;
  version: string;
  display_name: string;
  package_id: string;
  component_packages: Record<string, string>;
  component_ids: string[];
  active_revision_id: string;
  previous_revision_id: string;
  enabled: boolean;
  health: "healthy" | "failed" | "disabled";
  installed_at: number;
  updated_at: number;
};

export type ExtensionImportResult = {
  package: ExtensionPackage | null;
  inspection: {
    extension_id: string;
    kind: "skill" | "mcp";
    package_id: string;
    inventory_digest: string;
    tools: Array<Record<string, unknown>>;
    resources: Array<Record<string, unknown>>;
    prompts: Array<Record<string, unknown>>;
    requested_secrets: string[];
    withheld_tools: string[];
  };
  draft: ConfigDraft;
};

export type GatewaySession = {
  token: string;
  expires_at: number;
  device_id: string;
};

export type AgentSummary = {
  agent_id: string;
  display_name: string;
};

export type UserWorkStatus = {
  status: "queued" | "working" | "waiting_for_you" | "completed" | "failed" | "paused" | "cancelled";
  terminal: boolean;
  requires_user: boolean;
  recoverable: boolean;
  side_effect?: "none" | "possible" | "unknown";
  recommended_action: "wait" | "respond" | "retry" | "resume" | "none";
};

export type UnavailableAgent = {
  agent_id: string;
  display_name: string;
  reason: "disabled" | "delegate_only" | "system_only" | "runtime_unavailable" | string;
};

export type ManagedNodeAgent = {
  kind: "knoa" | "codex";
  display_name: string;
  instructions: string;
  instructions_ref: string;
  instructions_required: boolean;
  visibility: "user" | "delegate" | "system";
  enabled: boolean;
  model_binding: { ownership: "platform" | "runtime"; model: string; hint: string };
  max_concurrency: number;
  default_skill_refs: string[];
  allowed_skill_refs: string[];
  allowed_platform_tools: string[];
  platform_capability_ceiling: string[];
  native_capability_ceiling: string[];
  runtime_limits: { max_iterations: number | null; max_output_tokens: number | null };
  delegation: {
    allowed: boolean;
    targets: string[];
    max_depth: number;
    max_children: number;
    max_parallel_children: number;
    max_deadline_seconds: number;
  };
  callable_by: string[];
  command: string[];
  home: string;
  cwd: string;
  sandbox: string;
  approval_policy: string;
  request_timeout_seconds?: number;
  max_line_bytes?: number;
  max_event_queue?: number;
};

export type ManagedApprovalReviewConfig = {
  mode: "off" | "suggest" | "auto";
  agent_id: string;
  timeout_seconds: number;
  max_output_tokens: number;
  auto_max_risk: "low" | "medium";
};

export type ManagedOperationalConfig = {
  llm_temperature: number;
  max_iterations: number;
  max_total_tool_calls: number;
  max_output_tokens: number;
  context_window_budget: number;
  task_capacity: number;
  principal_task_capacity: number;
  generation_drain_seconds: number;
};

export type ManagedConfig = {
  schema_version: 2;
  providers: Record<string, {
    driver: "llamacpp" | "openai" | "openai_compatible" | "anthropic" | "workspace_remote";
    server_url: string;
    api_base: string;
    api_key_ref: string;
    api_key_env: string;
    remote_deployment_id: string;
    direct_gateway_url: string;
    secret_version: number;
    requires_api_key: boolean | null;
    timeout_seconds: number;
  }>;
  models: Record<string, {
    provider: string;
    model: string;
    supports_vision?: boolean | null;
    [key: string]: unknown;
  }>;
  model_deployments: Record<string, {
    model_alias: string;
    resource_id: string;
    display_name: string;
    enabled: boolean;
    share_enabled: boolean;
    max_remote_concurrency: number;
    allowed_node_ids: string[];
  }>;
  default_model: string;
  vision_model: string;
  fallback_model: string;
  fallback_enabled: boolean;
  agents: {
    agents: Record<string, ManagedNodeAgent>;
    default_agent: string;
  };
  approval_review: ManagedApprovalReviewConfig;
  skills: Record<string, { source: string; enabled: boolean; content_digest: string }>;
  mcp_servers: Record<string, { transport: string; enabled: boolean; [key: string]: unknown }>;
  operational: ManagedOperationalConfig;
};

export type ConfigRevision = {
  revision_id: string;
  parent_revision_id: string;
  document: ManagedConfig;
  config_digest: string;
  change_summary: string;
  created_by: string;
  created_at: number;
};

export type ConfigControlState = {
  desired_revision_id: string;
  applied_revision_id: string;
  apply_status: "idle" | "applying" | "failed";
  apply_error_code: string;
  updated_at: number;
};

export type ConfigGeneration = {
  agent_id: string;
  active_generation: string;
  draining_generation: string;
  active_leases: number;
  draining_leases: number;
  enabled: boolean;
};

export type ConfigDraft = {
  draft_id: string;
  base_revision_id: string;
  document: ManagedConfig;
  draft_version: number;
  updated_by: string;
  updated_at: number;
};

export type ConfigValidationResult = {
  valid: boolean;
  issues: Array<{ code: string; path: string; message: string }>;
};

export type ConfigPublishResult = {
  revision: ConfigRevision;
  state: ConfigControlState;
};

export type ConfigChange = {
  op: "add" | "remove" | "replace";
  path: string;
  old?: unknown;
  value?: unknown;
};

export type TaskState =
  | "queued"
  | "running"
  | "waiting_approval"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type TaskDefinitionState = "active" | "paused" | "archived";

export type TaskLaunchKind = "immediate" | "scheduled" | "event";

export type TaskLaunchPolicy = {
  kind: TaskLaunchKind;
  schedule_type: "one_time" | "interval" | "cron" | null;
  run_at: number | null;
  interval_seconds: number | null;
  cron: string;
  timezone: string;
  event_source: string;
  source_config: Record<string, unknown>;
};

export type TaskLaunchReason = "created" | "manual" | "scheduled" | "event" | "rerun" | "follow_up";

export type MCPResourceCatalogItem = {
  server_id: string;
  uri: string;
  name: string;
  description: string;
  mime_type: string;
  subscribable: boolean;
};

export type ArtifactInput = {
  artifact_id: string;
  caption?: string;
};

export type ChatTurnState = "running" | "waiting_approval" | "completed" | "failed" | "cancelled";

export type ConversationSession = {
  session_handle: string;
  agent_id: string;
  title: string;
  state: "active" | "archived";
  turn_count: number;
  last_turn_at: number | null;
  created_at: number;
  updated_at: number;
  revision: number;
};

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
  display?: ApprovalDisplay;
  state: string;
  created_at: number;
  resolved_at: number | null;
  resolved_by: string;
};

export type ApprovalDisplay = {
  tool_name: string;
  effect: string;
  risk: string;
  arguments_preview: string;
  reversible: boolean;
  action_summary?: string;
  target_summary?: string;
  instruction_excerpt?: string;
  reviewer_decision?: "approve" | "deny" | "escalate" | "";
  reviewer_reason?: string;
  reviewer_id?: string;
  reviewer_model?: string;
  manual_reason?: "policy_confirmation" | "high_risk" | "reviewer_suggest_only" | "reviewer_escalated";
};

export type HumanInteraction = {
  interaction_id: string;
  owner_kind: "conversation_turn" | "task_execution";
  owner_id: string;
  kind: "user_input" | "mcp_elicitation";
  state: "pending" | "resolved" | "cancelled" | "expired" | "runtime_lost";
  display: {
    title?: string;
    description?: string;
    fields?: Array<{
      id: string;
      title?: string;
      description?: string;
      options?: Array<{ value: string; label: string; description?: string }>;
      allow_other?: boolean;
    }>;
  };
  resolution_schema: {
    type?: string;
    properties?: Record<string, {
      type?: string;
      title?: string;
      enum?: string[];
      minLength?: number;
      maxLength?: number;
      properties?: Record<string, {
        type?: string;
        title?: string;
        enum?: string[];
        minLength?: number;
        maxLength?: number;
      }>;
      required?: string[];
      additionalProperties?: boolean;
    }>;
    required?: string[];
    additionalProperties?: boolean;
  };
  resolution: unknown;
  created_at: number;
  resolved_at: number | null;
  expires_at: number | null;
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
  interactions?: HumanInteraction[];
  timeline: ChatTimelineEntry[];
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  revision: number;
  work_status?: UserWorkStatus;
};

export type Task = {
  task_id: string;
  session_handle: string;
  agent_id: string;
  title: string;
  goal: string;
  attachments: ArtifactInput[];
  tools_enabled: boolean;
  priority: number;
  launch_policy: TaskLaunchPolicy;
  notification_policy: Record<string, boolean>;
  state: TaskDefinitionState;
  revision: number;
  latest_execution_id: string;
  execution_count: number;
  latest_execution_state: TaskState | null;
  latest_execution_phase: string;
  latest_execution_summary: string;
  latest_execution_failure_code: string;
  latest_execution_updated_at: number | null;
  pending_approval_count: number;
  created_at: number;
  updated_at: number;
  work_status?: UserWorkStatus;
};

export type TaskPreflightCheck = {
  check_id: "task_state" | "goal" | "runtime" | string;
  status: "ready" | "warning" | "blocked";
  detail: string;
  recommended_action: "none" | "retry" | "resume" | "configure";
};

export type TaskPreflight = {
  task_id: string;
  ready: boolean;
  checks: TaskPreflightCheck[];
};

export type TaskExecution = {
  execution_id: string;
  task_id: string;
  agent_id_snapshot: string;
  task_revision: number;
  launch_reason: TaskLaunchReason;
  goal_snapshot: string;
  attachment_snapshots: ArtifactInput[];
  policy_snapshot: TaskLaunchPolicy;
  state: TaskState;
  phase: string;
  cancel_requested: boolean;
  final_result: string;
  failure_code: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  trace: TaskExecutionTrace | null;
  approvals: TaskApproval[];
  interactions?: HumanInteraction[];
  work_status?: UserWorkStatus;
};

export type TaskApproval = {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
  display?: ApprovalDisplay;
  state: "pending" | "approved" | "denied" | "expired";
  created_at: number;
  resolved_at: number | null;
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
  executionId: string;
  toolName: string;
  reason: string;
};

export type UserFacingError = {
  code: string;
  message: string;
  retryable: boolean;
  suggestedAction?: string;
};

export type AndroidRelease = {
  platform: "android";
  channel: "personal" | "hosted";
  version_name: string;
  version_code: number;
  min_supported_version_code: number;
  size_bytes: number;
  sha256: string;
  published_at: number;
  release_notes: string;
  download_path: string;
};
