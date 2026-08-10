import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatTurnSnapshot } from "./models";
import { ChatTurnWatcher } from "./chatTurnWatcher";

function snapshot(state: ChatTurnSnapshot["state"] = "running"): ChatTurnSnapshot {
  return {
    turn_id: "turn-1",
    session_handle: "session-1",
    client_request_id: "request-1",
    user_input: "你好",
    attachments: [],
    tools_enabled: true,
    state,
    reasoning: "",
    content: "",
    final_output: state === "completed" ? "你好" : "",
    artifacts: [],
    failure_code: "",
    cancel_requested: false,
    tool_steps: [],
    approvals: [],
    timeline: [],
    created_at: 1,
    updated_at: 1,
    finished_at: state === "completed" ? 2 : null,
    revision: 1,
  };
}

describe("ChatTurnWatcher", () => {
  afterEach(() => vi.useRealTimers());

  it("recovers a transient stream failure without reporting an interruption", async () => {
    vi.useFakeTimers();
    const streams: Array<{ error(error: Error): void }> = [];
    const onSnapshot = vi.fn();
    const onUnavailable = vi.fn();
    const watcher = new ChatTurnWatcher({
      connection: () => ({ gatewayUrl: "https://knoa.example.com", token: "token" }),
      fetchSnapshot: vi.fn(async () => snapshot()),
      onSnapshot,
      onUnavailable,
      retryDelays: [10],
      subscribe: (input) => {
        streams.push({ error: input.onError });
        input.onOpen();
        return { close: vi.fn() };
      },
    });

    watcher.watch("turn-1");
    streams[0]?.error(new Error("network changed"));
    await vi.advanceTimersByTimeAsync(10);

    expect(onSnapshot).toHaveBeenCalledWith(expect.objectContaining({ turn_id: "turn-1" }));
    expect(onUnavailable).not.toHaveBeenCalled();
    expect(streams).toHaveLength(2);
    watcher.closeAll();
  });

  it("stops reconnecting after the REST snapshot reaches a terminal state", async () => {
    vi.useFakeTimers();
    let failStream: ((error: Error) => void) | undefined;
    const subscribe = vi.fn((input: Parameters<NonNullable<ConstructorParameters<typeof ChatTurnWatcher>[0]["subscribe"]>>[0]) => {
      failStream = input.onError;
      return { close: vi.fn() };
    });
    const watcher = new ChatTurnWatcher({
      connection: () => ({ gatewayUrl: "https://knoa.example.com", token: "token" }),
      fetchSnapshot: vi.fn(async () => snapshot("completed")),
      onSnapshot: vi.fn(),
      onUnavailable: vi.fn(),
      retryDelays: [10],
      subscribe,
    });

    watcher.watch("turn-1");
    failStream?.(new Error("stream ended"));
    await vi.runAllTimersAsync();

    expect(subscribe).toHaveBeenCalledOnce();
  });
});
