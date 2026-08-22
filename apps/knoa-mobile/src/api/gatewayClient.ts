import type {
  AgentSummary,
  UnavailableAgent,
  AndroidRelease,
  ArtifactInput,
  ChatApproval,
  ChatTurnSnapshot,
  ConfigChange,
  ConfigControlState,
  ConfigDraft,
  ConfigGeneration,
  ConfigPublishResult,
  ConfigRevision,
  ConfigValidationResult,
  ConversationSession,
  GatewaySession,
  HumanInteraction,
  MCPResourceCatalogItem,
  ManagedConfig,
  NodeDescriptor,
  ExtensionPackage,
  ExtensionImportResult,
  PairingPayload,
  PrincipalTaskEvent,
  Task,
  TaskDefinitionState,
  TaskExecution,
  TaskEvent,
  TaskLaunchPolicy,
  TaskPreflight,
} from "./models";
import { DirectFetchTransport, type GatewayTransport } from "./gatewayTransportBase";

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const LONG_REQUEST_TIMEOUT_MS = 120_000;

type Json = Record<string, unknown>;

export class GatewayError extends Error {
  readonly retryable: boolean;

  constructor(
    readonly status: number,
    readonly code: string,
    message?: string,
  ) {
    super(message || userMessage(status, code));
    this.retryable = status === 408 || status === 429 || status >= 500;
  }
}

function userMessage(status: number, code: string): string {
  if (status === 401) return "连接认证已失效，正在尝试重新认证";
  if (status === 404) return "内容不存在或已经被删除";
  if (status === 413 || code === "payload_too_large") return "内容过大，请减少附件后重试";
  if (status === 422 || code === "rejected") return "当前状态不允许这个操作，请刷新后重试";
  if (status === 429) return "操作太频繁，请稍后再试";
  if (status >= 500 || code === "unavailable") return "小诺服务暂时不可用，请稍后重试";
  return "请求未完成，请检查输入后重试";
}

export class GatewayClient {
  constructor(
    readonly baseUrl: string,
    private readonly token: string | null = null,
    private readonly transport: GatewayTransport = new DirectFetchTransport(),
  ) {}

  authenticated(token: string): GatewayClient {
    return new GatewayClient(this.baseUrl, token, this.transport);
  }

  transportMode(): "direct" | "p2p" | "relay" {
    return this.transport.mode();
  }

  close(): void {
    this.transport.close?.();
  }

  async pairChallenge(grantId: string): Promise<Challenge> {
    return this.json("/v1/pair/challenge", {
      method: "POST",
      body: { grant_id: grantId },
      authenticated: false,
    });
  }

  async pairComplete(input: PairComplete): Promise<{ device_id: string; principal_id: string; node: NodeDescriptor }> {
    return this.json("/v1/pair/complete", {
      method: "POST",
      body: input,
      authenticated: false,
    });
  }

  async nodeDescriptor(): Promise<NodeDescriptor> {
    return this.json("/v1/node");
  }

  async hubStatus(): Promise<{
    enrolled: boolean;
    relay_connected: boolean;
    last_error: string;
    hub: null | { hub_url: string; hub_id: string; hub_signing_public_key: string; enrolled_at: number };
  }> {
    return this.json("/v1/hub");
  }

  async enrollHub(input: {
    hub_url: string;
    hub_id: string;
    hub_signing_public_key: string;
    grant_id: string;
    grant_secret: string;
    challenge: string;
    display_name: string;
  }): Promise<{ enrollment: Record<string, unknown>; relay_connected: boolean }> {
    return this.json("/v1/hub/enroll", { method: "POST", body: input });
  }

  async removeHub(): Promise<void> {
    await this.json("/v1/hub", { method: "DELETE" });
  }

  async listExtensionPackages(): Promise<ExtensionPackage[]> {
    const response = await this.json<{ packages: ExtensionPackage[] }>("/v1/extensions/packages");
    return response.packages;
  }

  async importSkill(sourcePath: string): Promise<ExtensionImportResult> {
    const response = await this.json<{ result: ExtensionImportResult }>("/v1/extensions/import/skill", {
      method: "POST",
      body: { source_path: sourcePath },
    });
    return response.result;
  }

