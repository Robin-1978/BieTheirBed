import { describe, expect, it, vi } from "vitest";

import { GatewayError } from "../api/gatewayClient";
import type { CodingSessionMessage, CodingSessionSummary } from "../api/models";
import {
  canInterruptCodingSession,
  codingMessageRoleLabel,
  codingSessionErrorMessage,
  codingSessionRouteKey,
  codingSessionStateLabel,
  interruptOutcomeMessage,
  loadCodingSessionList,
  readableCodingMessages,
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

function message(overrides: Partial<CodingSessionMessage> = {}): CodingSessionMessage {
  return { message_id: "m1", role: "assistant", text: "hello", created_at: 1, ...overrides };
}

const listing = (sessions: CodingSessionSummary[], providerErrors: Array<{ provider: string; error: string }> = []) =>
  vi.fn(async () => ({ sessions, nextCursor: "", providerErrors }));

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

describe("loadCodingSessionList", () => {
  it("returns the listed sessions without an error", async () => {
    const snapshot = await loadCodingSessionList({ list: listing([summary(), summary({ external_session_id: "thread-b" })]) });

    expect(snapshot.sessions).toHaveLength(2);
    expect(snapshot.error).toBe("");
  });

  it("surfaces provider errors only when no session could be listed", async () => {
    const providerErrors = [{ provider: "opencode", error: "connection refused" }];

    const empty = await loadCodingSessionList({ list: listing([], providerErrors) });
    const partial = await loadCodingSessionList({ list: listing([summary()], providerErrors) });

    expect(empty.error).toBe("opencode: connection refused");
    expect(partial.error).toBe("");
  });

  it("propagates a failed listing so the caller can report it", async () => {
    await expect(
      loadCodingSessionList({
        list: async () => {
          throw new GatewayError(503, "unavailable");
        },
      }),
    ).rejects.toBeInstanceOf(GatewayError);
  });
});

describe("codingSessionStateLabel", () => {
  it("names every state the gateway reports", () => {
    expect(codingSessionStateLabel("running")).toBe("运行中 · 只读");
    expect(codingSessionStateLabel("interrupted")).toBe("已中断");
    expect(codingSessionStateLabel("failed")).toBe("失败");
    expect(codingSessionStateLabel("idle")).toBe("空闲，可继续");
    expect(codingSessionStateLabel("unknown")).toBe("未知");
  });

  it("falls back to unknown for a state a newer gateway may add", () => {
    expect(codingSessionStateLabel("queued")).toBe("未知");
  });
});

describe("codingMessageRoleLabel", () => {
  it("names the two speakers and passes anything else through", () => {
    expect(codingMessageRoleLabel("user")).toBe("你");
    expect(codingMessageRoleLabel("assistant")).toBe("助手");
    expect(codingMessageRoleLabel("tool")).toBe("tool");
  });
});

describe("codingSessionRouteKey", () => {
  it("reads provider and session id from the router params", () => {
    expect(codingSessionRouteKey({ provider: "opencode", id: "ses_1" })).toEqual({
      provider: "opencode",
      external_session_id: "ses_1",
    });
  });

  it("takes the first value when the router hands over an array", () => {
    expect(codingSessionRouteKey({ provider: ["codex", "x"], id: ["thread-a", "y"] })).toEqual({
      provider: "codex",
      external_session_id: "thread-a",
    });
  });

  it("is null when either part is missing or blank, so the screen can refuse to load", () => {
    expect(codingSessionRouteKey({})).toBeNull();
    expect(codingSessionRouteKey({ provider: "codex" })).toBeNull();
    expect(codingSessionRouteKey({ id: "thread-a" })).toBeNull();
    expect(codingSessionRouteKey({ provider: "  ", id: "thread-a" })).toBeNull();
    expect(codingSessionRouteKey({ provider: "codex", id: [] })).toBeNull();
  });
});

describe("readableCodingMessages", () => {
  it("keeps the messages that carry text, in order, and counts the hidden steps", () => {
    const result = readableCodingMessages([
      message({ message_id: "m1", role: "user", text: "fix it" }),
      message({ message_id: "m2", role: "assistant", text: "" }),
      message({ message_id: "m3", role: "assistant", text: "done" }),
      message({ message_id: "m4", role: "assistant", text: "" }),
    ]);

    expect(result.messages.map((item) => item.message_id)).toEqual(["m1", "m3"]);
    expect(result.hidden).toBe(2);
  });

  it("treats whitespace-only text as empty", () => {
    const result = readableCodingMessages([message({ text: " \n\t " }), message({ message_id: "m2", text: "ok" })]);

    expect(result.messages.map((item) => item.message_id)).toEqual(["m2"]);
    expect(result.hidden).toBe(1);
  });

  it("keeps the whole conversation instead of cutting it to the last few messages", () => {
    const many = Array.from({ length: 60 }, (_, index) => message({ message_id: `m${index}`, text: `text ${index}` }));

    const result = readableCodingMessages(many);

    expect(result.messages).toHaveLength(60);
    expect(result.hidden).toBe(0);
  });

  it("reports nothing visible when every message is empty", () => {
    const result = readableCodingMessages([message({ text: "" }), message({ message_id: "m2", text: "" })]);

    expect(result.messages).toEqual([]);
    expect(result.hidden).toBe(2);
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
