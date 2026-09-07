import EventSource from "react-native-sse";

import type { ChatTurnSnapshot } from "./models";

export type ChatTurnSubscription = { close(): void };

export type ChatTurnDelta = {
  turn_id: string;
  field: "content" | "reasoning";
  delta: string;
  revision: number;
};

export function subscribeChatTurn(input: {
  gatewayUrl: string;
  token: string;
  turnId: string;
  onOpen?(): void;
  onSnapshot(turn: ChatTurnSnapshot): void;
  onDelta?(delta: ChatTurnDelta): void;
  onError(error: Error): void;
}): ChatTurnSubscription {
  let latestSnapshot: ChatTurnSnapshot | null = null;
  const source = new EventSource<"snapshot" | "delta">(
    `${input.gatewayUrl.replace(/\/$/, "")}/v1/conversations/turns/${encodeURIComponent(input.turnId)}/stream?format=delta`,
    {
      headers: { Authorization: `Bearer ${input.token}` },
      pollingInterval: 3000,
    },
  );
  source.addEventListener("open", () => input.onOpen?.());
  source.addEventListener("snapshot", (message) => {
    if (!message.data) return;
    try {
      const parsed = JSON.parse(message.data) as { turn: ChatTurnSnapshot };
      latestSnapshot = parsed.turn;
      input.onSnapshot(parsed.turn);
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Invalid ChatTurn snapshot"));
    }
  });
  source.addEventListener("delta", (message) => {
    if (!message.data) return;
    try {
      const delta = JSON.parse(message.data) as ChatTurnDelta;
      if (input.onDelta) {
        input.onDelta(delta);
      }
      if (latestSnapshot) {
        latestSnapshot = {
          ...latestSnapshot,
          [delta.field]: (latestSnapshot[delta.field] ?? "") + delta.delta,
          revision: delta.revision,
          updated_at: Date.now() / 1000,
        };
        input.onSnapshot(latestSnapshot);
      }
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Invalid ChatTurn delta"));
    }
  });
  source.addEventListener("error", (event) => {
    input.onError(
      new Error(
        "message" in event && typeof event.message === "string"
          ? event.message
          : "ChatTurn stream unavailable",
      ),
    );
  });
  return { close: () => source.close() };
}