  async importLocalMcp(sourcePath: string, serverId: string): Promise<ExtensionImportResult> {
    const response = await this.json<{ result: ExtensionImportResult }>("/v1/extensions/import/mcp/local", {
      method: "POST",
      body: { source_path: sourcePath, server_id: serverId },
    });
    return response.result;
  }

  async importRemoteMcp(
    serverId: string,
    url: string,
    allowPrivateNetwork: boolean,
  ): Promise<ExtensionImportResult> {
    const response = await this.json<{ result: ExtensionImportResult }>("/v1/extensions/import/mcp/remote", {
      method: "POST",
      body: { server_id: serverId, url, allow_private_network: allowPrivateNetwork },
    });
    return response.result;
  }

  async secretStatus(reference: string): Promise<{
    reference: string;
    configured: boolean;
    rotated_at: number;
    fingerprint?: string;
  }> {
    return this.json(`/v1/secrets/${encodeURIComponent(reference)}`);
  }

  async writeSecret(reference: string, value: string): Promise<{
    reference: string;
    configured: boolean;
    rotated_at: number;
    fingerprint?: string;
  }> {
    return this.json(`/v1/secrets/${encodeURIComponent(reference)}`, {
      method: "PUT",
      body: { value },
    });
  }

  async authChallenge(deviceId: string): Promise<Challenge> {
    return this.json("/v1/auth/challenge", {
      method: "POST",
      body: { device_id: deviceId },
      authenticated: false,
    });
  }

  async authComplete(input: AuthComplete): Promise<GatewaySession> {
    return this.json("/v1/auth/complete", {
      method: "POST",
      body: input,
      authenticated: false,
    });
  }

  async createSession(agentId?: string): Promise<string> {
    const response = await this.json<{ session_handle: string }>("/v1/sessions", {
      method: "POST",
      body: agentId ? { agent_id: agentId } : undefined,
    });
    return response.session_handle;
  }

  async listAgents(): Promise<{ defaultAgentId: string; agents: AgentSummary[] }> {
    const response = await this.json<{ default_agent: string; agents: AgentSummary[] }>("/v1/agents");
    return { defaultAgentId: response.default_agent, agents: response.agents };
  }

  async listAgentAvailability(): Promise<UnavailableAgent[]> {
    const response = await this.json<{ unavailable: UnavailableAgent[] }>("/v1/agents/availability");
    return response.unavailable;
  }

  async getConfigCurrent(): Promise<{
    revision: ConfigRevision;
    state: ConfigControlState;
    generations: ConfigGeneration[];
  }> {
    return this.json("/v1/config/current");
  }

  async createConfigDraft(): Promise<ConfigDraft> {
    const response = await this.json<{ draft: ConfigDraft }>("/v1/config/drafts", {
      method: "POST",
    });
    return response.draft;
  }

  async getConfigDraft(draftId: string): Promise<ConfigDraft> {
    const response = await this.json<{ draft: ConfigDraft }>(
      `/v1/config/drafts/${encodeURIComponent(draftId)}`,
    );
    return response.draft;
  }

  async replaceConfigDraft(
    draftId: string,
    document: ManagedConfig,
    expectedVersion: number,
  ): Promise<ConfigDraft> {
    const response = await this.json<{ draft: ConfigDraft }>(
      `/v1/config/drafts/${encodeURIComponent(draftId)}`,
      {
        method: "PUT",
        body: { document, expected_version: expectedVersion },
      },
    );
    return response.draft;
  }

  async validateConfigDraft(
    draftId: string,
    preflight = false,
  ): Promise<ConfigValidationResult> {
    const action = preflight ? "preflight" : "validate";
    const response = await this.json<{ result: ConfigValidationResult }>(
      `/v1/config/drafts/${encodeURIComponent(draftId)}/${action}`,
      { method: "POST" },
    );
    return response.result;
  }

  async publishConfigDraft(
    draftId: string,
    expectedVersion: number,
    summary: string,
  ): Promise<ConfigPublishResult> {
    const response = await this.json<{ result: ConfigPublishResult }>(
      `/v1/config/drafts/${encodeURIComponent(draftId)}/publish`,
      { method: "POST", body: { expected_version: expectedVersion, summary } },
    );
    return response.result;
  }

