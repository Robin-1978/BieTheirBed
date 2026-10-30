import { describe, expect, it, vi } from "vitest";

import { GatewayError } from "../api/gatewayClient";
import type { CodingSessionDetail, CodingSessionSummary } from "../api/models";
import {
  type CodingSessionKey,
  canInterruptCodingSession,
  codingSessionErrorMessage,
  interruptOutcomeMessage,
  loadCodingSessionSnapshot,
  sameCodingSession,
} from "./codingSessionState";

function summary(overrides: Partial<CodingSessionSummary> = {}): CodingSessionSummary {
  return {
    provider: "codex",
    external_session_id: "thread-a",
    title: "Fix search",
    directory: "/work/project",
    state: "idle",
    updated_at: 1,
    latest_user_message: "",
    latest_assistant_message: "",
    read_only: false,
    capabilities: ["read", "continue"],
    native_state: "",
    revision: "turn-1",
    ...overrides,
  };
}

function detail(overrides: Partial<CodingSessionSummary> = {}): CodingSessionDetail {
  return { session: summary(overrides), messages: [] };
}

const listing = (sessions: CodingSessionSummary[], providerErrors: Array<{ provider: string; error: string }> = []) =>
  vi.fn(async () => ({ sessions, nextCursor: "", providerErrors }));

describe("sameCodingSession", () => {
  it("matches only when provider and external session id both match", () => {
    const a: CodingSessionKey = { provider: "codex", external_session_id: "thread-a" };
    expect(sameCodingSession(a, { provider: "codex", external_session_id: "thread-a" })).toBe(true);
    expect(sameCodingSession(a, { provider: "opencode", external_session_id: "thread-a" })).toBe(false);
    expect(sameCodingSession(a, { provider: "codex", external_session_id: "thread-b" })).toBe(false);
  });

  it("treats a missing side as a different session", () => {
    const a: CodingSessionKey = { provider: "codex", external_session_id: "thread-a" };
    expect(sameCodingSession(a, null)).toBe(false);
    expect(sameCodingSession(undefined, a)).toBe(false);
    expect(sameCodingSession(null, null)).toBe(false);
  });
});

describe("canInterruptCodingSession", () => {
  it("is true only when the provider declares interrupt", () => {
    expect(canInterruptCodingSession(summary({ capabilities: ["read", "interrupt"], read_only: true }))).toBe(true);
    expect(canInterruptCodingSession(summary({ capabilities: ["read", "continue"] }))).toBe(false);
  });

  it("stays hidden for a running session owned by another client", () => {
    expect(canInterruptCodingSession(summary({ state: "running", capabilities: ["read"], read_only: true }))).toBe(false);
  });
});

describe("interruptOutcomeMessage", () => {
  it("is empty when a running task was interrupted", () => {
    expect(interruptOutcomeMessage(true)).toBe("");
  });

  it("explains the no-op instead of silently refreshing", () => {
    expect(interruptOutcomeMessage(false)).toMatch(/没有正在运行/);
  });
});

