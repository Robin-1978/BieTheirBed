import { describe, expect, it, vi } from "vitest";

import type { ChatTurnSnapshot } from "./models";
import { subscribeChatTurn, type ChatTurnDelta } from "./chatTurns";

const { MockEventSource } = vi.hoisted(() => {
  type Listener = (event: any) => void;
  class MockEventSource {
    static instances: MockEventSource[] = [];
    listeners = new Map<string, Listener[]>();
    url: string;
    options: any;

    constructor(url: string, options: any) {
      this.url = url;
      this.options = options;
      MockEventSource.instances.push(this);
    }

    addEventListener(type: string, listener: Listener) {
      const list = this.listeners.get(type) ?? [];
      list.push(listener);
      this.listeners.set(type, list);
    }

    emit(type: string, event: any) {
      const list = this.listeners.get(type) ?? [];
      for (const listener of list) {
        listener(event);
      }
    }

    close = vi.fn();
  }
  return { MockEventSource };
});

vi.mock("react-native-sse", () => ({
  default: MockEventSource,
}));

function baseSnapshot(): ChatTurnSnapshot {
  return {
    turn_id: "turn-1",
    session_handle: "session-1",
    client_request_id: "req-1",
    user_input: "test query",
    attachments: [],
    tools_enabled: true,
    state: "running",
    reasoning: "",
    content: "Initial",
    final_output: "",
    artifacts: [],
    failure_code: "",
    cancel_requested: false,
    tool_steps: [],
    approvals: [],
    timeline: [],
    created_at: 100,
    updated_at: 100,
    finished_at: null,
    revision: 1,
  };
}

describe("subscribeChatTurn", () => {
  it("connects with format=delta query param and handles snapshot and delta events", () => {
    const onOpen = vi.fn();
    const onSnapshot = vi.fn();
    const onDelta = vi.fn();
    const onError = vi.fn();

    const sub = subscribeChatTurn({
      gatewayUrl: "http://127.0.0.1:9531",
      token: "test_token",
      turnId: "turn-1",
      onOpen,
      onSnapshot,
      onDelta,
      onError,
    });

    const es = MockEventSource.instances.at(-1)!;
    expect(es.url).toBe("http://127.0.0.1:9531/v1/conversations/turns/turn-1/stream?format=delta");
    expect(es.options.headers.Authorization).toBe("Bearer test_token");

    // 1. open
    es.emit("open", {});
    expect(onOpen).toHaveBeenCalledTimes(1);

    // 2. snapshot
    const snap = baseSnapshot();
    es.emit("snapshot", { data: JSON.stringify({ turn: snap }) });
    expect(onSnapshot).toHaveBeenCalledWith(snap);

    // 3. delta
    const deltaPayload: ChatTurnDelta = {
      turn_id: "turn-1",
      field: "content",
      delta: " streaming words",
      revision: 2,
    };
    es.emit("delta", { data: JSON.stringify(deltaPayload) });
    expect(onDelta).toHaveBeenCalledWith(deltaPayload);
    // onSnapshot should also be called with the patched snapshot
    expect(onSnapshot).toHaveBeenCalledWith(
      expect.objectContaining({
        content: "Initial streaming words",
        revision: 2,
      }),
    );

    // 4. close
    sub.close();
    expect(es.close).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("handles malformed JSON gracefully through onError", () => {
    const onError = vi.fn();
    subscribeChatTurn({
      gatewayUrl: "http://127.0.0.1:9531",
      token: "test_token",
      turnId: "turn-2",
      onSnapshot: vi.fn(),
      onError,
    });

    const es = MockEventSource.instances.at(-1)!;
    es.emit("snapshot", { data: "invalid json {" });
    expect(onError).toHaveBeenCalledWith(expect.any(Error));

    es.emit("delta", { data: "invalid delta {" });
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
  });

  it("detects delta revision gap and triggers onError to recover snapshot", () => {
    const onError = vi.fn();
    const onSnapshot = vi.fn();
    subscribeChatTurn({
      gatewayUrl: "http://127.0.0.1:9531",
      token: "test_token",
      turnId: "turn-3",
      onSnapshot,
      onError,
    });

    const es = MockEventSource.instances.at(-1)!;
    const snap = baseSnapshot(); // revision 1
    es.emit("snapshot", { data: JSON.stringify({ turn: snap }) });

    // Emitting revision 3 (skipping revision 2) should trigger onError
    es.emit("delta", {
      data: JSON.stringify({
        turn_id: "turn-3",
        field: "content",
        delta: " dropped packet text",
        revision: 3,
      }),
    });

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("ChatTurn delta revision gap"),
      }),
    );
  });

  it("safely ignores duplicate or outdated delta revisions", () => {
    const onDelta = vi.fn();
    const onSnapshot = vi.fn();
    subscribeChatTurn({
      gatewayUrl: "http://127.0.0.1:9531",
      token: "test_token",
      turnId: "turn-4",
      onSnapshot,
      onDelta,
      onError: vi.fn(),
    });

    const es = MockEventSource.instances.at(-1)!;
    const snap = { ...baseSnapshot(), revision: 5 };
    es.emit("snapshot", { data: JSON.stringify({ turn: snap }) });

    // Outdated revision 4 should be ignored
    es.emit("delta", {
      data: JSON.stringify({
        turn_id: "turn-4",
        field: "content",
        delta: " stale text",
        revision: 4,
      }),
    });

    expect(onDelta).not.toHaveBeenCalled();
  });
});