  async getConfigDiff(fromRevisionId: string, toRevisionId: string): Promise<ConfigChange[]> {
    const query = new URLSearchParams({
      from_revision_id: fromRevisionId,
      to_revision_id: toRevisionId,
    });
    const response = await this.json<{ changes: ConfigChange[] }>(`/v1/config/diff?${query}`);
    return response.changes;
  }

  async listConversationSessions(input: {
    includeArchived?: boolean;
    limit?: number;
    cursor?: string;
  } = {}): Promise<{ sessions: ConversationSession[]; nextCursor: string }> {
    const query = new URLSearchParams();
    query.set("include_archived", input.includeArchived ? "true" : "false");
    query.set("limit", String(input.limit ?? 50));
    if (input.cursor) query.set("cursor", input.cursor);
    const response = await this.json<{
      sessions: ConversationSession[];
      next_cursor?: string;
    }>(`/v1/conversations/sessions?${query}`);
    return { sessions: response.sessions, nextCursor: response.next_cursor ?? "" };
  }

  async getConversationSession(sessionHandle: string): Promise<ConversationSession> {
    const response = await this.json<{ session: ConversationSession }>(
      `/v1/conversations/sessions/${encodeURIComponent(sessionHandle)}`,
    );
    return response.session;
  }

  async updateConversationSession(
    sessionHandle: string,
    changes: { title?: string; state?: "active" | "archived"; expectedRevision: number },
  ): Promise<ConversationSession> {
    const response = await this.json<{ session: ConversationSession }>(
      `/v1/conversations/sessions/${encodeURIComponent(sessionHandle)}`,
      {
        method: "PATCH",
        body: {
          title: changes.title,
          state: changes.state,
          expected_revision: changes.expectedRevision,
        },
      },
    );
    return response.session;
  }

  async deleteConversationSession(sessionHandle: string): Promise<void> {
    await this.json(`/v1/conversations/sessions/${encodeURIComponent(sessionHandle)}`, {
      method: "DELETE",
    });
  }

  async gatewaySession(): Promise<{ expires_at: number }> {
    return this.json("/v1/session");
  }

  async listTasks(input: {
    state?: TaskDefinitionState;
    includeArchived?: boolean;
    limit?: number;
  } = {}): Promise<{ tasks: Task[] }> {
    const query = new URLSearchParams();
    if (input.state) query.set("state", input.state);
    if (input.includeArchived) query.set("include_archived", "true");
    query.set("limit", String(input.limit ?? 100));
    return this.json(`/v1/tasks?${query}`);
  }

  async getTask(taskId: string): Promise<Task> {
    const response = await this.json<{ task: Task }>(
      `/v1/tasks/${encodeURIComponent(taskId)}`,
    );
    return response.task;
  }

  async preflightTask(taskId: string): Promise<TaskPreflight> {
    return this.json<TaskPreflight>(
      `/v1/tasks/${encodeURIComponent(taskId)}/preflight`,
    );
  }

  async taskExecutionEvents(executionId: string, afterSeq = 0): Promise<TaskEvent[]> {
    const response = await this.json<{ events: TaskEvent[] }>(
      `/v1/task-executions/${encodeURIComponent(executionId)}/events?after_seq=${afterSeq}`,
    );
    return response.events;
  }

