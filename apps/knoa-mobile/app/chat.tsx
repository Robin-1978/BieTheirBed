import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import * as Crypto from "expo-crypto";
import { File, Paths } from "expo-file-system";
import * as Linking from "expo-linking";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  FlatList,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  Platform,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type {
  ChatApproval,
  ChatTurnSnapshot,
  DesktopGlanceRecord,
  HumanInteraction,
} from "@/api/models";
import type { ActionCardInvocation } from "@/components/action_card";
import { AgentSelector } from "@/components/AgentSelector";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { DesktopGlanceModal } from "@/components/DesktopGlanceModal";
import {
  AgentPickerSheet,
  ChatComposer,
  ChatFeedbackBanner,
  ChatTurnItem,
  ClipboardSuggestionPill,
  PendingTurnItem,
  ProactiveDeck,
  type ChatListItem,
  type ClipboardSuggestion,
  type Feedback,
  type InputMode,
  type PendingAttachment,
  type PendingChatTurn,
  TERMINAL_STATES,
  TIMESTAMP_GROUP_MS,
  agentReasonLabel,
} from "@/components/chat";
import { presentNodeName } from "@/presentation/nodePresentation";
import { calculateTotalSavedHours } from "@/components/trophyPresentation";
import { loadCapabilityCache, type CapabilityCache } from "@/storage/capabilityCache";
import {
  assistantArtifactItems,
  resolveAssistantArtifactFile,
  type AssistantArtifactItem,
  type ResolvedArtifactFile,
} from "@/api/chatArtifacts";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { GatewayError, type GatewayClient } from "@/api/gatewayClient";
import { agentImageSupport } from "@/media/agentImageSupport";
import { shouldResetConversation } from "@/state/conversationTransition";
import { useChatTurns } from "@/hooks/useChatTurns";
import { useClipboardSuggestion } from "@/hooks/useClipboardSuggestion";
import { useLiveGlance } from "@/hooks/useLiveGlance";
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import { useI18n } from "@/i18n";
import { useConnection, useFleet, useSession } from "@/state/GatewayProvider";
import {
  loadConversationCache,
  storeConversationCache,
} from "@/storage/conversationCache";
import { mergeConversationTurns } from "@/storage/conversationMerge";
import {
  loadConversationDraft,
  removeConversationDraft,
  storeConversationDraft,
} from "@/security/conversationDrafts";
import { colors, radii, shadows, spacing } from "@/theme";

const SCROLL_OFFSET_THRESHOLD = 80;

