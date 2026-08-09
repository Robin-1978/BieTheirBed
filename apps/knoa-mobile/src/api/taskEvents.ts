import * as SecureStore from "expo-secure-store";
import EventSource from "react-native-sse";

import type { PrincipalTaskEvent } from "./models";
import { EventCursor } from "./eventCursor";

const CURSOR_KEY = "knoa.gateway.event-cursor.v1";
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
  "artifact",
  "context_compacted",
  "warning",
  "final_output",
  "completed",
  "failed",
  "cancelled",
] as const;
type TaskEventType = (typeof TASK_EVENT_TYPES)[number];

export type TaskEventSubscription = { close(): void };

export async function subscribeTaskEvents(input: {
  gatewayUrl: string;
  token: string;
  onEvent(event: PrincipalTaskEvent): void;
  onError(error: Error): void;
}): Promise<TaskEventSubscription> {
  const cursor = new EventCursor({
    async load() {
      return Number((await SecureStore.getItemAsync(CURSOR_KEY)) ?? 0);
    },
    async save(value) {
      await SecureStore.setItemAsync(CURSOR_KEY, String(value));
    },
  });
  const afterId = await cursor.initialize();
  const source = new EventSource<TaskEventType>(
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
      if (await cursor.accept(parsed.feed_event_id)) input.onEvent(parsed);
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Invalid Task event"));
    }
  };
  for (const eventType of TASK_EVENT_TYPES) {
    source.addEventListener(eventType, receive);
  }
  source.addEventListener("error", (event) => {
    input.onError(
      new Error(
        "message" in event && typeof event.message === "string"
          ? event.message
          : "Task event stream unavailable",
      ),
    );
  });
  return { close: () => source.close() };
}