describe("loadCodingSessionSnapshot", () => {
  it("lists sessions without fetching a detail when nothing is selected", async () => {
    const getDetail = vi.fn(async (): Promise<CodingSessionDetail> => detail());

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail,
      selectedKey: () => null,
    });

    expect(snapshot.sessions).toHaveLength(1);
    expect(snapshot.detail).toBeNull();
    expect(snapshot.error).toBe("");
    expect(getDetail).not.toHaveBeenCalled();
  });

  it("returns the fresh detail of the selected session", async () => {
    const selected: CodingSessionKey = { provider: "codex", external_session_id: "thread-a" };
    const getDetail = vi.fn(async (): Promise<CodingSessionDetail> => detail({ revision: "turn-9" }));

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail,
      selectedKey: () => selected,
    });

    expect(getDetail).toHaveBeenCalledWith(selected);
    expect(snapshot.detail?.session.revision).toBe("turn-9");
  });

  it("drops a detail that arrives after the user closed the panel", async () => {
    let selected: CodingSessionKey | null = { provider: "codex", external_session_id: "thread-a" };

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail: async () => {
        selected = null;
        return detail();
      },
      selectedKey: () => selected,
    });

    expect(snapshot.detail).toBeNull();
  });

  it("drops a detail that arrives after the user opened another session", async () => {
    let selected: CodingSessionKey | null = { provider: "codex", external_session_id: "thread-a" };

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary(), summary({ external_session_id: "thread-b" })]),
      getDetail: async () => {
        selected = { provider: "codex", external_session_id: "thread-b" };
        return detail();
      },
      selectedKey: () => selected,
    });

    expect(snapshot.sessions).toHaveLength(2);
    expect(snapshot.detail).toBeNull();
  });

  it("keeps the session list and reports a detail failure of the selected session", async () => {
    const selected: CodingSessionKey = { provider: "codex", external_session_id: "thread-a" };

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail: async () => {
        throw new GatewayError(404, "not_found");
      },
      selectedKey: () => selected,
    });

    expect(snapshot.sessions).toHaveLength(1);
    expect(snapshot.detail).toBeNull();
    expect(snapshot.error).toBe("内容不存在或已经被删除");
  });

  it("explains a 403 on the selected session instead of the generic gateway text", async () => {
    const selected: CodingSessionKey = { provider: "codex", external_session_id: "thread-a" };

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail: async () => {
        throw new GatewayError(403, "forbidden");
      },
      selectedKey: () => selected,
    });

    expect(snapshot.error).toMatch(/所有者/);
    expect(snapshot.error).toMatch(/工作目录/);
  });

  it("stays silent about a detail failure for a session the user already left", async () => {
    let selected: CodingSessionKey | null = { provider: "codex", external_session_id: "thread-a" };

    const snapshot = await loadCodingSessionSnapshot({
      list: listing([summary()]),
      getDetail: async () => {
        selected = null;
        throw new GatewayError(404, "not_found");
      },
      selectedKey: () => selected,
    });

    expect(snapshot.error).toBe("");
  });

  it("surfaces provider errors only when no session could be listed", async () => {
    const providerErrors = [{ provider: "opencode", error: "connection refused" }];

    const empty = await loadCodingSessionSnapshot({
      list: listing([], providerErrors),
      getDetail: async () => detail(),
      selectedKey: () => null,
    });
    const partial = await loadCodingSessionSnapshot({
      list: listing([summary()], providerErrors),
      getDetail: async () => detail(),
      selectedKey: () => null,
    });

    expect(empty.error).toBe("opencode: connection refused");
    expect(partial.error).toBe("");
  });

  it("propagates a failed listing so the caller can report it", async () => {
    await expect(
      loadCodingSessionSnapshot({
        list: async () => {
          throw new GatewayError(503, "unavailable");
        },
        getDetail: async () => detail(),
        selectedKey: () => null,
      }),
    ).rejects.toBeInstanceOf(GatewayError);
  });
});

describe("codingSessionErrorMessage", () => {
  it("explains a 403 as an owner or directory rejection for every action", () => {
    for (const action of ["open", "attach", "prompt", "interrupt"] as const) {
      const message = codingSessionErrorMessage(new GatewayError(403, "forbidden"), action, "fallback");
      expect(message).toMatch(/所有者/);
      expect(message).toMatch(/工作目录/);
    }
  });

  it("explains both attach conflicts instead of the generic gateway text", () => {
    const message = codingSessionErrorMessage(new GatewayError(409, "conflict"), "attach", "绑定失败");
    expect(message).toMatch(/已归属于另一个小诺会话/);
    expect(message).toMatch(/已绑定了别的编码会话/);
  });

  it("tells the user to bind first when continuing or interrupting conflicts", () => {
    const prompt = codingSessionErrorMessage(new GatewayError(409, "conflict"), "prompt", "继续会话失败");
    const interrupt = codingSessionErrorMessage(new GatewayError(409, "conflict"), "interrupt", "中断失败");
    expect(prompt).toMatch(/先绑定/);
    expect(prompt).toMatch(/桌面端/);
    expect(interrupt).toMatch(/先绑定/);
    expect(interrupt).toMatch(/可由本服务中断/);
  });

  it("keeps the gateway message for other failures", () => {
    const message = codingSessionErrorMessage(new GatewayError(503, "unavailable"), "prompt", "fallback");
    expect(message).toBe("小诺服务暂时不可用，请稍后重试");
  });

  it("uses the fallback when the cause carries no message", () => {
    expect(codingSessionErrorMessage("boom", "attach", "绑定失败")).toBe("绑定失败");
    expect(codingSessionErrorMessage(undefined, "interrupt", "中断失败")).toBe("中断失败");
    expect(codingSessionErrorMessage(new Error(""), "open", "无法读取会话详情")).toBe("无法读取会话详情");
  });
});
