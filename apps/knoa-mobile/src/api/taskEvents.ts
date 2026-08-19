import EventSource from "react-native-sse";

import { loadEventCursor, storeEventCursor } from "@/security/deviceIdentity";
import type { PrincipalTaskEvent } from "./models";
import type { GatewayClient } from "./gatewayClient";
import { EventCursor } from "./eventCursor";
export { isPresentationTaskEvent, shouldRefreshExecution } from "./taskEventPolicy";

const TASK_EVENT_TYPES = [
  "task_created",
  "state_changed",
  "reasoning_delta",
  "content_delta",
  "plan",
  "tool_call",
  "tool_result",
  "approval_requested",
  "approval_resolved",
  "interaction_requested",
  "interaction_resolved",
  "artifact",
  "context_compacted",
  "warning",
  "final_output",
  "completed",
  "failed",
  "cancelled",
] as const;
export type TaskEventType = (typeof TASK_EVENT_TYPES)[number];

export type TaskEventSubscription = { close(): void };

export async function subscribeTaskEvents(input: {
  client: GatewayClient;
  gatewayUrl: string;
  token: string;
  onEvent(event: PrincipalTaskEvent): void;
  onError(error: Error): void;
}): Promise<TaskEventSubscription> {
  const cursor = new EventCursor({
    async load() {
      return loadEventCursor();
    },
    async save(value) {
      await storeEventCursor(value);
    },
  });
  const afterId = await cursor.initialize();
  let closed = false;
  let source: EventSource<TaskEventType> | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  const accept = async (parsed: PrincipalTaskEvent) => {
    if (await cursor.accept(parsed.feed_event_id)) input.onEvent(parsed);
  };
  const poll = async () => {
    if (closed) return;
    try {
      const events = await input.client.pollEvents(cursor.current(), 100);
      for (const event of events) await accept(event);
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Task event polling unavailable"));
    } finally {
      if (!closed) pollTimer = setTimeout(() => void poll(), 2000);
    }
  };
  if (input.client.transportMode() !== "direct") {
    void poll();
    return {
      close: () => {
        closed = true;
        if (pollTimer) clearTimeout(pollTimer);
      },
    };
  }
  source = new EventSource<TaskEventType>(
    `${input.gatewayUrl.replace(/\/$/, "")}/v1/events?after_id=${afterId}`,
    {
      headers: { Authorization: `Bearer ${input.token}` },
      pollingInterval: 3000,
    },
  );
  const receive = async (message: { data?: string | null }) => {
    if (!message.data) return;
    try {
      const parsed = JSON.parse(message.data) as PrincipalTaskEvent;
      await accept(parsed);
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Invalid Task event"));
    }
  };
  for (const eventType of TASK_EVENT_TYPES) {
    source.addEventListener(eventType, receive);
  }
  // Some native XHR implementations/proxies omit the custom SSE event field.
  // The server payload is still valid and the cursor de-duplicates replays.
  source.addEventListener("message", receive);
  source.addEventListener("error", (event) => {
    input.onError(
      new Error(
        "message" in event && typeof event.message === "string"
          ? event.message
          : "Task event stream unavailable",
      ),
    );
    source?.close();
    source = null;
    void poll();
  });
  return {
    close: () => {
      closed = true;
      source?.close();
      if (pollTimer) clearTimeout(pollTimer);
    },
  };
}
