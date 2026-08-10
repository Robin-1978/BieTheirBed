import EventSource from "react-native-sse";

import type { ChatTurnSnapshot } from "./models";

export type ChatTurnSubscription = { close(): void };

export function subscribeChatTurn(input: {
  gatewayUrl: string;
  token: string;
  turnId: string;
  onOpen?(): void;
  onSnapshot(turn: ChatTurnSnapshot): void;
  onError(error: Error): void;
}): ChatTurnSubscription {
  const source = new EventSource<"snapshot">(
    `${input.gatewayUrl.replace(/\/$/, "")}/v1/conversations/turns/${encodeURIComponent(input.turnId)}/stream`,
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
      input.onSnapshot(parsed.turn);
    } catch (error) {
      input.onError(error instanceof Error ? error : new Error("Invalid ChatTurn snapshot"));
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
