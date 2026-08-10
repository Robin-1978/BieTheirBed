import * as DocumentPicker from "expo-document-picker";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { router, useLocalSearchParams } from "expo-router";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
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
import Svg, { Circle, Line, Path, Rect } from "react-native-svg";

import { ChatTurnWatcher } from "@/api/chatTurnWatcher";
import {
  assistantArtifactItems,
  resolveAssistantArtifactFile,
  type AssistantArtifactItem,
  type ResolvedArtifactFile,
} from "@/api/chatArtifacts";
import { GatewayError } from "@/api/gatewayClient";
import type { ArtifactInput, ChatApproval, ChatTurnSnapshot } from "@/api/models";
import { AppMarkdown } from "@/components/AppMarkdown";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import { loadConversationDraft, storeConversationDraft } from "@/security/conversationDrafts";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
  status?: "pending" | "uploading" | "uploaded" | "failed";
  uploaded?: ArtifactInput;
};

type InputMode = "text" | "voice";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export default function ChatScreen() {
  const gateway = useGateway();
  const insets = useSafeAreaInsets();
  const gatewayRef = useRef(gateway);
  gatewayRef.current = gateway;
  const params = useLocalSearchParams<{ capturedUri?: string; capturedName?: string }>();
  const list = useRef<FlatList<ChatTurnSnapshot>>(null);
  const nearBottom = useRef(true);
  const draftReady = useRef(false);
  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [text, setText] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [sending, setSending] = useState(false);
  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<ResolvedArtifactFile | null>(null);
  const [message, setMessage] = useState("");
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useAudioRecorderState(recorder, 250);
  const [turnWatcher] = useState(() => new ChatTurnWatcher({
    connection: () => gatewayRef.current.connection(),
    fetchSnapshot: (turnId) => gatewayRef.current.runAuthenticated(
      (client) => client.getChatTurn(turnId),
    ),
    onSnapshot: (snapshot) => {
      setTurns((current) => {
        const index = current.findIndex((turn) => turn.turn_id === snapshot.turn_id);
        if (index < 0) return [...current, snapshot];
        const next = [...current];
        next[index] = snapshot;
        return next;
      });
    },
    onUnavailable: () => setMessage("暂时无法继续接收回复，请检查网络后重试"),
  }));

  const watchTurn = useCallback((turnId: string) => {
    turnWatcher.watch(turnId);
  }, [turnWatcher]);

  const refresh = useCallback(async () => {
    if (!gateway.client || !gateway.sessionHandle) return;
    try {
      const history = await gateway.runAuthenticated(
        (client) => client.listChatTurns(gateway.sessionHandle, 100),
      );
      setTurns(history);
      for (const turn of history) {
        if (!TERMINAL_STATES.has(turn.state)) watchTurn(turn.turn_id);
      }
    } catch (error) {
      if (error instanceof GatewayError && error.status === 404) {
        await gateway.newConversation();
        setMessage("原会话已不可用，已为你创建一个新会话");
        return;
      }
      throw error;
    }
  }, [gateway.client, gateway.newConversation, gateway.runAuthenticated, gateway.sessionHandle, watchTurn]);

  useEffect(() => {
    setTurns([]);
    turnWatcher.closeAll();
    void refresh();
    return () => turnWatcher.closeAll();
  }, [gateway.sessionHandle, refresh, turnWatcher]);

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
    if (!draftReady.current || !gateway.sessionHandle) return;
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

  const hasComposerContent = Boolean(text.trim() || attachments.length);
  const canSend = Boolean(!sending && gateway.client && !gateway.requiredUpdate && hasComposerContent);

  async function chooseFile() {
    const picked = await DocumentPicker.getDocumentAsync({
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    setAttachments((current) => [
      ...current,
      ...picked.assets.slice(0, 8 - current.length).map((asset) => ({
        uri: asset.uri,
        name: asset.name,
        mediaType: asset.mimeType ?? "application/octet-stream",
      })),
    ].slice(0, 8));
  }

  async function send() {
    if (!gateway.client || !canSend) return;
    const message = text.trim() || "请看一下这些内容";
    const pending = [...attachments];
    setSending(true);
    setMessage("");
    try {
      const uploaded: ArtifactInput[] = [];
      let failed = false;
      for (let index = 0; index < pending.length; index += 1) {
        const item = pending[index]!;
        if (item.uploaded) {
          uploaded.push(item.uploaded);
          continue;
        }
        setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "uploading" } : value));
        try {
          const response = await fetch(item.uri);
          const bytes = await response.arrayBuffer();
          const artifact = await gateway.runAuthenticated((client) => client.uploadArtifact({
            sessionHandle: gateway.sessionHandle,
            bytes,
            mediaType: item.mediaType,
            name: item.name,
            caption: item.name,
          }));
          uploaded.push(artifact);
          const nextItem: PendingAttachment = { ...item, status: "uploaded", uploaded: artifact };
          pending[index] = nextItem;
          setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? nextItem : value));
        } catch {
          failed = true;
          const nextItem: PendingAttachment = { ...item, status: "failed" };
          pending[index] = nextItem;
          setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? nextItem : value));
        }
      }
      if (failed) {
        setMessage("有附件上传失败，点击失败项可单独重试");
        return;
      }
      const accepted = await gateway.runAuthenticated((client) => client.createChatTurn({
        sessionHandle: gateway.sessionHandle,
        text: message,
        attachments: uploaded,
      }));
      setTurns((current) => [...current, accepted]);
      watchTurn(accepted.turn_id);
      setText("");
      await storeConversationDraft(gateway.sessionHandle, "");
      setAttachments([]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "消息没有发出去，请重试");
    } finally {
      setSending(false);
    }
  }

  async function retryAttachment(index: number) {
    const item = attachments[index];
    if (!item || item.status !== "failed" || !gateway.client) return;
    setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "uploading" } : value));
    try {
      const response = await fetch(item.uri);
      const bytes = await response.arrayBuffer();
      const uploaded = await gateway.runAuthenticated((client) => client.uploadArtifact({
        sessionHandle: gateway.sessionHandle,
        bytes,
        mediaType: item.mediaType,
        name: item.name,
        caption: item.name,
      }));
      setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "uploaded", uploaded } : value));
      setMessage("附件已上传，可以继续发送");
    } catch {
      setAttachments((current) => current.map((value, itemIndex) => itemIndex === index ? { ...value, status: "failed" } : value));
      setMessage("这个附件仍未上传成功，请检查网络后重试");
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
        const response = await fetch(uri);
        const extension = uri.toLowerCase().endsWith(".webm") ? "webm" : "m4a";
        const bytes = await response.arrayBuffer();
        const artifact = await gateway.runAuthenticated((client) => client.uploadArtifact({
          sessionHandle: gateway.sessionHandle,
          bytes,
          mediaType: extension === "webm" ? "audio/webm" : "audio/mp4",
          name: `voice-${Date.now()}.${extension}`,
          caption: "语音输入",
        }));
        const transcript = await gateway.runAuthenticated((client) => client.transcribeArtifact(
          gateway.sessionHandle,
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
    if (!permission.granted) return;
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
    } catch (error) {
      await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
      setMessage(error instanceof Error ? error.message : "录音无法开始，请重试");
    }
  }

  async function resolve(approval: ChatApproval, approved: boolean) {
    if (!gateway.client || resolving) return;
    setResolving(approval.approval_id);
    try {
      await gateway.runAuthenticated(
        (client) => client.resolveChatApproval(approval.approval_id, approved),
      );
    } finally {
      setResolving("");
    }
  }

  const loadArtifact = useCallback(async (
    item: AssistantArtifactItem,
  ): Promise<ResolvedArtifactFile> => resolveAssistantArtifactFile(item, {
    cachedUri: (cacheFileName) => {
      const file = new File(Paths.cache, cacheFileName);
      return file.exists ? file.uri : null;
    },
    download: (artifactId) => gateway.runAuthenticated((client) => client.downloadArtifact(
      gateway.sessionHandle,
      artifactId,
    )),
    write: (cacheFileName, bytes) => {
      const file = new File(Paths.cache, cacheFileName);
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
      setMessage(error instanceof Error ? error.message : "附件暂时无法打开，请重试");
    }
  }, [loadArtifact]);

  async function startNewTopic() {
    if (startingTopic || sending) return;
    setStartingTopic(true);
    try {
      await gateway.newConversation();
      setText("");
      setAttachments([]);
    } finally {
      setStartingTopic(false);
    }
  }

  async function cancelTurn(turn: ChatTurnSnapshot) {
    try {
      const cancelled = await gateway.runAuthenticated((client) => client.cancelChatTurn(turn.turn_id));
      setTurns((current) => current.map((item) => item.turn_id === cancelled.turn_id ? cancelled : item));
    } catch {
      setMessage("暂时无法停止这次回复，请重试");
    }
  }

  async function retryTurn(turn: ChatTurnSnapshot) {
    try {
      const accepted = await gateway.runAuthenticated((client) => client.retryChatTurn(turn.turn_id));
      setTurns((current) => [...current, accepted]);
      watchTurn(accepted.turn_id);
    } catch {
      setMessage("这次回复暂时无法重试，你也可以编辑后重新发送");
    }
  }

  function editTurn(turn: ChatTurnSnapshot) {
    setText(turn.user_input);
    setMessage("已放回输入框，修改后可以重新发送");
  }

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      keyboardVerticalOffset={insets.top + (Platform.OS === "ios" ? 44 : 56)}
    >
      <View style={styles.topbar}>
        <Text style={styles.subtitle}>随时告诉我你想做什么</Text>
        <View style={styles.topActions}>
          <Pressable onPress={() => void startNewTopic()} disabled={startingTopic || sending}>
            <Text style={styles.link}>{startingTopic ? "创建中…" : "新话题"}</Text>
          </Pressable>
          <Pressable onPress={() => router.push("/conversations")}><Text style={styles.link}>会话</Text></Pressable>
        </View>
      </View>
      {gateway.status !== "ready" ? (
        <View style={styles.connectionBanner}>
          <View style={styles.bannerCopy}>
            <Text style={styles.connectionTitle}>暂时没有连接到小诺</Text>
            <Text style={styles.connectionDetail}>{gateway.error || "正在重新建立安全连接"}</Text>
          </View>
          <Pressable onPress={() => void gateway.reconnect()} style={styles.bannerButton}>
            <Text style={styles.bannerButtonText}>重连</Text>
          </Pressable>
        </View>
      ) : null}
      {gateway.availableUpdate ? (
        <Pressable style={styles.updateBanner} onPress={() => router.push("/update")}>
          <View style={styles.bannerCopy}>
            <Text style={styles.updateTitle}>小诺 {gateway.availableUpdate.version_name} 可以更新</Text>
            <Text style={styles.updateDetail}>查看版本说明并下载安装</Text>
          </View>
          <Text style={styles.updateLink}>更新</Text>
        </Pressable>
      ) : null}
      {gateway.requiredUpdate ? (
        <Pressable style={styles.requiredUpdateBanner} onPress={() => router.push("/update")}>
          <Text style={styles.requiredUpdateTitle}>需要更新后才能继续新对话</Text>
          <Text style={styles.updateDetail}>当前版本已不再支持创建消息，点击立即更新</Text>
        </Pressable>
      ) : null}
      <FlatList
        ref={list}
        style={styles.list}
        data={turns}
        keyExtractor={(item) => item.turn_id}
        contentContainerStyle={styles.messages}
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => {
          if (nearBottom.current) list.current?.scrollToEnd({ animated: true });
        }}
        onScroll={({ nativeEvent }) => {
          const distance = nativeEvent.contentSize.height
            - nativeEvent.layoutMeasurement.height
            - nativeEvent.contentOffset.y;
          nearBottom.current = distance < 80;
        }}
        scrollEventThrottle={100}
        ListEmptyComponent={<Text style={styles.empty}>你好，我是小诺。</Text>}
        renderItem={({ item }) => (
          <ChatTurn
            turn={item}
            resolving={Boolean(resolving)}
            onResolve={resolve}
            onLoadArtifact={loadArtifact}
            onOpenArtifact={openArtifact}
            onCancel={cancelTurn}
            onRetry={retryTurn}
            onEdit={editTurn}
          />
        )}
      />
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
                {item.status ? <Text style={[styles.attachmentStatus, item.status === "failed" && styles.attachmentFailed]}>{attachmentStatusLabel(item.status)}</Text> : null}
              </Pressable>
              <Pressable accessibilityLabel={`移除 ${item.name}`} onPress={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}>
                <Text style={styles.remove}>×</Text>
              </Pressable>
            </View>
          ))}
        </View>
      ) : null}
      {message ? (
        <Pressable onPress={() => setMessage("")} style={styles.errorBanner}>
          <Text style={styles.errorText}>{message}</Text>
        </Pressable>
      ) : null}
      <View style={styles.composer}>
        <Pressable
          accessibilityLabel="添加照片或文件"
          onPress={() => setActionsOpen(true)}
          style={styles.roundAction}
        >
          <LineIcon name="plus" color={colors.accent} />
        </Pressable>
        <View style={styles.inputShell}>
          <Pressable
            accessibilityLabel={inputMode === "text" ? "切换到语音输入" : "切换到文字输入"}
            disabled={recording.isRecording || transcribing}
            onPress={() => setInputMode((current) => current === "text" ? "voice" : "text")}
            style={styles.inputMode}
          >
            <LineIcon name={inputMode === "text" ? "mic" : "keyboard"} color={colors.muted} size={20} />
          </Pressable>
          <TextInput
            editable={inputMode === "text"}
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder={inputMode === "text" ? "和小诺说点什么…" : "语音转写会出现在这里"}
            placeholderTextColor={colors.muted}
            multiline
          />
        </View>
        <Pressable
          accessibilityLabel={inputMode === "voice" ? recording.isRecording ? "停止录音" : "开始录音" : "发送"}
          onPress={() => {
            if (inputMode === "voice") void toggleRecording();
            else void send();
          }}
          disabled={inputMode === "text"
            ? !canSend
            : sending || transcribing || (!gateway.client && !recording.isRecording)}
          style={[
            styles.primaryAction,
            recording.isRecording && styles.primaryRecording,
            (inputMode === "text"
              ? !canSend
              : sending || transcribing || (!gateway.client && !recording.isRecording)) && styles.sendDisabled,
          ]}
        >
          {sending || transcribing ? (
            <ActivityIndicator color="white" size="small" />
          ) : recording.isRecording ? (
            <View style={styles.recordingContent}>
              <LineIcon name="stop" color="white" size={17} />
              <Text style={styles.recordingTime}>{Math.round(recording.durationMillis / 1000)}s</Text>
            </View>
          ) : inputMode === "text" ? (
            <Text style={styles.sendText}>发送</Text>
          ) : (
            <LineIcon name="mic" color="white" />
          )}
        </Pressable>
      </View>
      <ArtifactViewer
        file={imagePreview}
        onClose={() => setImagePreview(null)}
        onMessage={setMessage}
      />
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
            <Text style={styles.sheetTitle}>添加内容</Text>
            <View style={styles.sheetActions}>
              <MediaAction
                icon="camera"
                label="拍照"
                onPress={() => {
                  setActionsOpen(false);
                  router.push("/capture");
                }}
              />
              <MediaAction
                icon="file"
                label="文件"
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
  );
}

