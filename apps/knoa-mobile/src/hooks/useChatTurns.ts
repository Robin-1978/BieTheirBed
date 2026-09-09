import { useCallback, useEffect, useRef, useState } from "react";

import { ChatTurnWatcher } from "@/api/chatTurnWatcher";
import type { ChatTurnSnapshot } from "@/api/models";
import { TERMINAL_STATES } from "@/components/chat";
import { mergeConversationTurns } from "@/storage/conversationMerge";
import { GatewayError, type GatewayClient } from "@/api/gatewayClient";
import type { GatewayConnection } from "@/state/types";

export interface UseChatTurnsOptions {
  getConnection: () => GatewayConnection | null;
  runAuthenticated: <T>(operation: (client: GatewayClient) => Promise<T>) => Promise<T>;
  sessionHandle: string;
  hasClient: boolean;
  onSessionReplaced?: () => Promise<void>;
  showFeedback: (text: string, tone?: "info" | "success" | "warning" | "error") => void;
  t: (key: any, params?: any) => string;
}

export function useChatTurns({
  getConnection,
  runAuthenticated,
  sessionHandle,
  hasClient,
  onSessionReplaced,
  showFeedback,
  t,
}: UseChatTurnsOptions) {
  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [nextTurnCursor, setNextTurnCursor] = useState<string | null>(null);
  const refreshPromiseRef = useRef<Promise<void> | null>(null);
  const sessionHandleRef = useRef(sessionHandle);
  sessionHandleRef.current = sessionHandle;

  const [turnWatcher] = useState(() => new ChatTurnWatcher({
    connection: getConnection,
    fetchSnapshot: (turnId) => runAuthenticated((client) => client.getChatTurn(turnId)),
    onSnapshot: (snapshot) => {
      setTurns((current) => mergeConversationTurns(current, [snapshot]));
    },
    onUnavailable: () => showFeedback(t("chat.streamUnavailable"), "error"),
  }));

  const watchTurn = useCallback((turnId: string) => {
    turnWatcher.watch(turnId);
  }, [turnWatcher]);

  useEffect(() => () => {
    turnWatcher.closeAll();
  }, [turnWatcher]);

  const refresh = useCallback((): Promise<void> => {
    if (!hasClient || !sessionHandle) return Promise.resolve();
    if (refreshPromiseRef.current) return refreshPromiseRef.current;
    const currentSession = sessionHandle;
    const pending = (async () => {
      try {
        const history = await runAuthenticated(
          (client) => client.listChatTurns(currentSession, 100),
        );
        if (sessionHandleRef.current !== currentSession) return;
        setTurns((current) => mergeConversationTurns(current, history.turns));
        setNextTurnCursor(history.nextCursor);
        for (const turn of history.turns) {
          if (!TERMINAL_STATES.has(turn.state)) watchTurn(turn.turn_id);
        }
      } catch (error) {
        if (error instanceof GatewayError && error.status === 404) {
          if (onSessionReplaced) await onSessionReplaced();
          showFeedback(t("chat.sessionReplaced"), "warning");
          return;
        }
        showFeedback(t("chat.syncUnavailable"), "warning");
      }
    })();
    const tracked = pending.finally(() => {
      if (refreshPromiseRef.current === tracked) refreshPromiseRef.current = null;
    });
    refreshPromiseRef.current = tracked;
    return tracked;
  }, [hasClient, onSessionReplaced, runAuthenticated, sessionHandle, showFeedback, t, watchTurn]);

  return {
    turns,
    setTurns,
    nextTurnCursor,
    setNextTurnCursor,
    watchTurn,
    refresh,
    turnWatcher,
  };
}