export default function ChatScreen() {
  const session = useSession();
  const fleet = useFleet();
  const connection = useConnection();
  const insets = useSafeAreaInsets();
  const sessionRef = useRef(session);
  sessionRef.current = session;

  // Keep hook callbacks stable. Passing an inline wrapper here causes
  // useChatTurns.refresh to be recreated on every render, which in turn
  // re-runs useFocusEffect and can issue an unbounded refresh storm.
  // The ref only tracks the session slice now, so transport/fleet updates
  // no longer re-render this screen through the gateway object.
  const runAuthenticated = useCallback(
    <T,>(operation: (client: GatewayClient) => Promise<T>) => (
      sessionRef.current.runAuthenticated(operation)
    ),
    [],
  );

  const { locale, t } = useI18n();
  const params = useLocalSearchParams<{
    workspaceId?: string;
    workspaceName?: string;
    nodeId?: string;
    capturedUri?: string;
    capturedName?: string;
    capturedMediaType?: string;
    capturedArtifactId?: string;
    capturedSessionHandle?: string;
    prefill?: string;
  }>();

  const [pendingTurn, setPendingTurn] = useState<PendingChatTurn | null>(null);
  const [queuedTurn, setQueuedTurn] = useState<PendingChatTurn | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const [text, setText] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);

  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [resolvingApproved, setResolvingApproved] = useState<boolean | null>(null);
  const [resolvingInteraction, setResolvingInteraction] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [validatingInput, setValidatingInput] = useState(false);

  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const currentNodeId = fleet.nodeId || stringParam(params.nodeId);
  const currentNode = fleet.nodes.find((item) => item.nodeId === currentNodeId);
  const [nodeCapability, setNodeCapability] = useState<CapabilityCache | null>(null);

  useEffect(() => {
    if (!currentNodeId) return;
    let active = true;
    void loadCapabilityCache(currentNodeId).then((cached) => {
      if (active) setNodeCapability(cached);
    });
    return () => { active = false; };
  }, [currentNodeId]);

  const listRef = useRef<FlatList<ChatListItem>>(null);
  const followLatest = useRef(true);
  const userDragging = useRef(false);
  const scrollIntent = useRef<"instant" | "smooth">("instant");
  const initialScrollPending = useRef(false);
  const scrollFrame = useRef<number | null>(null);
  const displayedSession = useRef(session.sessionHandle);

  const {
    glance: liveGlance,
    visible: glanceModalVisible,
    refreshing: glanceRefreshing,
    open: handleOpenLiveGlance,
    refresh: handleRefreshLiveGlance,
    close: handleCloseGlance,
  } = useLiveGlance({ hasClient: Boolean(session.client), runAuthenticated });
  const [savedHours, setSavedHours] = useState(0);
  const [completedTasksCount, setCompletedTasksCount] = useState(0);

  useEffect(() => {
    if (connection.status !== "ready") return;
    let active = true;
    void session
      .runAuthenticated((client) => client.listTasks({ includeArchived: true, limit: 100 }))
      .then((res) => {
        if (!active) return;
        const taskList = res.tasks || [];
        setSavedHours(calculateTotalSavedHours(taskList));
        setCompletedTasksCount(
          taskList.filter((item) => item.latest_execution_state === "completed" || item.state === "archived").length
        );
      })
      .catch(() => {});
    return () => { active = false; };
  }, [connection.status, fleet.nodeId]);

  const showFeedback = useCallback((value: string, tone: Feedback["tone"] = "info") => {
    setFeedback({ text: value, tone });
  }, []);

  useEffect(() => {
    if (!feedback) return;
    const duration = feedback.tone === "error" || feedback.tone === "warning" ? 5000 : 3000;
    const timeout = setTimeout(() => setFeedback(null), duration);
    return () => clearTimeout(timeout);
  }, [feedback]);

  useEffect(() => () => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
  }, []);

  const {
    clipboardSuggestion,
    copyMessage,
    checkClipboard,
    dismissSuggestion: dismissClipboardSuggestion,
  } = useClipboardSuggestion({
    text,
    showFeedback,
    copiedMessageText: t("chat.messageCopied"),
  });

  const {
    turns,
    setTurns,
    nextTurnCursor,
    setNextTurnCursor,
    watchTurn,
    refresh,
    turnWatcher,
  } = useChatTurns({
    getConnection: () => sessionRef.current.connection(),
    runAuthenticated,
    sessionHandle: session.sessionHandle,
    hasClient: Boolean(session.client),
    onSessionReplaced: session.newConversation,
    showFeedback,
    t,
  });

  const {
    recordingState,
    transcribing,
    toggleRecording,
  } = useVoiceRecorder({
    runAuthenticated,
    ensureConversation: session.ensureConversation,
    hasClient: Boolean(session.client),
    onTranscription: (transcript) => setText((current) => current ? `${current}\n${transcript}` : transcript),
    showFeedback,
    t,
  });

  useEffect(() => {
    let active = true;
    const sessionHandle = session.sessionHandle;
    const previousSession = displayedSession.current;
    displayedSession.current = sessionHandle;
    const switchedConversation = shouldResetConversation(previousSession, sessionHandle);

    if (switchedConversation) {
      setTurns([]);
      setPendingTurn(null);
      setNextTurnCursor("");
      initialScrollPending.current = Boolean(sessionHandle);
      followLatest.current = true;
      setShowJumpToLatest(false);
      scrollIntent.current = "instant";
      turnWatcher.closeAll();
    }
    if (sessionHandle) {
      void loadConversationCache(sessionHandle).then((cached) => {
        if (active && sessionRef.current.sessionHandle === sessionHandle) {
          setTurns((current) => mergeConversationTurns(current, cached));
        }
      }).finally(() => {
        if (active && sessionRef.current.sessionHandle === sessionHandle) void refresh();
      });
    }
    return () => { active = false; };
  }, [session.sessionHandle, refresh, turnWatcher]);

  useEffect(() => {
    if (!session.client || session.sessionHandle) return;
    void session.ensureConversation().catch(() => undefined);
  }, [session.client, session.ensureConversation, session.sessionHandle]);

  useEffect(() => {
    if (!session.sessionHandle || !turns.length) return;
    const timeout = setTimeout(() => {
      void storeConversationCache(session.sessionHandle, turns);
    }, 250);
    return () => clearTimeout(timeout);
  }, [session.sessionHandle, turns]);

  useFocusEffect(useCallback(() => {
    void refresh();
    void checkClipboard();
  }, [checkClipboard, refresh]));

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        void refresh();
        void checkClipboard();
      }
    });
    return () => subscription.remove();
  }, [checkClipboard, refresh]);

  useEffect(() => {
    let active = true;
    const sessionHandle = session.sessionHandle;
    void loadConversationDraft(sessionHandle).then((draft: string) => {
      if (!active) return;
      if (params.prefill?.trim()) setText(params.prefill.trim());
      else if (draft) setText(draft);
    });
    return () => { active = false; };
  }, [session.sessionHandle, params.prefill]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      void storeConversationDraft(session.sessionHandle, text);
    }, 250);
    return () => clearTimeout(timeout);
  }, [session.sessionHandle, text]);

  useEffect(() => {
    const capturedUri = params.capturedUri?.trim();
    if (!capturedUri) return;
    setAttachments((current) => {
      if (current.some((item) => item.uri === capturedUri)) return current;
      const filename = params.capturedName?.trim()
        || capturedUri.split("/").pop()
        || `capture-${Date.now()}.jpg`;
      const mediaType = params.capturedMediaType?.trim() || "image/jpeg";
      return [...current, { uri: capturedUri, name: filename, mediaType }];
    });
  }, [params.capturedUri, params.capturedName, params.capturedMediaType]);

  // Artifact-id continuation from the Space page: the turn references the
  // original artifact (no download + re-upload duplicate). Images lazily
  // fetch a local thumbnail for the composer preview only.
  const capturedArtifactId = params.capturedArtifactId?.trim() ?? "";
  const capturedArtifactSession = params.capturedSessionHandle?.trim() ?? "";
  const capturedArtifactName = params.capturedName?.trim() ?? "";
  const capturedArtifactMediaType = params.capturedMediaType?.trim() ?? "";
  const currentSessionHandle = session.sessionHandle;
  const sessionReady = Boolean(session.client);
  useEffect(() => {
    if (!capturedArtifactId || !sessionReady) return;
    const mediaType = capturedArtifactMediaType || "application/octet-stream";
    const name = capturedArtifactName || capturedArtifactId;
    let cancelled = false;
    setAttachments((current) => {
      if (current.some((item) => item.uploaded?.artifact_id === capturedArtifactId)) return current;
      return [...current, {
        uri: `artifact:${capturedArtifactId}`,
        name,
        mediaType,
        status: "uploaded" as const,
        uploaded: { artifact_id: capturedArtifactId, caption: name },
      }];
    });
    if (!mediaType.startsWith("image/")) return;
    void (async () => {
      try {
        const ownerHandle = capturedArtifactSession || currentSessionHandle;
        if (!ownerHandle) return;
        const items = assistantArtifactItems([{
          artifact_id: capturedArtifactId,
          name,
          media_type: mediaType,
        }]);
        const target = items[0];
        if (!target) return;
        const resolved = await resolveAssistantArtifactFile(
          target,
          {
            cachedUri: (fileName) => {
              const file = new File(Paths.document, `artifact-${fileName}`);
              return file.exists ? file.uri : null;
            },
            download: (artifactId) => session.runAuthenticated(
              (client) => client.downloadArtifact(ownerHandle, artifactId),
            ),
            write: (fileName, bytes) => {
              const file = new File(Paths.document, `artifact-${fileName}`);
              file.create({ overwrite: true, intermediates: true });
              file.write(bytes);
              return file.uri;
            },
          },
        );
        if (cancelled) return;
        setAttachments((current) => current.map((item) =>
          item.uploaded?.artifact_id === capturedArtifactId ? { ...item, uri: resolved.uri } : item,
        ));
      } catch {
        // Thumbnail stays a placeholder; the turn still references the id.
      }
    })();
    return () => { cancelled = true; };
  }, [
    capturedArtifactId, capturedArtifactSession, capturedArtifactName,
    capturedArtifactMediaType, currentSessionHandle, sessionReady,
    session.runAuthenticated,
  ]);

  const activeTurn = useMemo(
    () => turns.find((turn) => !TERMINAL_STATES.has(turn.state)),
    [turns],
  );
  const transportOnline = connection.status === "ready"
    || connection.relayState === "ready"
    || connection.relayState === "active"
    || connection.p2pState === "ready"
    || connection.p2pState === "active"
    || connection.lanState === "found";
  const sending = pendingTurn?.state === "sending";
  const hasComposerContent = Boolean(text.trim() || attachments.length);
  // 与顶部状态胶囊（NodeHeader 的 isOnline）保持同一语义：任一传输就绪即视为
  // 可发送。 Relay/P2P 诊断回调先于 status=ready 到达，之前这里单独卡
  // session.client 会让胶囊显示"在线·P2P"而发送键仍是禁用；发送时
  // runAuthenticated 会顺带完成认证恢复。
  const canSend = Boolean(
    !pendingTurn
      && !validatingInput
      && (session.client || transportOnline)
      && !fleet.requiredUpdate
      && hasComposerContent,
  );

  const listItems = useMemo<ChatListItem[]>(() => {
    const base: ChatListItem[] = [
      ...turns.map((turn) => ({
        kind: "turn" as const,
        key: turn.turn_id,
        turn,
        timestampMs: turn.created_at * 1000,
        showTimestamp: false,
      })),
      ...(pendingTurn
        ? [{
            kind: "pending" as const,
            key: pendingTurn.localId,
            pending: pendingTurn,
            timestampMs: pendingTurn.createdAt,
            showTimestamp: false,
          }]
        : []),
      ...(queuedTurn
        ? [{
            kind: "pending" as const,
            key: queuedTurn.localId,
            pending: queuedTurn,
            timestampMs: queuedTurn.createdAt,
            showTimestamp: false,
          }]
        : []),
    ];
    let previousMs: number | null = null;
    return base.map((item): ChatListItem => {
      const showTimestamp = previousMs === null || item.timestampMs - previousMs > TIMESTAMP_GROUP_MS;
      previousMs = item.timestampMs;
      return item.kind === "turn"
        ? { kind: "turn", key: item.key, turn: item.turn, timestampMs: item.timestampMs, showTimestamp }
        : { kind: "pending", key: item.key, pending: item.pending, timestampMs: item.timestampMs, showTimestamp };
    });
  }, [pendingTurn, queuedTurn, turns]);

  async function submitPendingTurn(pending: PendingChatTurn) {
    if (!session.client && !transportOnline) return;
    setPendingTurn({ ...pending, state: "sending", error: "" });
    setFeedback(null);

    try {
      const wasNewConversation = !session.sessionHandle;
      const sessionHandle = await session.ensureConversation();
      const uploadedItems = await Promise.all(pending.attachments.map(async (item, index) => {
        if (item.uploaded) return { ...item, status: "uploaded" as const };
        setPendingTurn((current) => current?.localId === pending.localId ? {
          ...current,
          attachments: current.attachments.map((cand, candIdx) => candIdx === index ? { ...cand, status: "uploading" } : cand),
        } : current);
        try {
          const response = await fetch(item.uri);
          const bytes = await response.arrayBuffer();
          const uploaded = await session.runAuthenticated((client) => client.uploadArtifact({
            sessionHandle,
            bytes,
            mediaType: item.mediaType,
            name: item.name,
            caption: item.name,
          }));
          const completed = { ...item, status: "uploaded" as const, uploaded };
          setPendingTurn((current) => current?.localId === pending.localId ? {
            ...current,
            attachments: current.attachments.map((cand, candIdx) => candIdx === index ? completed : cand),
          } : current);
          return completed;
        } catch {
          const failed = { ...item, status: "failed" as const };
          setPendingTurn((current) => current?.localId === pending.localId ? {
            ...current,
            attachments: current.attachments.map((cand, candIdx) => candIdx === index ? failed : cand),
          } : current);
          return failed;
        }
      }));

      if (uploadedItems.some((item) => item.status === "failed")) {
        setPendingTurn({
          ...pending,
          attachments: uploadedItems,
          state: "failed",
          error: t("chat.attachmentUploadFailed"),
        });
        return;
      }

      const accepted = await session.runAuthenticated((client) => client.createChatTurn({
        clientRequestId: pending.requestId,
        sessionHandle,
        text: pending.userInput,
        attachments: uploadedItems.flatMap((item) => item.uploaded ? [item.uploaded] : []),
        agentId: fleet.activeAgentId || fleet.selectedAgentId,
      }));
      setTurns((current) => mergeConversationTurns(current, [accepted]));
      setPendingTurn(null);
      watchTurn(accepted.turn_id);
      void storeConversationDraft(sessionHandle, "");
      if (wasNewConversation) {
        void session.commitConversation(sessionHandle).catch(() => {
          showFeedback(t("chat.sessionSyncPending"), "warning");
        });
      }
    } catch {
      setPendingTurn({
        ...pending,
        state: "failed",
        error: t("chat.sendFailed"),
      });
    }
  }

  async function send() {
    if (!session.client || !canSend) return;
    setFeedback(null);
    if (attachments.some((item) => item.mediaType.startsWith("image/"))) {
      setValidatingInput(true);
      try {
        const current = await session.runAuthenticated((client) => client.getConfigCurrent());
        const agentId = fleet.activeAgentId || fleet.selectedAgentId || current.revision.document.agents.default_agent;
        const support = agentImageSupport(current.revision.document, agentId);
        if (!support.supported) {
          Alert.alert(
            t("chat.imageUnsupportedTitle"),
            t("chat.imageUnsupportedDetail", { model: support.modelAlias || t("chat.currentModel") }),
            [
              { text: t("chat.keepEditing"), style: "cancel" },
              { text: t("chat.configureAgent"), onPress: () => router.push("/settings/agents") },
            ],
          );
          return;
        }
      } finally {
        setValidatingInput(false);
      }
    }

    const userInput = text.trim();
    const queuedAttachments = attachments.map((item) => ({ ...item, status: "pending" as const }));
    const localId = `local-${Date.now()}`;
    const requestId = `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    const outgoing: PendingChatTurn = {
      localId,
      requestId,
      userInput,
      attachments: queuedAttachments,
      state: "sending",
      error: "",
      createdAt: Date.now(),
    };

    // While a server turn is still open (running or waiting approval),
    // park the message locally instead of failing against it. At most one.
    if (activeTurn) {
      if (queuedTurn) {
        showFeedback(t("chat.queueFullHint"), "warning");
        return;
      }
      setQueuedTurn(outgoing);
      setText("");
      setAttachments([]);
      setFeedback(null);
      followLatest.current = true;
      setShowJumpToLatest(false);
      scrollIntent.current = "instant";
      showFeedback(t("chat.queuedHint"), "info");
      return;
    }

    setText("");
    setAttachments([]);
    setFeedback(null);
    followLatest.current = true;
    setShowJumpToLatest(false);
    scrollIntent.current = "instant";

    void submitPendingTurn(outgoing);
  }

  // Auto-send the parked message once the server turn closes. submitRef
  // avoids re-subscribing the effect on every render (submitPendingTurn
  // is a plain function). All read values are in deps.
  const submitRef = useRef(submitPendingTurn);
  submitRef.current = submitPendingTurn;
  const activeTurnId = activeTurn?.turn_id ?? "";
  useEffect(() => {
    if (activeTurnId || pendingTurn || !queuedTurn) return;
    const next = queuedTurn;
    setQueuedTurn(null);
    void submitRef.current({ ...next, state: "sending", error: "" });
  }, [activeTurnId, pendingTurn, queuedTurn]);

  const handleSelectPrompt = useCallback((prompt: string, autoSend = false) => {
    const transportOnline = connection.status === "ready"
      || connection.relayState === "ready"
      || connection.relayState === "active"
      || connection.p2pState === "ready"
      || connection.p2pState === "active"
      || connection.lanState === "found";
    const canAutoSend = Boolean(
      !pendingTurn
        && !validatingInput
        && (session.client || transportOnline)
        && !fleet.requiredUpdate,
    );
    if (autoSend && canAutoSend) {
      const localId = `local-${Date.now()}`;
      const requestId = `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setText("");
      setAttachments([]);
      setFeedback(null);
      followLatest.current = true;
      setShowJumpToLatest(false);
      scrollIntent.current = "instant";
      void submitPendingTurn({
        localId,
        requestId,
        userInput: prompt.trim(),
        attachments: [],
        state: "sending",
        error: "",
        createdAt: Date.now(),
      });
    } else {
      setText(prompt);
      showFeedback(t("chat.editedToComposer"), "info");
    }
  }, [session.client, fleet.requiredUpdate, connection.status, connection.relayState, connection.p2pState, connection.lanState, pendingTurn, showFeedback, t, validatingInput]);

  const handleLaunchTask = useCallback((title: string, goal: string) => {
    const targetNodeId = fleet.nodeId || stringParam(params.nodeId);
    router.push({
      pathname: "/tasks/new",
      params: {
        ...nodeRouteParams(params),
        ...(targetNodeId ? { nodeId: targetNodeId } : {}),
        title,
        goal,
      },
    });
  }, [fleet.nodeId, params]);

  async function cancelTurn(turn: ChatTurnSnapshot) {
    if (!session.client || cancelling) return;
    setCancelling(true);
    try {
      const cancelled = await session.runAuthenticated(
        (client) => client.cancelChatTurn(turn.turn_id),
      );
      setTurns((current) => mergeConversationTurns(current, [cancelled]));
      watchTurn(cancelled.turn_id);
    } catch {
      showFeedback(t("chat.syncUnavailable"), "error");
    } finally {
      setCancelling(false);
    }
  }

  async function resolve(approval: ChatApproval, approved: boolean) {
    if (!session.client || resolving) return;
    setResolving(approval.approval_id);
    setResolvingApproved(approved);
    const turn = turns.find((item) => item.approvals.some(
      (candidate) => candidate.approval_id === approval.approval_id,
    ));
    try {
      const result = await session.runAuthenticated(
        (client) => client.resolveChatApproval(approval.approval_id, approved),
      );
      setTurns((current) => current.map((item) => {
        if (!item.approvals.some((cand) => cand.approval_id === result.approval.approval_id)) {
          return item;
        }
        return {
          ...item,
          approvals: item.approvals.map((cand) => cand.approval_id === result.approval.approval_id ? result.approval : cand),
        };
      }));
      if (turn) {
        watchTurn(turn.turn_id);
        void session.runAuthenticated((client) => client.getChatTurn(turn.turn_id)).then((fresh) => {
          setTurns((current) => mergeConversationTurns(current, [fresh]));
          if (!TERMINAL_STATES.has(fresh.state)) watchTurn(fresh.turn_id);
        }).catch(() => undefined);
      } else if (result.approval.state === "pending") {
        void refresh();
      }
    } catch {
      showFeedback(t("chat.approvalFailed"), "error");
    } finally {
      setResolving("");
      setResolvingApproved(null);
    }
  }

  async function resolveInteraction(interaction: HumanInteraction, value: Record<string, unknown>) {
    if (!session.client || resolvingInteraction) return;
    setResolvingInteraction(interaction.interaction_id);
    try {
      const result = await session.runAuthenticated(
        (client) => client.resolveInteraction(interaction.interaction_id, value),
      );
      setTurns((current) => current.map((turn) => turn.turn_id !== interaction.owner_id ? turn : {
        ...turn,
        interactions: (turn.interactions ?? []).map((cand) => cand.interaction_id === interaction.interaction_id ? result.interaction : cand),
      }));
      watchTurn(interaction.owner_id);
      void session.runAuthenticated((client) => client.getChatTurn(interaction.owner_id)).then((fresh) => {
        setTurns((current) => mergeConversationTurns(current, [fresh]));
      }).catch(() => undefined);
    } catch {
      showFeedback(t("interaction.submitFailed"), "error");
    } finally {
      setResolvingInteraction("");
    }
  }

  async function invokeActionCard(invocation: ActionCardInvocation) {
    if (!session.client || !session.sessionHandle) return;
    try {
      const actionText = invocation.tool_name
        ? `执行动作【${invocation.action_id}】调用工具 ${invocation.tool_name}，参数: ${JSON.stringify(invocation.arguments || {})}`
        : `执行动作【${invocation.action_id}】`;
      const accepted = await session.runAuthenticated((client) => client.createChatTurn({
        clientRequestId: Crypto.randomUUID(),
        sessionHandle: session.sessionHandle!,
        text: actionText,
        attachments: [],
        agentId: fleet.activeAgentId || fleet.selectedAgentId,
      }));
      setTurns((current) => mergeConversationTurns(current, [accepted]));
      watchTurn(accepted.turn_id);
    } catch {
      showFeedback("执行卡片动作失败，请重试", "error");
    }
  }

  const loadArtifact = useCallback(async (
    item: AssistantArtifactItem,
  ): Promise<ResolvedArtifactFile> => resolveAssistantArtifactFile(item, {
    cachedUri: (cacheFileName) => {
      const file = new File(Paths.document, `received-${cacheFileName}`);
      return file.exists ? file.uri : null;
    },
    download: (artifactId) => session.runAuthenticated((client) => client.downloadArtifact(
      session.sessionHandle,
      artifactId,
    )),
    write: (cacheFileName, bytes) => {
      const file = new File(Paths.document, `received-${cacheFileName}`);
      file.create({ overwrite: true, intermediates: true });
      file.write(bytes);
      return file.uri;
    },
  }), [session.runAuthenticated, session.sessionHandle]);

  const openArtifact = useCallback(async (item: AssistantArtifactItem) => {
    try {
      const resolved = await loadArtifact(item);
      if (item.isImage) {
        setImagePreview(resolved);
      } else {
        await Sharing.shareAsync(resolved.uri, { mimeType: resolved.mediaType });
      }
    } catch {
      showFeedback(t("chat.artifactOpenFailed"), "error");
    }
  }, [loadArtifact, showFeedback, t]);

  const saveArtifact = useCallback(async (item: AssistantArtifactItem) => {
    try {
      const resolved = await loadArtifact(item);
      showFeedback(await saveArtifactFile(resolved, {
        saveDialog: t("artifact.save"),
        saveToFile: t("artifact.saveToFile"),
        cancelled: t("artifact.saveCancelled"),
        saved: t("artifact.savedFile"),
      }), "success");
    } catch {
      showFeedback(t("chat.fileSaveFailed"), "error");
    }
  }, [loadArtifact, showFeedback, t]);

  async function startNewTopic(agentId?: string) {
    if (startingTopic || sending) return;
    setStartingTopic(true);
    setFeedback(null);
    try {
      const previousSession = session.sessionHandle;
      await session.newConversation(agentId);
      void removeConversationDraft(previousSession).catch(() => undefined);
      void removeConversationDraft("").catch(() => undefined);
      setTurns([]);
      setPendingTurn(null);
      setNextTurnCursor("");
      setText("");
      setAttachments([]);
      followLatest.current = true;
      setShowJumpToLatest(false);
      scrollIntent.current = "instant";
      initialScrollPending.current = true;
      turnWatcher.closeAll();
    } catch {
      showFeedback(t("chat.syncUnavailable"), "error");
    } finally {
      setStartingTopic(false);
    }
  }

  const scrollToBottom = useCallback((animated = true) => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = requestAnimationFrame(() => {
      listRef.current?.scrollToEnd({ animated });
    });
  }, []);

  const handleScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (!userDragging.current) return;
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const distanceToBottom = contentSize.height - (contentOffset.y + layoutMeasurement.height);
    const atBottom = distanceToBottom <= SCROLL_OFFSET_THRESHOLD;
    followLatest.current = atBottom;
    setShowJumpToLatest(!atBottom && contentSize.height > layoutMeasurement.height + SCROLL_OFFSET_THRESHOLD);
  }, []);

  const selectedAgentId = fleet.activeAgentId || fleet.selectedAgentId;
  const currentAgent = fleet.agents.find((agent) => agent.agent_id === selectedAgentId);
  const agentLocked = Boolean(turns.length);
  const showStopAction = Boolean(activeTurn) && !(inputMode === "text" && hasComposerContent);
  const stoppingResponse = Boolean(cancelling);

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      keyboardVerticalOffset={insets.top + (Platform.OS === "ios" ? 44 : 56)}
    >
      <View style={styles.topbar}>
          <AppPressable
            accessibilityLabel={t("agent.selectConversation")}
            onPress={() => setAgentPickerOpen(true)}
            style={styles.agentButton}
          >
            <AppIcon name="agent" color={colors.accent} size={18} />
            <Text style={styles.agentButtonText} numberOfLines={1}>
              {currentAgent?.display_name || t("agent.selectConversation")}
            </Text>
            <AppIcon name="chevron-down" color={colors.muted} size={14} />
          </AppPressable>

          <AppPressable
            accessibilityLabel={t("chat.newTopic")}
            onPress={() => void startNewTopic()}
            disabled={startingTopic || sending}
            style={styles.newTopicButton}
          >
            {startingTopic ? (
              <ActivityIndicator color={colors.accent} size="small" />
            ) : (
              <>
                <AppIcon name="plus" color={colors.accent} size={16} />
                <Text style={styles.newTopicText}>{t("chat.newTopic")}</Text>
              </>
            )}
          </AppPressable>
        </View>

        <ChatFeedbackBanner feedback={feedback} onDismiss={() => setFeedback(null)} />

        <View style={styles.listArea}>
          <FlatList
            ref={listRef}
            contentContainerStyle={styles.messages}
            keyboardShouldPersistTaps="handled"
            data={listItems}
            keyExtractor={(item) => item.key}
            onContentSizeChange={() => {
              if (followLatest.current) scrollToBottom(scrollIntent.current === "smooth");
            }}
            onScrollBeginDrag={() => { userDragging.current = true; }}
            onScroll={handleScroll}
            onScrollEndDrag={() => { userDragging.current = false; }}
            renderItem={({ item }) => (
              item.kind === "turn" ? (
                <ChatTurnItem
                  turn={item.turn}
                  showTimestamp={item.showTimestamp}
                  timestampMs={item.timestampMs}
                  locale={locale}
                  onCopy={copyMessage}
                  resolving={resolving}
                  resolvingApproved={resolvingApproved}
                  resolvingInteraction={resolvingInteraction}
                  onResolve={resolve}
                  onResolveInteraction={resolveInteraction}
                  onInvokeActionCard={invokeActionCard}
                  onLoadArtifact={loadArtifact}
                  onOpenArtifact={openArtifact}
                  onSaveArtifact={saveArtifact}
                  onRetry={async (turn) => {
                    try {
                      const accepted = await session.runAuthenticated((client) => client.retryChatTurn(turn.turn_id));
                      setTurns((curr) => [...curr, accepted]);
                      watchTurn(accepted.turn_id);
                    } catch {
                      showFeedback(t("chat.retryFailed"), "error");
                    }
                  }}
                  onEdit={(turn) => {
                    setText(turn.user_input);
                    showFeedback(t("chat.editedToComposer"), "info");
                  }}
                  onConvertToTask={(turn) => {
                    const goal = turn.user_input;
                    const title = goal.length > 24 ? `${goal.slice(0, 24)}…` : goal;
                    const targetNodeId = fleet.nodeId || stringParam(params.nodeId);
                    router.push({
                      pathname: "/tasks/new",
                      params: {
                        ...nodeRouteParams(params),
                        ...(targetNodeId ? { nodeId: targetNodeId } : {}),
                        title,
                        goal,
                      },
                    });
                  }}
                />
              ) : (
                <PendingTurnItem
                  pending={item.pending}
                  queued={Boolean(activeTurn) || item.pending.localId === queuedTurn?.localId}
                  showTimestamp={item.showTimestamp}
                  timestampMs={item.timestampMs}
                  locale={locale}
                  onCopy={copyMessage}
                  onRetry={(pending) => {
                    if (pending.localId === queuedTurn?.localId) {
                      if (activeTurn || pendingTurn) {
                        showFeedback(t("chat.queueFullHint"), "warning");
                        return;
                      }
                      setQueuedTurn(null);
                    }
                    void submitPendingTurn(pending);
                  }}
                  onEdit={(pending) => {
                    setText(pending.userInput);
                    setAttachments(pending.attachments.filter((att) => !att.uploaded));
                    if (pending.localId === queuedTurn?.localId) setQueuedTurn(null);
                    else setPendingTurn(null);
                    showFeedback(t("chat.editedToComposer"), "info");
                  }}
                />
              )
            )}
            ListEmptyComponent={
              <ProactiveDeck
                computerName={currentNode ? presentNodeName(currentNode, t("common.unnamedComputer")) : undefined}
                toolCount={nodeCapability?.toolCount}
                modelName={nodeCapability?.document?.default_model}
                isOnline={transportOnline}
                savedHours={savedHours}
                completedTasksCount={completedTasksCount}
                onSelectPrompt={handleSelectPrompt}
                onLaunchTask={handleLaunchTask}
                onPressGlance={handleOpenLiveGlance}
              />
            }
          />

          {showJumpToLatest ? (
            <AppPressable
              accessibilityLabel={t("chat.jumpLatest")}
              onPress={() => scrollToBottom(true)}
              style={styles.jumpLatest}
            >
              <AppIcon name="chevron-down" color={colors.accent} size={16} />
              <Text style={styles.jumpLatestText}>{t("chat.jumpLatest")}</Text>
            </AppPressable>
          ) : null}
        </View>

        {clipboardSuggestion ? (
          <ClipboardSuggestionPill
            suggestion={clipboardSuggestion}
            onApply={(appliedText) => {
              setText(appliedText);
              dismissClipboardSuggestion();
            }}
            onDismiss={() => {
              dismissClipboardSuggestion();
            }}
          />
        ) : null}

        {Boolean(activeTurn) ? (
          <View style={styles.generatingStopPillWrap}>
            <AppPressable
              accessibilityLabel={t("chat.stopGenerating")}
              disabled={cancelling}
              onPress={() => { if (activeTurn) void cancelTurn(activeTurn); }}
              style={styles.generatingStopPill}
            >
              {cancelling ? (
                <ActivityIndicator color={colors.danger} size="small" />
              ) : (
                <AppIcon name="stop" color={colors.danger} size={13} />
              )}
              <Text style={styles.generatingStopText}>
                {cancelling ? t("chat.stoppingNow") : t("chat.generatingNow")}
              </Text>
              <View style={styles.generatingStopDivider} />
              <Text style={styles.generatingStopBtnText}>
                {t("chat.stopGenerating")}
              </Text>
            </AppPressable>
          </View>
        ) : null}

        <ChatComposer
          text={text}
          onTextChange={(val) => {
            setText(val);
            if (feedback?.tone === "error") setFeedback(null);
          }}
          inputMode={inputMode}
          onInputModeChange={setInputMode}
          attachments={attachments}
          onAttachmentsChange={setAttachments}
          onRetryAttachment={(idx) => {
            const att = attachments[idx];
            if (att) {
              setAttachments((curr) => curr.map((c, i) => i === idx ? { ...c, status: "pending" } : c));
            }
          }}
          canSend={canSend}
          sending={sending}
          validatingInput={validatingInput}
          cancelling={cancelling}
          showStopAction={showStopAction}
          stoppingResponse={stoppingResponse}
          onSend={() => void send()}
          onStop={() => { if (activeTurn) void cancelTurn(activeTurn); }}
          onToggleRecording={toggleRecording}
          recordingState={recordingState}
          transcribing={transcribing}
          nodeRouteParams={nodeRouteParams(params)}
          onNewTopic={() => void startNewTopic()}
        />

        <ArtifactViewer
          file={imagePreview}
          onClose={() => setImagePreview(null)}
          onMessage={(val, tone = "info") => showFeedback(val, tone)}
        />

        <AgentPickerSheet
          visible={agentPickerOpen}
          agents={fleet.agents}
          unavailableAgents={fleet.unavailableAgents}
          selectedAgentId={selectedAgentId}
          agentLocked={agentLocked}
          onClose={() => setAgentPickerOpen(false)}
          onSelect={(agentId) => {
            setAgentPickerOpen(false);
            if (agentId === selectedAgentId) return;
            if (agentLocked) void startNewTopic(agentId);
            else fleet.selectAgent(agentId);
          }}
          onConfigure={() => {
            setAgentPickerOpen(false);
            router.push("/settings/agents");
          }}
        />

        <DesktopGlanceModal
          glance={liveGlance}
          visible={glanceModalVisible}
          onClose={handleCloseGlance}
          onRefresh={handleRefreshLiveGlance}
          refreshing={glanceRefreshing}
        />
      </KeyboardAvoidingView>
  );
}

