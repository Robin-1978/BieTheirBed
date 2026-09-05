import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import * as Clipboard from "expo-clipboard";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { File, Paths } from "expo-file-system";
import * as Linking from "expo-linking";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  FlatList,
  Modal,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type {
  ChatApproval,
  ChatTurnSnapshot,
  HumanInteraction,
} from "@/api/models";
import { AgentSelector } from "@/components/AgentSelector";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import {
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
import { loadCapabilityCache, type CapabilityCache } from "@/storage/capabilityCache";
import {
  resolveAssistantArtifactFile,
  type AssistantArtifactItem,
  type ResolvedArtifactFile,
} from "@/api/chatArtifacts";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { ChatTurnWatcher } from "@/api/chatTurnWatcher";
import { GatewayError } from "@/api/gatewayClient";
import { agentImageSupport } from "@/media/agentImageSupport";
import { shouldResetConversation } from "@/state/conversationTransition";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
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
  const gateway = useGateway();
  const insets = useSafeAreaInsets();
  const gatewayRef = useRef(gateway);
  gatewayRef.current = gateway;

  const { locale, t } = useI18n();
  const params = useLocalSearchParams<{
    workspaceId?: string;
    workspaceName?: string;
    nodeId?: string;
    capturedUri?: string;
    prefill?: string;
  }>();

  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [pendingTurn, setPendingTurn] = useState<PendingChatTurn | null>(null);
  const [nextTurnCursor, setNextTurnCursor] = useState("");
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
  const [transcribing, setTranscribing] = useState(false);

  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const currentNodeId = gateway.nodeId || stringParam(params.nodeId);
  const currentNode = gateway.nodes.find((item) => item.nodeId === currentNodeId);
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
  const displayedSession = useRef(gateway.sessionHandle);

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recordingState = useAudioRecorderState(recorder, 250);

  const lastDismissedClipboardRef = useRef("");
  const [clipboardSuggestion, setClipboardSuggestion] = useState<ClipboardSuggestion | null>(null);

  const showFeedback = useCallback((value: string, tone: Feedback["tone"] = "info") => {
    setFeedback({ text: value, tone });
  }, []);

  const copyMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;
    lastDismissedClipboardRef.current = content.trim();
    await Clipboard.setStringAsync(content);
    showFeedback(t("chat.messageCopied"), "success");
  }, [showFeedback, t]);

  const checkClipboard = useCallback(async () => {
    try {
      const hasStr = await Clipboard.hasStringAsync();
      if (!hasStr) return;
      const content = (await Clipboard.getStringAsync())?.trim();
      if (!content || content.length < 4 || content.length > 2000) return;
      if (content === lastDismissedClipboardRef.current) return;
      if (text.trim().includes(content)) return;

      const isUrl = /^https?:\/\/[^\s]+$/i.test(content);
      const isCode = content.includes("\n") && (
        content.includes("Error") ||
        content.includes("error") ||
        content.includes("Exception") ||
        content.includes("failed") ||
        content.includes("function")
      );
      setClipboardSuggestion({
        text: content,
        kind: isUrl ? "url" : isCode ? "code" : "text",
      });
    } catch {
      // ignore clipboard permission error
    }
  }, [text]);

  const [turnWatcher] = useState(() => new ChatTurnWatcher({
    connection: () => gatewayRef.current.connection(),
    fetchSnapshot: (turnId) => gatewayRef.current.runAuthenticated(
      (client) => client.getChatTurn(turnId),
    ),
    onSnapshot: (snapshot) => {
      setTurns((current) => mergeConversationTurns(current, [snapshot]));
    },
    onUnavailable: () => showFeedback(t("chat.streamUnavailable"), "error"),
  }));

  useEffect(() => {
    if (!feedback) return;
    const duration = feedback.tone === "error" || feedback.tone === "warning" ? 5000 : 3000;
    const timeout = setTimeout(() => setFeedback(null), duration);
    return () => clearTimeout(timeout);
  }, [feedback]);

  const watchTurn = useCallback((turnId: string) => {
    turnWatcher.watch(turnId);
  }, [turnWatcher]);

  useEffect(() => () => {
    turnWatcher.closeAll();
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
  }, [turnWatcher]);

  const refresh = useCallback(async () => {
    if (!gateway.client || !gateway.sessionHandle) return;
    const sessionHandle = gateway.sessionHandle;
    try {
      const history = await gateway.runAuthenticated(
        (client) => client.listChatTurns(sessionHandle, 100),
      );
      if (gatewayRef.current.sessionHandle !== sessionHandle) return;
      setTurns((current) => mergeConversationTurns(current, history.turns));
      setNextTurnCursor(history.nextCursor);
      for (const turn of history.turns) {
        if (!TERMINAL_STATES.has(turn.state)) watchTurn(turn.turn_id);
      }
    } catch (error) {
      if (error instanceof GatewayError && error.status === 404) {
        await gateway.newConversation();
        showFeedback(t("chat.sessionReplaced"), "warning");
        return;
      }
      showFeedback(t("chat.syncUnavailable"), "warning");
    }
  }, [gateway.client, gateway.newConversation, gateway.runAuthenticated, gateway.sessionHandle, showFeedback, t, watchTurn]);

  useEffect(() => {
    let active = true;
    const sessionHandle = gateway.sessionHandle;
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
        if (active && gatewayRef.current.sessionHandle === sessionHandle) {
          setTurns((current) => mergeConversationTurns(current, cached));
        }
      }).finally(() => {
        if (active && gatewayRef.current.sessionHandle === sessionHandle) void refresh();
      });
    }
    return () => { active = false; };
  }, [gateway.sessionHandle, refresh, turnWatcher]);

  useEffect(() => {
    if (!gateway.client || gateway.sessionHandle) return;
    void gateway.ensureConversation().catch(() => undefined);
  }, [gateway.client, gateway.ensureConversation, gateway.sessionHandle]);

  useEffect(() => {
    if (!gateway.sessionHandle || !turns.length) return;
    const timeout = setTimeout(() => {
      void storeConversationCache(gateway.sessionHandle, turns);
    }, 250);
    return () => clearTimeout(timeout);
  }, [gateway.sessionHandle, turns]);

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
    const sessionHandle = gateway.sessionHandle;
    void loadConversationDraft(sessionHandle).then((draft: string) => {
      if (!active) return;
      if (params.prefill?.trim()) setText(params.prefill.trim());
      else if (draft) setText(draft);
    });
    return () => { active = false; };
  }, [gateway.sessionHandle, params.prefill]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      void storeConversationDraft(gateway.sessionHandle, text);
    }, 250);
    return () => clearTimeout(timeout);
  }, [gateway.sessionHandle, text]);

  useEffect(() => {
    const capturedUri = params.capturedUri?.trim();
    if (!capturedUri) return;
    setAttachments((current) => {
      if (current.some((item) => item.uri === capturedUri)) return current;
      const filename = capturedUri.split("/").pop() || `capture-${Date.now()}.jpg`;
      return [...current, { uri: capturedUri, name: filename, mediaType: "image/jpeg" }];
    });
  }, [params.capturedUri]);

  const activeTurn = useMemo(
    () => turns.find((turn) => !TERMINAL_STATES.has(turn.state)),
    [turns],
  );
  const sending = pendingTurn?.state === "sending";
  const hasComposerContent = Boolean(text.trim() || attachments.length);
  const canSend = Boolean(
    !pendingTurn
      && !validatingInput
      && gateway.client
      && !gateway.requiredUpdate
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
    ];
    let previousMs: number | null = null;
    return base.map((item): ChatListItem => {
      const showTimestamp = previousMs === null || item.timestampMs - previousMs > TIMESTAMP_GROUP_MS;
      previousMs = item.timestampMs;
      return item.kind === "turn"
        ? { kind: "turn", key: item.key, turn: item.turn, timestampMs: item.timestampMs, showTimestamp }
        : { kind: "pending", key: item.key, pending: item.pending, timestampMs: item.timestampMs, showTimestamp };
    });
  }, [pendingTurn, turns]);

  async function submitPendingTurn(pending: PendingChatTurn) {
    if (!gateway.client) return;
    setPendingTurn({ ...pending, state: "sending", error: "" });
    setFeedback(null);

    try {
      const wasNewConversation = !gateway.sessionHandle;
      const sessionHandle = await gateway.ensureConversation();
      const uploadedItems = await Promise.all(pending.attachments.map(async (item, index) => {
        if (item.uploaded) return { ...item, status: "uploaded" as const };
        setPendingTurn((current) => current?.localId === pending.localId ? {
          ...current,
          attachments: current.attachments.map((cand, candIdx) => candIdx === index ? { ...cand, status: "uploading" } : cand),
        } : current);
        try {
          const response = await fetch(item.uri);
          const bytes = await response.arrayBuffer();
          const uploaded = await gateway.runAuthenticated((client) => client.uploadArtifact({
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

      const accepted = await gateway.runAuthenticated((client) => client.createChatTurn({
        clientRequestId: pending.requestId,
        sessionHandle,
        text: pending.userInput,
        attachments: uploadedItems.flatMap((item) => item.uploaded ? [item.uploaded] : []),
        agentId: gateway.activeAgentId || gateway.selectedAgentId,
      }));
      setTurns((current) => mergeConversationTurns(current, [accepted]));
      setPendingTurn(null);
      watchTurn(accepted.turn_id);
      void storeConversationDraft(sessionHandle, "");
      if (wasNewConversation) {
        void gateway.commitConversation(sessionHandle).catch(() => {
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
    if (!gateway.client || !canSend) return;
    setFeedback(null);
    if (attachments.some((item) => item.mediaType.startsWith("image/"))) {
      setValidatingInput(true);
      try {
        const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
        const agentId = gateway.activeAgentId || gateway.selectedAgentId || current.revision.document.agents.default_agent;
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

    setText("");
    setAttachments([]);
    setFeedback(null);
    followLatest.current = true;
    setShowJumpToLatest(false);
    scrollIntent.current = "instant";

    void submitPendingTurn({
      localId,
      requestId,
      userInput,
      attachments: queuedAttachments,
      state: "sending",
      error: "",
      createdAt: Date.now(),
    });
  }

  const handleSelectPrompt = useCallback((prompt: string, autoSend = false) => {
    const canAutoSend = Boolean(
      !pendingTurn
        && !validatingInput
        && gateway.client
        && !gateway.requiredUpdate,
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
  }, [gateway.client, gateway.requiredUpdate, pendingTurn, showFeedback, t, validatingInput]);

  const handleLaunchTask = useCallback((title: string, goal: string) => {
    const targetNodeId = gateway.nodeId || stringParam(params.nodeId);
    router.push({
      pathname: "/tasks/new",
      params: {
        ...nodeRouteParams(params),
        ...(targetNodeId ? { nodeId: targetNodeId } : {}),
        title,
        goal,
      },
    });
  }, [gateway.nodeId, params]);

  async function cancelTurn(turn: ChatTurnSnapshot) {
    if (!gateway.client || cancelling) return;
    setCancelling(true);
    try {
      const cancelled = await gateway.runAuthenticated(
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
    if (!gateway.client || resolving) return;
    setResolving(approval.approval_id);
    setResolvingApproved(approved);
    const turn = turns.find((item) => item.approvals.some(
      (candidate) => candidate.approval_id === approval.approval_id,
    ));
    try {
      const result = await gateway.runAuthenticated(
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
        void gateway.runAuthenticated((client) => client.getChatTurn(turn.turn_id)).then((fresh) => {
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
    if (!gateway.client || resolvingInteraction) return;
    setResolvingInteraction(interaction.interaction_id);
    try {
      const result = await gateway.runAuthenticated(
        (client) => client.resolveInteraction(interaction.interaction_id, value),
      );
      setTurns((current) => current.map((turn) => turn.turn_id !== interaction.owner_id ? turn : {
        ...turn,
        interactions: (turn.interactions ?? []).map((cand) => cand.interaction_id === interaction.interaction_id ? result.interaction : cand),
      }));
      watchTurn(interaction.owner_id);
      void gateway.runAuthenticated((client) => client.getChatTurn(interaction.owner_id)).then((fresh) => {
        setTurns((current) => mergeConversationTurns(current, [fresh]));
      }).catch(() => undefined);
    } catch {
      showFeedback(t("interaction.submitFailed"), "error");
    } finally {
      setResolvingInteraction("");
    }
  }

  const loadArtifact = useCallback(async (
    item: AssistantArtifactItem,
  ): Promise<ResolvedArtifactFile> => resolveAssistantArtifactFile(item, {
    cachedUri: (cacheFileName) => {
      const file = new File(Paths.document, `received-${cacheFileName}`);
      return file.exists ? file.uri : null;
    },
    download: (artifactId) => gateway.runAuthenticated((client) => client.downloadArtifact(
      gateway.sessionHandle,
      artifactId,
    )),
    write: (cacheFileName, bytes) => {
      const file = new File(Paths.document, `received-${cacheFileName}`);
      file.create({ overwrite: true, intermediates: true });
      file.write(bytes);
      return file.uri;
    },
  }), [gateway.runAuthenticated, gateway.sessionHandle]);

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
      const previousSession = gateway.sessionHandle;
      await gateway.newConversation(agentId);
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

  async function toggleRecording() {
    if (!gateway.client || transcribing) return;
    if (recordingState.isRecording) {
      await recorder.stop();
      const uri = recorder.uri;
      if (!uri) return;
      setTranscribing(true);
      try {
        const sessionHandle = await gateway.ensureConversation();
        const response = await fetch(uri);
        const extension = uri.toLowerCase().endsWith(".webm") ? "webm" : "m4a";
        const bytes = await response.arrayBuffer();
        const artifact = await gateway.runAuthenticated((client) => client.uploadArtifact({
          sessionHandle,
          bytes,
          mediaType: extension === "webm" ? "audio/webm" : "audio/mp4",
          name: `voice-${Date.now()}.${extension}`,
          caption: t("chat.voiceCaption"),
        }));
        const transcript = await gateway.runAuthenticated((client) => client.transcribeArtifact(
          sessionHandle,
          artifact.artifact_id,
        ));
        setText((current) => current ? `${current}\n${transcript}` : transcript);
      } catch {
        showFeedback(t("chat.transcriptionFailed"), "error");
      } finally {
        await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
        setTranscribing(false);
      }
      return;
    }

    const permission = await requestRecordingPermissionsAsync();
    if (!permission.granted) {
      showFeedback(permission.canAskAgain
        ? t("chat.microphoneRequired")
        : t("chat.microphoneDisabled"), "warning");
      if (!permission.canAskAgain) await Linking.openSettings();
      return;
    }
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
    } catch {
      await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
      showFeedback(t("chat.recordingFailed"), "error");
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

  const selectedAgentId = gateway.activeAgentId || gateway.selectedAgentId;
  const currentAgent = gateway.agents.find((agent) => agent.agent_id === selectedAgentId);
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
                  onLoadArtifact={loadArtifact}
                  onOpenArtifact={openArtifact}
                  onSaveArtifact={saveArtifact}
                  onRetry={async (turn) => {
                    try {
                      const accepted = await gateway.runAuthenticated((client) => client.retryChatTurn(turn.turn_id));
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
                    const targetNodeId = gateway.nodeId || stringParam(params.nodeId);
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
                  queued={Boolean(activeTurn)}
                  showTimestamp={item.showTimestamp}
                  timestampMs={item.timestampMs}
                  locale={locale}
                  onCopy={copyMessage}
                  onRetry={(pending) => void submitPendingTurn(pending)}
                  onEdit={(pending) => {
                    setText(pending.userInput);
                    setAttachments(pending.attachments.filter((att) => !att.uploaded));
                    setPendingTurn(null);
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
                isOnline={gateway.status === "ready"}
                onSelectPrompt={handleSelectPrompt}
                onLaunchTask={handleLaunchTask}
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
              lastDismissedClipboardRef.current = clipboardSuggestion.text;
              setClipboardSuggestion(null);
            }}
            onDismiss={() => {
              lastDismissedClipboardRef.current = clipboardSuggestion.text;
              setClipboardSuggestion(null);
            }}
          />
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

        <Modal
          animationType="fade"
          onRequestClose={() => setAgentPickerOpen(false)}
          transparent
          visible={agentPickerOpen}
        >
          <View style={styles.modalRoot}>
            <Pressable style={styles.backdrop} onPress={() => setAgentPickerOpen(false)} />
            <View style={styles.actionSheet}>
              <View style={styles.sheetHandle} />
              <AgentSelector
                agents={gateway.agents}
                selectedAgentId={selectedAgentId}
                label={agentLocked ? t("agent.changeConversation") : t("agent.selectConversation")}
                lockedLabel={t("agent.lockedConversation")}
                onChange={(agentId) => {
                  setAgentPickerOpen(false);
                  if (agentId === selectedAgentId) return;
                  if (agentLocked) void startNewTopic(agentId);
                  else gateway.selectAgent(agentId);
                }}
              />
              {gateway.unavailableAgents.length ? (
                <View style={styles.unavailableAgents}>
                  <Text style={styles.unavailableTitle}>{t("agent.unavailableTitle")}</Text>
                  {gateway.unavailableAgents.map((agent) => (
                    <Text key={agent.agent_id} style={styles.unavailableText}>
                      {agent.display_name} · {agentReasonLabel(agent.reason, t)}
                    </Text>
                  ))}
                  <AppPressable
                    style={styles.configureUnavailable}
                    onPress={() => {
                      setAgentPickerOpen(false);
                      router.push("/settings/agents");
                    }}
                  >
                    <Text style={styles.configureUnavailableText}>{t("agent.configureUnavailable")}</Text>
                    <AppIcon name="chevron-right" color={colors.accent} size={16} />
                  </AppPressable>
                </View>
              ) : null}
            </View>
          </View>
        </Modal>
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
    fontWeight: "800",
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
    fontWeight: "800",
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
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFill, backgroundColor: "rgba(0, 0, 0, 0.4)" },
  actionSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.large,
    borderTopRightRadius: radii.large,
    padding: spacing.large,
    gap: spacing.medium,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.line,
    alignSelf: "center",
  },
  unavailableAgents: {
    marginTop: spacing.small,
    paddingTop: spacing.medium,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    gap: spacing.xsmall,
  },
  unavailableTitle: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  unavailableText: { color: colors.muted, fontSize: 12 },
  configureUnavailable: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: spacing.small,
  },
  configureUnavailableText: { color: colors.accent, fontSize: 12, fontWeight: "700" },
});
