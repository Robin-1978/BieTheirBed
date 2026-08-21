import * as DocumentPicker from "expo-document-picker";
import * as Crypto from "expo-crypto";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Linking from "expo-linking";
import * as Sharing from "expo-sharing";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  AppState,
  FlatList,
  Image,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ChatTurnWatcher } from "@/api/chatTurnWatcher";
import {
  assistantArtifactItems,
  resolveAssistantArtifactFile,
  type AssistantArtifactItem,
  type ResolvedArtifactFile,
} from "@/api/chatArtifacts";
import { GatewayError } from "@/api/gatewayClient";
import type { ArtifactInput, ChatApproval, ChatTurnSnapshot, HumanInteraction } from "@/api/models";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppMarkdown } from "@/components/AppMarkdown";
import { AppPressable } from "@/components/AppPressable";
import { ApprovalRequestDetails } from "@/components/ApprovalRequestDetails";
import { AgentSelector } from "@/components/AgentSelector";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { InteractionCard } from "@/components/InteractionCard";
import { PrimarySwipeNavigation } from "@/components/PrimarySwipeNavigation";
import { TurnProgress } from "@/components/TurnProgress";
import { useI18n } from "@/i18n";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { loadConversationDraft, removeConversationDraft, storeConversationDraft } from "@/security/conversationDrafts";
import { useGateway } from "@/state/GatewayProvider";
import { shouldResetConversation } from "@/state/conversationTransition";
import { loadConversationCache, storeConversationCache } from "@/storage/conversationCache";
import { mergeConversationTurns } from "@/storage/conversationMerge";
import { prepareImageAttachment } from "@/media/prepareImageAttachment";
import { agentImageSupport } from "@/media/agentImageSupport";
import { colors } from "@/theme";

type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
  status?: "pending" | "uploading" | "uploaded" | "failed";
  uploaded?: ArtifactInput;
};

type InputMode = "text" | "voice";

type PendingChatTurn = {
  localId: string;
  requestId: string;
  userInput: string;
  attachments: PendingAttachment[];
  state: "sending" | "failed";
  error: string;
};

type ChatListItem =
  | { kind: "turn"; key: string; turn: ChatTurnSnapshot }
  | { kind: "pending"; key: string; pending: PendingChatTurn };