function nodeRouteParams(params: Record<string, string | string[] | undefined>): Record<string, string> {
  return {
    workspaceId: stringParam(params.workspaceId),
    workspaceName: stringParam(params.workspaceName),
    nodeId: stringParam(params.nodeId),
  };
}

function stringParam(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] ?? "" : value ?? "";
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  topbar: {
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  agentButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: spacing.medium,
    paddingVertical: 6,
    borderRadius: radii.medium,
    backgroundColor: colors.accentSoft,
    maxWidth: "60%",
  },
  agentButtonText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  newTopicButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.medium,
    paddingVertical: 6,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  newTopicText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  listArea: { flex: 1 },
  messages: {
    padding: spacing.large,
    paddingBottom: spacing.xlarge,
    gap: spacing.large,
    flexGrow: 1,
  },
  empty: {
    marginTop: 48,
    alignSelf: "center",
    width: "100%",
    maxWidth: 480,
    gap: spacing.medium,
    alignItems: "center",
  },
  emptyTitle: {
    color: colors.ink,
    textAlign: "center",
    fontSize: 20,
    fontWeight: "700",
  },
  emptyBody: {
    color: colors.muted,
    textAlign: "center",
    lineHeight: 20,
    fontSize: 13,
  },
  emptyExamples: {
    width: "100%",
    marginTop: spacing.medium,
    gap: spacing.small,
  },
  emptyExample: {
    minHeight: 44,
    paddingHorizontal: spacing.large,
    borderRadius: radii.medium,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    justifyContent: "center",
  },
  emptyExampleText: {
    color: colors.ink,
    fontSize: 13,
    fontWeight: "600",
  },
  jumpLatest: {
    position: "absolute",
    right: spacing.large,
    bottom: spacing.medium,
    minHeight: 36,
    paddingHorizontal: spacing.medium,
    borderRadius: radii.large,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
    backgroundColor: colors.surfaceElevated,
    borderWidth: 1,
    borderColor: colors.line,
    ...shadows.card,
  },
  jumpLatestText: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "700",
  },
  generatingStopPillWrap: {
    alignItems: "center",
    marginBottom: spacing.small,
    paddingHorizontal: spacing.large,
  },
  generatingStopPill: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: radii.pill,
    gap: 8,
    ...shadows.card,
  },
  generatingStopText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "500",
  },
  generatingStopDivider: {
    width: 1,
    height: 12,
    backgroundColor: colors.line,
  },
  generatingStopBtnText: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "700",
  },
});