  async listChatTurns(
    sessionHandle: string,
    limit = 100,
    cursor = "",
  ): Promise<{ turns: ChatTurnSnapshot[]; nextCursor: string }> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set("cursor", cursor);
    const response = await this.json<{ turns: ChatTurnSnapshot[]; next_cursor?: string }>(
      `/v1/conversations/sessions/${encodeURIComponent(sessionHandle)}/turns?${query}`,
    );
    return { turns: response.turns, nextCursor: response.next_cursor ?? "" };
  }

  async getChatTurn(turnId: string): Promise<ChatTurnSnapshot> {
    const response = await this.json<{ turn: ChatTurnSnapshot }>(
      `/v1/conversations/turns/${encodeURIComponent(turnId)}`,
    );
    return response.turn;
  }

  async createChatTurn(input: {
    clientRequestId: string;
    sessionHandle: string;
    text?: string;
    attachments?: ArtifactInput[];
    toolsEnabled?: boolean;
    agentId?: string;
  }): Promise<ChatTurnSnapshot> {
    const response = await this.json<{ turn: ChatTurnSnapshot }>(
      `/v1/conversations/sessions/${encodeURIComponent(input.sessionHandle)}/turns`,
      {
        method: "POST",
        body: {
          client_request_id: input.clientRequestId,
          input: input.text ?? "",
          attachments: input.attachments ?? [],
          tools_enabled: input.toolsEnabled ?? true,
          agent_id: input.agentId,
        },
      },
    );
    return response.turn;
  }

  async cancelChatTurn(turnId: string): Promise<ChatTurnSnapshot> {
    const response = await this.json<{ turn: ChatTurnSnapshot }>(
      `/v1/conversations/turns/${encodeURIComponent(turnId)}/cancel`,
      { method: "POST" },
    );
    return response.turn;
  }

  async retryChatTurn(turnId: string): Promise<ChatTurnSnapshot> {
    const response = await this.json<{ turn: ChatTurnSnapshot }>(
      `/v1/conversations/turns/${encodeURIComponent(turnId)}/retry`,
      { method: "POST" },
    );
    return response.turn;
  }

  async resolveChatApproval(
    approvalId: string,
    approved: boolean,
  ): Promise<{ approval: ChatApproval; resolved: boolean }> {
    return this.json(
      `/v1/conversations/approvals/${encodeURIComponent(approvalId)}/resolve`,
      { method: "POST", body: { approved } },
    );
  }

  async resolveInteraction(
    interactionId: string,
    value: Record<string, unknown>,
  ): Promise<{ interaction: HumanInteraction; resolved: boolean }> {
    return this.json(`/v1/interactions/${encodeURIComponent(interactionId)}/resolve`, {
      method: "POST",
      body: { value },
    });
  }

  async createTask(input: {
    clientRequestId: string;
    title?: string;
    goal: string;
    attachments?: ArtifactInput[];
    toolsEnabled?: boolean;
    launchPolicy?: TaskLaunchPolicy;
    notificationPolicy?: Record<string, boolean>;
    agentId?: string;
  }): Promise<{ task: Task; execution: TaskExecution | null }> {
    return this.json("/v1/tasks", {
      method: "POST",
      body: {
        client_request_id: input.clientRequestId,
        title: input.title ?? "",
        goal: input.goal,
        attachments: input.attachments ?? [],
        tools_enabled: input.toolsEnabled ?? true,
        launch_policy: input.launchPolicy,
        notification_policy: input.notificationPolicy ?? {},
        agent_id: input.agentId,
      },
    });
  }

  async updateTask(
    taskId: string,
    changes: {
      title?: string;
      goal?: string;
      toolsEnabled?: boolean;
      launchPolicy?: TaskLaunchPolicy;
      notificationPolicy?: Record<string, boolean>;
      expectedRevision: number;
    },
  ): Promise<Task> {
    const response = await this.json<{ task: Task }>(
      `/v1/tasks/${encodeURIComponent(taskId)}`,
      {
        method: "PATCH",
        body: {
          title: changes.title,
          goal: changes.goal,
          tools_enabled: changes.toolsEnabled,
          launch_policy: changes.launchPolicy,
          notification_policy: changes.notificationPolicy,
          expected_revision: changes.expectedRevision,
        },
      },
    );
    return response.task;
  }

  async taskDefinitionCommand(
    taskId: string,
    command: "pause" | "resume" | "archive" | "restore",
  ): Promise<Task> {
    const response = await this.json<{ task: Task }>(
      `/v1/tasks/${encodeURIComponent(taskId)}/${command}`,
      { method: "POST" },
    );
    return response.task;
  }

  async deleteTask(taskId: string): Promise<void> {
    await this.json(`/v1/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
  }

  async executeTask(taskId: string): Promise<TaskExecution> {
    const response = await this.json<{ execution: TaskExecution }>(
      `/v1/tasks/${encodeURIComponent(taskId)}/execute`,
      { method: "POST" },
    );
    return response.execution;
  }

  async continueTask(input: {
    clientRequestId: string;
    taskId: string;
    text?: string;
    attachments?: ArtifactInput[];
  }): Promise<TaskExecution> {
    const response = await this.json<{ execution: TaskExecution }>(
      `/v1/tasks/${encodeURIComponent(input.taskId)}/continue`,
      {
        method: "POST",
        body: {
          client_request_id: input.clientRequestId,
          input: input.text ?? "",
          attachments: input.attachments ?? [],
        },
      },
    );
    return response.execution;
  }

  async listTaskExecutions(taskId: string): Promise<TaskExecution[]> {
    const response = await this.json<{ executions: TaskExecution[] }>(
      `/v1/tasks/${encodeURIComponent(taskId)}/executions?limit=200`,
    );
    return response.executions;
  }

  async getTaskExecution(executionId: string): Promise<TaskExecution> {
    const response = await this.json<{ execution: TaskExecution }>(
      `/v1/task-executions/${encodeURIComponent(executionId)}`,
    );
    return response.execution;
  }

  async taskExecutionCommand(
    executionId: string,
    command: "cancel" | "pause" | "resume" | "rerun",
    reason = "",
  ): Promise<TaskExecution | null> {
    const response = await this.json<Json>(
      `/v1/task-executions/${encodeURIComponent(executionId)}/${command}`,
      command === "rerun" ? { method: "POST" } : { method: "POST", body: { reason } },
    );
    return "execution" in response ? response.execution as TaskExecution : null;
  }

  async deleteTaskExecution(executionId: string): Promise<void> {
    await this.json(`/v1/task-executions/${encodeURIComponent(executionId)}`, {
      method: "DELETE",
    });
  }

  async resolveApproval(approvalId: string, approved: boolean): Promise<Json> {
    return this.json(`/v1/approvals/${encodeURIComponent(approvalId)}/resolve`, {
      method: "POST",
      body: { approved },
    });
  }

  async uploadArtifact(input: {
    sessionHandle: string;
    bytes: ArrayBuffer;
    mediaType: string;
    name: string;
    caption?: string;
  }): Promise<ArtifactInput> {
    const query = new URLSearchParams({
      session_handle: input.sessionHandle,
      name: input.name,
      caption: input.caption ?? "",
    });
    const response = await this.request(`/v1/artifacts?${query}`, {
      method: "POST",
      headers: { "Content-Type": input.mediaType },
      body: input.bytes,
    });
    const payload = (await response.json()) as { artifact: { artifact_id: string } };
    return { artifact_id: payload.artifact.artifact_id, caption: input.caption };
  }

  async searchArtifacts(input: {
    sessionHandle: string;
    query?: string;
    kind?: "image" | "file";
    limit?: number;
  }): Promise<{ artifacts: Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string; [key: string]: unknown }>; nextCursor: string }> {
    const query = new URLSearchParams({
      session_handle: input.sessionHandle,
      q: input.query ?? "",
      kind: input.kind ?? "",
      limit: String(input.limit ?? 50),
    });
    const response = await this.json<{ artifacts: Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string; [key: string]: unknown }>; next_cursor?: string }>(
      `/v1/artifacts?${query}`,
    );
    return { artifacts: response.artifacts, nextCursor: response.next_cursor ?? "" };
  }

  async downloadArtifact(
    sessionHandle: string,
    artifactId: string,
  ): Promise<{ bytes: Uint8Array; name: string; mediaType: string }> {
    const response = await this.request(
      `/v1/artifacts/${encodeURIComponent(artifactId)}?session_handle=${encodeURIComponent(sessionHandle)}`,
      {},
    );
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1];
    return {
      bytes: new Uint8Array(await response.arrayBuffer()),
      name: encodedName ? decodeURIComponent(encodedName) : "artifact",
      mediaType: response.headers.get("Content-Type") ?? "application/octet-stream",
    };
  }

  async transcribeArtifact(
    sessionHandle: string,
    artifactId: string,
  ): Promise<string> {
    const response = await this.json<{ result: { transcript: string } }>(
      `/v1/artifacts/${encodeURIComponent(artifactId)}/transcribe?session_handle=${encodeURIComponent(sessionHandle)}`,
      { method: "POST" },
    );
    return response.result.transcript;
  }

  async runtimeStatus(sessionHandle: string): Promise<Json> {
    return this.json(
      `/v1/runtime/status?session_handle=${encodeURIComponent(sessionHandle)}`,
    );
  }

  async tools(sessionHandle: string): Promise<Json> {
    return this.json(`/v1/tools?session_handle=${encodeURIComponent(sessionHandle)}`);
  }

  async listMcpResources(): Promise<MCPResourceCatalogItem[]> {
    const response = await this.json<{ result?: { resources?: MCPResourceCatalogItem[] } }>(
      "/v1/mcp/resources",
    );
    return response.result?.resources ?? [];
  }

  async deviceAudit(afterId = 0): Promise<Json> {
    return this.json(`/v1/device/audit?after_id=${afterId}&limit=100`);
  }

  async pollEvents(afterId = 0, limit = 100): Promise<PrincipalTaskEvent[]> {
    const response = await this.json<{ events: PrincipalTaskEvent[] }>(
      `/v1/events/poll?after_id=${afterId}&limit=${limit}`,
    );
    return response.events;
  }

  async latestAndroidRelease(): Promise<AndroidRelease> {
    return this.json("/v1/mobile/releases/android/latest");
  }

  async revokeCurrentDevice(): Promise<void> {
    await this.json("/v1/device", { method: "DELETE" });
  }

  private async json<T>(
    path: string,
    options: {
      method?: string;
      body?: Json;
      authenticated?: boolean;
    } = {},
  ): Promise<T> {
    const response = await this.request(path, {
      method: options.method,
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
    }, options.authenticated ?? true);
    const raw = await response.text();
    if (!raw.trim()) throw new GatewayError(502, "invalid_response");
    try {
      return JSON.parse(raw) as T;
    } catch {
      throw new GatewayError(502, "invalid_response");
    }
  }

  private async request(
    path: string,
    init: RequestInit,
    authenticated = true,
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    if (authenticated) {
      if (!this.token) throw new GatewayError(401, "missing_session");
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const timeoutMs = isLongRequest(path, init.method) ? LONG_REQUEST_TIMEOUT_MS : DEFAULT_REQUEST_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const abortExternal = () => controller.abort();
    init.signal?.addEventListener("abort", abortExternal, { once: true });
    let response: Response;
    try {
      response = await this.transport.request(this.baseUrl, path, {
        ...init,
        headers,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
      init.signal?.removeEventListener("abort", abortExternal);
    }
    if (!response.ok) {
      let code = `http_${response.status}`;
      let message = "";
      try {
        const payload = (await response.json()) as {
          error?: string;
          message?: string;
          preflight?: { checks?: Array<{ status?: string; detail?: string }> };
        };
        code = payload.error ?? code;
        const blocked = payload.preflight?.checks
          ?.filter((check) => check.status === "blocked" && check.detail)
          .map((check) => check.detail as string)
          .join("；");
        message = blocked || payload.message || "";
      } catch {
        // The status remains sufficient when the peer did not return JSON.
      }
      throw new GatewayError(response.status, code, message);
    }
    return response;
  }
}

function isLongRequest(path: string, method?: string): boolean {
  if (method === "POST" && (path.includes("/turns") || path.includes("/execute") || path.includes("/continue") || path.includes("/artifacts"))) return true;
  return path.includes("/transcribe") || path.includes("/rerun");
}

type Challenge = {
  challenge_id: string;
  nonce: string;
  expires_at: number;
};

type PairComplete = {
  grant_id: string;
  grant_secret: string;
  challenge_id: string;
  nonce: string;
  display_name: string;
  public_key: string;
  signature: string;
};

type AuthComplete = {
  device_id: string;
  challenge_id: string;
  nonce: string;
  signature: string;
};

export function parsePairingPayload(raw: string, now = Date.now() / 1000): PairingPayload {
  const parsed = JSON.parse(raw) as Partial<PairingPayload>;
  if (
    parsed.version !== "v3" ||
    (parsed.transport !== "direct" && parsed.transport !== "relay") ||
    typeof parsed.gateway_url !== "string" ||
    !/^https?:\/\//.test(parsed.gateway_url) ||
    typeof parsed.node_id !== "string" ||
    typeof parsed.node_signing_public_key !== "string" ||
    typeof parsed.node_configuration_public_key !== "string" ||
    typeof parsed.grant_id !== "string" ||
    typeof parsed.grant_secret !== "string" ||
    parsed.grant_secret.length < 32 ||
    typeof parsed.expires_at !== "number" ||
    parsed.expires_at <= now
  ) {
    throw new Error("配对信息无效或已过期");
  }
  return parsed as PairingPayload;
}