function MediaAction({ icon, label, onPress }: { icon: "camera" | "file"; label: string; onPress(): void }) {
  return (
    <Pressable onPress={onPress} style={styles.mediaAction}>
      <View style={styles.mediaIcon}><LineIcon name={icon} color={colors.accent} size={28} /></View>
      <Text style={styles.mediaLabel}>{label}</Text>
    </Pressable>
  );
}

function LineIcon({
  name,
  color,
  size = 22,
}: {
  name: "plus" | "camera" | "file" | "mic" | "keyboard" | "stop";
  color: string;
  size?: number;
}) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      {name === "plus" ? (
        <><Line x1="12" y1="5" x2="12" y2="19" stroke={color} strokeWidth="2" strokeLinecap="round" /><Line x1="5" y1="12" x2="19" y2="12" stroke={color} strokeWidth="2" strokeLinecap="round" /></>
      ) : null}
      {name === "camera" ? (
        <><Path d="M4 7.5h3l1.4-2h7.2l1.4 2h3v11H4z" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Circle cx="12" cy="13" r="3.2" stroke={color} strokeWidth="1.8" /></>
      ) : null}
      {name === "file" ? (
        <><Path d="M7 3.5h6l4 4V20.5H7z" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Path d="M13 3.5v4h4" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Line x1="9.5" y1="12" x2="14.5" y2="12" stroke={color} strokeWidth="1.6" strokeLinecap="round" /><Line x1="9.5" y1="15.5" x2="14.5" y2="15.5" stroke={color} strokeWidth="1.6" strokeLinecap="round" /></>
      ) : null}
      {name === "mic" ? (
        <><Rect x="9" y="3" width="6" height="11" rx="3" stroke={color} strokeWidth="1.8" /><Path d="M6.5 11.5a5.5 5.5 0 0 0 11 0M12 17v4M9 21h6" stroke={color} strokeWidth="1.8" strokeLinecap="round" /></>
      ) : null}
      {name === "keyboard" ? (
        <><Rect x="3" y="6" width="18" height="12" rx="2.5" stroke={color} strokeWidth="1.8" /><Path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M6.5 13.5h.01M10 13.5h.01M14 13.5h.01M17.5 13.5h.01M8 16h8" stroke={color} strokeWidth="2" strokeLinecap="round" /></>
      ) : null}
      {name === "stop" ? <Rect x="6" y="6" width="12" height="12" rx="2.5" fill={color} /> : null}
    </Svg>
  );
}