type Feedback = {
  text: string;
  tone: "success" | "error" | "info" | "warning";
};

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export default function ChatScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const insets = useSafeAreaInsets();
  const gatewayRef = useRef(gateway);
  gatewayRef.current = gateway;
  const params = useLocalSearchParams<{
    capturedUri?: string;
    capturedName?: string;
    workspaceId?: string;
    workspaceName?: string;
    nodeId?: string;
  }>();
  const list = useRef<FlatList<ChatListItem>>(null);
  const followLatest = useRef(true);
  const userDragging = useRef(false);
  const initialScrollPending = useRef(true);
  const scrollIntent = useRef<"none" | "instant" | "smooth">("instant");
  const smoothScrollUntil = useRef(0);
  const scrollFrame = useRef<number | null>(null);
  const displayedSession = useRef("");
  const draftReady = useRef(false);
  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [pendingTurn, setPendingTurn] = useState<PendingChatTurn | null>(null);
  const [nextTurnCursor, setNextTurnCursor] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [preservingOlder, setPreservingOlder] = useState(false);
  const [text, setText] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [resolvingApproved, setResolvingApproved] = useState<boolean | null>(null);
  const [resolvingInteraction, setResolvingInteraction] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [validatingInput, setValidatingInput] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const showFeedback = useCallback((value: string, tone: Feedback["tone"] = "error") => {
    setFeedback({ text: value, tone });
  }, []);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useAudioRecorderState(recorder, 250);
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
    if (!feedback || feedback.tone === "error" || feedback.tone === "warning") return;
    const timeout = setTimeout(() => setFeedback(null), 3200);
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
    const switchedConversation = shouldResetConversation(
      previousSession,
      sessionHandle,
    );
    if (switchedConversation) {
      setTurns([]);
      setPendingTurn(null);
      setNextTurnCursor("");
      setPreservingOlder(false);
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
    return () => {
      active = false;
    };
  }, [gateway.sessionHandle, refresh, turnWatcher]);

  useEffect(() => {
    if (!gateway.sessionHandle || !turns.length) return;
    const timeout = setTimeout(() => {
      void storeConversationCache(gateway.sessionHandle, turns);
    }, 250);
    return () => clearTimeout(timeout);
  }, [gateway.sessionHandle, turns]);

  useFocusEffect(useCallback(() => {
    void refresh();
  }, [refresh]));

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") void refresh();
    });
    return () => subscription.remove();
  }, [refresh]);

  useEffect(() => {
    let active = true;
    draftReady.current = false;
    void loadConversationDraft(gateway.sessionHandle).then((draft) => {
      if (!active) return;
      setText(draft);
      draftReady.current = true;
    });
    return () => { active = false; };
  }, [gateway.sessionHandle]);

  useEffect(() => {
    if (!draftReady.current) return;
    const timeout = setTimeout(() => {
      void storeConversationDraft(gateway.sessionHandle, text);
    }, 300);
    return () => clearTimeout(timeout);
  }, [gateway.sessionHandle, text]);

  useEffect(() => {
    const uri = params.capturedUri?.trim();
    if (!uri) return;
    setAttachments((current) => current.some((item) => item.uri === uri)
      ? current
      : [...current, {
          uri,
          name: params.capturedName?.trim() || `camera-${Date.now()}.jpg`,
          mediaType: "image/jpeg",
        }]);
    router.setParams({ capturedUri: "", capturedName: "" });
  }, [params.capturedName, params.capturedUri]);

  const activeTurn = useMemo(
    () => [...turns].reverse().find((turn) => !TERMINAL_STATES.has(turn.state)) ?? null,
    [turns],
  );
  const sending = pendingTurn?.state === "sending";
  const hasComposerContent = Boolean(text.trim() || attachments.length);
  const canSend = Boolean(
    !pendingTurn
      && !activeTurn
      && !validatingInput
      && gateway.client
      && !gateway.requiredUpdate
      && hasComposerContent,
  );
  const listItems = useMemo<ChatListItem[]>(() => [
    ...turns.map((turn) => ({ kind: "turn" as const, key: turn.turn_id, turn })),
    ...(pendingTurn
      ? [{ kind: "pending" as const, key: pendingTurn.localId, pending: pendingTurn }]
      : []),
  ], [pendingTurn, turns]);

  async function chooseFile() {
    const picked = await DocumentPicker.getDocumentAsync({
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    const available = Math.max(0, 8 - attachments.length);
    const prepared = await Promise.all(picked.assets.slice(0, available).map(async (asset) => {
      const mediaType = asset.mimeType ?? "application/octet-stream";
      if (!mediaType.startsWith("image/")) {
        return { uri: asset.uri, name: asset.name, mediaType };
      }
      return prepareImageAttachment(asset.uri, asset.name);
    }));
    setAttachments((current) => [...current, ...prepared].slice(0, 8));
  }

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
          attachments: current.attachments.map((candidate, candidateIndex) => candidateIndex === index ? { ...candidate, status: "uploading" } : candidate),
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
            attachments: current.attachments.map((candidate, candidateIndex) => candidateIndex === index ? completed : candidate),
          } : current);
          return completed;
        } catch {
          const failed = { ...item, status: "failed" as const };
          setPendingTurn((current) => current?.localId === pending.localId ? {
            ...current,
            attachments: current.attachments.map((candidate, candidateIndex) => candidateIndex === index ? failed : candidate),
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
    } catch (error) {
      setPendingTurn({
        ...pending,
        state: "failed",
        error: t("chat.sendFailed"),
      });
    }
  }

  async function send() {
    if (!gateway.client || !canSend) return;
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
      } catch {
        // Capability preflight is advisory. The Node remains authoritative and
        // will return a durable failure code if configuration changed meanwhile.
      } finally {
        setValidatingInput(false);
      }
    }
    const pending: PendingChatTurn = {
      localId: `pending:${Crypto.randomUUID()}`,
      requestId: Crypto.randomUUID(),
      userInput: text.trim() || t("chat.attachmentOnly"),
      attachments: attachments.map((item) => ({ ...item, status: item.uploaded ? "uploaded" : "pending" })),
      state: "sending",
      error: "",
    };
    scrollIntent.current = "smooth";
    followLatest.current = true;
    setPendingTurn(pending);
    setText("");
    setAttachments([]);
    void removeConversationDraft(gateway.sessionHandle);
    void submitPendingTurn(pending);
  }

  function editPendingTurn(pending: PendingChatTurn) {
    setText(pending.userInput);
    setAttachments(pending.attachments.map((item) => ({ ...item, status: item.uploaded ? "uploaded" : undefined })));
    setPendingTurn(null);
  }

  async function retryAttachment(index: number) {
    const item = attachments[index];
    if (!item || item.status !== "failed" || !gateway.client) return;
    setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "uploading" } : value));
    try {
      const sessionHandle = await gateway.ensureConversation();
      const response = await fetch(item.uri);
      const bytes = await response.arrayBuffer();
      const uploaded = await gateway.runAuthenticated((client) => client.uploadArtifact({
        sessionHandle,
        bytes,
        mediaType: item.mediaType,
        name: item.name,
        caption: item.name,
      }));
      setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "uploaded", uploaded } : value));
      showFeedback(t("chat.attachmentUploaded"), "success");
    } catch {
      setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "failed" } : value));
      showFeedback(t("chat.attachmentRetryFailed"), "error");
    }
  }

  async function toggleRecording() {
    if (!gateway.client || transcribing) return;
    if (recording.isRecording) {
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
    } catch (error) {
      await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
      showFeedback(t("chat.recordingFailed"), "error");
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
        if (!item.approvals.some((candidate) => candidate.approval_id === result.approval.approval_id)) {
          return item;
        }
        return {
          ...item,
          approvals: item.approvals.map((candidate) => (
            candidate.approval_id === result.approval.approval_id
              ? result.approval
              : candidate
          )),
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
    } catch (error) {
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
        interactions: (turn.interactions ?? []).map((candidate) => candidate.interaction_id === interaction.interaction_id ? result.interaction : candidate),
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
    } catch (error) {
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
    } catch (error) {
      showFeedback(t("chat.fileSaveFailed"), "error");
    }
  }, [loadArtifact, showFeedback, t]);

  async function startNewTopic(agentId?: string) {
    if (startingTopic || sending) return;
    setStartingTopic(true);
    try {
      const previousSession = gateway.sessionHandle;
      await gateway.newConversation(agentId);
      void removeConversationDraft(previousSession).catch(() => undefined);
      void removeConversationDraft("").catch(() => undefined);
      setTurns([]);
      setPendingTurn(null);
      setNextTurnCursor("");
      scrollIntent.current = "instant";
      turnWatcher.closeAll();
      setText("");
      setAttachments([]);
      setFeedback(null);
    } catch (error) {
      showFeedback(t("chat.newTopicFailed"), "error");
    } finally {
      setStartingTopic(false);
    }
  }

  async function loadOlderTurns() {
    if (!gateway.sessionHandle || !nextTurnCursor || loadingOlder) return;
    setLoadingOlder(true);
    scrollIntent.current = "none";
    try {
      const page = await gateway.runAuthenticated(
        (client) => client.listChatTurns(gateway.sessionHandle, 100, nextTurnCursor),
      );
      setPreservingOlder(true);
      setTurns((current) => {
        const existing = new Set(current.map((turn) => turn.turn_id));
        return [...page.turns.filter((turn) => !existing.has(turn.turn_id)), ...current];
      });
      setNextTurnCursor(page.nextCursor);
    } catch (error) {
      showFeedback(t("chat.olderFailed"), "error");
    } finally {
      setLoadingOlder(false);
    }
  }

  async function cancelTurn(turn: ChatTurnSnapshot) {
    if (cancelling) return;
    setCancelling(turn.turn_id);
    setTurns((current) => current.map((item) => item.turn_id === turn.turn_id
      ? { ...item, cancel_requested: true }
      : item));
    try {
      const cancelled = await gateway.runAuthenticated((client) => client.cancelChatTurn(turn.turn_id));
      setTurns((current) => current.map((item) => item.turn_id === cancelled.turn_id ? cancelled : item));
    } catch {
      setTurns((current) => current.map((item) => item.turn_id === turn.turn_id ? turn : item));
      showFeedback(t("chat.stopFailed"), "error");
    } finally {
      setCancelling("");
    }
  }

  async function retryTurn(turn: ChatTurnSnapshot) {
    try {
      const accepted = await gateway.runAuthenticated((client) => client.retryChatTurn(turn.turn_id));
      setTurns((current) => [...current, accepted]);
      watchTurn(accepted.turn_id);
    } catch {
      showFeedback(t("chat.retryFailed"), "error");
    }
  }

  function editTurn(turn: ChatTurnSnapshot) {
    setText(turn.user_input);
    showFeedback(t("chat.editedToComposer"), "info");
  }

  const stoppingResponse = Boolean(activeTurn);
  const primaryDisabled = stoppingResponse
    ? Boolean(cancelling)
    : inputMode === "text"
      ? !canSend
      : sending || transcribing || (!gateway.client && !recording.isRecording);
  const selectedAgentId = gateway.activeAgentId || gateway.selectedAgentId;
  const selectedAgentName = gateway.agents.find((agent) => agent.agent_id === selectedAgentId)?.display_name ?? selectedAgentId;
  const agentLocked = Boolean(gateway.activeAgentId || gateway.sessionHandle);

  return (
    <PrimarySwipeNavigation current="chat">
      <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      keyboardVerticalOffset={insets.top + (Platform.OS === "ios" ? 44 : 56)}
    >
      <View style={styles.topbar}>
        <Text style={styles.subtitle} numberOfLines={1}>{t("chat.agentSubtitle", { agent: selectedAgentName })}</Text>
        <View style={styles.topActions}>
          <AppPressable
            accessibilityLabel={t("chat.newTopic")}
            onPress={() => void startNewTopic()}
            disabled={startingTopic || sending}
            style={styles.topAction}
          >
            {startingTopic
              ? <ActivityIndicator color={colors.accent} size="small" />
              : <AppIcon name="new-topic" color={colors.accent} size={22} />}
          </AppPressable>
          <AppPressable
            accessibilityLabel={t("chat.history")}
            onPress={() => router.push({ pathname: "/conversations", params: nodeRouteParams(params) })}
            style={styles.topAction}
          >
            <AppIcon name="history" color={colors.accent} size={21} />
          </AppPressable>
          {gateway.agents.length ? (
            <AppPressable
              accessibilityRole="button"
              accessibilityLabel={agentLocked
                ? t("agent.changeConversation")
                : t("agent.selectConversation")}
              onPress={() => setAgentPickerOpen(true)}
              style={[
                styles.topAction,
                !agentLocked && selectedAgentId !== gateway.defaultAgentId && styles.selectedAgentAction,
              ]}
            >
              <AppIcon
                name="agent"
                color={!agentLocked && selectedAgentId !== gateway.defaultAgentId ? colors.accent : colors.muted}
                size={21}
              />
            </AppPressable>
          ) : null}
        </View>
      </View>
      {gateway.status !== "ready" ? (
        <View style={styles.connectionBanner}>
          <View style={styles.bannerCopy}>
            <Text style={styles.connectionTitle}>{t("chat.disconnected")}</Text>
            <Text style={styles.connectionDetail}>{gateway.status === "error" ? t("chat.connectionProblem") : t("chat.reconnecting")}</Text>
          </View>
          <AppPressable onPress={() => void gateway.reconnect()} style={styles.bannerButton}>
            <Text style={styles.bannerButtonText}>{t("chat.retryConnection")}</Text>
          </AppPressable>
        </View>
      ) : null}
      {gateway.availableUpdate ? (
        <Pressable style={styles.updateBanner} onPress={() => router.push("/update")}>
          <View style={styles.bannerCopy}>
            <Text style={styles.updateTitle}>{t("tasks.updateAvailable", { version: gateway.availableUpdate.version_name })}</Text>
            <Text style={styles.updateDetail}>{t("chat.updateDetail")}</Text>
          </View>
          <Text style={styles.updateLink}>{t("chat.update")}</Text>
        </Pressable>
      ) : null}
      {gateway.requiredUpdate ? (
        <Pressable style={styles.requiredUpdateBanner} onPress={() => router.push("/update")}>
          <Text style={styles.requiredUpdateTitle}>{t("chat.requiredUpdate")}</Text>
          <Text style={styles.updateDetail}>{t("chat.requiredUpdateDetail")}</Text>
        </Pressable>
      ) : null}
      <View style={styles.listArea}>
      <FlatList
        ref={list}
        style={styles.list}
        data={listItems}
        keyExtractor={(item) => item.key}
        contentContainerStyle={styles.messages}
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
        maintainVisibleContentPosition={preservingOlder ? { minIndexForVisible: 0 } : undefined}
        onContentSizeChange={() => {
          if (preservingOlder) {
            requestAnimationFrame(() => setPreservingOlder(false));
            return;
          }
          if (initialScrollPending.current && listItems.length) {
            initialScrollPending.current = false;
            followLatest.current = true;
            scrollIntent.current = "none";
            if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
            scrollFrame.current = requestAnimationFrame(() => {
              scrollFrame.current = null;
              list.current?.scrollToEnd({ animated: false });
            });
            return;
          }
          if (!followLatest.current || loadingOlder) return;
          const mode = scrollIntent.current;
          scrollIntent.current = "none";
          if (mode === "none" && Date.now() < smoothScrollUntil.current) return;
          if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
          scrollFrame.current = requestAnimationFrame(() => {
            scrollFrame.current = null;
            if (mode === "smooth") smoothScrollUntil.current = Date.now() + 360;
            list.current?.scrollToEnd({ animated: mode === "smooth" });
          });
        }}
        onScrollBeginDrag={() => { userDragging.current = true; }}
        onScroll={({ nativeEvent }) => {
          if (!userDragging.current) return;
          const distance = nativeEvent.contentSize.height
            - nativeEvent.layoutMeasurement.height
            - nativeEvent.contentOffset.y;
          followLatest.current = distance < 80;
          setShowJumpToLatest(!followLatest.current);
        }}
        onScrollEndDrag={({ nativeEvent }) => {
          const distance = nativeEvent.contentSize.height
            - nativeEvent.layoutMeasurement.height
            - nativeEvent.contentOffset.y;
          followLatest.current = distance < 80;
          setShowJumpToLatest(!followLatest.current);
          userDragging.current = false;
        }}
        onMomentumScrollEnd={({ nativeEvent }) => {
          const distance = nativeEvent.contentSize.height
            - nativeEvent.layoutMeasurement.height
            - nativeEvent.contentOffset.y;
          followLatest.current = distance < 80;
          setShowJumpToLatest(!followLatest.current);
        }}
        scrollEventThrottle={32}
        ListHeaderComponent={nextTurnCursor ? (
          <AppPressable
            disabled={loadingOlder}
            onPress={() => void loadOlderTurns()}
            style={styles.loadOlder}
          >
            {loadingOlder
              ? <ActivityIndicator color={colors.accent} size="small" />
              : <Text style={styles.loadOlderText}>{t("chat.loadOlder")}</Text>}
          </AppPressable>
        ) : null}
        ListEmptyComponent={(
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{t("chat.empty")}</Text>
            <Text style={styles.emptyBody}>{t("chat.emptyBody")}</Text>
            <View style={styles.emptyExamples}>
              {[t("chat.exampleGitLab"), t("chat.exampleJira"), t("chat.exampleTask")].map((example) => (
                <AppPressable key={example} onPress={() => setText(example)} style={styles.emptyExample}>
                  <Text style={styles.emptyExampleText}>{example}</Text>
                  <AppIcon name="chevron-right" color={colors.accent} size={16} />
                </AppPressable>
              ))}
            </View>
            <Text style={styles.emptyHint}>{t("chat.emptyTaskHint")}</Text>
          </View>
        )}
        renderItem={({ item }) => item.kind === "pending" ? (
          <PendingTurn
            pending={item.pending}
            onRetry={submitPendingTurn}
            onEdit={editPendingTurn}
          />
        ) : (
          <ChatTurn
            turn={item.turn}
            resolving={resolving}
            resolvingApproved={resolvingApproved}
            resolvingInteraction={resolvingInteraction}
            onResolve={resolve}
            onResolveInteraction={resolveInteraction}
            onLoadArtifact={loadArtifact}
            onOpenArtifact={openArtifact}
            onSaveArtifact={saveArtifact}
            onRetry={retryTurn}
            onEdit={editTurn}
          />
        )}
      />
      {showJumpToLatest ? (
        <AppPressable
          accessibilityLabel={t("chat.jumpLatest")}
          onPress={() => {
            followLatest.current = true;
            setShowJumpToLatest(false);
            list.current?.scrollToEnd({ animated: true });
          }}
          style={styles.jumpLatest}
        >
          <AppIcon name="arrow-down" color={colors.accent} size={18} />
          <Text style={styles.jumpLatestText}>{t("chat.jumpLatest")}</Text>
        </AppPressable>
      ) : null}
      </View>
      {attachments.length ? (
        <View style={styles.attachmentStrip}>
          {attachments.map((item, index) => (
            <View
              key={`${item.uri}:${index}`}
              style={styles.attachment}
            >
              {item.mediaType.startsWith("image/") ? <Image source={{ uri: item.uri }} style={styles.thumbnail} /> : null}
              <Pressable disabled={item.status !== "failed"} onPress={() => void retryAttachment(index)} style={styles.attachmentCopy}>
                <Text style={styles.attachmentName} numberOfLines={1}>{item.name}</Text>
                {item.status ? <Text style={[styles.attachmentStatus, item.status === "failed" && styles.attachmentFailed]}>{attachmentStatusLabel(item.status, t)}</Text> : null}
              </Pressable>
              <AppPressable accessibilityLabel={t("chat.removeAttachment", { name: item.name })} onPress={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))} style={styles.removeAction}>
                <AppIcon name="x" color={colors.muted} size={17} />
              </AppPressable>
            </View>
          ))}
        </View>
      ) : null}
      {feedback ? (
        <Pressable
          accessibilityRole="alert"
          onPress={() => setFeedback(null)}
          style={[
            styles.feedbackBanner,
            feedback.tone === "error" && styles.feedbackError,
            feedback.tone === "warning" && styles.feedbackWarning,
            feedback.tone === "success" && styles.feedbackSuccess,
          ]}
        >
          <Text style={[styles.feedbackText, feedback.tone === "error" && styles.feedbackErrorText]}>{feedback.text}</Text>
        </Pressable>
      ) : null}
      <View style={styles.composer}>
        <AppPressable
          accessibilityLabel={t("chat.add")}
          onPress={() => setActionsOpen(true)}
          style={styles.roundAction}
        >
          <AppIcon name="plus" color={colors.accent} />
        </AppPressable>
        <View style={styles.inputShell}>
          <AppPressable
            accessibilityLabel={inputMode === "text" ? t("chat.switchVoice") : t("chat.switchText")}
            disabled={recording.isRecording || transcribing}
            onPress={() => setInputMode((current) => current === "text" ? "voice" : "text")}
            style={styles.inputMode}
          >
            <AppIcon name={inputMode === "text" ? "mic" : "keyboard"} color={colors.muted} size={20} />
          </AppPressable>
          <TextInput
            editable={inputMode === "text"}
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder={inputMode === "text" ? t("chat.placeholder") : t("chat.voicePlaceholder")}
            placeholderTextColor={colors.muted}
            multiline
          />
        </View>
        <AppPressable
          accessibilityLabel={stoppingResponse
            ? t("chat.stop")
            : inputMode === "voice"
              ? recording.isRecording ? t("chat.stopRecording") : t("chat.startRecording")
              : t("chat.send")}
          onPress={() => {
            if (activeTurn) void cancelTurn(activeTurn);
            else if (inputMode === "voice") void toggleRecording();
            else void send();
          }}
          disabled={primaryDisabled}
          style={[
            styles.primaryAction,
            recording.isRecording && styles.primaryRecording,
            stoppingResponse && styles.primaryStopping,
            primaryDisabled && styles.sendDisabled,
          ]}
        >
          {sending || validatingInput || transcribing || cancelling ? (
            <ActivityIndicator color="white" size="small" />
          ) : stoppingResponse ? (
            <AppIcon name="stop" color="white" size={17} />
          ) : recording.isRecording ? (
            <View style={styles.recordingContent}>
              <AppIcon name="stop" color="white" size={17} />
              <Text style={styles.recordingTime}>{Math.round(recording.durationMillis / 1000)}s</Text>
            </View>
          ) : inputMode === "text" ? (
            <AppIcon name="send" color="white" size={19} />
          ) : (
            <AppIcon name="mic" color="white" />
          )}
        </AppPressable>
      </View>
      <ArtifactViewer
        file={imagePreview}
        onClose={() => setImagePreview(null)}
        onMessage={(value, tone = "info") => showFeedback(value, tone)}
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
      <Modal
        animationType="fade"
        onRequestClose={() => setActionsOpen(false)}
        transparent
        visible={actionsOpen}
      >
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setActionsOpen(false)} />
          <View style={styles.actionSheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>{t("chat.addContent")}</Text>
            <View style={styles.sheetActions}>
              <MediaAction
                icon="camera"
                label={t("chat.camera")}
                onPress={() => {
                  setActionsOpen(false);
                  router.push({ pathname: "/capture", params: nodeRouteParams(params) });
                }}
              />
              <MediaAction
                icon="file"
                label={t("chat.file")}
                onPress={() => {
                  setActionsOpen(false);
                  void chooseFile();
                }}
              />
            </View>
          </View>
        </View>
      </Modal>
      </KeyboardAvoidingView>
    </PrimarySwipeNavigation>
  );
}

