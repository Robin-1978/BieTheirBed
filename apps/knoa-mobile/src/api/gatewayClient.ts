import type {
  AndroidRelease,
  ArtifactInput,
  GatewaySession,
  PairingPayload,
  TaskSnapshot,
  TaskEvent,
  TaskState,
} from "./models";

type Json = Record<string, unknown>;

export class GatewayError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`Gateway request failed: ${code}`);
  }
}

export class GatewayClient {
  constructor(
    readonly baseUrl: string,
    private readonly token: string | null = null,
  ) {}

  authenticated(token: string): GatewayClient {
    return new GatewayClient(this.baseUrl, token);
  }

  async pairChallenge(grantId: string): Promise<Challenge> {
    return this.json("/v1/pair/challenge", {
      method: "POST",
      body: { grant_id: grantId },
      authenticated: false,
    });
  }

  async pairComplete(input: PairComplete): Promise<{ device_id: string; principal_id: string }> {
    return this.json("/v1/pair/complete", {
      method: "POST",
      body: input,
      authenticated: false,
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

  async createSession(): Promise<string> {
    const response = await this.json<{ session_handle: string }>("/v1/sessions", {
      method: "POST",
    });
    return response.session_handle;
  }

  async gatewaySession(): Promise<{ expires_at: number }> {
    return this.json("/v1/session");
  }

  async listTasks(input: {
    sessionHandle?: string;
    state?: TaskState;
    limit?: number;
    cursor?: string;
    kind?: "all" | "chat" | "task";
  } = {}): Promise<{ tasks: TaskSnapshot[]; next_cursor: string }> {
    const query = new URLSearchParams();
    if (input.sessionHandle) query.set("session_handle", input.sessionHandle);
    if (input.state) query.set("state", input.state);
    if (input.kind) query.set("kind", input.kind);
    query.set("limit", String(input.limit ?? 50));
    if (input.cursor) query.set("cursor", input.cursor);
    return this.json(`/v1/tasks?${query}`);
  }

  async getTask(taskId: string): Promise<TaskSnapshot> {
    const response = await this.json<{ task: TaskSnapshot }>(
      `/v1/tasks/${encodeURIComponent(taskId)}`,
    );
    return response.task;
  }

  async taskEvents(taskId: string, afterSeq = 0): Promise<TaskEvent[]> {
    const response = await this.json<{ events: TaskEvent[] }>(
      `/v1/tasks/${encodeURIComponent(taskId)}/events?after_seq=${afterSeq}`,
    );
    return response.events;
  }

  async createTask(input: {
    sessionHandle: string;
    text?: string;
    attachments?: ArtifactInput[];
    toolsEnabled?: boolean;
    kind?: "chat" | "task";
  }): Promise<{ task_id: string; state: TaskState }> {
    return this.json("/v1/tasks", {
      method: "POST",
      body: {
        session_handle: input.sessionHandle,
        input: input.text ?? "",
        attachments: input.attachments ?? [],
        tools_enabled: input.toolsEnabled ?? true,
        kind: input.kind ?? "chat",
      },
    });
  }

  async taskCommand(
    taskId: string,
    command: "cancel" | "pause" | "resume" | "retry",
    reason = "",
  ): Promise<Json> {
    return this.json(
      `/v1/tasks/${encodeURIComponent(taskId)}/${command}`,
      { method: "POST", body: { reason } },
    );
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

  async deviceAudit(afterId = 0): Promise<Json> {
    return this.json(`/v1/device/audit?after_id=${afterId}&limit=100`);
  }

  async latestAndroidRelease(): Promise<AndroidRelease> {
    return this.json("/v1/mobile/releases/android/latest");
  }

  async registerPush(token: string): Promise<void> {
    await this.json("/v1/device/push", {
      method: "PUT",
      body: { provider: "expo", token },
    });
  }

  async unregisterPush(): Promise<void> {
    await this.json("/v1/device/push", { method: "DELETE" });
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
    return (await response.json()) as T;
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
    const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      headers,
    });
    if (!response.ok) {
      let code = `http_${response.status}`;
      try {
        const payload = (await response.json()) as { error?: string };
        code = payload.error ?? code;
      } catch {
        // The status remains sufficient when the peer did not return JSON.
      }
      throw new GatewayError(response.status, code);
    }
    return response;
  }
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
    parsed.version !== "v1" ||
    typeof parsed.gateway_url !== "string" ||
    !/^https?:\/\//.test(parsed.gateway_url) ||
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