function ChatTurn({
  turn,
  resolving,
  onResolve,
  onLoadArtifact,
  onOpenArtifact,
  onCancel,
  onRetry,
  onEdit,
}: {
  turn: ChatTurnSnapshot;
  resolving: boolean;
  onResolve(approval: ChatApproval, approved: boolean): void;
  onLoadArtifact(item: AssistantArtifactItem): Promise<ResolvedArtifactFile>;
  onOpenArtifact(item: AssistantArtifactItem): Promise<void>;
  onCancel(turn: ChatTurnSnapshot): void;
  onRetry(turn: ChatTurnSnapshot): void;
  onEdit(turn: ChatTurnSnapshot): void;
}) {
  const response = turn.final_output || turn.content;
  const approval = turn.approvals.find((item) => item.state === "pending") ?? null;
  const activity = activityText(turn);
  const artifactItems = useMemo(() => assistantArtifactItems(turn.artifacts), [turn.artifacts]);
  return (
    <View style={styles.turn}>
      <View style={styles.userBubble}>
        <Text style={styles.userText}>{turn.user_input}</Text>
        {turn.attachments.length ? <Text style={styles.userMeta}>附件 {turn.attachments.length}</Text> : null}
      </View>
      <View style={styles.assistantBubble}>
        {response ? (
          <AppMarkdown value={response} style={styles.markdownList} />
        ) : (
          <View style={styles.activityRow}>
            {!TERMINAL_STATES.has(turn.state) ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            <Text style={styles.activity}>{activity}</Text>
          </View>
        )}
        {artifactItems.length ? (
          <View style={styles.generatedArtifacts}>
            {artifactItems.map((item) => (
              <AssistantArtifact
                key={item.key}
                item={item}
                onLoad={onLoadArtifact}
                onOpen={onOpenArtifact}
              />
            ))}
          </View>
        ) : null}
        {approval ? (
          <View style={styles.approval}>
            <Text style={styles.approvalReason}>{approval.reason || approval.tool_name}</Text>
            <View style={styles.approvalActions}>
              <Pressable style={styles.deny} disabled={resolving} onPress={() => onResolve(approval, false)}>
                <Text style={styles.denyText}>取消</Text>
              </Pressable>
              <Pressable style={styles.approve} disabled={resolving} onPress={() => onResolve(approval, true)}>
                <Text style={styles.approveText}>确认</Text>
              </Pressable>
            </View>
          </View>
        ) : null}
        {turn.state === "running" || turn.state === "waiting_approval" ? (
          <Pressable accessibilityRole="button" onPress={() => onCancel(turn)} style={styles.turnAction}>
            <Text style={styles.turnActionText}>停止</Text>
          </Pressable>
        ) : null}
        {turn.state === "failed" || turn.state === "cancelled" ? (
          <View style={styles.turnActions}>
            <Pressable accessibilityRole="button" onPress={() => onRetry(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>重试</Text>
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => onEdit(turn)} style={styles.turnAction}>
              <Text style={styles.turnActionText}>编辑后重发</Text>
            </Pressable>
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
}: {
  item: AssistantArtifactItem;
  onLoad(item: AssistantArtifactItem): Promise<ResolvedArtifactFile>;
  onOpen(item: AssistantArtifactItem): Promise<void>;
}) {
  const [previewUri, setPreviewUri] = useState("");
  const [loading, setLoading] = useState(item.isImage);
  const [failed, setFailed] = useState(false);
  const [opening, setOpening] = useState(false);
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

  if (item.isImage) {
    return (
      <Pressable
        accessibilityLabel={failed ? `重新加载 ${item.displayName}` : `打开 ${item.displayName}`}
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
              {failed ? "加载失败，点击重试" : "正在加载图片…"}
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
    <Pressable
      accessibilityLabel={`打开 ${item.displayName}`}
      disabled={opening}
      onPress={() => void open()}
      style={styles.generatedFile}
    >
      <View style={styles.generatedFileBadge}><Text style={styles.generatedFileBadgeText}>附件</Text></View>
      <Text style={styles.generatedArtifactName} numberOfLines={2}>{item.displayName}</Text>
      {opening ? <ActivityIndicator color={colors.accent} size="small" /> : null}
    </Pressable>
  );
}

function activityText(turn: ChatTurnSnapshot): string {
  if (turn.state === "failed") return "这次没有完成";
  if (turn.state === "cancelled") return "已停止";
  if (turn.state === "waiting_approval") return "等你确认";
  const tool = [...turn.timeline].reverse().find((entry) => entry.kind === "tool_call");
  if (tool) return `正在使用 ${tool.tool_name || "工具"}`;
  return "正在思考…";
}

function attachmentStatusLabel(status: NonNullable<PendingAttachment["status"]>): string {
  return ({
    pending: "等待上传",
    uploading: "上传中…",
    uploaded: "已上传",
    failed: "上传失败 · 点击重试",
  })[status];
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  list: { flex: 1 },
  topbar: { paddingHorizontal: 16, paddingVertical: 10, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  subtitle: { color: colors.muted, fontSize: 13 },
  topActions: { flexDirection: "row", gap: 16 },
  link: { color: colors.accent, fontWeight: "600" },
  messages: { padding: 16, paddingBottom: 24, gap: 18, flexGrow: 1 },
  empty: { color: colors.muted, textAlign: "center", marginTop: 80, fontSize: 17 },
  turn: { gap: 8 },
  userBubble: { alignSelf: "flex-end", maxWidth: "84%", backgroundColor: colors.accent, borderRadius: 18, borderBottomRightRadius: 5, paddingHorizontal: 15, paddingVertical: 11 },
  userText: { color: "white", fontSize: 16, lineHeight: 23 },
  userMeta: { color: "#DCEAE4", fontSize: 12, marginTop: 5 },
  assistantBubble: { alignSelf: "stretch", width: "100%", backgroundColor: colors.surface, borderRadius: 18, borderBottomLeftRadius: 5, padding: 15, borderWidth: 1, borderColor: colors.line },
  markdownList: { width: "100%", alignSelf: "stretch" },
  activityRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  activity: { color: colors.muted },
  approval: { marginTop: 12, borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12, gap: 10 },
  approvalReason: { color: colors.ink, lineHeight: 21 },
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
  connectionBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: "#FFF3ED", flexDirection: "row", alignItems: "center", gap: 12 },
  bannerCopy: { flex: 1 },
  connectionTitle: { color: colors.ink, fontWeight: "700" },
  connectionDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  bannerButton: { paddingHorizontal: 13, paddingVertical: 8, borderRadius: 11, backgroundColor: colors.surface },
  bannerButtonText: { color: colors.accent, fontWeight: "700" },
  updateBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: colors.accentSoft, flexDirection: "row", alignItems: "center", gap: 12 },
  requiredUpdateBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: "#FCE9E7", gap: 4 },
  requiredUpdateTitle: { color: colors.danger, fontWeight: "700" },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  updateLink: { color: colors.accent, fontWeight: "700" },
  errorBanner: { paddingHorizontal: 16, paddingVertical: 9, backgroundColor: "#FFF3ED", borderTopWidth: 1, borderTopColor: "#F3D7CB" },
  errorText: { color: colors.danger, textAlign: "center", fontSize: 13 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.line },
  roundAction: { width: 42, height: 42, borderRadius: 21, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  inputShell: { flex: 1, minHeight: 42, maxHeight: 120, flexDirection: "row", alignItems: "flex-end", backgroundColor: colors.background, borderRadius: 16 },
  inputMode: { width: 40, height: 42, alignItems: "center", justifyContent: "center" },
  input: { flex: 1, minHeight: 42, maxHeight: 120, color: colors.ink, paddingRight: 13, paddingVertical: 10, textAlignVertical: "top" },
  primaryAction: { minWidth: 52, height: 42, paddingHorizontal: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, borderRadius: 14 },
  primaryRecording: { backgroundColor: colors.danger },
  recordingContent: { flexDirection: "row", alignItems: "center", gap: 5 },
  recordingTime: { color: "white", fontSize: 12, fontWeight: "700" },
  sendDisabled: { opacity: 0.45 },
  sendText: { color: "white", fontWeight: "700" },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { position: "absolute", top: 0, right: 0, bottom: 0, left: 0, backgroundColor: "rgba(25, 31, 29, 0.28)" },
  actionSheet: { backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 22, paddingTop: 10, paddingBottom: 34, gap: 18 },
  sheetHandle: { width: 38, height: 4, borderRadius: 2, backgroundColor: colors.line, alignSelf: "center" },
  sheetTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
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