function MediaAction({ icon, label, onPress }: { icon: AppIconName; label: string; onPress(): void }) {
  return (
    <Pressable onPress={onPress} style={styles.mediaAction}>
      <View style={styles.mediaIcon}><AppIcon name={icon} color={colors.accent} size={28} /></View>
      <Text style={styles.mediaLabel}>{label}</Text>
    </Pressable>
  );
}

function nodeRouteParams(params: { workspaceId?: string; workspaceName?: string; nodeId?: string }) {
  return {
    workspaceId: params.workspaceId ?? "",
    workspaceName: params.workspaceName ?? "",
    nodeId: params.nodeId ?? "",
  };
}

const ChatTurn = memo(function ChatTurn({
  turn,
  resolving,
  resolvingApproved,
  resolvingInteraction,
  onResolve,
  onResolveInteraction,
  onLoadArtifact,
  onOpenArtifact,
  onSaveArtifact,
  onRetry,
  onEdit,
}: {
  turn: ChatTurnSnapshot;
  resolving: string;
  resolvingApproved: boolean | null;
  resolvingInteraction: string;
  onResolve(approval: ChatApproval, approved: boolean): void;
  onResolveInteraction(interaction: HumanInteraction, value: Record<string, unknown>): void;
  onLoadArtifact(item: AssistantArtifactItem): Promise<ResolvedArtifactFile>;
  onOpenArtifact(item: AssistantArtifactItem): Promise<void>;
  onSaveArtifact(item: AssistantArtifactItem): Promise<void>;
  onRetry(turn: ChatTurnSnapshot): void;
  onEdit(turn: ChatTurnSnapshot): void;
}) {
  const { t } = useI18n();
  const terminal = TERMINAL_STATES.has(turn.state);
  const response = terminal ? turn.final_output || turn.content : "";
  const approval = turn.approvals.find((item) => item.state === "pending") ?? null;
  const interaction = turn.interactions?.find((item) => item.state === "pending") ?? null;
  const artifactItems = useMemo(() => assistantArtifactItems(turn.artifacts), [turn.artifacts]);
  return (
    <View style={styles.turn}>
      <View style={styles.userBubble}>
        <Text style={styles.userText}>{turn.user_input}</Text>
        {turn.attachments.length ? <Text style={styles.userMeta}>{t("chat.attachments", { count: turn.attachments.length })}</Text> : null}
      </View>
      <View style={styles.assistantBubble}>
        <TurnProgress turn={turn} />
        {response ? <AppMarkdown value={response} style={styles.markdownList} /> : null}
        {artifactItems.length ? (
          <View style={styles.generatedArtifacts}>
            {artifactItems.map((item) => (
              <AssistantArtifact
                key={item.key}
                item={item}
                onLoad={onLoadArtifact}
                onOpen={onOpenArtifact}
                onSave={onSaveArtifact}
              />
            ))}
          </View>
        ) : null}
        {interaction ? (
          <InteractionCard
            interaction={interaction}
            submitting={resolvingInteraction === interaction.interaction_id}
            onSubmit={(value) => onResolveInteraction(interaction, value)}
          />
        ) : null}
        {approval ? (
          <View style={styles.approval}>
            <ApprovalRequestDetails toolName={approval.tool_name} arguments={approval.arguments} display={approval.display} />
            <View style={styles.approvalActions}>
              <AppPressable style={styles.deny} disabled={Boolean(resolving)} onPress={() => onResolve(approval, false)}>
                {resolving === approval.approval_id && resolvingApproved === false
                  ? <ActivityIndicator color={colors.ink} size="small" />
                  : <Text style={styles.denyText}>{t("execution.denyAction")}</Text>}
              </AppPressable>
              <AppPressable style={styles.approve} disabled={Boolean(resolving)} onPress={() => onResolve(approval, true)}>
                {resolving === approval.approval_id && resolvingApproved === true
                  ? <ActivityIndicator color="white" size="small" />
                  : <Text style={styles.approveText}>{t("execution.allowAction")}</Text>}
              </AppPressable>
            </View>
          </View>
        ) : null}
        {turn.state === "failed" || turn.state === "cancelled" ? (
          <View style={styles.turnActions}>
            <Pressable accessibilityRole="button" onPress={() => onRetry(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.retry")}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => onEdit(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.editResend")}</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
    </View>
  );
});

function PendingTurn({
  pending,
  onRetry,
  onEdit,
}: {
  pending: PendingChatTurn;
  onRetry(pending: PendingChatTurn): void;
  onEdit(pending: PendingChatTurn): void;
}) {
  const { t } = useI18n();
  return (
    <View style={styles.turn}>
      <View style={styles.userBubble}>
        <Text style={styles.userText}>{pending.userInput}</Text>
        {pending.attachments.length
          ? <Text style={styles.userMeta}>{t("chat.attachments", { count: pending.attachments.length })}</Text>
          : null}
        {pending.attachments.length ? (
          <View style={styles.pendingAttachments}>
            {pending.attachments.map((item, index) => (
              <View key={`${item.uri}:${index}`} style={styles.pendingAttachmentRow}>
                {item.status === "uploading" ? <ActivityIndicator color={colors.accentSoft} size="small" /> : null}
                <Text numberOfLines={1} style={styles.pendingAttachmentName}>{item.name}</Text>
                <Text style={[styles.pendingAttachmentState, item.status === "failed" && styles.pendingAttachmentFailed]}>{attachmentStatusLabel(item.status ?? "pending", t)}</Text>
              </View>
            ))}
          </View>
        ) : null}
      </View>
      <View style={styles.assistantBubble}>
        <View style={styles.activityRow}>
          {pending.state === "sending" ? <ActivityIndicator color={colors.accent} size="small" /> : null}
          <Text style={pending.state === "failed" ? styles.pendingError : styles.activity}>
            {pending.state === "sending" ? t("chat.sending") : pending.error || t("chat.sendFailed")}
          </Text>
        </View>
        {pending.state === "failed" ? (
          <View style={styles.turnActions}>
            <AppPressable accessibilityRole="button" onPress={() => onRetry(pending)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("chat.retry")}</Text>
            </AppPressable>
            <AppPressable accessibilityRole="button" onPress={() => onEdit(pending)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>{t("taskDetail.edit")}</Text>
            </AppPressable>
          </View>
        ) : null}
      </View>
    </View>
  );
}

function AssistantArtifact({
  item,
  onLoad,
  onOpen,
  onSave,
}: {
  item: AssistantArtifactItem;
  onLoad(item: AssistantArtifactItem): Promise<ResolvedArtifactFile>;
  onOpen(item: AssistantArtifactItem): Promise<void>;
  onSave(item: AssistantArtifactItem): Promise<void>;
}) {
  const { t } = useI18n();
  const [previewUri, setPreviewUri] = useState("");
  const [loading, setLoading] = useState(item.isImage);
  const [failed, setFailed] = useState(false);
  const [opening, setOpening] = useState(false);
  const [saving, setSaving] = useState(false);
  const request = useRef(0);

  const loadPreview = useCallback(async () => {
    if (!item.isImage) return;
    const currentRequest = ++request.current;
    setLoading(true);
    setFailed(false);
    try {
      const resolved = await onLoad(item);
      if (request.current === currentRequest) setPreviewUri(resolved.uri);
    } catch {
      if (request.current === currentRequest) setFailed(true);
    } finally {
      if (request.current === currentRequest) setLoading(false);
    }
  }, [item, onLoad]);

  useEffect(() => {
    void loadPreview();
    return () => { request.current += 1; };
  }, [loadPreview]);

  const open = useCallback(async () => {
    if (failed) {
      await loadPreview();
      return;
    }
    setOpening(true);
    try {
      await onOpen(item);
    } finally {
      setOpening(false);
    }
  }, [failed, item, loadPreview, onOpen]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await onSave(item);
    } finally {
      setSaving(false);
    }
  }, [item, onSave]);

  if (item.isImage) {
    return (
      <Pressable
        accessibilityLabel={failed ? t("chat.reloadArtifact", { name: item.displayName }) : t("chat.openArtifact", { name: item.displayName })}
        disabled={loading || opening}
        onPress={() => void open()}
        style={styles.generatedImageCard}
      >
        {previewUri ? (
          <Image
            onError={() => {
              setPreviewUri("");
              setFailed(true);
            }}
            resizeMode="contain"
            source={{ uri: previewUri }}
            style={styles.generatedImage}
          />
        ) : (
          <View style={styles.generatedImageState}>
            {loading ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            <Text style={failed ? styles.generatedArtifactError : styles.generatedArtifactHint}>
              {failed ? t("chat.imageRetry") : t("chat.imageLoading")}
            </Text>
          </View>
        )}
        <View style={styles.generatedArtifactCaption}>
          <Text style={styles.generatedArtifactName} numberOfLines={1}>{item.displayName}</Text>
          {opening ? <ActivityIndicator color={colors.accent} size="small" /> : null}
        </View>
      </Pressable>
    );
  }

  return (
    <View style={styles.generatedFile}>
      <View style={styles.generatedFileBadge}><Text style={styles.generatedFileBadgeText}>{t("execution.attachment")}</Text></View>
      <Text style={styles.generatedArtifactName} numberOfLines={2}>{item.displayName}</Text>
      <AppPressable
        accessibilityLabel={t("chat.openOrShare", { name: item.displayName })}
        disabled={opening || saving}
        onPress={() => void open()}
        style={styles.fileAction}
      >
        {opening ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.fileActionText}>{t("execution.open")}</Text>}
      </AppPressable>
      <AppPressable
        accessibilityLabel={t("chat.saveArtifact", { name: item.displayName })}
        disabled={opening || saving}
        onPress={() => void save()}
        style={styles.fileAction}
      >
        {saving ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.fileActionText}>{t("execution.save")}</Text>}
      </AppPressable>
    </View>
  );
}

function attachmentStatusLabel(status: NonNullable<PendingAttachment["status"]>, t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    pending: t("chat.uploadPending"),
    uploading: t("chat.uploading"),
    uploaded: t("chat.uploaded"),
    failed: t("chat.uploadRetry"),
  })[status];
}

function agentReasonLabel(reason: string, t: ReturnType<typeof useI18n>["t"]): string {
  if (reason === "runtime_unavailable") return t("agent.unavailableRuntime");
  if (reason === "delegate_only") return t("agent.unavailableDelegate");
  if (reason === "system_only") return t("agent.unavailableSystem");
  return t("agent.unavailableDisabled");
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  listArea: { flex: 1 },
  list: { flex: 1 },
  topbar: { paddingHorizontal: 16, paddingVertical: 10, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  subtitle: { color: colors.muted, fontSize: 13, flex: 1, marginRight: 8 },
  topActions: { flexDirection: "row", gap: 6 },
  topAction: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: 12 },
  selectedAgentAction: { backgroundColor: colors.accentSoft },
  messages: { padding: 16, paddingBottom: 24, gap: 18, flexGrow: 1 },
  empty: { marginTop: 42, alignSelf: "center", width: "100%", maxWidth: 520, gap: 10 },
  emptyTitle: { color: colors.ink, textAlign: "center", fontSize: 20, fontWeight: "700" },
  emptyBody: { color: colors.muted, textAlign: "center", lineHeight: 21, paddingHorizontal: 12 },
  emptyExamples: { marginTop: 6, gap: 8 },
  emptyExample: { minHeight: 46, paddingHorizontal: 13, borderRadius: 13, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, flexDirection: "row", alignItems: "center", gap: 8 },
  emptyExampleText: { color: colors.ink, flex: 1, fontWeight: "600", lineHeight: 20 },
  emptyHint: { color: colors.muted, textAlign: "center", fontSize: 12, lineHeight: 18, paddingHorizontal: 8 },
  loadOlder: { alignSelf: "center", paddingHorizontal: 14, paddingVertical: 8, marginBottom: 4 },
  loadOlderText: { color: colors.accent, fontWeight: "600", fontSize: 13 },
  jumpLatest: { position: "absolute", right: 16, bottom: 12, minHeight: 40, paddingHorizontal: 13, borderRadius: 20, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.surfaceElevated, borderWidth: 1, borderColor: colors.line },
  jumpLatestText: { color: colors.accent, fontSize: 13, fontWeight: "700" },
  turn: { gap: 8 },
  userBubble: { alignSelf: "flex-end", maxWidth: "84%", backgroundColor: colors.accent, borderRadius: 18, borderBottomRightRadius: 5, paddingHorizontal: 15, paddingVertical: 11 },
  userText: { color: "white", fontSize: 16, lineHeight: 23 },
  userMeta: { color: colors.accentSoft, fontSize: 12, marginTop: 5 },
  pendingAttachments: { marginTop: 7, gap: 5 },
  pendingAttachmentRow: { minHeight: 24, flexDirection: "row", alignItems: "center", gap: 6 },
  pendingAttachmentName: { color: "white", flex: 1, fontSize: 12 },
  pendingAttachmentState: { color: colors.accentSoft, fontSize: 10 },
  pendingAttachmentFailed: { color: "#FFD1CC" },
  assistantBubble: { alignSelf: "stretch", width: "100%", backgroundColor: colors.surface, borderRadius: 18, borderBottomLeftRadius: 5, padding: 15, borderWidth: 1, borderColor: colors.line },
  markdownList: { width: "100%", alignSelf: "stretch" },
  activityRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  activity: { color: colors.muted },
  pendingError: { color: colors.danger, flex: 1 },
  approval: { marginTop: 12, borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12, gap: 10 },
  approvalActions: { flexDirection: "row", gap: 10 },
  turnActions: { flexDirection: "row", gap: 10, marginTop: 12 },
  turnAction: { alignSelf: "flex-start", marginTop: 12, borderRadius: 10, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 12, paddingVertical: 8 },
  turnActionText: { color: colors.accent, fontWeight: "700" },
  deny: { flex: 1, alignItems: "center", borderWidth: 1, borderColor: colors.line, borderRadius: 11, padding: 10 },
  denyText: { color: colors.ink, fontWeight: "600" },
  approve: { flex: 1, alignItems: "center", backgroundColor: colors.accent, borderRadius: 11, padding: 10 },
  approveText: { color: "white", fontWeight: "700" },
  generatedArtifacts: { marginTop: 12, gap: 10 },
  generatedImageCard: { overflow: "hidden", borderWidth: 1, borderColor: colors.line, borderRadius: 12, backgroundColor: colors.background },
  generatedImage: { width: "100%", minHeight: 180, maxHeight: 360, aspectRatio: 16 / 9, backgroundColor: colors.background },
  generatedImageState: { minHeight: 150, alignItems: "center", justifyContent: "center", gap: 9, padding: 16 },
  generatedArtifactCaption: { minHeight: 42, paddingHorizontal: 11, paddingVertical: 9, flexDirection: "row", alignItems: "center", gap: 8, borderTopWidth: 1, borderTopColor: colors.line },
  generatedArtifactName: { color: colors.ink, flex: 1, fontWeight: "600" },
  generatedArtifactHint: { color: colors.muted, fontSize: 13 },
  generatedArtifactError: { color: colors.danger, fontSize: 13 },
  generatedFile: { minHeight: 54, padding: 10, flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderColor: colors.line, borderRadius: 12, backgroundColor: colors.background },
  fileAction: { minHeight: 36, minWidth: 48, alignItems: "center", justifyContent: "center", borderRadius: 9, backgroundColor: colors.accentSoft },
  fileActionText: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  generatedFileBadge: { borderRadius: 8, paddingHorizontal: 8, paddingVertical: 6, backgroundColor: colors.accentSoft },
  generatedFileBadgeText: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  attachmentStrip: { paddingHorizontal: 12, paddingVertical: 8, flexDirection: "row", gap: 8, borderTopWidth: 1, borderTopColor: colors.line },
  attachment: { maxWidth: 150, flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: colors.surface, borderRadius: 10, padding: 6, borderWidth: 1, borderColor: colors.line },
  thumbnail: { width: 34, height: 34, borderRadius: 6 },
  attachmentName: { color: colors.ink, fontSize: 12, flexShrink: 1 },
  attachmentCopy: { flex: 1 },
  attachmentStatus: { color: colors.muted, fontSize: 10, marginTop: 2 },
  attachmentFailed: { color: colors.danger },
  remove: { color: colors.muted, fontSize: 18 },
  removeAction: { width: 40, height: 40, alignItems: "center", justifyContent: "center", borderRadius: 10 },
  connectionBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: colors.warningSoft, flexDirection: "row", alignItems: "center", gap: 12 },
  bannerCopy: { flex: 1 },
  connectionTitle: { color: colors.ink, fontWeight: "700" },
  connectionDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  bannerButton: { paddingHorizontal: 13, paddingVertical: 8, borderRadius: 11, backgroundColor: colors.surface },
  bannerButtonText: { color: colors.accent, fontWeight: "700" },
  updateBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: colors.accentSoft, flexDirection: "row", alignItems: "center", gap: 12 },
  requiredUpdateBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: colors.dangerSoft, gap: 4 },
  requiredUpdateTitle: { color: colors.danger, fontWeight: "700" },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  updateLink: { color: colors.accent, fontWeight: "700" },
  feedbackBanner: { paddingHorizontal: 16, paddingVertical: 10, backgroundColor: colors.surfaceMuted, borderTopWidth: 1, borderTopColor: colors.line },
  feedbackWarning: { backgroundColor: colors.warningSoft },
  feedbackError: { backgroundColor: colors.dangerSoft },
  feedbackSuccess: { backgroundColor: colors.accentFaint },
  feedbackText: { color: colors.ink, textAlign: "center", fontSize: 13 },
  feedbackErrorText: { color: colors.danger },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.line },
  roundAction: { width: 42, height: 42, borderRadius: 21, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  inputShell: { flex: 1, minHeight: 42, maxHeight: 120, flexDirection: "row", alignItems: "flex-end", backgroundColor: colors.background, borderRadius: 16 },
  inputMode: { width: 44, height: 44, alignItems: "center", justifyContent: "center" },
  input: { flex: 1, minHeight: 42, maxHeight: 120, color: colors.ink, paddingRight: 13, paddingVertical: 10, textAlignVertical: "top" },
  primaryAction: { minWidth: 52, height: 42, paddingHorizontal: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, borderRadius: 14 },
  primaryRecording: { backgroundColor: colors.warning },
  primaryStopping: { backgroundColor: colors.stop },
  recordingContent: { flexDirection: "row", alignItems: "center", gap: 5 },
  recordingTime: { color: "white", fontSize: 12, fontWeight: "700" },
  sendDisabled: { opacity: 0.45 },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { position: "absolute", top: 0, right: 0, bottom: 0, left: 0, backgroundColor: colors.overlay },
  actionSheet: { backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 22, paddingTop: 10, paddingBottom: 34, gap: 18 },
  sheetHandle: { width: 38, height: 4, borderRadius: 2, backgroundColor: colors.line, alignSelf: "center" },
  sheetTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  unavailableAgents: { gap: 4, marginTop: 12, padding: 10, borderRadius: 12, backgroundColor: colors.background },
  unavailableTitle: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  unavailableText: { color: colors.muted, fontSize: 11, lineHeight: 17 },
  configureUnavailable: { minHeight: 34, flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, marginTop: 4, paddingTop: 7 },
  configureUnavailableText: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  sheetActions: { flexDirection: "row", gap: 24 },
  mediaAction: { width: 76, alignItems: "center", gap: 8 },
  mediaIcon: { width: 58, height: 58, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  mediaLabel: { color: colors.ink, fontWeight: "600" },
  previewRoot: { flex: 1, backgroundColor: "#090B0A" },
  previewToolbar: { position: "absolute", top: 0, left: 0, right: 0, zIndex: 2, minHeight: 64, paddingHorizontal: 14, paddingBottom: 6, flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: "rgba(9, 11, 10, 0.82)" },
  previewButton: { minHeight: 40, justifyContent: "center", paddingHorizontal: 8 },
  previewButtonText: { color: "white", fontWeight: "700" },
  previewName: { color: "white", flex: 1, textAlign: "center", fontSize: 13 },
  previewCanvas: { flex: 1, alignItems: "center", justifyContent: "center", overflow: "hidden" },
  previewImage: { width: "100%", height: "100%" },
  previewHint: { position: "absolute", alignSelf: "center", color: "rgba(255, 255, 255, 0.72)", fontSize: 12, backgroundColor: "rgba(9, 11, 10, 0.62)", borderRadius: 14, paddingHorizontal: 12, paddingVertical: 7 },
});
